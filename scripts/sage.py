#!/usr/bin/env python3
"""Atlas Sage — Long-Cycle Promotion Agent.

Sage runs every 4 hours, scanning the research sandbox (config/candidates/)
and the experiment queue for 'passed' experiments, sanity-checking them,
and promoting up to 2 per cycle to the active config.

Usage:
    python3 scripts/sage.py [--once] [--market sp500]

Options:
    --once      Run one cycle and exit (useful for systemd OneShot or testing)
    --market    Only promote for this market (default: all markets)
    --dry-run   Validate but do not actually promote
    --cycle-hours  Cycle length in hours (default: 4)
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ── Project Setup ──────────────────────────────────────────────────────────

PROJECT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT)
sys.path.insert(0, str(PROJECT))

# ── Constants ──────────────────────────────────────────────────────────────

HEARTBEAT_PATH = Path("/tmp/sage-heartbeat.json")
STOP_PATH      = Path("/tmp/sage-stop")
LOG_PATH       = PROJECT / "logs" / "sage.log"
DEFAULT_CYCLE_HOURS = 4
MAX_PROMOTIONS_PER_CYCLE = 2

# ── Logging ────────────────────────────────────────────────────────────────

logger = logging.getLogger("sage")


def setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.handlers.clear()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [sage] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(LOG_PATH, mode="a"),
        ],
        force=True,
    )
    for noisy in ("urllib3", "matplotlib", "numexpr"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Stop / Heartbeat ───────────────────────────────────────────────────────

def should_stop() -> bool:
    return STOP_PATH.exists()


def write_heartbeat(phase: str, cycle: int, **extra) -> None:
    """Atomically write a heartbeat JSON to /tmp/sage-heartbeat.json."""
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "phase": phase,
        "cycle": cycle,
        "status": "running",
        **extra,
    }
    tmp = HEARTBEAT_PATH.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(HEARTBEAT_PATH)
    except OSError as e:
        logger.debug("Heartbeat write failed: %s", e)


def write_stopped_heartbeat(cycle: int) -> None:
    data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "phase": "stopped",
        "cycle": cycle,
        "status": "stopped",
    }
    try:
        HEARTBEAT_PATH.write_text(json.dumps(data, indent=2))
    except OSError:
        pass


# ── Telegram ──────────────────────────────────────────────────────────────

def send_telegram(message: str, level=None) -> None:
    """Best-effort Telegram notification."""
    try:
        from utils.telegram import notify, IMPORTANT
        if level is None:
            level = IMPORTANT
        notify(message, level=level, category="sage")
    except Exception as e:
        logger.debug("Telegram failed: %s", e)


# ── Candidate Scanning ────────────────────────────────────────────────────

def scan_candidates(market: str | None = None) -> list[dict]:
    """Scan for promotion candidates from two sources:

    1. config/candidates/*.json — staged candidate configs from research_promote
    2. research/queue.json with status='passed' — experiments the researcher flagged

    Returns list of dicts: {experiment_id, market, source, candidate_path}
    """
    from research.models import read_queue, ExperimentStatus, CANDIDATES_DIR

    found: list[dict] = []

    # ── Source 1: Staged candidate configs ──────────────────────
    candidates_dir = PROJECT / "config" / "candidates"
    if candidates_dir.exists():
        for path in sorted(candidates_dir.glob("*.json")):
            if path.name.startswith("."):
                continue
            try:
                with open(path) as f:
                    cfg = json.load(f)
                meta = cfg.get("_promotion_metadata", {})
                exp_id = meta.get("experiment_id", path.stem)
                market_id = meta.get("market", path.stem.split("_")[0])
                if market and market_id != market:
                    continue
                # Skip if already promoted (has a version)
                if meta.get("promoted_at"):
                    continue
                found.append({
                    "experiment_id": exp_id,
                    "market": market_id,
                    "source": "candidates",
                    "candidate_path": str(path),
                })
            except (json.JSONDecodeError, OSError) as e:
                logger.debug("Skipping %s: %s", path.name, e)

    # ── Source 2: Research queue with status='passed' ────────────
    try:
        queue = read_queue()
        for entry in queue:
            if entry.get("status") not in (ExperimentStatus.PASSED, "passed"):
                continue
            exp_id = entry.get("id", "")
            market_id = entry.get("market", "sp500")
            if market and market_id != market:
                continue
            # Check if already in candidates list (avoid duplicates)
            if any(c["experiment_id"] == exp_id for c in found):
                continue
            # Check if a candidate file exists for this experiment
            candidate_path = PROJECT / "config" / "candidates" / f"{market_id}_{exp_id}.json"
            found.append({
                "experiment_id": exp_id,
                "market": market_id,
                "source": "queue",
                "candidate_path": str(candidate_path) if candidate_path.exists() else None,
                "queue_entry": entry,
            })
    except Exception as e:
        logger.warning("Queue scan failed: %s", e)

    return found


# ── Sanity Check ──────────────────────────────────────────────────────────

def sanity_check(candidate: dict) -> tuple[bool, str]:
    """Quick sanity checks before attempting full promotion validation.

    These are cheap pre-flight checks to avoid wasting time on obviously
    broken or already-promoted candidates.

    Returns (ok: bool, reason: str)
    """
    exp_id = candidate["experiment_id"]
    market_id = candidate["market"]
    candidate_path = candidate.get("candidate_path")

    # 1. Candidate config must exist for 'candidates' source
    if candidate["source"] == "candidates":
        if not candidate_path or not Path(candidate_path).exists():
            return False, f"Candidate file not found: {candidate_path}"

    # 2. Experiment must not already be promoted
    try:
        from research.models import load_experiment
        exp = load_experiment(exp_id)
        if exp and exp.get("promoted"):
            return False, f"Already promoted"
    except Exception:
        pass

    # 3. Check rate limit (at most 1 promotion per week per market)
    try:
        from research.models import get_recent_promotions
        recent = get_recent_promotions(market_id, days=7)
        if len(recent) >= 1:
            return False, f"Rate limited: {len(recent)} promotion(s) in past 7 days"
    except Exception:
        pass

    # 4. If no candidate config, try staging it from the queue entry
    if not candidate_path or not Path(candidate_path).exists():
        qe = candidate.get("queue_entry", {})
        strategy_params = qe.get("params_override")
        enable_strategy = qe.get("strategy_name")
        if not strategy_params and not enable_strategy:
            return False, "No candidate config and no params to stage"
        try:
            from scripts.research_promote import stage_candidate
            staged = stage_candidate(exp_id, market_id,
                                     strategy_params=strategy_params,
                                     enable_strategy=enable_strategy)
            candidate["candidate_path"] = str(staged)
            logger.info("Staged candidate for %s: %s", exp_id, staged)
        except Exception as e:
            return False, f"Staging failed: {e}"

    return True, "ok"


# ── Validation ────────────────────────────────────────────────────────────

def validate_candidate(candidate: dict, dry_run: bool = False) -> tuple[bool, dict]:
    """Run full OOS + regression validation on a candidate.

    Returns (passed: bool, validation_result: dict)
    """
    exp_id = candidate["experiment_id"]
    market_id = candidate["market"]
    candidate_path_str = candidate.get("candidate_path")
    if not candidate_path_str:
        return False, {"error": "No candidate_path"}

    candidate_path = Path(candidate_path_str)
    if not candidate_path.exists():
        return False, {"error": f"File not found: {candidate_path}"}

    if dry_run:
        logger.info("[DRY-RUN] Would validate %s for %s", exp_id, market_id)
        return True, {"dry_run": True, "overall_pass": True}

    try:
        from scripts.research_promote import validate_candidate as _validate
        result = _validate(exp_id, market_id,
                           candidate_path=candidate_path,
                           skip_oos=False)
        passed = result.get("overall_pass", False)
        return passed, result
    except Exception as e:
        logger.error("Validation error for %s: %s", exp_id, e, exc_info=True)
        return False, {"error": str(e)}


# ── Promotion ─────────────────────────────────────────────────────────────

def promote(candidate: dict, validation_result: dict, dry_run: bool = False) -> bool:
    """Promote a validated candidate to active config.

    Returns True on success.
    """
    exp_id = candidate["experiment_id"]
    market_id = candidate["market"]
    candidate_path_str = candidate.get("candidate_path")

    if dry_run:
        logger.info("[DRY-RUN] Would promote %s for %s", exp_id, market_id)
        # Send dry-run notification
        send_telegram(
            f"🔬 [Sage DRY-RUN] Would promote <code>{exp_id}</code> "
            f"for {market_id.upper()} — validation PASSED"
        )
        return True

    try:
        from scripts.research_promote import (
            promote_candidate, send_promotion_request
        )

        # Send Telegram promotion request (with approve/reject buttons)
        send_promotion_request(exp_id, market_id, validation_result)

        # Auto-promote (Sage is autonomous — no human gate in this mode)
        result = promote_candidate(exp_id, market_id,
                                   candidate_path=Path(candidate_path_str))
        if result.get("success"):
            version = result.get("version_path", "?")
            logger.info("PROMOTED %s → %s", exp_id, version)
            send_telegram(
                f"✅ <b>Sage promoted</b> <code>{exp_id}</code> "
                f"for {market_id.upper()}\n"
                f"Config: <code>{Path(version).name}</code>"
            )
            return True
        else:
            err = result.get("error", "unknown error")
            logger.warning("Promotion failed for %s: %s", exp_id, err)
            send_telegram(
                f"⚠️ <b>Sage promote FAILED</b> <code>{exp_id}</code> "
                f"({market_id.upper()}): {err}"
            )
            return False
    except Exception as e:
        logger.error("Promote error for %s: %s", exp_id, e, exc_info=True)
        send_telegram(
            f"❌ <b>Sage promote ERROR</b> <code>{exp_id}</code>: {e}"
        )
        return False


# ── Cycle ─────────────────────────────────────────────────────────────────

def run_cycle(cycle: int, market: str | None, dry_run: bool) -> dict:
    """Execute one full Sage cycle: scan → sanity check → validate → promote.

    Returns summary dict with counts.
    """
    logger.info("══ Sage cycle %d started (market=%s, dry_run=%s) ══",
                cycle, market or "all", dry_run)
    write_heartbeat("scan", cycle, candidates_found=0)

    # ── 1. Scan ──────────────────────────────────────────────────
    candidates = scan_candidates(market)
    logger.info("Scan found %d candidate(s)", len(candidates))
    write_heartbeat("scan", cycle, candidates_found=len(candidates))

    if not candidates:
        logger.info("No candidates found — cycle complete")
        return {"candidates_found": 0, "validated": 0, "promoted": 0}

    # ── 2. Sanity check → filter ──────────────────────────────────
    write_heartbeat("sanity_check", cycle, candidates_found=len(candidates))
    sane: list[dict] = []
    for c in candidates:
        ok, reason = sanity_check(c)
        if ok:
            sane.append(c)
            logger.info("SANE: %s (%s)", c["experiment_id"], c["market"])
        else:
            logger.info("SKIP: %s — %s", c["experiment_id"], reason)

    logger.info("Sanity: %d/%d passed", len(sane), len(candidates))
    if not sane:
        return {"candidates_found": len(candidates), "validated": 0, "promoted": 0}

    # ── 3. Validate & promote (max 2 per cycle) ───────────────────
    promotions = 0
    validated_count = 0

    for c in sane:
        if promotions >= MAX_PROMOTIONS_PER_CYCLE:
            logger.info("Reached max %d promotions — stopping", MAX_PROMOTIONS_PER_CYCLE)
            break
        if should_stop():
            logger.info("Stop requested during cycle — aborting")
            break

        exp_id = c["experiment_id"]
        write_heartbeat("validate", cycle,
                        candidates_found=len(candidates),
                        current_experiment=exp_id,
                        promotions_this_cycle=promotions)

        logger.info("Validating %s (%s)…", exp_id, c["market"])
        passed, result = validate_candidate(c, dry_run=dry_run)
        validated_count += 1

        if passed:
            write_heartbeat("promote", cycle,
                            candidates_found=len(candidates),
                            current_experiment=exp_id,
                            promotions_this_cycle=promotions)
            ok = promote(c, result, dry_run=dry_run)
            if ok:
                promotions += 1
        else:
            err = result.get("error") or "validation failed"
            logger.info("SKIP promote %s: %s", exp_id, err)

    summary = {
        "candidates_found": len(candidates),
        "validated": validated_count,
        "promoted": promotions,
    }
    logger.info("Cycle %d done: %s", cycle, summary)
    return summary


# ── Main ──────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Atlas Sage — long-cycle promotion agent")
    p.add_argument("--once",         action="store_true",
                   help="Run one cycle then exit")
    p.add_argument("--market",       type=str, default=None,
                   help="Restrict to this market (e.g. sp500, asx)")
    p.add_argument("--dry-run",      action="store_true",
                   help="Validate but do not actually promote")
    p.add_argument("--cycle-hours",  type=float, default=DEFAULT_CYCLE_HOURS,
                   help=f"Hours between cycles (default: {DEFAULT_CYCLE_HOURS})")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging()

    logger.info("Sage starting (pid=%d, once=%s, market=%s, dry_run=%s, cycle_hours=%g)",
                os.getpid(), args.once, args.market or "all",
                args.dry_run, args.cycle_hours)

    # Remove stale stop file if present from a previous run
    if STOP_PATH.exists():
        STOP_PATH.unlink(missing_ok=True)
        logger.info("Removed stale stop file")

    cycle = 0
    cycle_sleep_s = int(args.cycle_hours * 3600)

    send_telegram(
        f"🌿 <b>Sage started</b> (pid={os.getpid()}, "
        f"cycle={args.cycle_hours}h, dry_run={args.dry_run})"
    )

    try:
        while True:
            cycle += 1
            if should_stop():
                logger.info("Stop file detected before cycle %d — exiting", cycle)
                break

            write_heartbeat("cycle_start", cycle)
            try:
                summary = run_cycle(cycle, market=args.market, dry_run=args.dry_run)
            except Exception as e:
                logger.error("Cycle %d failed: %s", cycle, e, exc_info=True)
                send_telegram(f"❌ <b>Sage cycle {cycle} ERROR</b>: {e}")
                summary = {"error": str(e)}

            write_heartbeat("sleep", cycle,
                            last_summary=summary,
                            next_cycle_in_s=cycle_sleep_s)

            if args.once:
                logger.info("--once: exiting after cycle %d", cycle)
                break
            if should_stop():
                logger.info("Stop file detected after cycle %d — exiting", cycle)
                break

            logger.info("Sleeping %g hours until next cycle…", args.cycle_hours)
            # Sleep in 60s increments so stop file is checked regularly
            slept = 0
            while slept < cycle_sleep_s:
                if should_stop():
                    logger.info("Stop file detected during sleep — exiting")
                    break
                time.sleep(min(60, cycle_sleep_s - slept))
                slept += 60
            else:
                continue  # Inner while finished normally → continue outer loop
            break          # Stop file hit during sleep

    finally:
        write_stopped_heartbeat(cycle)
        logger.info("Sage stopped (cycle=%d)", cycle)
        send_telegram(f"🛑 <b>Sage stopped</b> (last cycle={cycle})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
