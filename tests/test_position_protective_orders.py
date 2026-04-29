#!/usr/bin/env python3
"""Tests for Phase A.1 — position_protective_orders table, migration, and accessors.

Covers:
  1.  Table + indexes exist after init_db (schema.sql path)
  2.  upsert_protective_record inserts a new row
  3.  upsert_protective_record updates an existing row with new IDs
  4.  upsert_protective_record sets last_synced_at
  5.  close_protective_record sets status='closed'
  6.  close_protective_record is idempotent
  7.  get_protective_record returns None for closed records
  8.  list_active_protective_records filters by market_id
  9.  list_protective_gaps finds uncovered open positions
  10. list_protective_gaps excludes covered open positions
  11. PRIMARY KEY enforces uniqueness per (market_id, ticker)
"""
from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

# ── Project path ──────────────────────────────────────────────────────────────
ATLAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATLAS_ROOT))

import db.atlas_db as _adb
from db.atlas_db import (
    close_protective_record,
    get_protective_record,
    init_db,
    list_active_protective_records,
    list_protective_gaps,
    upsert_protective_record,
)

# NOTE: _isolate_prod_db is autouse in conftest.py — every test automatically
# uses an isolated tmp DB. init_db() called there populates schema from schema.sql,
# which now includes position_protective_orders.
# We call _apply_migration() as belt-and-suspenders to verify the migration
# script itself is idempotent (even when table already exists from schema.sql).

_MIGRATION_PATH = (
    ATLAS_ROOT / "scripts" / "migrations"
    / "2026-04-29-add-position-protective-orders.py"
)


def _apply_migration() -> None:
    """Run migration script with --apply against the current test DB."""
    spec = importlib.util.spec_from_file_location("migration", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.main(["--apply"])


def _insert_open_trade(
    ticker: str,
    universe: str,
    stop_order_id: str = "",
    tp_order_id: str = "",
    stop_price: float = 90.0,
    take_profit: float = 110.0,
    entry_price: float = 100.0,
    shares: int = 10,
) -> int:
    """Insert a minimal open trade row; returns the new trade id."""
    with _adb.get_db() as db:
        cur = db.execute(
            """
            INSERT INTO trades
                (ticker, strategy, universe, direction, entry_date, entry_price,
                 shares, stop_price, take_profit,
                 stop_order_id, tp_order_id, status, superseded)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,'open',0)
            """,
            (
                ticker, "test_strategy", universe, "long",
                "2026-04-01", entry_price, shares,
                stop_price, take_profit,
                stop_order_id, tp_order_id,
            ),
        )
        return cur.lastrowid


# ════════════════════════════════════════════════════════════════════════════
# 1. Schema: table + indexes exist after init_db
# ════════════════════════════════════════════════════════════════════════════

class TestTableSchema:
    def test_table_exists_after_migration(self):
        """Table must exist (created by schema.sql via init_db, confirmed by migration)."""
        _apply_migration()  # idempotent — table already exists
        with _adb.get_db() as db:
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='position_protective_orders'"
            ).fetchone()
        assert row is not None, "position_protective_orders table not found"

    def test_status_index_exists(self):
        """idx_protective_status must exist."""
        _apply_migration()
        with _adb.get_db() as db:
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_protective_status'"
            ).fetchone()
        assert row is not None, "idx_protective_status index not found"

    def test_trade_id_index_exists(self):
        """idx_protective_trade_id must exist."""
        _apply_migration()
        with _adb.get_db() as db:
            row = db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND name='idx_protective_trade_id'"
            ).fetchone()
        assert row is not None, "idx_protective_trade_id index not found"


# ════════════════════════════════════════════════════════════════════════════
# 2–4. Upsert behaviour
# ════════════════════════════════════════════════════════════════════════════

