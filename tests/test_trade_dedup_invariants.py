"""Trade dedup invariant tests.

Verifies:
  - The partial UNIQUE index on closed trades (idx_trades_no_dup_closed)
    prevents duplicate (ticker, entry_day, exit_day) rows with status='closed'.
  - Superseded and open rows are NOT blocked by the closed-trade index.
  - The audit script correctly identifies seeded duplicate groups.
  - The audit script's --mark-superseded action works as expected.

All tests use the autouse _isolate_prod_db fixture — never touch data/atlas.db.
The fixture provisions a fresh isolated SQLite DB for each test function.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.atlas_db as _adb
from db.atlas_db import init_db


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_db_path() -> Path:
    """Return the currently-isolated DB path (set by _isolate_prod_db fixture)."""
    override = getattr(_adb, "_db_path_override", None)
    if override:
        return Path(override)
    return _adb.DB_PATH


def _raw_insert_closed(
    ticker: str,
    entry_date: str = "2026-01-10",
    exit_date: str = "2026-01-11",
    pnl: float = 10.0,
    strategy: str = "test_strategy",
    shares: int = 1,
    entry_price: float = 100.0,
    exit_price: float = 110.0,
) -> int:
    """Insert a closed trade row directly (bypasses record_trade_entry logic).

    Returns the new row id.
    """
    with _adb.get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades
              (ticker, strategy, universe, entry_date, entry_price, shares,
               exit_date, exit_price, pnl, status, direction, stop_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', 'long', ?)
            """,
            (
                ticker, strategy, "sp500", entry_date, entry_price, shares,
                exit_date, exit_price, pnl,
                entry_price * 0.9,  # valid long stop (< entry_price)
            ),
        )
        return cursor.lastrowid


def _raw_insert_open(
    ticker: str,
    entry_date: str = "2026-01-10",
    strategy: str = "test_strategy",
    shares: int = 1,
    entry_price: float = 100.0,
) -> int:
    """Insert an open trade row directly. Returns new row id."""
    with _adb.get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades
              (ticker, strategy, universe, entry_date, entry_price, shares,
               status, direction, stop_price)
            VALUES (?, ?, ?, ?, ?, ?, 'open', 'long', ?)
            """,
            (ticker, strategy, "sp500", entry_date, entry_price, shares,
             entry_price * 0.9),
        )
        return cursor.lastrowid


def _raw_insert_superseded(
    ticker: str,
    entry_date: str = "2026-01-10",
    exit_date: str = "2026-01-11",
    pnl: float = 10.0,
    strategy: str = "test_strategy",
    shares: int = 1,
    entry_price: float = 100.0,
    exit_price: float = 110.0,
) -> int:
    """Insert a superseded trade row. Returns new row id."""
    with _adb.get_db() as conn:
        cursor = conn.execute(
            """
            INSERT INTO trades
              (ticker, strategy, universe, entry_date, entry_price, shares,
               exit_date, exit_price, pnl, status, direction, stop_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'superseded', 'long', ?)
            """,
            (
                ticker, strategy, "sp500", entry_date, entry_price, shares,
                exit_date, exit_price, pnl,
                entry_price * 0.9,
            ),
        )
        return cursor.lastrowid


def _get_status(row_id: int) -> str | None:
    with _adb.get_db() as conn:
        row = conn.execute(
            "SELECT status FROM trades WHERE id=?", (row_id,)
        ).fetchone()
    return row["status"] if row else None


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestUniqueClosedDupRejected:
    """idx_trades_no_dup_closed prevents two closed rows for same (ticker, entry_day, exit_day)."""

    def test_unique_closed_dup_rejected(self):
        """Second closed insert with same ticker/dates raises IntegrityError."""
        _raw_insert_closed("AAPL", entry_date="2026-02-01", exit_date="2026-02-02", pnl=5.0)
        with pytest.raises(sqlite3.IntegrityError):
            _raw_insert_closed("AAPL", entry_date="2026-02-01", exit_date="2026-02-02", pnl=5.0)

    def test_unique_closed_different_exit_allowed(self):
        """Two closed rows same ticker but different exit dates are allowed."""
        _raw_insert_closed("MSFT", entry_date="2026-02-01", exit_date="2026-02-02", pnl=5.0)
        _raw_insert_closed("MSFT", entry_date="2026-02-01", exit_date="2026-02-03", pnl=6.0)
        with _adb.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE ticker='MSFT' AND status='closed'"
            ).fetchone()[0]
        assert count == 2, f"Expected 2 rows, got {count}"

    def test_unique_closed_different_ticker_allowed(self):
        """Two different tickers on same dates are fine."""
        _raw_insert_closed("GOOG", entry_date="2026-02-01", exit_date="2026-02-02", pnl=7.0)
        _raw_insert_closed("TSLA", entry_date="2026-02-01", exit_date="2026-02-02", pnl=3.0)
        with _adb.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE ticker IN ('GOOG','TSLA') AND status='closed'"
            ).fetchone()[0]
        assert count == 2


class TestSupersededDoesNotBlock:
    """superseded rows are excluded from the partial index — no conflict with closed rows."""

    def test_superseded_does_not_block_closed(self):
        """Insert superseded + closed for same ticker/dates → both succeed."""
        _raw_insert_superseded("CVX", entry_date="2026-03-01", exit_date="2026-03-02", pnl=3.99)
        _raw_insert_closed("CVX", entry_date="2026-03-01", exit_date="2026-03-02", pnl=3.99)
        with _adb.get_db() as conn:
            rows = conn.execute(
                "SELECT id, status FROM trades WHERE ticker='CVX' ORDER BY id"
            ).fetchall()
        statuses = [r["status"] for r in rows]
        assert "superseded" in statuses
        assert "closed" in statuses
        assert len(rows) == 2

    def test_two_superseded_rows_same_dates_allowed(self):
        """Two superseded rows with same ticker/dates are fine (not covered by index)."""
        _raw_insert_superseded("AMD", entry_date="2026-03-05", exit_date="2026-03-06", pnl=1.0)
        _raw_insert_superseded("AMD", entry_date="2026-03-05", exit_date="2026-03-06", pnl=1.0)
        with _adb.get_db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE ticker='AMD' AND status='superseded'"
            ).fetchone()[0]
        assert count == 2


class TestOpenDoesNotBlockClosedSameWindow:
    """open rows with same entry_date don't conflict with closed rows."""

    def test_open_does_not_block_closed_same_window(self):
        """Open trade with today's entry + closed trade same entry+exit → both succeed."""
        # Closed trade: entered and exited same day
        _raw_insert_closed(
            "GLD", entry_date="2026-04-01", exit_date="2026-04-01", pnl=-2.5
        )
        # Open trade: entered same day (no exit_date)
        _raw_insert_open("GLD", entry_date="2026-04-01")
        with _adb.get_db() as conn:
            closed_ct = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE ticker='GLD' AND status='closed'"
            ).fetchone()[0]
            open_ct = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE ticker='GLD' AND status='open'"
            ).fetchone()[0]
        assert closed_ct == 1
        assert open_ct == 1


