"""Regression tests for backfill_orphan_trades.py universe derivation.

FIX-TRADE-UNIV-001: backfill must use derive_universe(ticker) not the
broker state-file's market_id.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """Create a tmp atlas.db with the trades table schema."""
    db_path = tmp_path / "atlas.db"
    conn = sqlite3.connect(str(db_path))
    # Read minimal schema needed
    conn.executescript("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            universe TEXT,
            direction TEXT,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            shares INTEGER NOT NULL,
            stop_price REAL,
            take_profit REAL,
            confidence REAL,
            regime_at_entry TEXT,
            status TEXT DEFAULT 'open',
            config_version TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()

    # Override DB path
    from db import atlas_db
    monkeypatch.setattr(atlas_db, "_db_path_override", str(db_path))
    return db_path


def test_backfill_uses_canonical_universe_not_state_file(tmp_db, tmp_path, monkeypatch):
    """When FCX is in live_sp500.json (cross-market ghost), backfill must
    write universe=commodity_etfs (canonical), NOT sp500 (state-file)."""
    # Set up state files: FCX in WRONG file (sp500), to simulate the historical bug
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "live_sp500.json").write_text(json.dumps({
        "positions": [{"ticker": "FCX", "strategy": "connors_rsi2",
                       "entry_price": 30.0, "shares": 100,
                       "entry_date": "2026-04-29"}]
    }))

    # Patch BROKER_STATE_DIR
    from scripts import backfill_orphan_trades as bf
    monkeypatch.setattr(bf, "BROKER_STATE_DIR", state_dir)
    # No plans dir needed — strategy="connors_rsi2" is non-poison, uses broker path

    rc = bf.run(dry_run=False, quiet=True)
    assert rc == 0

    # Verify trade was inserted with canonical universe
    from db import atlas_db
    with atlas_db.get_db() as db:
        rows = list(db.execute("SELECT ticker, strategy, universe FROM trades"))
    assert len(rows) == 1
    assert rows[0]["ticker"] == "FCX"
    assert rows[0]["strategy"] == "connors_rsi2"
    assert rows[0]["universe"] == "commodity_etfs", \
        f"Expected canonical universe commodity_etfs, got {rows[0]['universe']}"


def test_backfill_correct_state_file_uses_canonical(tmp_db, tmp_path, monkeypatch):
    """When FCX is in live_commodity_etfs.json (correct file), still get commodity_etfs."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "live_commodity_etfs.json").write_text(json.dumps({
        "positions": [{"ticker": "FCX", "strategy": "connors_rsi2",
                       "entry_price": 30.0, "shares": 100,
                       "entry_date": "2026-04-29"}]
    }))

    from scripts import backfill_orphan_trades as bf
    monkeypatch.setattr(bf, "BROKER_STATE_DIR", state_dir)

    rc = bf.run(dry_run=False, quiet=True)
    assert rc == 0

    from db import atlas_db
    with atlas_db.get_db() as db:
        rows = list(db.execute("SELECT universe FROM trades"))
    assert rows[0]["universe"] == "commodity_etfs"


def test_backfill_unresolvable_ticker_logs_and_skips(tmp_db, tmp_path, monkeypatch, caplog):
    """If derive_universe returns None, skip the INSERT and log a warning."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Made-up ticker not in any universe definition
    (state_dir / "live_sp500.json").write_text(json.dumps({
        "positions": [{"ticker": "ZZZNOTREAL", "strategy": "momentum_breakout",
                       "entry_price": 30.0, "shares": 100,
                       "entry_date": "2026-04-29"}]
    }))

    from scripts import backfill_orphan_trades as bf
    monkeypatch.setattr(bf, "BROKER_STATE_DIR", state_dir)

    caplog.set_level(logging.WARNING)
    rc = bf.run(dry_run=False, quiet=True)
    # rc may be 1 because failures incremented; that's OK — point is no row inserted
    from db import atlas_db
    with atlas_db.get_db() as db:
        rows = list(db.execute("SELECT * FROM trades"))
    assert len(rows) == 0
    # Should have logged warning about derivation
    assert any(
        "derive universe" in r.getMessage()
        or "membership" in r.getMessage()
        or "universe" in r.getMessage()
        for r in caplog.records
    )
