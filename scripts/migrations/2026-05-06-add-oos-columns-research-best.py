#!/usr/bin/env python3
"""Migration: 2026-05-06-add-oos-columns-research-best.py

Add four OOS (out-of-sample) columns to research_best so that promotion
gates G, H, and I can be enforced rather than bypassed.

New columns
-----------
    oos_sharpe  REAL     — OOS Sharpe from time-period-split or perturbation test
    oos_trades  INTEGER  — OOS trade count
    oos_cagr    REAL     — OOS CAGR % (e.g. 5.2 means 5.2 %)
    oos_max_dd  REAL     — OOS max drawdown % (positive number)

All columns default NULL so existing rows are unaffected.  Run
scripts/backfill_oos_metrics_research_best.py after this migration to populate
values for rows that have a matching research_experiments OOS entry.

Gates enforced once columns exist (auto_promote_paper_to_live.py)
------------------------------------------------------------------
    G  oos_sharpe >= 0.3   (was BYPASS)
    H  oos_trades >= 30    (was BYPASS)
    I  oos_cagr   >= 5.0   (was BYPASS)

Idempotency
-----------
Uses PRAGMA table_info(research_best) to skip columns that already exist.

Schema version
--------------
Bumps schema_version 29 → 30.

Usage
-----
    python3 scripts/migrations/2026-05-06-add-oos-columns-research-best.py          # dry-run
    python3 scripts/migrations/2026-05-06-add-oos-columns-research-best.py --apply  # apply
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# ── Bootstrap ────────────────────────────────────────────────────────────────
ATLAS_ROOT = Path(__file__).resolve().parents[2]
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

DB_PATH = ATLAS_ROOT / "data" / "atlas.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)

TARGET_VERSION = 30

# Columns to add: (name, type, description)
NEW_COLUMNS: list[tuple[str, str, str]] = [
    ("oos_sharpe", "REAL",    "OOS Sharpe from time-period-split"),
    ("oos_trades", "INTEGER", "OOS trade count"),
    ("oos_cagr",   "REAL",    "OOS CAGR % (e.g. 5.2 = 5.2 %)"),
    ("oos_max_dd", "REAL",    "OOS max drawdown % (positive)"),
]


def _existing_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(research_best)").fetchall()
    return {row[1] for row in rows}


def _current_max_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row else None


def _run(apply: bool) -> None:
    if not DB_PATH.exists():
        logger.error("DB not found at %s", DB_PATH)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        logger.info("Migration: 2026-05-06-add-oos-columns-research-best")
        logger.info("DB:        %s", DB_PATH)
        logger.info("Mode:      %s", "APPLY" if apply else "DRY-RUN")

        existing = _existing_columns(conn)
        logger.info("Existing research_best columns: %s", sorted(existing))

        # Determine which columns need adding
        to_add = [(col, ctype, desc) for col, ctype, desc in NEW_COLUMNS if col not in existing]
        already_present = [col for col, _, _ in NEW_COLUMNS if col in existing]

        if already_present:
            logger.info("Already present (skip): %s", already_present)

        if not to_add:
            logger.info("✅  All OOS columns already present — schema change complete.")
        else:
            logger.info("Columns to add: %s", [c for c, _, _ in to_add])
            for col, ctype, desc in to_add:
                sql = f"ALTER TABLE research_best ADD COLUMN {col} {ctype}"
                logger.info("  SQL: %s  -- %s", sql, desc)
                if apply:
                    conn.execute(sql)

        # Bump schema_version
        current_version = _current_max_version(conn)
        logger.info("Current schema_version (MAX): %s", current_version)

        if current_version is not None and current_version >= TARGET_VERSION:
            logger.info(
                "✅  schema_version already at or above %d — version bump skipped.",
                TARGET_VERSION,
            )
        else:
            insert_sql = (
                "INSERT OR IGNORE INTO schema_version (version, applied_at) "
                "VALUES (?, datetime('now'));"
            )
            logger.info(
                "  SQL: INSERT OR IGNORE INTO schema_version (version) VALUES (%d)",
                TARGET_VERSION,
            )
            if apply:
                # Ensure applied_at column exists (self-migrating schema_version)
                sv_cols = {r[1] for r in conn.execute("PRAGMA table_info(schema_version)").fetchall()}
                if "applied_at" not in sv_cols:
                    conn.execute("ALTER TABLE schema_version ADD COLUMN applied_at TEXT")
                conn.execute(insert_sql, (TARGET_VERSION,))

        if apply:
            conn.commit()
            # Verify
            final_cols = _existing_columns(conn)
            new_version = _current_max_version(conn)
            oos_cols_present = all(col in final_cols for col, _, _ in NEW_COLUMNS)
            logger.info("")
            if oos_cols_present:
                logger.info("✅  All OOS columns present after migration.")
            else:
                missing = [col for col, _, _ in NEW_COLUMNS if col not in final_cols]
                logger.error("❌  Missing columns after migration: %s", missing)
                sys.exit(1)
            logger.info("✅  schema_version now %d", new_version)
        else:
            logger.info("")
            logger.info("--- Dry-run complete. Run with --apply to execute.")

    finally:
        conn.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply the migration (default: dry-run only)",
    )
    args = parser.parse_args(argv)
    _run(apply=args.apply)


if __name__ == "__main__":
    main()
