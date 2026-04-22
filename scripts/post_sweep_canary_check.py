#!/usr/bin/env python3
"""Post-sweep canary check — P1.1 universe-isolation regression guard.

Runs daily at 07:00 AEST (2h after the last universe sweep completes).
Compares current cross-universe identical-metric hit count against the
pre-fix baseline.  Regression → alert.  Count ≤ baseline → conditional purge.

Exit codes:  0 = ok/purged/already_purged   1 = regression/no_baseline
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

CANARY_STATE_DIR = ATLAS_ROOT / ".pi" / "canary-state"
CANARY_RUNS_DIR  = ATLAS_ROOT / ".pi" / "canary-runs"
LOG_FILE         = ATLAS_ROOT / "logs" / "canary_check.log"
BASELINE_FILE    = CANARY_STATE_DIR / "baseline.json"
PURGE_DONE_FILE  = CANARY_STATE_DIR / "purge_done.json"
DB_PATH          = ATLAS_ROOT / "data" / "atlas.db"
CORRUPT_SINCE    = "2026-04-17"
CORRUPT_UNTIL    = "2026-04-23"   # exclusive

logger = logging.getLogger(__name__)


def _setup_logging() -> None:
    fmt = "%(asctime)s [canary] %(levelname)s %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    logging.basicConfig(level=logging.INFO, format=fmt, datefmt=datefmt)
    fh = logging.FileHandler(LOG_FILE, mode="a")
    fh.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    logging.getLogger().addHandler(fh)


def load_baseline() -> dict[str, Any] | None:
    if not BASELINE_FILE.exists():
        return None
    try:
        return json.loads(BASELINE_FILE.read_text())
    except Exception as exc:
        logger.error("Failed to parse baseline.json: %s", exc)
        return None


def query_suspicious(db_path: Path, window_hours: int = 24) -> list[dict]:
    from scripts.data_integrity_monitor import query_suspicious as _q
    return _q(db_path, window_hours)


def _purge_corrupt_rows(db_path: Path, baseline_hits: list[dict], dry_run: bool) -> int:
    """Two-pass DELETE of known P1.1 corrupt rows.

    Pass 1: description='baseline' rows cloned across ETF universes.
    Pass 2: Identical-metric (strategy,sharpe,trades,universe) clusters.
    """
    BASELINE_STRATEGIES = ("opening_gap", "sector_rotation", "trend_following")
    BASELINE_UNIVERSES  = ("sector_etfs", "gold_etfs", "treasury_etfs", "defensive_etfs")

    # Build (strategy, s4, trades, universe) keys for pass 2
    pass2_keys: list[tuple[str, float, int, str]] = []
    for hit in baseline_hits:
        for uni in (hit.get("universes") or "").split(","):
            uni = uni.strip()
            if uni and uni != "sp500":
                pass2_keys.append((hit["strategy"], hit["s4"], hit["trades"], uni))

    if dry_run:
        logger.info("[dry-run] Pass 1: DELETE description=baseline rows for "
                    "%s in %s window %s..%s",
                    BASELINE_STRATEGIES, BASELINE_UNIVERSES, CORRUPT_SINCE, CORRUPT_UNTIL)
        logger.info("[dry-run] Pass 2: %d (strategy,s4,trades,universe) keys to purge",
                    len(pass2_keys))
        return 0

    ph_s = ",".join("?" for _ in BASELINE_STRATEGIES)
    ph_u = ",".join("?" for _ in BASELINE_UNIVERSES)
    total = 0

    with sqlite3.connect(str(db_path)) as conn:
        cur = conn.execute(
            f"DELETE FROM research_experiments WHERE description='baseline'"
            f" AND universe IN ({ph_u}) AND strategy IN ({ph_s})"
            f" AND created_at >= ? AND created_at < ?",
            list(BASELINE_UNIVERSES) + list(BASELINE_STRATEGIES) + [CORRUPT_SINCE, CORRUPT_UNTIL],
        )
        d1 = cur.rowcount
        total += d1
        logger.info("Pass 1 deleted %d baseline-description corrupt rows", d1)

        d2 = 0
        for (strategy, s4, trades, universe) in pass2_keys:
            c = conn.execute(
                "DELETE FROM research_experiments"
                " WHERE strategy=? AND ROUND(sharpe,4)=? AND trades=?"
                " AND universe=? AND created_at>=? AND created_at<?",
                (strategy, s4, trades, universe, CORRUPT_SINCE, CORRUPT_UNTIL),
            )
            d2 += c.rowcount
        total += d2
        logger.info("Pass 2 deleted %d identical-metric cluster rows", d2)
        conn.commit()

    return total


def _telegram(message: str) -> bool:
    try:
        from utils.telegram import send_message
        return send_message(message)
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _write_manifest(now: datetime, status: str, current_count: int,
                    baseline_count: int, rows_deleted: int | None,
                    telegram_sent: bool) -> None:
    CANARY_RUNS_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "run_at": now.isoformat(), "status": status,
        "current_count": current_count, "baseline_count": baseline_count,
        "telegram_sent": telegram_sent,
    }
    if rows_deleted is not None:
        manifest["rows_deleted"] = rows_deleted
    fname = CANARY_RUNS_DIR / f"{now.strftime('%Y%m%dT%H%M%S')}.json"
    try:
        fname.write_text(json.dumps(manifest, indent=2))
        logger.info("Manifest written: %s", fname.name)
    except Exception as exc:
        logger.warning("Could not write manifest: %s", exc)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Post-sweep P1.1 canary check + conditional purge.")
    p.add_argument("--dry-run", action="store_true",
                   help="Log what would happen; do NOT write to DB or state files.")
    p.add_argument("--force-purge", action="store_true",
                   help="Ignore purge_done.json sentinel and re-attempt DELETE.")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite DB path")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = _parse_args(argv)
    db_path = Path(args.db)
    now = datetime.now(timezone.utc)

    logger.info("=== Canary check start (dry_run=%s, force_purge=%s) ===",
                args.dry_run, args.force_purge)

    # 1. Load baseline
    baseline = load_baseline()
    if baseline is None:
        logger.error(
            "FATAL: baseline.json not found at %s. "
            "Capture with: python3 scripts/data_integrity_monitor.py --json > /tmp/b.json",
            BASELINE_FILE,
        )
        _write_manifest(now, "no_baseline", 0, 0, None, False)
        return 1

    baseline_count: int = baseline.get("count", 0)
    baseline_hits: list[dict] = baseline.get("hits", [])
    logger.info("Baseline loaded: %d patterns (recorded %s)",
                baseline_count, baseline.get("recorded_at", "?"))

    # 2. Current state
    if not db_path.exists():
        logger.error("DB not found: %s", db_path)
        _write_manifest(now, "error", 0, baseline_count, None, False)
        return 1

    current_hits  = query_suspicious(db_path, window_hours=24)
    current_count = len(current_hits)
    logger.info("Current hit count: %d  (baseline: %d)", current_count, baseline_count)

    # 3. Regression check
    if current_count > baseline_count:
        msg = (
            f"🚨 P1.1 canary regression: {current_count} cross-universe identical "
            f"patterns detected ({current_count - baseline_count} new vs baseline "
            f"{baseline_count}).  Fix did NOT hold.\n\n"
            "Run: python3 scripts/data_integrity_monitor.py --notify"
        )
        logger.error("REGRESSION: current=%d > baseline=%d — NOT purging.",
                     current_count, baseline_count)
        sent = _telegram(msg)
        _write_manifest(now, "alert", current_count, baseline_count, None, sent)
        return 1

    logger.info("Canary OK — fix held (current %d ≤ baseline %d).", current_count, baseline_count)

    # 4. Purge step (idempotent)
    if not args.force_purge and PURGE_DONE_FILE.exists():
        try:
            done = json.loads(PURGE_DONE_FILE.read_text())
            logger.info("Already purged on %s (%d rows). Use --force-purge to override.",
                        done.get("purged_at", "?"), done.get("rows_deleted", 0))
        except Exception:
            logger.warning("Cannot parse purge_done.json — treating as done.")
        _write_manifest(now, "already_purged", current_count, baseline_count, None, False)
        return 0

    rows_deleted = _purge_corrupt_rows(db_path, baseline_hits, dry_run=args.dry_run)

    if not args.dry_run:
        CANARY_STATE_DIR.mkdir(parents=True, exist_ok=True)
        PURGE_DONE_FILE.write_text(json.dumps({
            "purged_at": now.isoformat(),
            "rows_deleted": rows_deleted,
            "canary_count_at_purge": current_count,
        }, indent=2))
        logger.info("Sentinel written: %s", PURGE_DONE_FILE)
        conf = (f"✅ P1.1 canary purge complete — {rows_deleted} rows deleted, "
                f"canary count was {current_count} (baseline {baseline_count}).")
        sent = _telegram(conf)
        _write_manifest(now, "purged", current_count, baseline_count, rows_deleted, sent)
        logger.info("Purge complete: %d rows deleted.", rows_deleted)
    else:
        logger.info("[dry-run] No DB writes. Purge skipped.")
        _write_manifest(now, "ok", current_count, baseline_count, 0, False)

    return 0


if __name__ == "__main__":
    sys.exit(main())
