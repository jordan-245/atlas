#!/usr/bin/env python3
"""
Migration: 2026-04-27 — Add partial UNIQUE index preventing duplicate closed trades.

Background
----------
The reconciler was creating new trade rows instead of detecting existing closed
trades, producing ~30% duplicate rows in the closed-trade ledger.  An audit
(audit-fix-6) identified 6 duplicate groups (CVX, D, ECL, NOC, SLV, UNG) worth
$66.38 in PnL inflation; those rows were marked status='superseded'.

The existing UNIQUE constraint (idx_trades_unique_open) covers only open trades:
    UNIQUE INDEX ON trades(ticker, universe) WHERE status='open'

This migration adds a complementary partial index covering closed trades:
    UNIQUE INDEX ON trades(ticker, DATE(entry_date), DATE(exit_date))
    WHERE status = 'closed'

This prevents the reconciler from ever inserting a second closed trade for the
same (ticker, entry_day, exit_day) pair.  status='superseded' and status='open'
rows are excluded by the WHERE clause, so they never conflict.

SQLite partial indexes are supported since 3.8.9 (2014).
Expression indexes with DATE() are supported since 3.9.0 (2015).
Current SQLite: 3.45.1.

Flags
-----
--dry-run   Show what would be done, make no changes (default).
--apply     Execute the migration.

Service pause
-------------
The migration pauses atlas-dashboard and atlas-telegram-bot during the index
creation, then restarts them.  Index creation on the trades table is fast
(~200 rows) so downtime is under 2 seconds.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "data" / "atlas.db"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

INDEX_NAME = "idx_trades_no_dup_closed"
INDEX_DDL = f"""
CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
  ON trades(ticker, DATE(entry_date), DATE(exit_date))
  WHERE status = 'closed'
"""

SERVICES = ["atlas-dashboard", "atlas-telegram-bot"]


# ── Pre-flight ────────────────────────────────────────────────────────────────

def _preflight(conn: sqlite3.Connection) -> None:
    """Abort if any non-superseded closed-trade dupes remain (would block index creation)."""
    rows = conn.execute(
        """
        SELECT ticker, DATE(entry_date) AS entry_day, DATE(exit_date) AS exit_day,
               COUNT(*) AS n, GROUP_CONCAT(id ORDER BY id) AS ids
        FROM trades
        WHERE status = 'closed'
        GROUP BY ticker, DATE(entry_date), DATE(exit_date)
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if rows:
        logger.error(
            "Pre-flight FAILED — %d dup group(s) with status='closed' still present.",
            len(rows),
        )
        for r in rows:
            logger.error(
                "  ticker=%s entry=%s exit=%s n=%s ids=%s",
                r["ticker"], r["entry_day"], r["exit_day"], r["n"], r["ids"],
            )
        logger.error(
            "Run: python3 scripts/audit_duplicate_trades.py --relaxed --mark-superseded"
        )
        sys.exit(1)
    logger.info("Pre-flight OK — 0 closed-trade duplicate groups remain.")


def _index_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (INDEX_NAME,),
    ).fetchone()
    return row is not None


# ── Service management ────────────────────────────────────────────────────────

