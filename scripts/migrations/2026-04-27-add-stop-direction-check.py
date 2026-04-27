#!/usr/bin/env python3
"""
Migration: 2026-04-27 — Add CHECK (stop_price direction guard) to trades.

Background
----------
DB row 140 (AMD long, sp500) had stop_price=$294.80 which is ABOVE entry_price=$278.25
— structurally invalid for a long position. Root cause: backfill scripts
(reconcile_ledger.py, reconcile_positions.py, backfill_orphan_trades.py) copied
stop_price from broker state (trailing stop trigger) without direction sanity-check.
A trailing stop that has moved above entry is operationally valid, but should not
be recorded as stop_price; it should be NULL with stop_order_id tracking the order.

Also affected: closed trades 101-104 (COP, CVX, D, DOW) had trailing stops above entry.
All were set to NULL before this migration via audit-fix-3 data repair step.

What this migration does
------------------------
1. Pre-flight: abort if any inverted-stop rows remain.
2. Create trades_new with CHECK constraint:
     CHECK (
       stop_price IS NULL
       OR (direction = 'long'  AND stop_price < entry_price)
       OR (direction = 'short' AND stop_price > entry_price)
     )
3. INSERT INTO trades_new SELECT * FROM trades.
4. DROP TABLE trades; ALTER TABLE trades_new RENAME TO trades.
5. Recreate 4 indexes.
6. Verify constraint present and test INSERT rejects inverted stops.

Flags
-----
--dry-run   Show what would be done, but make no changes (default).
--apply     Execute the migration.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "atlas.db"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── New DDL ─────────────────────────────────────────────────────────────────

_TRADES_NEW_DDL = """
CREATE TABLE trades_new (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    strategy        TEXT    NOT NULL,
    universe        TEXT,
    direction       TEXT    DEFAULT 'long',
    entry_date      TEXT    NOT NULL,
    entry_price     REAL    NOT NULL,
    shares          INTEGER NOT NULL,
    stop_price      REAL,
    take_profit     REAL,
    exit_date       TEXT,
    exit_price      REAL,
    exit_reason     TEXT,
    pnl             REAL,
    pnl_pct         REAL,
    mae             REAL,
    mfe             REAL,
    hold_days       INTEGER,
    confidence      REAL,
    regime_at_entry TEXT,
    regime_at_exit  TEXT,
    status          TEXT    DEFAULT 'open',
    config_version  TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    stop_order_id   TEXT    DEFAULT '',
    tp_order_id     TEXT    DEFAULT '',
    CHECK (exit_date IS NULL OR exit_date >= entry_date),
    CHECK (
        stop_price IS NULL
        OR (direction = 'long'  AND stop_price < entry_price)
        OR (direction = 'short' AND stop_price > entry_price)
    )
)
"""

_INDEX_DDLS = [
    "CREATE INDEX idx_trades_status   ON trades_new(status)",
    "CREATE INDEX idx_trades_strategy ON trades_new(strategy)",
    "CREATE INDEX idx_trades_dates    ON trades_new(entry_date, exit_date)",
    "CREATE UNIQUE INDEX idx_trades_unique_open ON trades_new(ticker, universe) WHERE status='open'",
]


# ── Pre-flight ───────────────────────────────────────────────────────────────

def _preflight(conn: sqlite3.Connection) -> None:
    """Abort if any inverted-stop rows remain."""
    rows = conn.execute(
        """
        SELECT id, ticker, direction, entry_price, stop_price, status
        FROM trades
        WHERE stop_price IS NOT NULL
          AND stop_price > 0
          AND (
            (direction = 'long'  AND stop_price >= entry_price)
            OR (direction = 'short' AND stop_price <= entry_price)
          )
        """
    ).fetchall()
    if rows:
        logger.error("Pre-flight FAILED — %d inverted-stop trade(s) remain:", len(rows))
        for r in rows:
            logger.error(
                "  id=%s %s (%s) dir=%s entry=%s stop=%s",
                r[0], r[1], r[5], r[2], r[3], r[4],
            )
        logger.error(
            "Fix inverted stops first (set stop_price=NULL), then re-run."
        )
        sys.exit(1)
    logger.info("Pre-flight OK — 0 inverted-stop trades.")


# ── Migration ────────────────────────────────────────────────────────────────

def _run_migration(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        # 1. Create trades_new with both CHECK constraints
        conn.execute(_TRADES_NEW_DDL)
        logger.info("Created trades_new with stop-direction CHECK constraint.")

        # 2. Copy all rows
        conn.execute(
            """
            INSERT INTO trades_new
            SELECT id, ticker, strategy, universe, direction,
                   entry_date, entry_price, shares, stop_price, take_profit,
                   exit_date, exit_price, exit_reason, pnl, pnl_pct,
                   mae, mfe, hold_days, confidence,
                   regime_at_entry, regime_at_exit, status, config_version,
                   created_at, updated_at, stop_order_id, tp_order_id
            FROM trades
            """
        )
        count = conn.execute("SELECT COUNT(*) FROM trades_new").fetchone()[0]
        logger.info("Copied %d rows into trades_new.", count)

        # 3. Drop old table + rename
        conn.execute("DROP TABLE trades")
        conn.execute("ALTER TABLE trades_new RENAME TO trades")
        logger.info("Renamed trades_new → trades.")

        # 4. Recreate indexes
        for ddl in _INDEX_DDLS:
            final_ddl = ddl.replace(" ON trades_new(", " ON trades(")
            conn.execute(final_ddl)
        logger.info("Recreated %d indexes.", len(_INDEX_DDLS))

        conn.execute("COMMIT")
        logger.info("Migration committed successfully.")
    except Exception:
        conn.execute("ROLLBACK")
        logger.exception("Migration FAILED — rolled back.")
        raise


# ── Verification ─────────────────────────────────────────────────────────────

def _verify(conn: sqlite3.Connection) -> None:
    """Confirm the CHECK constraint is present and rejects inverted inserts."""
    schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
    ).fetchone()[0]
    if "stop_price IS NULL" not in schema:
        logger.error("VERIFY FAILED — stop-direction CHECK not found in schema!")
        sys.exit(1)
    logger.info("Verified: stop-direction CHECK present in schema.")

    indexes = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trades'"
    ).fetchall()
    logger.info("Indexes present: %s", [r[0] for r in indexes])

    count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    logger.info("Row count after migration: %d", count)

    # Test 1: inverted long stop → must raise IntegrityError
    rejected = False
    try:
        conn.execute(
            """
            INSERT INTO trades (ticker, strategy, entry_date, entry_price, shares,
                                direction, stop_price, status)
            VALUES ('TEST_CHECK', 'test', date('now'), 100.0, 1, 'long', 110.0, 'open')
            """
        )
        conn.execute("DELETE FROM trades WHERE ticker='TEST_CHECK'")
        logger.error("VERIFY FAILED — inverted long stop was NOT rejected!")
        sys.exit(1)
    except sqlite3.IntegrityError:
        rejected = True
    if rejected:
        logger.info("Verified: inverted long stop correctly raises IntegrityError.")

    # Test 2: valid long stop → must succeed
    try:
        conn.execute(
            """
            INSERT INTO trades (ticker, strategy, entry_date, entry_price, shares,
                                direction, stop_price, status)
            VALUES ('TEST_CHECK_OK', 'test', date('now'), 100.0, 1, 'long', 90.0, 'open')
            """
        )
        conn.execute("DELETE FROM trades WHERE ticker='TEST_CHECK_OK'")
        logger.info("Verified: valid long stop accepted.")
    except sqlite3.IntegrityError as e:
        logger.error("VERIFY FAILED — valid long stop was incorrectly rejected: %s", e)
        sys.exit(1)


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add stop-direction CHECK constraint to trades table"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Execute the migration (default: dry-run only)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview without executing (default mode if --apply is not set)",
    )
    args = parser.parse_args()

    dry_run = not args.apply

    logger.info(
        "Migration: stop-direction CHECK constraint — DB=%s — mode=%s",
        DB_PATH,
        "DRY RUN" if dry_run else "APPLY",
    )

    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=OFF")  # Disable during table swap

    try:
        _preflight(conn)

        if dry_run:
            # Count rows and check schema
            count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            current_schema = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='trades'"
            ).fetchone()[0]
            already_has_check = "stop_price IS NULL" in current_schema
            logger.info(
                "DRY RUN: would migrate %d rows; constraint already present: %s",
                count,
                already_has_check,
            )
            logger.info("Re-run with --apply to execute.")
            return

        _run_migration(conn)
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()

    # Verify using fresh connection
    conn2 = sqlite3.connect(str(DB_PATH))
    try:
        _verify(conn2)
    finally:
        conn2.close()

    logger.info("Migration 2026-04-27-add-stop-direction-check: COMPLETE")


if __name__ == "__main__":
    main()