class TestAuditScriptFindsDups:
    """Audit script's run_audit() finds known dup pairs in dry-run mode."""

    def test_audit_script_finds_known_dups(self, tmp_path: Path):
        """Seed 2 dup pairs in a raw DB (without the constraint) and verify
        run_audit(relaxed=False) finds both groups without modifying the DB.

        Why a raw_db: the isolated test DB now has idx_trades_no_dup_closed
        which correctly blocks dup inserts. To test the audit script's
        detection logic, we seed a separate raw DB that mirrors the schema
        BEFORE the constraint was added (simulating pre-migration state).
        """
        from scripts.audit_duplicate_trades import run_audit

        # Create a raw DB without the idx_trades_no_dup_closed constraint
        raw_db = tmp_path / "raw_audit.db"
        conn_raw = sqlite3.connect(str(raw_db))
        conn_raw.row_factory = sqlite3.Row
        # Initialize schema (without the new UNIQUE index)
        init_db_str = """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            universe TEXT,
            direction TEXT DEFAULT 'long',
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            stop_price REAL,
            take_profit REAL,
            exit_date TEXT,
            exit_price REAL,
            exit_reason TEXT,
            pnl REAL,
            pnl_pct REAL,
            mae REAL,
            mfe REAL,
            hold_days INTEGER,
            confidence REAL,
            regime_at_entry TEXT,
            regime_at_exit TEXT,
            status TEXT DEFAULT 'open',
            config_version TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            stop_order_id TEXT DEFAULT '',
            tp_order_id TEXT DEFAULT '',
            CHECK (exit_date IS NULL OR exit_date >= entry_date)
        )
        """
        conn_raw.execute(init_db_str)
        # Seed two dup pairs
        conn_raw.executemany(
            "INSERT INTO trades (ticker, strategy, universe, entry_date, entry_price, "
            "shares, exit_date, exit_price, pnl, status, direction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', 'long')",
            [
                ("D",  "mean_reversion", "sp500", "2026-03-24", 100.0, 13, "2026-03-25", 103.16, 41.08),
                ("D",  "mean_reversion", "sp500", "2026-03-24", 100.0, 13, "2026-03-25", 103.16, 41.08),
                ("SLV","momentum_breakout","sp500","2026-04-22", 27.0,  6, "2026-04-22", 26.07, -5.6),
                ("SLV","connors_rsi2",    "sp500", "2026-04-22", 27.0,  6, "2026-04-22", 26.07, -5.6),
            ],
        )
        conn_raw.commit()
        conn_raw.close()

        result = run_audit(db_path=raw_db, relaxed=False, mark_superseded=False)

        assert result["dup_group_count"] == 2, (
            f"Expected 2 dup groups, got {result['dup_group_count']}"
        )
        assert result["superseded_row_count"] == 2, (
            f"Expected 2 rows to supersede, got {result['superseded_row_count']}"
        )
        assert abs(result["pnl_inflation"] - (41.08 + (-5.6))) < 0.01, (
            f"Expected PnL inflation ~$35.48, got ${result['pnl_inflation']:.2f}"
        )

        # Confirm no changes were made (dry-run)
        conn_check = sqlite3.connect(str(raw_db))
        conn_check.row_factory = sqlite3.Row
        closed_ct = conn_check.execute(
            "SELECT COUNT(*) FROM trades WHERE status='closed'"
        ).fetchone()[0]
        conn_check.close()
        assert closed_ct == 4, f"Dry-run should not modify rows, got {closed_ct} closed"


