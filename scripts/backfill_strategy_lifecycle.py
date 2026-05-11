#!/usr/bin/env python3
"""Backfill strategy_lifecycle table with RESEARCH state for missing (strategy, universe) pairs.

For every (strategy, universe) pair found in config/active/*.json that lacks a row
in strategy_lifecycle, inserts a RESEARCH-state row. Idempotent (INSERT OR IGNORE).

Usage:
    python3 scripts/backfill_strategy_lifecycle.py           # dry-run
    python3 scripts/backfill_strategy_lifecycle.py --apply   # write to DB

Audit ref: F-10 (strategy_lifecycle not joined in /api/admin/strategies)
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Bootstrap sys.path for standalone execution
_ATLAS_ROOT = Path(__file__).resolve().parents[1]
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

from db.atlas_db import get_db

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

_ACTIVE_DIR = _ATLAS_ROOT / "config" / "active"
_KNOWN_MARKETS = [
    "sp500", "sector_etfs", "commodity_etfs", "gold_etfs",
    "defensive_etfs", "treasury_etfs", "asx", "crypto",
]


def _collect_config_pairs() -> set[tuple[str, str]]:
    """Return all (strategy, universe) pairs from config/active/*.json."""
    import json

    pairs: set[tuple[str, str]] = set()
    for mid in _KNOWN_MARKETS:
        cfg_path = _ACTIVE_DIR / f"{mid}.json"
        if not cfg_path.exists():
            logger.debug("No config file for %s — skipping", mid)
            continue
        try:
            with open(cfg_path) as f:
                cfg = json.load(f)
            market_key = cfg.get("market", mid)
            strats = cfg.get("strategies") or {}
            for sname in strats.keys():
                pairs.add((sname, market_key))
        except Exception as e:
            logger.warning("Failed to read config for %s: %s", mid, e)
    return pairs


def backfill(apply: bool = False) -> dict:
    """Identify and backfill missing strategy_lifecycle rows.

    Args:
        apply: If True, write to DB. If False, dry-run only.

    Returns:
        dict with 'total_pairs', 'existing', 'missing', 'backfilled' counts.
    """
    config_pairs = _collect_config_pairs()

    with get_db() as db:
        existing_rows = db.execute(
            "SELECT strategy, universe FROM strategy_lifecycle"
        ).fetchall()
        existing = {(r[0], r[1]) for r in existing_rows}

        missing = config_pairs - existing
        logger.info("Config pairs: %d, existing lifecycle rows: %d, missing: %d",
                    len(config_pairs), len(existing), len(missing))

        if not missing:
            logger.info("Nothing to backfill — all pairs already have lifecycle rows.")
            return {
                "total_pairs": len(config_pairs),
                "existing": len(existing),
                "missing": 0,
                "backfilled": 0,
            }

        for (strategy, universe) in sorted(missing):
            logger.info("%s: INSERT (strategy=%s, universe=%s, state=RESEARCH)",
                        "DRY-RUN" if not apply else "INSERT", strategy, universe)

        if apply:
            count = 0
            for (strategy, universe) in sorted(missing):
                db.execute(
                    """INSERT OR IGNORE INTO strategy_lifecycle
                           (strategy, universe, state, entered_state_at, transition_reason)
                       VALUES (?, ?, 'RESEARCH', datetime('now'),
                       'Backfilled by F-10 audit fix 2026-05-11')""",
                    (strategy, universe),
                )
                count += 1
            logger.info("Backfilled %d rows into strategy_lifecycle", count)
        else:
            logger.info("DRY-RUN: would backfill %d rows (use --apply to write)", len(missing))
            count = 0

    return {
        "total_pairs": len(config_pairs),
        "existing": len(existing),
        "missing": len(missing),
        "backfilled": count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Write backfill rows to DB (default: dry-run)",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Print final strategy_lifecycle count after backfill",
    )
    args = parser.parse_args(argv)

    result = backfill(apply=args.apply)
    logger.info("Result: %s", result)

    if args.verify:
        with get_db() as db:
            total = db.execute("SELECT COUNT(*) FROM strategy_lifecycle").fetchone()[0]
            null_state = db.execute(
                "SELECT COUNT(*) FROM strategy_lifecycle WHERE state IS NULL"
            ).fetchone()[0]
            print(f"\nstrategy_lifecycle total rows: {total}")
            print(f"Rows with NULL state: {null_state}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
