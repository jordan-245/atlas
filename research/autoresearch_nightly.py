#!/usr/bin/env python3
"""Nightly autoresearch orchestrator — runs all strategies in parallel.

Spawns one ``autoresearch_runner.py`` subprocess per strategy.  Workers share
the same frozen data snapshot but maintain separate backtests, TSV logs, and
brain param files.

Resource budget: each worker uses ~1-2 cores during backtest and ~2 GB RAM.
With ``--workers 5`` on an 8-core VPS, leaves 3 cores for system + cron.

Usage::

    # Parallel sweep of all 5 strategies for 8 hours:
    python3 research/autoresearch_nightly.py --hours 8 --workers 5 --notify

    # Only 2 strategies:
    python3 research/autoresearch_nightly.py --hours 4 --workers 2 \\
        --strategies mean_reversion,trend_following

Concurrency safety:
- Each worker writes to its own ``research/results/{strategy}.tsv``
- Each worker writes to its own ``research/best/{strategy}.json``
- Brain param files are per-param, workers rarely overlap
- ``research/journal.json`` uses ``fcntl.LOCK_EX`` (via ``_locked_append``)
- Evaluation lock files are per-session (unique session IDs)
- The data snapshot is read-only
"""

import argparse
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

from research.db import log_session, end_session
from research.snapshots import find_latest_snapshot as _find_latest_snapshot

# Strategies covered by the nightly autoresearch sweep.
# Includes everything in scripts/strategy_evaluator.STRATEGY_REGISTRY that has
# active live use OR is enabled in any config/active/*.json, OR has a
# research_best row. (bb_squeeze: research_best sp500 sharpe=0.49;
# mtf_momentum: disabled everywhere + no research_best — intentionally excluded)
DEFAULT_STRATEGIES = [
    "mean_reversion",
    "trend_following",
    "opening_gap",
    "momentum_breakout",
    "sector_rotation",
    "connors_rsi2",
    "short_term_mr",
    "bb_squeeze",
]

RUNNER_SCRIPT = ATLAS_ROOT / "research" / "autoresearch_runner.py"
LOGS_DIR = ATLAS_ROOT / "logs"
RESULTS_DIR = ATLAS_ROOT / "research" / "results"

import logging
_logger = logging.getLogger(__name__)

# Per-universe operator-set FLOORS (lower bounds): alert if the sweep produces
# fewer rows than this.  Calibrated 2026-05-12 against last-11-days production
# data.  Threshold = max(operator_floor, enabled_strategies * MIN_ROWS_PER_STRATEGY)
# so neither floor can weaken the other.
# errors table ids 19,20,21,27-29 db → gold/commodity false-positives fixed by
# lowering calibrated floors to match actual narrow-universe output.
MIN_ROWS_PER_UNIVERSE = {
    "sp500": 50,          # typical 100-330 rows, 2 enabled — preserve alert sensitivity
    "commodity_etfs": 5,  # typical 6-30 rows, 3 enabled (recent runs low)
    "sector_etfs": 20,    # typical 13-44 rows, 2 enabled
    "gold_etfs": 3,       # typical 1-8 rows, 1 enabled strategy
    "treasury_etfs": 10,  # no enabled strategies — conservative sentinel
    "defensive_etfs": 10,
    "crypto": 10,
    "asx": 10,
}
DEFAULT_MIN_ROWS = 10
MIN_ROWS_PER_STRATEGY = 3  # Absolute floor: at least 3 rows per enabled strategy

# Expected solo-screen rows produced by ONE enabled strategy in a nightly
# fast-screen sweep, per universe. Calibrated 2026-06-01 against production
# output: a single sp500 momentum_breakout fast-screen run emits ~38 rows.
# The silent-failure / degraded floor scales with the number of CURRENTLY
# enabled strategies (enabled * per_strategy) so a trimmed allow-list is not
# falsely flagged (a 1-strategy sp500 sweep expects ~25, not a stale 50), while
# a genuine collapse toward zero still alerts. (#392)
ROWS_PER_STRATEGY_BY_UNIVERSE = {
    "sp500": 25,
    "commodity_etfs": 3,
    "sector_etfs": 10,
    "gold_etfs": 3,
    "treasury_etfs": 5,
    "defensive_etfs": 5,
    "crypto": 5,
    "asx": 5,
}
DEFAULT_ROWS_PER_STRATEGY = 3

# DEGRADED guard (#392): every TSV experiment row is mirrored to SQLite via
# log_experiment, so a healthy sweep has DB rows of the same order as the TSV
# screened count. The DEGRADED warning only fires when SQLite holds LESS than
# this fraction of the TSV output it should mirror (a real DB-write degradation).
# A healthy low-yield run (e.g. 1 active strategy, ~38 screened, 0 keeps) is
# never flagged because its DB rows track its TSV rows.
TSV_DB_CONSISTENCY_FRACTION = 0.5

# Exhaustion / rotation guard (#392). When a strategy's recent experiment
# history shows enough attempts with ZERO real keeps, its parameter space is
# treated as exhausted and the nightly run emits a clear recommendation to
# redirect effort toward #387 (volatility-aware / fractional-Kelly sizing) or
# #388 (validate one additive strategy with OOS + correlation gates) rather than
# endlessly fine-tuning the same params. This is REPORTING ONLY — it never
# enables dormant strategies and never promotes/stages a config.
EXHAUSTION_LOOKBACK = 120          # examine the most recent N non-baseline experiments
EXHAUSTION_MIN_EXPERIMENTS = 50    # require this much recent signal before flagging
EXHAUSTION_NEXT_STEPS = (
    "redirect to #387 (volatility-aware / fractional-Kelly sizing) or #388 "
    "(validate one additive strategy with OOS + correlation gates) instead of "
    "further parameter fine-tuning. No strategy is enabled and no config is "
    "promoted automatically."
)