class TestUpsertProtectiveRecord:
    def test_upsert_inserts_new_record(self):
        """First upsert creates a new row."""
        upsert_protective_record(
            market_id="sp500", ticker="AAPL",
            trade_id=42, position_qty=10.0,
            stop_order_id="stop-001", stop_price=140.0,
            tp_order_id="tp-001", tp_price=180.0,
            oco_class="oco",
        )
        rec = get_protective_record("sp500", "AAPL")
        assert rec is not None
        assert rec["ticker"] == "AAPL"
        assert rec["market_id"] == "sp500"
        assert rec["stop_order_id"] == "stop-001"
        assert rec["tp_order_id"] == "tp-001"
        assert rec["status"] == "active"

    def test_upsert_updates_existing_record(self):
        """Second upsert with new IDs replaces the previous row."""
        upsert_protective_record(
            market_id="sp500", ticker="MSFT",
            trade_id=7, position_qty=5.0,
            stop_order_id="stop-old", stop_price=290.0,
        )
        upsert_protective_record(
            market_id="sp500", ticker="MSFT",
            trade_id=7, position_qty=5.0,
            stop_order_id="stop-new", stop_price=295.0,
            tp_order_id="tp-new", tp_price=350.0,
        )
        rec = get_protective_record("sp500", "MSFT")
        assert rec is not None
        assert rec["stop_order_id"] == "stop-new"
        assert rec["tp_order_id"] == "tp-new"
        assert float(rec["stop_price"]) == 295.0

    def test_upsert_sets_last_synced_at(self):
        """upsert_protective_record must populate last_synced_at."""
        upsert_protective_record(
            market_id="sp500", ticker="NVDA",
            trade_id=None, position_qty=3.0,
        )
        rec = get_protective_record("sp500", "NVDA")
        assert rec is not None
        assert rec["last_synced_at"], "last_synced_at must not be empty"
        # Should be a valid ISO timestamp string
        assert "T" in rec["last_synced_at"] or "-" in rec["last_synced_at"]

    def test_upsert_null_optional_fields(self):
        """Upsert with all optional fields None should succeed."""
        upsert_protective_record(
            market_id="commodity_etfs", ticker="GLD",
            trade_id=None, position_qty=2.0,
        )
        rec = get_protective_record("commodity_etfs", "GLD")
        assert rec is not None
        assert rec["stop_order_id"] is None
        assert rec["tp_order_id"] is None
        assert rec["status"] == "active"


# ════════════════════════════════════════════════════════════════════════════
# 5–6. Close behaviour
# ════════════════════════════════════════════════════════════════════════════

class TestCloseProtectiveRecord:
    def test_close_marks_closed(self):
        """close_protective_record should set status='closed'."""
        upsert_protective_record(
            market_id="sp500", ticker="CAT",
            trade_id=99, position_qty=1.0,
        )
        close_protective_record("sp500", "CAT")
        with _adb.get_db() as db:
            row = db.execute(
                "SELECT status FROM position_protective_orders "
                "WHERE market_id='sp500' AND ticker='CAT'",
            ).fetchone()
        assert row is not None
        assert row["status"] == "closed"

    def test_close_is_idempotent(self):
        """Calling close_protective_record twice must not raise."""
        upsert_protective_record(
            market_id="sp500", ticker="XOM",
            trade_id=55, position_qty=4.0,
        )
        close_protective_record("sp500", "XOM")
        close_protective_record("sp500", "XOM")  # second call — must not raise
        with _adb.get_db() as db:
            row = db.execute(
                "SELECT status FROM position_protective_orders "
                "WHERE market_id='sp500' AND ticker='XOM'",
            ).fetchone()
        assert row["status"] == "closed"

    def test_close_nonexistent_row_is_safe(self):
        """close_protective_record on a missing row must not raise."""
        close_protective_record("sp500", "DOESNOTEXIST")  # should be silent no-op


# ════════════════════════════════════════════════════════════════════════════
# 7. get_protective_record returns None for closed records
# ════════════════════════════════════════════════════════════════════════════

class TestGetProtectiveRecord:
    def test_get_protective_record_returns_active_only(self):
        """get_protective_record must ignore closed records."""
        upsert_protective_record(
            market_id="sp500", ticker="IBM",
            trade_id=10, position_qty=2.0,
        )
        close_protective_record("sp500", "IBM")
        rec = get_protective_record("sp500", "IBM")
        assert rec is None, "Should return None for closed record"

    def test_get_protective_record_missing_returns_none(self):
        """get_protective_record for non-existent ticker returns None."""
        rec = get_protective_record("sp500", "NOPE")
        assert rec is None

    def test_get_protective_record_happy_path(self):
        """get_protective_record returns correct row for active record."""
        upsert_protective_record(
            market_id="sector_etfs", ticker="XLK",
            trade_id=200, position_qty=8.0,
            stop_order_id="s-xyz", stop_price=155.0,
        )
        rec = get_protective_record("sector_etfs", "XLK")
        assert rec is not None
        assert rec["trade_id"] == 200
        assert float(rec["stop_price"]) == 155.0


