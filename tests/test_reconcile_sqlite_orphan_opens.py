"""Regression tests for reconcile_sqlite_orphan_opens.py.

FIX-SQLITE-ORPHANS-001: SQLite trades with status='open' but no broker
position must be detected and closeable.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import pytest


@pytest.fixture
def tmp_atlas_with_trades(tmp_path, monkeypatch):
    """Build a tmp atlas.db + brokers/state with an orphan."""
    db_path = tmp_path / "atlas.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            universe TEXT NOT NULL,
            direction TEXT,
            entry_date TEXT,
            entry_price REAL,
            shares INTEGER,
            stop_price REAL,
            take_profit REAL,
            confidence REAL,
            regime_at_entry TEXT,
            status TEXT,
            exit_date TEXT,
            exit_price REAL,
            pnl REAL,
            pnl_pct REAL,
            exit_reason TEXT,
            config_version TEXT,
            updated_at TEXT
        );
    """)
    conn.execute(
        "INSERT INTO trades (ticker, strategy, universe, direction, entry_date, "
        "entry_price, shares, status) VALUES "
        "('HELDBYBROKER', 'momentum_breakout', 'sp500', 'long', '2026-04-29', 100.0, 10, 'open'), "
        "('ORPHAN', 'connors_rsi2', 'sp500', 'long', '2026-04-29', 200.0, 5, 'open'), "
        "('CLOSED', 'momentum_breakout', 'sp500', 'long', '2026-04-29', 50.0, 20, 'closed')"
    )
    conn.commit()
    conn.close()

    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True)
    (state_dir / "live_sp500.json").write_text(json.dumps({
        "positions": [{"ticker": "HELDBYBROKER", "strategy": "momentum_breakout"}]
    }))
    # ORPHAN intentionally not in any state file

    from db import atlas_db
    monkeypatch.setattr(atlas_db, "_db_path_override", str(db_path))
    from scripts import reconcile_sqlite_orphan_opens as rec
    monkeypatch.setattr(rec, "STATE_DIR", state_dir)
    return db_path, state_dir


def test_find_orphan_open_trades_finds_orphan(tmp_atlas_with_trades):
    from scripts import reconcile_sqlite_orphan_opens as rec
    orphans = rec.find_orphan_open_trades()
    assert len(orphans) == 1
    assert orphans[0]["ticker"] == "ORPHAN"


def test_find_orphan_open_trades_skips_held(tmp_atlas_with_trades):
    """HELDBYBROKER should NOT be in the orphan list."""
    from scripts import reconcile_sqlite_orphan_opens as rec
    orphans = rec.find_orphan_open_trades()
    tickers = {o["ticker"] for o in orphans}
    assert "HELDBYBROKER" not in tickers


def test_find_orphan_open_trades_skips_closed(tmp_atlas_with_trades):
    """CLOSED trades are not orphans even if not in state file."""
    from scripts import reconcile_sqlite_orphan_opens as rec
    orphans = rec.find_orphan_open_trades()
    tickers = {o["ticker"] for o in orphans}
    assert "CLOSED" not in tickers


def test_close_orphan_trade_dry_run_does_not_modify(tmp_atlas_with_trades, monkeypatch):
    """Dry run must not write."""
    from scripts import reconcile_sqlite_orphan_opens as rec
    # Force fallback path (no broker)
    monkeypatch.setattr(rec, "fetch_broker_exit_price", lambda t, q: (None, "no_broker"))

    ok, info = rec.close_orphan_trade(2, "ORPHAN", 5, 200.0, dry_run=True)
    assert ok
    assert "status" not in info  # not yet closed

    # Verify DB unchanged
    from db import atlas_db
    with atlas_db.get_db() as db:
        row = db.execute("SELECT status FROM trades WHERE ticker='ORPHAN'").fetchone()
    assert row["status"] == "open"


def test_close_orphan_trade_apply_marks_closed(tmp_atlas_with_trades, monkeypatch):
    """Apply mode marks closed with exit_reason='reconciled_orphan'."""
    from scripts import reconcile_sqlite_orphan_opens as rec
    monkeypatch.setattr(rec, "fetch_broker_exit_price", lambda t, q: (210.0, "broker_fill_exact_qty"))

    ok, info = rec.close_orphan_trade(2, "ORPHAN", 5, 200.0, dry_run=False)
    assert ok
    assert info["exit_price"] == 210.0
    assert info["pnl"] == 50.0  # (210-200)*5

    from db import atlas_db
    with atlas_db.get_db() as db:
        row = db.execute("SELECT status, exit_price, pnl, exit_reason "
                         "FROM trades WHERE ticker='ORPHAN'").fetchone()
    assert row["status"] == "closed"
    assert row["exit_price"] == 210.0
    assert row["pnl"] == 50.0
    assert row["exit_reason"] == "reconciled_orphan"


def test_run_clean_state_returns_zero(tmp_path, monkeypatch):
    """No orphans → exit 0."""
    from db import atlas_db
    db_path = tmp_path / "atlas.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("CREATE TABLE trades (id INTEGER PRIMARY KEY, ticker TEXT, status TEXT, strategy TEXT, universe TEXT, direction TEXT, entry_date TEXT, entry_price REAL, shares INTEGER, stop_price REAL, take_profit REAL, confidence REAL, regime_at_entry TEXT, exit_date TEXT, exit_price REAL, pnl REAL, pnl_pct REAL, exit_reason TEXT, config_version TEXT, updated_at TEXT);")
    conn.commit()
    conn.close()
    monkeypatch.setattr(atlas_db, "_db_path_override", str(db_path))

    from scripts import reconcile_sqlite_orphan_opens as rec
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    monkeypatch.setattr(rec, "STATE_DIR", state_dir)

    rc = rec.run(report_only=False)
    assert rc == 0


def test_run_report_only_does_not_close(tmp_atlas_with_trades, monkeypatch):
    """--report mode lists but does not close."""
    from scripts import reconcile_sqlite_orphan_opens as rec
    monkeypatch.setattr(rec, "fetch_broker_exit_price", lambda t, q: (210.0, "broker_fill"))

    rc = rec.run(report_only=True)
    assert rc == 0

    from db import atlas_db
    with atlas_db.get_db() as db:
        row = db.execute("SELECT status FROM trades WHERE ticker='ORPHAN'").fetchone()
    assert row["status"] == "open"  # unchanged