# ─── TSV Parsing ─────────────────────────────────────────────────────────────


def _parse_session_results(
    strategy: str,
    session_start_ts: float,
) -> Dict:
    """Parse a strategy's TSV for experiments written after *session_start_ts*.

    Returns dict with counts and Sharpe values.
    """
    tsv_path = RESULTS_DIR / f"{strategy}.tsv"
    result = {
        "strategy": strategy,
        "screened": 0,
        "promoted": 0,
        "kept": 0,
        "baseline": 0,  # baseline rows — the bar to beat, never a real keep (#392)
        "starting_sharpe": 0.0,
        "final_sharpe": 0.0,
    }
    if not tsv_path.exists():
        return result

    # Read lines written during this session (after session_start_ts)
    cutoff = datetime.fromtimestamp(session_start_ts, tz=timezone.utc)
    lines = tsv_path.read_text().strip().split("\n")
    if len(lines) <= 1:
        return result

    session_lines = []
    for line in lines[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) < 9:
            continue
        try:
            row_ts = datetime.fromisoformat(parts[0].replace("Z", "+00:00"))
            if row_ts.tzinfo is None:
                row_ts = row_ts.replace(tzinfo=timezone.utc)
            if row_ts >= cutoff:
                session_lines.append(parts)
        except (ValueError, IndexError):
            continue

    if not session_lines:
        return result

    # Count by status column (index 7). Baseline rows are recorded with
    # status='keep'/description='baseline' (and, for SQLite, status='baseline');
    # they establish the bar to beat and must NEVER be counted as a real keep or
    # promotion (#392). Track them in a dedicated `baseline` bucket so the
    # nightly summary can distinguish them from actual kept improvements.
    for parts in session_lines:
        status = parts[7].strip() if len(parts) > 7 else ""
        description = parts[8].strip() if len(parts) > 8 else ""
        is_baseline_row = (
            status == "baseline"
            or (status == "keep" and description.lower() == "baseline")
        )
        if is_baseline_row:
            result["baseline"] += 1
        elif status == "discard_solo":
            result["screened"] += 1
        elif status == "discard":
            result["screened"] += 1
            result["promoted"] += 1
        elif status == "keep":
            result["screened"] += 1
            result["promoted"] += 1
            result["kept"] += 1

    # Sharpe: baseline is the first baseline row (status='baseline' OR
    # status='keep'/description='baseline').
    # Final Sharpe: last real 'keep' that isn't baseline, or baseline if no keeps.
    for parts in session_lines:
        if len(parts) <= 8:
            continue
        _status = parts[7].strip()
        _desc = parts[8].strip().lower()
        if _status == "baseline" or (_status == "keep" and _desc == "baseline"):
            try:
                result["starting_sharpe"] = float(parts[1])
                result["final_sharpe"] = float(parts[1])
            except ValueError:
                pass
            break

    # Find the last kept experiment's Sharpe (if any)
    for parts in reversed(session_lines):
        if len(parts) > 8 and parts[7].strip() == "keep" and parts[8].strip().lower() != "baseline":
            try:
                result["final_sharpe"] = float(parts[1])
            except ValueError:
                pass
            break

    return result