class TestAuditScriptMarksSuperseded:
    """Audit script's --mark-superseded action works correctly."""

    def test_audit_script_marks_superseded(self, tmp_path: Path):
        """Seed dup pair; run_audit(mark_superseded=True); verify older row stays closed,
        newer row becomes superseded."""
        from scripts.audit_duplicate_trades import run_audit

        raw_db = tmp_path / "mark_test.db"
        conn_raw = sqlite3.connect(str(raw_db))
        conn_raw.row_factory = sqlite3.Row
        conn_raw.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL,
                universe TEXT,
                direction TEXT DEFAULT 'long',
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                shares INTEGER NOT NULL,
                stop_price REAL,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                CHECK (exit_date IS NULL OR exit_date >= entry_date)
            )
            """
        )
        conn_raw.executemany(
            "INSERT INTO trades (ticker, strategy, universe, entry_date, entry_price, "
            "shares, exit_date, exit_price, pnl, status, direction) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'closed', 'long')",
            [
                ("NOC", "connors_rsi2", "sp500", "2026-03-24", 220.0, 1, "2026-03-25", 227.75, 7.75),
                ("NOC", "connors_rsi2", "sp500", "2026-03-24", 220.0, 1, "2026-03-25", 227.75, 7.75),
            ],
        )
        conn_raw.commit()
        # Get IDs of inserted rows
        rows = conn_raw.execute(
            "SELECT id FROM trades WHERE ticker='NOC' ORDER BY id"
        ).fetchall()
        canon_id = rows[0]["id"]
        super_id = rows[1]["id"]
        conn_raw.close()

        result = run_audit(db_path=raw_db, relaxed=False, mark_superseded=True)

        assert result["dup_group_count"] == 1
        assert result["superseded_row_count"] == 1

        # Verify DB state
        conn_check = sqlite3.connect(str(raw_db))
        conn_check.row_factory = sqlite3.Row
        canon_status = conn_check.execute(
            "SELECT status FROM trades WHERE id=?", (canon_id,)
        ).fetchone()["status"]
        super_status = conn_check.execute(
            "SELECT status FROM trades WHERE id=?", (super_id,)
        ).fetchone()["status"]
        conn_check.close()

        assert canon_status == "closed", (
            f"Canonical row id={canon_id} should remain 'closed', got '{canon_status}'"
        )
        assert super_status == "superseded", (
            f"Superseded row id={super_id} should be 'superseded', got '{super_status}'"
        )

    def test_audit_script_idempotent(self, tmp_path: Path):
        """Running --mark-superseded twice produces the same result (idempotent)."""
        from scripts.audit_duplicate_trades import run_audit

        raw_db = tmp_path / "idem_test.db"
        conn_raw = sqlite3.connect(str(raw_db))
        conn_raw.execute(
            """
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL,
                strategy TEXT NOT NULL DEFAULT 'test',
                universe TEXT,
                direction TEXT DEFAULT 'long',
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL DEFAULT 100.0,
                shares INTEGER NOT NULL DEFAULT 1,
                exit_date TEXT,
                exit_price REAL,
                pnl REAL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                CHECK (exit_date IS NULL OR exit_date >= entry_date)
            )
            """
        )
        conn_raw.executemany(
            "INSERT INTO trades (ticker, entry_date, exit_date, pnl, status, direction) "
            "VALUES (?, ?, ?, ?, 'closed', 'long')",
            [
                ("ECL", "2026-03-24", "2026-03-25", 19.16),
                ("ECL", "2026-03-24", "2026-03-25", 19.16),
            ],
        )
        conn_raw.commit()
        conn_raw.close()

        r1 = run_audit(db_path=raw_db, mark_superseded=True)
        r2 = run_audit(db_path=raw_db, mark_superseded=True)

        assert r1["superseded_row_count"] == 1, f"First run: {r1['superseded_row_count']}"
        assert r2["superseded_row_count"] == 0, (
            f"Second run should be no-op, got {r2['superseded_row_count']} — idempotency broken"
        )