def _stop_services() -> None:
    for svc in SERVICES:
        result = subprocess.run(
            ["systemctl", "stop", svc],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("Stopped service: %s", svc)
        else:
            # Service may already be stopped or not installed — non-fatal
            logger.warning("Could not stop %s (may be already stopped): %s", svc, result.stderr.strip())


def _start_services() -> None:
    for svc in SERVICES:
        result = subprocess.run(
            ["systemctl", "start", svc],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            logger.info("Started service: %s", svc)
        else:
            logger.warning("Could not start %s: %s", svc, result.stderr.strip())


# ── Migration ─────────────────────────────────────────────────────────────────

def _run_migration(conn: sqlite3.Connection) -> None:
    if _index_exists(conn):
        logger.info("Index %s already exists — nothing to do.", INDEX_NAME)
        return

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(INDEX_DDL)
        count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
        logger.info(
            "Created %s — covers %d closed-trade rows.", INDEX_NAME, count
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        logger.exception("Migration FAILED — rolled back.")
        raise


# ── Verification ──────────────────────────────────────────────────────────────

def _verify(conn: sqlite3.Connection) -> None:
    """Confirm index is present and rejects dup closed-trade inserts."""
    if not _index_exists(conn):
        logger.error("VERIFY FAILED — index %s not found after migration!", INDEX_NAME)
        sys.exit(1)
    logger.info("Verified: index %s present.", INDEX_NAME)

    # Get a real closed trade to clone for the dup-rejection test
    real = conn.execute(
        "SELECT ticker, entry_date, exit_date, entry_price, shares, strategy FROM trades "
        "WHERE status='closed' ORDER BY id LIMIT 1"
    ).fetchone()
    if not real:
        logger.warning("No closed trades to test rejection against — skipping dup-insert test.")
        return

    ticker     = real["ticker"]
    entry_date = real["entry_date"]
    exit_date  = real["exit_date"]
    entry_price = real["entry_price"]
    shares     = real["shares"]
    strategy   = real["strategy"]

    rejected = False
    try:
        conn.execute(
            """
            INSERT INTO trades
              (ticker, strategy, entry_date, entry_price, shares, exit_date,
               exit_price, pnl, status, direction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'closed', 'long')
            """,
            (ticker, strategy, entry_date, entry_price, shares, exit_date, entry_price, 0.0),
        )
        conn.execute("ROLLBACK")
        logger.error(
            "VERIFY FAILED — dup closed insert for %s was NOT rejected!", ticker
        )
        sys.exit(1)
    except sqlite3.IntegrityError:
        rejected = True
        conn.execute("ROLLBACK")

    if rejected:
        logger.info(
            "Verified: duplicate closed-trade insert for %s correctly raises IntegrityError.",
            ticker,
        )

    # Confirm superseded rows are NOT blocked
    conn.execute("BEGIN")
    try:
        conn.execute(
            """
            INSERT INTO trades
              (ticker, strategy, entry_date, entry_price, shares, exit_date,
               exit_price, pnl, status, direction)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'superseded', 'long')
            """,
            (ticker, strategy, entry_date, entry_price, shares, exit_date, entry_price, 0.0),
        )
        conn.execute("ROLLBACK")
        logger.info("Verified: superseded insert is NOT blocked by the index (correct).")
    except sqlite3.IntegrityError as e:
        conn.execute("ROLLBACK")
        logger.error("VERIFY FAILED — superseded insert was incorrectly blocked: %s", e)
        sys.exit(1)

    count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
    logger.info("Row count (closed): %d", count)

    all_idx = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trades' ORDER BY name"
    ).fetchall()
    logger.info("All indexes on trades: %s", [r[0] for r in all_idx])


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add partial UNIQUE index on closed trades to prevent reconciler dupes"
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
        "Migration: add-trades-no-dup-closed-index — DB=%s — mode=%s",
        DB_PATH,
        "DRY RUN" if dry_run else "APPLY",
    )

    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row

    try:
        _preflight(conn)

        if dry_run:
            count = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()[0]
            already = _index_exists(conn)
            logger.info(
                "DRY RUN: would create index %s on %d closed rows; already exists: %s",
                INDEX_NAME, count, already,
            )
            logger.info("Re-run with --apply to execute.")
            return

        _stop_services()
        try:
            _run_migration(conn)
        finally:
            _start_services()
    finally:
        conn.close()

    # Verify with fresh connection
    conn2 = sqlite3.connect(str(DB_PATH), timeout=30)
    conn2.row_factory = sqlite3.Row
    try:
        _verify(conn2)
    finally:
        conn2.close()

    logger.info("Migration 2026-04-27-add-trades-no-dup-closed-index: COMPLETE")


if __name__ == "__main__":
    main()