def _count_rows_added(universe: str, session_start_ts: float) -> int:
    """Count rows inserted into research_experiments for *universe* since *session_start_ts*.

    Used by silent-failure detection. Returns 0 on any DB error (caller treats
    that as silent failure, which is the correct conservative behavior).

    NOTE: queries the ``universe`` column (the schema column is named ``universe``,
    not ``market`` — log_experiment maps its ``market`` param to this column).
    """
    try:
        from db.atlas_db import get_db
        # Use SQLite datetime('now') format: 'YYYY-MM-DD HH:MM:SS' (no T, no tz suffix)
        # The datetime ISO format produces 'YYYY-MM-DDTHH:MM:SS+00:00': the 'T' separator
        # (ASCII 84) is GREATER than the space (ASCII 32) that SQLite uses, so
        # 'YYYY-MM-DD HH:MM:SS' < 'YYYY-MM-DDTHH:MM:SS+00:00' always → rows=0. (#216)
        cutoff = datetime.fromtimestamp(session_start_ts, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        with get_db() as db:
            cur = db.execute(
                "SELECT COUNT(*) FROM research_experiments "
                "WHERE universe = ? AND created_at > ?",
                (universe, cutoff),
            )
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception as exc:
        _logger.error("_count_rows_added failed: %s", exc, exc_info=True)
        return 0


def _resolve_min_rows(universe: str) -> int:
    """Allow-list-aware silent-failure / degraded row floor.

    Threshold = max(MIN_ROWS_PER_STRATEGY, enabled_strategies * per_strategy)

    where ``per_strategy`` comes from :data:`ROWS_PER_STRATEGY_BY_UNIVERSE`
    (defaulting to :data:`DEFAULT_ROWS_PER_STRATEGY`).

    The floor scales with the number of CURRENTLY enabled strategies so it tracks
    the active allow-list instead of a stale fixed total (#392). Before this fix
    sp500 used a flat operator floor of 50 calibrated for a 2-strategy allow-list;
    once trimmed to a single ``momentum_breakout`` strategy (~38 rows per sweep)
    every nightly run tripped ``rows_added < 50`` as a false DEGRADED warning.
    With per-strategy scaling a 1-strategy sp500 sweep expects ~25 rows, so a
    healthy ~38-row run is no longer flagged, while a 2-strategy sweep still
    expects ~50 (preserving sensitivity to a real collapse).

    Fail-safe: on missing config, zero enabled strategies, or any error, falls
    back to the static :data:`MIN_ROWS_PER_UNIVERSE` floor (or
    :data:`DEFAULT_MIN_ROWS`) — better to surface a false-positive than to miss a
    real silent failure.
    """
    try:
        import json as _json
        cfg_path = ATLAS_ROOT / "config" / "active" / f"{universe}.json"
        if not cfg_path.exists():
            return MIN_ROWS_PER_UNIVERSE.get(universe, DEFAULT_MIN_ROWS)
        with open(cfg_path) as f:
            cfg = _json.load(f)
        enabled = sum(
            1 for s in cfg.get("strategies", {}).values()
            if isinstance(s, dict) and s.get("enabled", False)
        )
        if enabled == 0:
            # Universe has no enabled strategies; sweep should not run at all.
            # Use the static floor so we still alert if rows ARE produced.
            return MIN_ROWS_PER_UNIVERSE.get(universe, DEFAULT_MIN_ROWS)
        per_strategy = ROWS_PER_STRATEGY_BY_UNIVERSE.get(
            universe, DEFAULT_ROWS_PER_STRATEGY
        )
        return max(MIN_ROWS_PER_STRATEGY, enabled * per_strategy)
    except Exception:
        _logger.warning(
            "_resolve_min_rows(%s) failed — falling back to static threshold",
            universe, exc_info=True,
        )
        return MIN_ROWS_PER_UNIVERSE.get(universe, DEFAULT_MIN_ROWS)


# ─── Exhaustion / Rotation Guard (#392) ──────────────────────────────────────


def assess_exhaustion(
    strategy: str,
    universe: str,
    lookback: int = EXHAUSTION_LOOKBACK,
    min_experiments: int = EXHAUSTION_MIN_EXPERIMENTS,
) -> Dict:
    """Assess whether *strategy*/*universe* parameter sweeps look exhausted.

    Reads the most recent ``lookback`` non-baseline rows from the SQLite
    ``research_experiments`` table and computes:

    - ``recent_experiments`` — number of non-baseline experiments examined,
    - ``real_keeps``         — experiments that were genuinely kept
                               (``status='kept'``, baseline rows excluded),
    - ``consecutive_discards`` — discards since the most recent real keep.

    A strategy is flagged ``exhausted`` when it has accumulated at least
    ``min_experiments`` recent attempts with ZERO real keeps. The returned
    ``recommendation`` then points at #387 (sizing) / #388 (additive strategy +
    OOS). This function NEVER mutates config or strategy state — it only reports.

    Fails safe: any DB error returns ``exhausted=False`` with the error noted.
    """
    out: Dict = {
        "strategy": strategy,
        "universe": universe,
        "assessed": True,
        "exhausted": False,
        "recent_experiments": 0,
        "real_keeps": 0,
        "consecutive_discards": 0,
        "recommendation": None,
    }
    try:
        from db.atlas_db import get_db
        with get_db() as db:
            cur = db.execute(
                "SELECT status, description FROM research_experiments "
                "WHERE strategy = ? AND universe = ? "
                "ORDER BY created_at DESC, id DESC LIMIT ?",
                (strategy, universe, int(lookback)),
            )
            rows = cur.fetchall()
    except Exception as exc:
        _logger.warning("assess_exhaustion DB read failed for %s/%s: %s",
                        strategy, universe, exc)
        out["assessed"] = False
        out["error"] = str(exc)
        return out

    def _is_baseline(status: str, description: str) -> bool:
        return (
            (status or "").strip().lower() == "baseline"
            or (description or "").strip().lower() == "baseline"
        )

    non_baseline = [
        (str(r[0] or ""), str(r[1] or ""))
        for r in rows
        if not _is_baseline(r[0] if len(r) > 0 else "", r[1] if len(r) > 1 else "")
    ]

    real_keeps = sum(1 for status, _ in non_baseline if status.strip().lower() == "kept")
    consecutive_discards = 0
    for status, _ in non_baseline:  # rows are newest-first
        if status.strip().lower() == "kept":
            break
        consecutive_discards += 1

    out["recent_experiments"] = len(non_baseline)
    out["real_keeps"] = real_keeps
    out["consecutive_discards"] = consecutive_discards

    if len(non_baseline) >= min_experiments and real_keeps == 0:
        out["exhausted"] = True
        out["recommendation"] = (
            f"{strategy} parameter space appears exhausted: "
            f"{len(non_baseline)} recent experiments, 0 real keeps, "
            f"{consecutive_discards} consecutive discards — {EXHAUSTION_NEXT_STEPS}"
        )
    return out


def assess_exhaustion_for_strategies(
    strategies: List[str],
    universe: str,
) -> Dict:
    """Run :func:`assess_exhaustion` for each strategy and aggregate (#392).

    Returns a dict with a per-strategy breakdown plus the list of exhausted
    strategy names and a combined recommendation string.
    """
    per_strategy = [assess_exhaustion(s, universe) for s in strategies]
    exhausted = [a["strategy"] for a in per_strategy if a.get("exhausted")]
    recommendations = [a["recommendation"] for a in per_strategy if a.get("recommendation")]
    return {
        "universe": universe,
        "any_exhausted": bool(exhausted),
        "exhausted_strategies": exhausted,
        "per_strategy": per_strategy,
        "recommendation": " | ".join(recommendations) if recommendations else None,
    }


def _print_exhaustion_summary(exhaustion: Dict) -> None:
    """Print a clear exhaustion/rotation recommendation block to stdout (#392)."""
    if not exhaustion or not exhaustion.get("any_exhausted"):
        return
    _logger.warning(
        "RESEARCH_EXHAUSTION universe=%s strategies=%s — %s",
        exhaustion.get("universe"),
        ",".join(exhaustion.get("exhausted_strategies", [])),
        EXHAUSTION_NEXT_STEPS,
    )
    print(
        f"\n{'='*65}\n"
        f"  ⚠ RESEARCH EXHAUSTION GUARD ({exhaustion.get('universe')})\n"
        f"{'='*65}"
    )
    for a in exhaustion.get("per_strategy", []):
        if a.get("exhausted"):
            print(
                f"  {a['strategy']}: {a['recent_experiments']} recent experiments, "
                f"0 real keeps, {a['consecutive_discards']} consecutive discards"
            )
    print(f"  Recommendation: {EXHAUSTION_NEXT_STEPS}")
    print(f"{'='*65}\n")


# ─── Worker Management ───────────────────────────────────────────────────────


def _spawn_workers(
    strategies: List[str],
    market: str,
    hours: float,
    snapshot_id: Optional[str],
    max_workers: int,
    universe: str = "sp500",
) -> List[Dict]:
    """Spawn autoresearch_runner subprocesses, respecting *max_workers* limit.

    Returns list of worker dicts with keys: strategy, proc, log_path, start_time.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    date_str = datetime.now().strftime("%Y%m%d")

    workers: List[Dict] = []
    pending = list(strategies)
    active: List[Dict] = []

    def _launch(strat: str) -> Dict:
        log_path = LOGS_DIR / f"autoresearch_{strat}_{date_str}.log"
        log_fh = open(log_path, "w")
        cmd = [
            sys.executable,
            str(RUNNER_SCRIPT),
            "--strategy", strat,
            "--market", market,
            "--hours", str(hours),
            "--fast-screen",
            "--universe", universe,
        ]
        if snapshot_id:
            cmd.extend(["--snapshot", snapshot_id])
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            cwd=str(ATLAS_ROOT),
        )
        w = {
            "strategy": strat,
            "proc": proc,
            "log_fh": log_fh,
            "log_path": str(log_path),
            "start_time": time.time(),
            "exit_code": None,
        }
        print(f"  ▶ Spawned {strat} (PID {proc.pid}) → {log_path}")
        return w

    # Initial launch up to max_workers
    while pending and len(active) < max_workers:
        strat = pending.pop(0)
        w = _launch(strat)
        active.append(w)
        workers.append(w)

    # Monitor loop: poll every 60s, launch pending when slots open
    while active:
        time.sleep(60)

        still_active = []
        for w in active:
            rc = w["proc"].poll()
            if rc is not None:
                w["exit_code"] = rc
                w["log_fh"].close()
                elapsed = (time.time() - w["start_time"]) / 60
                status = "✓" if rc == 0 else f"✗ (exit {rc})"
                print(f"  {status} {w['strategy']} finished in {elapsed:.1f} min")
            else:
                still_active.append(w)

        active = still_active

        # Fill slots with pending strategies
        while pending and len(active) < max_workers:
            strat = pending.pop(0)
            w = _launch(strat)
            active.append(w)
            workers.append(w)

        # Status line
        running_names = [w["strategy"] for w in active]
        if running_names:
            print(f"  … {len(active)} running: {', '.join(running_names)}")

    return workers


# ─── Telegram ────────────────────────────────────────────────────────────────


def _send_summary_telegram(
    results: List[Dict],
    runtime_s: float,
    num_workers: int,
) -> None:
    """Send a combined Telegram summary for all strategies."""
    try:
        from utils.telegram import notify
    except ImportError:
        print("Telegram not configured (utils.telegram not found).")
        return

    mins = runtime_s / 60
    total_screened = sum(r["screened"] for r in results)
    total_promoted = sum(r["promoted"] for r in results)
    total_kept = sum(r["kept"] for r in results)
    failures = [r for r in results if r.get("exit_code", 0) != 0]

    lines = [
        "<b>🔬 Nightly Autoresearch Complete</b>",
        f"Runtime: {mins:.0f} min | {len(results)} strategies | {num_workers} workers",
        "",
    ]
    for r in results:
        s = r["strategy"]
        sc = r["screened"]
        pr = r["promoted"]
        kp = r["kept"]
        s_sharpe = r["starting_sharpe"]
        f_sharpe = r["final_sharpe"]
        if r.get("exit_code", 0) != 0:
            lines.append(f"  {s}: ❌ FAILED (exit {r['exit_code']})")
        elif kp > 0:
            lines.append(
                f"  {s}: {sc} screened → {pr} promoted → {kp} kept "
                f"(Sharpe {s_sharpe:.3f} → {f_sharpe:.3f})"
            )
        else:
            lines.append(
                f"  {s}: {sc} screened → {pr} promoted → 0 kept "
                f"(Sharpe {s_sharpe:.3f})"
            )

    lines.append("")
    lines.append(f"Total: {total_screened} screened, {total_promoted} promoted, {total_kept} kept")
    if failures:
        lines.append(f"⚠️ {len(failures)} worker(s) failed")

    try:
        notify("\n".join(lines), category="autoresearch")
    except Exception as e:
        print(f"Telegram send failed (non-fatal): {e}")




# ─── Strategy Filter ─────────────────────────────────────────────────────────


def _filter_enabled_strategies(strategies: List[str], market_or_universe: str) -> List[str]:
    """Drop strategies whose `enabled` flag is False in the active config.

    Args:
        strategies: List of strategy names to filter.
        market_or_universe: Config key to read enabled flags from
            (use universe for non-sp500 sweeps so gold_etfs/sector_etfs
            configs are read instead of sp500).

    Returns the filtered list; logs any strategies that were skipped.
    """
    try:
        from utils.config import get_active_config
        cfg = get_active_config(market_or_universe)
    except FileNotFoundError as exc:
        # #372 fix — a missing active config means the universe was retired or
        # was never enabled. Fail CLOSED: return zero strategies so the caller
        # short-circuits the sweep (no workers spawned, no LLM loop run). The
        # previous fail-open behaviour caused commodity_etfs sweeps to run with
        # the global DEFAULT_STRATEGIES against a sp500 fallback config, then
        # ResearchSession would raise market mismatch in the LLM loop.
        print(
            f"[filter] No active config for {market_or_universe} ({exc}) — "
            "retired/disabled universe, returning [] (no strategies)"
        )
        _logger.warning(
            "_filter_enabled_strategies: active config missing for universe=%s — "
            "returning no strategies (fail-closed). exc=%s",
            market_or_universe, exc,
        )
        return []
    except Exception as exc:
        # Transient/unexpected errors (corrupt JSON, override layer failure):
        # remain fail-open with full strategy list so a one-off glitch does
        # not silence the entire sweep.
        print(f"[filter] Could not load active config for {market_or_universe}: {exc} — running all strategies")
        return strategies

    strat_cfg = cfg.get("strategies", {}) or {}
    enabled = []
    for s in strategies:
        entry = strat_cfg.get(s, {})
        # Missing entry → assume enabled (don't silently drop strategies
        # not yet configured for this universe)
        is_enabled = entry.get("enabled", True) if isinstance(entry, dict) else True
        if is_enabled:
            enabled.append(s)
        else:
            print(f"[filter] Skipping {s} — disabled in {market_or_universe} active config")
    return enabled


# ─── Promotion Sweep ──────────────────────────────────────────────────────────


def _run_promotion_sweep(results: List[Dict], market: str, universe: str) -> List[Dict]:
    """Call auto_promote() for each strategy that produced kept experiments.

    Reads per-strategy best params from research/best/{strategy}.json (or
    research/best/{strategy}_{universe}.json for non-sp500). If the best
    beats the current active config's Sharpe, fires auto_promote which
    runs all 4 gates and queues a Telegram APPROVE/REJECT request.

    Returns a list of promotion outcome dicts (one per strategy processed).
    """
    from research.promoter import auto_promote
    from utils.config import get_active_config
    import json as _json

    outcomes = []
    best_dir = ATLAS_ROOT / "research" / "best"

    # Ensure config directories exist
    (ATLAS_ROOT / "config").mkdir(parents=True, exist_ok=True)
    (ATLAS_ROOT / "config" / "candidates").mkdir(parents=True, exist_ok=True)

    try:
        active_cfg = get_active_config(market)
    except Exception as exc:
        print(f"[promo] Could not load active config for {market}: {exc} — skipping promotion sweep")
        return outcomes

    for r in results:
        strategy = r.get("strategy")
        kept = r.get("kept", 0)
        if kept <= 0:
            continue
        if r.get("exit_code", 0) != 0:
            continue  # don't promote from failed workers

        # Read best params from SQLite (canonical), fall back to JSON file.
        best_params = {}
        best_metrics = {}
        try:
            from db.atlas_db import get_research_best
            import json as _json_pkg
            rows = get_research_best(strategy, universe)
            row = rows[0] if rows else None
            if row:
                raw_params = row.get("params", {})
                best_params = (
                    _json_pkg.loads(raw_params)
                    if isinstance(raw_params, str)
                    else (raw_params or {})
                )
                best_metrics = {
                    "sharpe": row.get("sharpe"),
                    "total_trades": row.get("trades"),
                    "max_drawdown_pct": row.get("max_dd_pct"),
                }
        except Exception as exc:
            print(f"[promo] SQLite read failed for {strategy}/{universe}: {exc} — falling back to JSON")

        if not best_params:
            # JSON fallback (legacy path)
            candidate_file = best_dir / f"{strategy}_{universe}.json"
            if not candidate_file.exists():
                candidate_file = best_dir / f"{strategy}.json"
            if not candidate_file.exists():
                print(f"[promo] No best data for {strategy} ({universe}) — skipping")
                continue
            try:
                best_data = _json.loads(candidate_file.read_text())
                best_params = best_data.get("params", {}) or {}
                best_metrics = best_data.get("metrics", {}) or {}
            except Exception as exc:
                print(f"[promo] Failed to read {candidate_file}: {exc}")
                continue
        best_sharpe = best_metrics.get("sharpe")
        if best_sharpe is None:
            # Fall back to r['final_sharpe'] if set
            best_sharpe = r.get("final_sharpe", 0.0) or 0.0

        # Baseline = pre-sweep Sharpe captured by the runner
        initial_sharpe = r.get("starting_sharpe", 0.0) or 0.0

        # Gate against portfolio-contaminated metrics
        try:
            from research.integrity import check_solo
            _is_solo, _solo_frac, _note = check_solo(strategy, universe)
            if _is_solo is False:  # explicit false, not None
                _msg = (
                    f"Refusing to promote {strategy}/{universe} on contaminated portfolio metrics "
                    f"(solo_fraction={_solo_frac:.2%}). Run a true solo backtest first. {_note}"
                )
                print(f"[promo] BLOCKED: {_msg}")
                _logger.warning(_msg)
                outcomes.append({
                    "strategy": strategy,
                    "promoted": False,
                    "reason": f"contaminated_metrics: solo_fraction={_solo_frac:.2%}",
                })
                continue
        except ImportError:
            pass  # integrity module not yet available — skip gate

        # Gate delta-Sharpe client-side so we don't spam promoter
        # with tiny improvements (promoter has its own gates but
        # this saves an OOS validation subprocess per insignificant
        # improvement).
        delta = (best_sharpe or 0.0) - initial_sharpe
        if delta < 0.05:
            print(f"[promo] {strategy}: delta_sharpe={delta:+.4f} below client gate 0.05 — skipping")
            continue

        improvements = [f"nightly sweep: Sharpe {initial_sharpe:.4f} -> {best_sharpe:.4f}"]

        try:
            outcome = auto_promote(
                strategy=strategy,
                improved_params=best_params,
                initial_sharpe=float(initial_sharpe),
                final_sharpe=float(best_sharpe),
                improvements=improvements,
                market=market,
            )
            outcome["strategy"] = strategy
            outcomes.append(outcome)
            print(f"[promo] {strategy}: {outcome.get('reason', 'no reason')}")
        except Exception as exc:
            print(f"[promo] auto_promote failed for {strategy}: {exc}")
            outcomes.append({"strategy": strategy, "promoted": False, "reason": f"exception: {exc}"})

    return outcomes


# ─── Main ────────────────────────────────────────────────────────────────────


def run_nightly(
    strategies: Optional[List[str]] = None,
    market: str = "sp500",
    hours: float = 8.0,
    workers: int = 5,
    notify: bool = False,
    snapshot_id: Optional[str] = None,
    universe: str = "sp500",
    dry_run_telegram: bool = False,
) -> Dict:
    """Run parallel autoresearch sessions for multiple strategies.

    Args:
        strategies:  List of strategy names.  Defaults to :data:`DEFAULT_STRATEGIES`.
        market:      Market ID (default ``'sp500'``).
        hours:       Time budget per worker in hours.
        workers:     Max concurrent worker processes.
        notify:      Send Telegram summary on completion.
        snapshot_id: Explicit snapshot to use (auto-discovered if ``None``).

    Returns:
        Summary dict with per-strategy results and aggregate counts.
    """
    session_start = time.time()

    _logger.info(
        "RESEARCH_NIGHTLY_START universe=%s market=%s timestamp=%s",
        universe, market, datetime.now(timezone.utc).isoformat(),
    )

    # When sweeping a non-sp500 universe, treat universe as the effective market so
    # downstream config loads (get_active_config) hit the universe's config file.
    if universe != "sp500" and market == "sp500":
        market = universe

    # Defensive: after coercion, market must equal universe for non-sp500 sweeps
    if universe != "sp500":
        assert market == universe, (
            f"market ({market}) must equal universe ({universe}) for non-sp500 sweeps"
        )

    strategies = strategies or list(DEFAULT_STRATEGIES)
    strategies = _filter_enabled_strategies(strategies, universe)

    if not strategies:
        print("[filter] No enabled strategies — nothing to run")
        return {
            "status": "no_strategies",
            "strategies": [],
            "total_screened": 0,
            "total_promoted": 0,
            "total_kept": 0,
            "failures": 0,
            "runtime_s": 0.0,
            "snapshot_id": None,
        }

    session_id = None
    try:
        session_id = log_session(mode="nightly_sweep", strategy=",".join(strategies))

        # Resolve snapshot — only sp500 uses file-based snapshots;
        # other universes load from build_from_definition() inside the runner
        if universe == "sp500":
            if snapshot_id is None:
                snapshot_id = _find_latest_snapshot(market)
        else:
            snapshot_id = None  # non-sp500 universes don't use snapshots
        print(
            f"\n{'='*65}\n"
            f"  Atlas Nightly Autoresearch Orchestrator\n"
            f"{'='*65}\n"
            f"  Strategies : {', '.join(strategies)}\n"
            f"  Market     : {market}\n"
            f"  Universe   : {universe}\n"
            f"  Budget     : {hours:.1f} h per worker\n"
            f"  Workers    : {workers}\n"
            f"  Snapshot   : {snapshot_id or '(none — universe uses build_from_definition)'}\n"
            f"  Started    : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
            f"{'='*65}\n"
        )

        # Spawn and monitor workers
        worker_list = _spawn_workers(strategies, market, hours, snapshot_id, workers, universe=universe)

        # Collect results
        runtime_s = time.time() - session_start
        results = []
        for w in worker_list:
            r = _parse_session_results(w["strategy"], session_start)
            r["exit_code"] = w["exit_code"]
            r["log_path"] = w["log_path"]
            results.append(r)

        # Print summary
        total_screened = sum(r["screened"] for r in results)
        total_promoted = sum(r["promoted"] for r in results)
        total_kept = sum(r["kept"] for r in results)
        total_baseline = sum(r.get("baseline", 0) for r in results)
        failures = [r for r in results if r.get("exit_code", 0) != 0]
        mins = runtime_s / 60

        print(
            f"\n{'='*65}\n"
            f"  Nightly Autoresearch Summary\n"
            f"{'='*65}"
        )
        for r in results:
            s = r["strategy"]
            sc = r["screened"]
            pr = r["promoted"]
            kp = r["kept"]
            s_sharpe = r["starting_sharpe"]
            f_sharpe = r["final_sharpe"]
            if r.get("exit_code", 0) != 0:
                print(f"  {s:25s} ❌ FAILED (exit {r['exit_code']})")
            elif kp > 0:
                print(
                    f"  {s:25s} {sc:3d} screened → {pr:2d} promoted → {kp:2d} kept "
                    f"(Sharpe {s_sharpe:.3f} → {f_sharpe:.3f})"
                )
            else:
                print(
                    f"  {s:25s} {sc:3d} screened → {pr:2d} promoted →  0 kept "
                    f"(Sharpe {s_sharpe:.3f})"
                )
        print(
            f"\n  Total: {total_screened} screened, {total_promoted} promoted, {total_kept} kept"
            f"  (baseline rows excluded: {total_baseline})"
        )
        if failures:
            print(f"  ⚠️  {len(failures)} worker(s) failed")
        print(f"  Runtime: {mins:.1f} min")
        print(f"{'='*65}\n")

        # INIT-1: Promotion sweep — queues Telegram APPROVE/REJECT for strategies
        # that improved beyond threshold. Human gate remains intact — this does
        # NOT auto-write to config/active.
        promotion_outcomes = _run_promotion_sweep(results, market, universe)
        print(f"\n[promo] Promotion sweep outcome: {len(promotion_outcomes)} strategies processed")
        for o in promotion_outcomes:
            print(f"  - {o.get('strategy')}: promoted={o.get('promoted')} pending={o.get('pending')} reason={o.get('reason')}")

        # Telegram
        if notify:
            _send_summary_telegram(results, runtime_s, workers)

        result = {
            "status": "complete",
            "strategies": results,
            "total_screened": total_screened,
            "total_promoted": total_promoted,
            "total_kept": total_kept,
            "total_baseline": total_baseline,
            "failures": len(failures),
            "runtime_s": round(runtime_s, 1),
            "snapshot_id": snapshot_id,
        }

        # ─── Exhaustion / rotation guard (#392) ──────────────────────────────────
        # Detect strategies whose parameter space looks exhausted (enough recent
        # attempts, zero real keeps) and surface a clear recommendation to
        # redirect effort to #387 (sizing) / #388 (additive strategy + OOS).
        # Reporting only — never enables strategies or promotes configs.
        try:
            exhaustion = assess_exhaustion_for_strategies(
                [r["strategy"] for r in results if r.get("exit_code", 0) == 0],
                universe,
            )
            result["exhaustion"] = exhaustion
            _print_exhaustion_summary(exhaustion)
        except Exception as _eexc:
            _logger.warning("exhaustion assessment failed (non-fatal): %s", _eexc)
            result["exhaustion"] = {"assessed": False, "error": str(_eexc)}

        # ─── Silent-failure detection ────────────────────────────────────────────
        # Verify rows were actually inserted into research_experiments.
        # Catches the Apr 22-30 0-byte-log silent failures.
        # NOTE: _count_rows_added uses SQLite datetime format to avoid the
        # ISO-vs-space separator mismatch bug (fixed 2026-05-14 #216).
        rows_added = _count_rows_added(universe, session_start)
        min_rows = _resolve_min_rows(universe)
        tsv_ran = total_screened > 0

        # DB-write degradation cross-check (#392). Every TSV experiment row is
        # mirrored to SQLite via log_experiment, so a healthy sweep has DB rows
        # of the same order as the TSV screened count. Compare DB output to the
        # TSV output it should mirror — NOT to a fixed floor — so the check
        # self-calibrates to the active allow-list. A trimmed 1-strategy sweep
        # that legitimately produces ~38 rows is never flagged; only a real
        # write degradation (TSV rows present, SQLite far below them) is.
        # Known root causes: (a) discard_solo rows written to DB under the wrong
        # universe (fixed #392 by passing market=), or (b) log_experiment()
        # silently swallowing an exception.
        db_consistency_floor = max(1, int(total_screened * TSV_DB_CONSISTENCY_FRACTION))
        db_write_degraded = tsv_ran and rows_added < db_consistency_floor

        if db_write_degraded:
            import json as _json_sentinel
            sentinel = {
                "status": "completed_no_keeps",
                "universe": universe,
                "screened": total_screened,
                "promoted": total_promoted,
                "kept": total_kept,
                "rows_added": rows_added,
                "min_rows": min_rows,
            }
            _logger.warning(
                "RESEARCH_NIGHTLY_DEGRADED universe=%s rows_added=%d tsv_screened=%d "
                "db_consistency_floor=%d min_required=%d — TSV shows the sweep ran "
                "but SQLite holds far fewer rows than it mirrors (probable "
                "log_experiment write degradation). Treating as completed_no_keeps "
                "(not a genuine silent failure; that requires TSV=0 AND DB below floor).",
                universe, rows_added, total_screened, db_consistency_floor, min_rows,
            )
            # Emit sentinel JSON to stdout so run_compute_matrix.py can parse
            print(f"ATLAS_NIGHTLY_STATUS: {_json_sentinel.dumps(sentinel)}")
            result["status"] = "completed_no_keeps"
            result["rows_added"] = rows_added
            result["silent_failure"] = False
            if session_id is not None:
                try:
                    end_session(session_id, experiments_run=total_screened,
                                experiments_kept=total_kept, status="completed")
                except Exception:
                    _logger.warning("end_session failed in completed_no_keeps path", exc_info=True)
            return result

        # Genuine silent failure: NO output anywhere — the TSV shows no screened
        # rows AND SQLite is below the allow-list-aware floor. (#392 / #216)
        silent_failure = (not tsv_ran) and (rows_added < min_rows)

        status_str = "SILENT_FAILURE" if silent_failure else "OK"
        _logger.info(
            "RESEARCH_NIGHTLY_END universe=%s rows_added=%d min_required=%d status=%s",
            universe, rows_added, min_rows, status_str,
        )
        result["rows_added"] = rows_added
        result["silent_failure"] = silent_failure

        if silent_failure:
            msg = (
                f"🚨 Research sweep silent failure: "
                f"universe={universe} rows={rows_added} threshold={min_rows} "
                f"(see {LOGS_DIR}/autoresearch_*_{datetime.now().strftime('%Y%m%d')}.log)"
            )
            _logger.error(msg)
            if dry_run_telegram:
                print(f"[TELEGRAM-DRY-RUN] {msg}")
            else:
                try:
                    from utils.telegram import notify as _tg_notify
                    _tg_notify(msg, category="autoresearch_silent_failure")
                except Exception as exc:
                    _logger.warning("Telegram silent-failure alert failed: %s", exc)
            if session_id is not None:
                try:
                    end_session(session_id, experiments_run=total_screened,
                                experiments_kept=total_kept, status="silent_failure")
                except Exception:
                    _logger.warning("end_session failed in silent_failure path", exc_info=True)
            return result

        # Healthy completion. A 0-keep run is a legitimate no-op (the parameter
        # space may simply be exhausted) — NOT a degraded/silent failure. Emit
        # the completed_no_keeps sentinel for run_compute_matrix WITHOUT a
        # DEGRADED warning so a normal low-yield run is not flagged. (#392)
        if tsv_ran and total_kept == 0:
            import json as _json_okk
            print(
                "ATLAS_NIGHTLY_STATUS: "
                + _json_okk.dumps({
                    "status": "completed_no_keeps",
                    "universe": universe,
                    "screened": total_screened,
                    "promoted": total_promoted,
                    "kept": total_kept,
                    "rows_added": rows_added,
                    "min_rows": min_rows,
                })
            )
            result["status"] = "completed_no_keeps"

        if session_id is not None:
            end_session(session_id, experiments_run=total_screened,
                        experiments_kept=total_kept, status="completed")
        return result

    except Exception:
        if session_id is not None:
            try:
                end_session(session_id, experiments_run=0, experiments_kept=0, status="failed")
            except Exception:
                _logger.warning("end_session failed in exception cleanup path", exc_info=True)
        raise


# ─── CLI ─────────────────────────────────────────────────────────────────────


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nightly autoresearch orchestrator — parallel strategy sweeps.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Example:\n"
            "  python3 research/autoresearch_nightly.py --hours 8 --workers 5 --notify\n"
            "\n"
            "  # Only specific strategies:\n"
            "  python3 research/autoresearch_nightly.py --hours 4 --workers 2 \\\n"
            "      --strategies mean_reversion,trend_following\n"
        ),
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=8.0,
        help="Time budget per worker in hours (default: 8).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Max concurrent worker processes (default: 5).",
    )
    parser.add_argument(
        "--market",
        default="sp500",
        help="Market ID (default: sp500).",
    )
    parser.add_argument(
        "--strategies",
        default=None,
        help="Comma-separated strategy list (default: top 5 by weight).",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        default=False,
        help="Send Telegram summary on completion.",
    )
    parser.add_argument(
        "--snapshot",
        default=None,
        help="Snapshot ID (auto-discovered if omitted).",
    )
    parser.add_argument(
        "--universe",
        default="sp500",
        help="Universe ID (default: sp500). Non-sp500 universes use build_from_definition() instead of snapshots.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print config and exit without spawning workers.",
    )
    parser.add_argument(
        "--dry-run-telegram",
        action="store_true",
        default=False,
        help=(
            "Replace Telegram silent-failure alerts with stdout prints. "
            "Used by the test harness to verify alert dispatch without spamming."
        ),
    )
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    strats = args.strategies.split(",") if args.strategies else None
    if args.dry_run:
        print(
            f"\n{'='*65}\n"
            f"  Dry-run mode — no workers will be spawned\n"
            f"{'='*65}\n"
            f"  Strategies : {', '.join(strats or ['(defaults)'])}\n"
            f"  Market     : {args.market}\n"
            f"  Universe   : {args.universe}\n"
            f"  Budget     : {args.hours:.1f} h per worker\n"
            f"  Workers    : {args.workers}\n"
            f"  Snapshot   : {args.snapshot or '(auto-discover or none for non-sp500)'}\n"
            f"{'='*65}\n"
        )
        sys.exit(0)
    result = run_nightly(
        strategies=strats,
        market=args.market,
        hours=args.hours,
        workers=args.workers,
        notify=args.notify,
        snapshot_id=args.snapshot,
        universe=args.universe,
        dry_run_telegram=args.dry_run_telegram,
    )
    failures = result.get("failures", 0)
    silent_failure = result.get("silent_failure", False)
    if silent_failure:
        sys.exit(2)  # distinct exit code so systemd journal shows failure mode
    elif failures == len(result.get("strategies", [])):
        sys.exit(1)
    else:
        sys.exit(0)