# ════════════════════════════════════════════════════════════════════════════
# 8. list_active_protective_records market filter
# ════════════════════════════════════════════════════════════════════════════

class TestListActive:
    def test_list_active_filters_by_market(self):
        """list_active_protective_records(market_id=X) must exclude other markets."""
        upsert_protective_record("sp500", "AAPL", 1, 5.0)
        upsert_protective_record("commodity_etfs", "GLD", 2, 2.0)
        upsert_protective_record("sector_etfs", "XLF", 3, 3.0)

        sp_records = list_active_protective_records("sp500")
        tickers = [r["ticker"] for r in sp_records]
        assert "AAPL" in tickers
        assert "GLD" not in tickers
        assert "XLF" not in tickers

    def test_list_active_all_markets(self):
        """list_active_protective_records() with no filter returns all active."""
        upsert_protective_record("sp500", "TSLA", 10, 5.0)
        upsert_protective_record("commodity_etfs", "SLV", 11, 2.0)

        all_records = list_active_protective_records()
        tickers = [r["ticker"] for r in all_records]
        assert "TSLA" in tickers
        assert "SLV" in tickers

    def test_list_active_excludes_closed(self):
        """Closed records must not appear in list_active_protective_records."""
        upsert_protective_record("sp500", "WMT", 20, 3.0)
        close_protective_record("sp500", "WMT")

        records = list_active_protective_records("sp500")
        tickers = [r["ticker"] for r in records]
        assert "WMT" not in tickers


# ════════════════════════════════════════════════════════════════════════════
# 9–10. list_protective_gaps
# ════════════════════════════════════════════════════════════════════════════

class TestProtectiveGaps:
    def test_list_protective_gaps_finds_uncovered_positions(self):
        """Open trade with NO active protective record must appear in gaps."""
        _insert_open_trade("UNCOVERED", "sp500")
        # No upsert_protective_record call
        gaps = list_protective_gaps()
        tickers = [g["ticker"] for g in gaps]
        assert "UNCOVERED" in tickers

    def test_list_protective_gaps_excludes_covered_positions(self):
        """Open trade WITH an active protective record must NOT appear in gaps."""
        _insert_open_trade("COVERED", "sp500", entry_price=100.0, stop_price=90.0)
        upsert_protective_record("sp500", "COVERED", trade_id=None, position_qty=10.0)
        gaps = list_protective_gaps()
        tickers = [g["ticker"] for g in gaps]
        assert "COVERED" not in tickers

    def test_list_protective_gaps_market_filter(self):
        """list_protective_gaps(market_id=X) only returns gaps for that market."""
        _insert_open_trade("GAP_SP", "sp500")
        _insert_open_trade("GAP_COM", "commodity_etfs")
        # Neither has a protective record
        sp_gaps = list_protective_gaps("sp500")
        sp_tickers = [g["ticker"] for g in sp_gaps]
        assert "GAP_SP" in sp_tickers
        assert "GAP_COM" not in sp_tickers

    def test_list_protective_gaps_gap_returns_trade_metadata(self):
        """Gap entries must include trade_id, ticker, market_id, entry_date, days_open."""
        _insert_open_trade("META_GAP", "sp500")
        gaps = list_protective_gaps()
        gap = next((g for g in gaps if g["ticker"] == "META_GAP"), None)
        assert gap is not None
        assert "trade_id" in gap
        assert "ticker" in gap
        assert "market_id" in gap
        assert "entry_date" in gap
        assert "days_open" in gap


# ════════════════════════════════════════════════════════════════════════════
# 11. PRIMARY KEY uniqueness enforcement
# ════════════════════════════════════════════════════════════════════════════

class TestUniqueness:
    def test_uniqueness_enforced(self):
        """INSERT (not upsert) of duplicate (market_id, ticker) must raise."""
        upsert_protective_record("sp500", "DUP", 1, 5.0)

        with _adb.get_db() as db:
            with pytest.raises(sqlite3.IntegrityError):
                # Direct INSERT (not OR REPLACE) should violate PRIMARY KEY
                db.execute(
                    "INSERT INTO position_protective_orders "
                    "(market_id, ticker, position_qty, last_synced_at, status) "
                    "VALUES (?,?,?,?,'active')",
                    ("sp500", "DUP", 5.0, "2026-04-29T00:00:00Z"),
                )
