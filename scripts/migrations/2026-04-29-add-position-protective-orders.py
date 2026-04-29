#!/usr/bin/env python3
"""
Migration: 2026-04-29-add-position-protective-orders.py

Creates the position_protective_orders table — a single canonical row per open
position tracking the broker-confirmed stop and TP order IDs.

Eliminates the multi-writer drift on trades.stop_order_id (3 writers, 18+ leak
commits) by providing one authoritative table for protective-order state.

Usage:
    python3 scripts/migrations/2026-04-29-add-position-protective-orders.py            # dry-run
    python3 scripts/migrations/2026-04-29-add-position-protective-orders.py --apply    # apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ATLAS_ROOT))

from db.atlas_db import get_db  # noqa: E402

# ── DDL ──────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = """\
CREATE TABLE IF NOT EXISTS position_protective_orders (
    market_id       TEXT NOT NULL,
    ticker          TEXT NOT NULL,
    trade_id        INTEGER,               -- FK to trades.id (nullable for legacy)
    position_qty    REAL NOT NULL,
    stop_order_id   TEXT,                  -- Alpaca order_id of stop
    stop_price      REAL,                  -- The stop trigger price
    tp_order_id     TEXT,                  -- Alpaca order_id of TP limit
    tp_price        REAL,                  -- The TP limit price
    oco_class       TEXT,                  -- 'oco' | 'bracket' | NULL (independent)
    last_synced_at  TEXT NOT NULL,         -- ISO timestamp of last sync from broker truth
    status          TEXT NOT NULL DEFAULT 'active',  -- 'active' | 'closed' | 'detached'
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (market_id, ticker)
);"""

CREATE_INDEXES_SQL = [
    "CREATE INDEX IF NOT EXISTS idx_protective_status   ON position_protective_orders(status);",
    "CREATE INDEX IF NOT EXISTS idx_protective_trade_id ON position_protective_orders(trade_id);",
]

ALL_DDL = [CREATE_TABLE_SQL] + CREATE_INDEXES_SQL

_TABLE_NAME = "position_protective_orders"


def _run(apply: bool) -> None:
    print(f"Migration: 2026-04-29-add-position-protective-orders")
    print(f"Mode:      {'APPLY' if apply else 'DRY-RUN'}")
    print()

    print("=== DDL to execute ===")
    for ddl in ALL_DDL:
        print(ddl)
    print()

    if not apply:
        print("--- Dry-run complete. Run with --apply to execute.")
        return

    try:
        with get_db() as db:
            # Report row count before (0 if table doesn't exist yet)
            existing = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE_NAME,),
            ).fetchone()
            before_count: int | str = 0
            if existing:
                before_count = db.execute(
                    f"SELECT COUNT(*) FROM {_TABLE_NAME}"
                ).fetchone()[0]
                print(f"Table {_TABLE_NAME} already exists — rows before: {before_count}")
            else:
                print(f"Table {_TABLE_NAME} does not yet exist — will create.")

            for ddl in ALL_DDL:
                db.executescript(ddl)

            # Verify
            check = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (_TABLE_NAME,),
            ).fetchone()
            if not check:
                print(f"\n❌ ERROR: table {_TABLE_NAME} not found after apply!", file=sys.stderr)
                sys.exit(1)

            after_count = db.execute(
                f"SELECT COUNT(*) FROM {_TABLE_NAME}"
            ).fetchone()[0]

            idx_count = db.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type='index' AND tbl_name=?",
                (_TABLE_NAME,),
            ).fetchone()[0]

            print(f"\n✅ Table {_TABLE_NAME} ready.")
            print(f"   Rows before: {before_count}  →  after: {after_count}")
            print(f"   Indexes on {_TABLE_NAME}: {idx_count}")

    except Exception as exc:
        print(f"\n❌ Migration failed: {exc}", file=sys.stderr)
        sys.exit(1)


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
