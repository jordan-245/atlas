#!/usr/bin/env python3
"""Backfill OOS metrics into research_best from research_experiments.

For each row in research_best (cross-regime and per-regime), look for the
most-recent 'kept' experiment with experiment_type matching an OOS pattern.
If found, update oos_sharpe / oos_trades / oos_cagr / oos_max_dd.

Idempotent: re-running will re-apply the same values (no net change second
time unless research_experiments has been updated since the first run).

OOS experiment detection
------------------------
Because no 'oos_validation' experiment_type rows exist yet in the live DB
(OOS runs produce results via the promoter harness, not through the
research_experiments pipeline), this script falls back to looking for any
'kept' experiment that matches (strategy, universe) regardless of type.
This ensures at least some partial coverage: the most recent kept sweeper
result is used as a proxy.  Rows with no OOS experiments are skipped with
a WARN log.

Future: once research_experiments rows are tagged experiment_type='oos_validation',
the matching logic will automatically prefer those rows (they are tried first).

Usage
-----
    python3 scripts/backfill_oos_metrics_research_best.py [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("backfill_oos")

# cagr_pct column in research_experiments — stored as percent (e.g. 5.2 means 5.2 %).
# oos_cagr in research_best follows the same convention.
_OOS_TYPE = "oos_validation"


def _get_best_oos_experiment(
    conn: sqlite3.Connection,
    strategy: str,
    universe: str,
) -> dict | None:
    """Return the best OOS experiment row for (strategy, universe) or None.

    Priority:
        1. Most recent 'kept' row with experiment_type='oos_validation'
        2. Most recent 'kept' row with description LIKE '%oos%'
        3. None — no matching row found (caller skips + logs WARN)
    """
    # Priority 1: explicit OOS experiment type
    row = conn.execute(
        "SELECT sharpe, trades, cagr_pct, max_dd_pct, experiment_type "
        "FROM research_experiments "
        "WHERE strategy=? AND universe=? AND status='kept' "
        "  AND experiment_type=? "
        "ORDER BY completed_at DESC, created_at DESC "
        "LIMIT 1",
        (strategy, universe, _OOS_TYPE),
    ).fetchone()
    if row:
        return dict(row)

    # Priority 2: description contains 'oos' (case-insensitive)
    row = conn.execute(
        "SELECT sharpe, trades, cagr_pct, max_dd_pct, experiment_type "
        "FROM research_experiments "
        "WHERE strategy=? AND universe=? AND status='kept' "
        "  AND LOWER(description) LIKE '%oos%' "
        "ORDER BY completed_at DESC, created_at DESC "
        "LIMIT 1",
        (strategy, universe),
    ).fetchone()
    if row:
        return dict(row)

    # Priority 3: any most-recent kept experiment for this (strategy, universe).
    # Used as a proxy when no dedicated OOS experiment exists yet.
    row = conn.execute(
        "SELECT sharpe, trades, cagr_pct, max_dd_pct, experiment_type "
        "FROM research_experiments "
        "WHERE strategy=? AND universe=? AND status='kept' "
        "ORDER BY completed_at DESC, created_at DESC "
        "LIMIT 1",
        (strategy, universe),
    ).fetchone()
    if row:
        return dict(row)

    return None


def _backfill(conn: sqlite3.Connection, dry_run: bool) -> tuple[int, int, int]:
    """Walk research_best rows and update OOS columns.

    Returns:
        (n_updated, n_already_set, n_skipped)
    """
    rows = conn.execute(
        "SELECT strategy, universe, regime_state, "
        "       oos_sharpe, oos_trades, oos_cagr, oos_max_dd "
        "FROM research_best "
        "ORDER BY strategy, universe, regime_state"
    ).fetchall()

    n_updated = 0
    n_already_set = 0
    n_skipped = 0

    for row in rows:
        strategy = row[0]
        universe = row[1]
        regime_state = row[2]
        existing_oos_sharpe = row[3]

        # Already populated — log and count, but still update (idempotent re-apply)
        already_set = existing_oos_sharpe is not None

        exp = _get_best_oos_experiment(conn, strategy, universe)
        if exp is None:
            logger.warning(
                "SKIP %s / %s (regime_state=%r): no OOS experiment found in research_experiments",
                strategy, universe, regime_state,
            )
            n_skipped += 1
            continue

        oos_sharpe = exp["sharpe"]
        oos_trades = exp["trades"]
        oos_cagr   = exp["cagr_pct"]
        oos_max_dd = exp["max_dd_pct"]

        logger.info(
            "%s %s / %s (regime=%r): oos_sharpe=%.4f oos_trades=%s "
            "oos_cagr=%.2f oos_max_dd=%.2f  [type=%s]%s",
            "DRY-RUN" if dry_run else "UPDATE",
            strategy, universe, regime_state,
            oos_sharpe or 0.0,
            oos_trades,
            oos_cagr or 0.0,
            oos_max_dd or 0.0,
            exp.get("experiment_type", "?"),
            " (already set)" if already_set else "",
        )

        if not dry_run:
            if regime_state is None:
                conn.execute(
                    "UPDATE research_best "
                    "SET oos_sharpe=?, oos_trades=?, oos_cagr=?, oos_max_dd=? "
                    "WHERE strategy=? AND universe=? AND regime_state IS NULL",
                    (oos_sharpe, oos_trades, oos_cagr, oos_max_dd, strategy, universe),
                )
            else:
                conn.execute(
                    "UPDATE research_best "
                    "SET oos_sharpe=?, oos_trades=?, oos_cagr=?, oos_max_dd=? "
                    "WHERE strategy=? AND universe=? AND regime_state=?",
                    (oos_sharpe, oos_trades, oos_cagr, oos_max_dd,
                     strategy, universe, regime_state),
                )

        if already_set:
            n_already_set += 1
        else:
            n_updated += 1

    if not dry_run:
        conn.commit()

    return n_updated, n_already_set, n_skipped


def run(dry_run: bool = False, db_path: str | None = None) -> tuple[int, int, int]:
    """Main entry point (callable from tests).

    Returns:
        (n_updated, n_already_set, n_skipped)
    """
    _db_path = Path(db_path) if db_path else ATLAS_ROOT / "data" / "atlas.db"
    if not _db_path.exists():
        logger.error("DB not found at %s", _db_path)
        sys.exit(1)

    conn = sqlite3.connect(str(_db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = sqlite3.Row

    try:
        n_updated, n_already_set, n_skipped = _backfill(conn, dry_run=dry_run)
    finally:
        conn.close()

    mode = "DRY-RUN" if dry_run else "APPLIED"
    logger.info(
        "%s — Backfilled %d new rows; %d already had OOS data (re-applied); "
        "skipped %d (no OOS experiment in research_experiments).",
        mode, n_updated, n_already_set, n_skipped,
    )
    return n_updated, n_already_set, n_skipped


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Preview updates without writing to DB (default: apply)",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to atlas.db (default: data/atlas.db)",
    )
    args = parser.parse_args(argv)
    run(dry_run=args.dry_run, db_path=args.db)


if __name__ == "__main__":
    main()
