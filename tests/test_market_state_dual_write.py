"""Tests for Wave D2 Phase 1-3: market_state + equity_history dual-write.

Uses a temporary SQLite DB via init_db(path) so the production atlas.db is
never touched.  All JSON state files are written to tmp_path — no production
brokers/state/ directory is read or modified.
"""

from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure project root is importable
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _load_migration_module():
    """Load the migration script via importlib (filename starts with a date)."""
    mig_path = PROJECT / "scripts" / "migrations" / "2026-04-28-add-market-state.py"
    spec = importlib.util.spec_from_file_location("migration_add_market_state", mig_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_state_json(
    tmp_path: Path,
    market_id: str = "sp500",
    halted: bool = False,
    halt_reason: str | None = None,
    daily_high_water: float = 5000.0,
    equity_history: list | None = None,
) -> Path:
    """Write a mock live_{market_id}.json and return its path."""
    if equity_history is None:
        equity_history = [{"date": "2026-04-28", "equity": daily_high_water, "cash": 1000.0}]
    state = {
        "market_id": market_id,
        "mode": "live",
        "positions": [],
        "closed_trades": [],
        "equity_history": equity_history,
        "daily_high_water": daily_high_water,
        "halted": halted,
        "halt_reason": halt_reason,
        "last_saved": "2026-04-28T00:00:00",
    }
    path = tmp_path / f"live_{market_id}.json"
    path.write_text(json.dumps(state, indent=2))
    return path


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Isolated tmp DB with full Atlas schema including new tables."""
    import db.atlas_db as atlas_db

    db_file = str(tmp_path / "test_atlas.db")
    # Set override before init so get_db() uses the tmp file
    monkeypatch.setattr(atlas_db, "_db_path_override", db_file)
    # Also clear WAL cache for clean start
    atlas_db._wal_initialized_paths.discard(db_file)
    atlas_db.init_db(db_file)
    yield db_file


@pytest.fixture()
def live_portfolio(tmp_path, tmp_db):
    """LivePortfolio instance wired to tmp state dir and tmp DB."""
    from brokers.live_portfolio import LivePortfolio

    config = {
        "risk": {
            "starting_equity": 10000,
            "max_risk_per_trade_pct": 0.01,
            "max_open_positions": 8,
            "max_sector_concentration": 2,
            "max_daily_drawdown_pct": 0.02,
            "leverage": 1.0,
        },
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
        "dual_write_market_state": True,
    }
    lp = LivePortfolio(config, market_id="test_market")
    lp.broker_data_valid = True
    lp.daily_high_water = 10000.0
    lp.equity_history = [{"date": "2026-04-28", "equity": 10000.0, "cash": 5000.0}]
    # Point state writes to tmp_path (safe from production)
    lp._state_path = lambda: tmp_path / f"live_{lp.market_id}.json"
    return lp


# ═════════════════════════════════════════════════════════════════════════════
# Test 1 — migration creates both tables
# ═════════════════════════════════════════════════════════════════════════════

def test_migration_creates_tables(tmp_path, monkeypatch):
    """Migration must create market_state and equity_history tables."""
    import db.atlas_db as atlas_db

    db_file = str(tmp_path / "mig_test.db")
    monkeypatch.setattr(atlas_db, "_db_path_override", db_file)
    atlas_db._wal_initialized_paths.discard(db_file)
    atlas_db.init_db(db_file)  # creates all existing tables

    mod = _load_migration_module()
    rc = mod.run(db_path=Path(db_file), state_dir=tmp_path)
    assert rc == 0

    conn = sqlite3.connect(db_file)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    assert "market_state" in tables, "market_state table not created"
    assert "equity_history" in tables, "equity_history table not created"


# ═════════════════════════════════════════════════════════════════════════════
# Test 2 — migration backfills all markets
# ═════════════════════════════════════════════════════════════════════════════

def test_migration_backfills_all_markets(tmp_path, monkeypatch):
    """Migration backfills market_state + equity_history for all JSON files."""
    import db.atlas_db as atlas_db

    db_file = str(tmp_path / "backfill_test.db")
    monkeypatch.setattr(atlas_db, "_db_path_override", db_file)
    atlas_db._wal_initialized_paths.discard(db_file)
    atlas_db.init_db(db_file)

    # Create mock JSON for 3 markets with differing history lengths
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    markets = {
        "sp500":          (True, "Drawdown exceeded",  10, 5000.0),
        "commodity_etfs": (False, None,                 5, 4800.0),
        "sector_etfs":    (False, None,                 2, 3000.0),
    }
    for mid, (halted, reason, n_eq, hwm) in markets.items():
        eq_hist = [
            {"date": f"2026-04-{i+1:02d}", "equity": hwm - i * 10.0}
            for i in range(n_eq)
        ]
        _make_state_json(
            state_dir, mid, halted=halted, halt_reason=reason,
            daily_high_water=hwm, equity_history=eq_hist,
        )

    mod = _load_migration_module()
    rc = mod.run(db_path=Path(db_file), state_dir=state_dir)
    assert rc == 0

    conn = sqlite3.connect(db_file)
    conn.row_factory = sqlite3.Row

    for mid, (halted, reason, n_eq, hwm) in markets.items():
        row = conn.execute(
            "SELECT halted, halt_reason, daily_high_water FROM market_state WHERE market_id=?",
            (mid,),
        ).fetchone()
        assert row is not None, f"market_state row missing for {mid}"
        assert bool(row["halted"]) == halted
        assert row["halt_reason"] == reason

        eq_count = conn.execute(
            "SELECT COUNT(*) FROM equity_history WHERE market_id=?", (mid,)
        ).fetchone()[0]
        assert eq_count == n_eq, f"{mid}: expected {n_eq} equity rows, got {eq_count}"

    conn.close()


# ═════════════════════════════════════════════════════════════════════════════
# Test 3 — migration handles missing keys gracefully
# ═════════════════════════════════════════════════════════════════════════════

def test_migration_handles_missing_keys(tmp_path, monkeypatch):
    """JSON missing daily_high_water or equity_history must not crash."""
    import db.atlas_db as atlas_db

    db_file = str(tmp_path / "missing_keys.db")
    monkeypatch.setattr(atlas_db, "_db_path_override", db_file)
    atlas_db._wal_initialized_paths.discard(db_file)
    atlas_db.init_db(db_file)

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Minimal JSON — missing daily_high_water and equity_history
    minimal = {"market_id": "sp500", "halted": False, "mode": "live"}
    (state_dir / "live_sp500.json").write_text(json.dumps(minimal))

    mod = _load_migration_module()
    rc = mod.run(db_path=Path(db_file), state_dir=state_dir)
    assert rc == 0  # must not crash

    conn = sqlite3.connect(db_file)
    row = conn.execute(
        "SELECT market_id, halted, daily_high_water FROM market_state WHERE market_id='sp500'"
    ).fetchone()
    conn.close()

    assert row is not None, "market_state row should exist even with minimal JSON"
    assert row[1] == 0   # halted=False
    assert row[2] is None  # daily_high_water=NULL


# ═════════════════════════════════════════════════════════════════════════════
# Test 4 — save_state writes both JSON and SQLite
# ═════════════════════════════════════════════════════════════════════════════

def test_save_state_writes_both_json_and_sqlite(tmp_path, live_portfolio, tmp_db):
    """save_state() must produce a JSON file AND a market_state SQLite row."""
    lp = live_portfolio
    lp.halted = False
    lp.daily_high_water = 10500.0

    lp.save_state()

    # JSON must exist
    json_path = tmp_path / f"live_{lp.market_id}.json"
    assert json_path.exists(), "JSON state file not written"
    with open(json_path) as f:
        saved = json.load(f)
    assert saved["daily_high_water"] == 10500.0

    # SQLite row must exist
    import db.atlas_db as atlas_db
    with atlas_db.get_db() as db:
        row = db.execute(
            "SELECT halted, daily_high_water FROM market_state WHERE market_id=?",
            (lp.market_id,),
        ).fetchone()

    assert row is not None, "market_state row not created in SQLite"
    assert row["daily_high_water"] == pytest.approx(10500.0, abs=0.01)
    assert row["halted"] == 0


# ═════════════════════════════════════════════════════════════════════════════
# Test 5 — SQLite failure does NOT break JSON write
# ═════════════════════════════════════════════════════════════════════════════

def test_save_state_sqlite_failure_does_not_break_json(tmp_path, live_portfolio, tmp_db, caplog):
    """If SQLite dual-write fails, JSON must still be written."""
    import logging
    lp = live_portfolio

    with patch("db.atlas_db.get_db", side_effect=Exception("db exploded")):
        with caplog.at_level(logging.ERROR, logger="atlas.live_portfolio"):
            lp.save_state()

    # JSON MUST be written
    json_path = tmp_path / f"live_{lp.market_id}.json"
    assert json_path.exists(), "JSON file must be written even when SQLite fails"

    # Error must be logged (not raised)
    assert any("dual_write_market_state FAILED" in r.message for r in caplog.records), \
        "Expected dual_write failure to be logged as ERROR"


# ═════════════════════════════════════════════════════════════════════════════
# Test 6 — equity_history row appears in SQLite
# ═════════════════════════════════════════════════════════════════════════════

def test_equity_history_row_appears(tmp_path, live_portfolio, tmp_db):
    """save_state() with equity_history must insert a row into equity_history table."""
    lp = live_portfolio
    lp.equity_history = [{"date": "2026-04-28", "equity": 99999.0, "pnl": 500.0}]

    lp.save_state()

    import db.atlas_db as atlas_db
    with atlas_db.get_db() as db:
        row = db.execute(
            "SELECT equity, pnl FROM equity_history WHERE market_id=? AND date=?",
            (lp.market_id, "2026-04-28"),
        ).fetchone()

    assert row is not None, "equity_history row not found"
    assert row["equity"] == pytest.approx(99999.0, abs=0.01)
    assert row["pnl"] == pytest.approx(500.0, abs=0.01)


# ═════════════════════════════════════════════════════════════════════════════
# Test 7 — check_market_state detects mismatch
# ═════════════════════════════════════════════════════════════════════════════

def test_check_market_state_detects_mismatch(tmp_path, tmp_db, monkeypatch):
    """check_market_state() must return False when JSON and SQLite disagree."""
    import scripts.verify_dual_write as vdw
    import db.atlas_db as atlas_db

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # JSON says halted=True
    _make_state_json(
        state_dir, "sp500",
        halted=True, halt_reason="test halt",
        daily_high_water=5000.0,
    )

    # SQLite says halted=False (mismatch!)
    with atlas_db.get_db() as db:
        db.execute(
            """INSERT INTO market_state
                   (market_id, halted, halt_reason, mode, daily_high_water, updated_at)
               VALUES ('sp500', 0, NULL, 'live', 5000.0, datetime('now'))"""
        )

    monkeypatch.setattr(vdw, "BROKER_STATE_DIR", state_dir)
    result = vdw.check_market_state()
    assert result is False, "check_market_state should FAIL on halted mismatch"


# ═════════════════════════════════════════════════════════════════════════════
# Test 8 — check_market_state passes on equal data
# ═════════════════════════════════════════════════════════════════════════════

def test_check_market_state_passes_on_equal_data(tmp_path, tmp_db, monkeypatch):
    """check_market_state() must return True when JSON and SQLite agree."""
    import scripts.verify_dual_write as vdw
    import db.atlas_db as atlas_db

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    _make_state_json(
        state_dir, "sp500",
        halted=True, halt_reason="Daily drawdown 2.00%",
        daily_high_water=5429.05,
    )

    with atlas_db.get_db() as db:
        db.execute(
            """INSERT INTO market_state
                   (market_id, halted, halt_reason, halted_at, mode,
                    daily_high_water, updated_at)
               VALUES ('sp500', 1, 'Daily drawdown 2.00%', '2026-04-28T00:00:00',
                       'live', 5429.05, datetime('now'))"""
        )

    monkeypatch.setattr(vdw, "BROKER_STATE_DIR", state_dir)
    result = vdw.check_market_state()
    assert result is True, "check_market_state should PASS when data agrees"


# ═════════════════════════════════════════════════════════════════════════════
# Test 9 — check_equity_history passes on matching rows
# ═════════════════════════════════════════════════════════════════════════════

def test_check_equity_history_passes_on_matching_rows(tmp_path, tmp_db, monkeypatch):
    """check_equity_history() must return True when last-N rows match."""
    import scripts.verify_dual_write as vdw
    import db.atlas_db as atlas_db

    state_dir = tmp_path / "state"
    state_dir.mkdir()

    eq_hist = [
        {"date": f"2026-04-{i+21:02d}", "equity": 5000.0 + i * 50.0}
        for i in range(7)
    ]
    _make_state_json(state_dir, "sp500", equity_history=eq_hist)

    with atlas_db.get_db() as db:
        for entry in eq_hist:
            db.execute(
                "INSERT OR IGNORE INTO equity_history (market_id, date, equity) VALUES (?, ?, ?)",
                ("sp500", entry["date"], entry["equity"]),
            )

    monkeypatch.setattr(vdw, "BROKER_STATE_DIR", state_dir)
    result = vdw.check_equity_history(N=7)
    assert result is True, "check_equity_history should PASS when rows match"


# ═════════════════════════════════════════════════════════════════════════════
# Test 10 — dual_write_market_state=False only writes JSON
# ═════════════════════════════════════════════════════════════════════════════

def test_dual_write_flag_off_only_writes_json(tmp_path, tmp_db, monkeypatch):
    """When dual_write_market_state=False, only JSON is written, SQLite unchanged."""
    from brokers.live_portfolio import LivePortfolio
    import db.atlas_db as atlas_db

    config = {
        "risk": {
            "starting_equity": 5000,
            "max_risk_per_trade_pct": 0.01,
            "max_open_positions": 8,
            "max_sector_concentration": 2,
            "max_daily_drawdown_pct": 0.02,
            "leverage": 1.0,
        },
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
        "dual_write_market_state": False,  # flag OFF
    }
    lp = LivePortfolio(config, market_id="flag_off_market")
    lp.broker_data_valid = True
    lp.daily_high_water = 5000.0
    lp.equity_history = [{"date": "2026-04-28", "equity": 5000.0}]
    lp._state_path = lambda: tmp_path / f"live_{lp.market_id}.json"

    lp.save_state()

    # JSON must exist
    json_path = tmp_path / f"live_{lp.market_id}.json"
    assert json_path.exists(), "JSON file must still be written"

    # SQLite must NOT have a row for this market
    with atlas_db.get_db() as db:
        row = db.execute(
            "SELECT market_id FROM market_state WHERE market_id=?",
            (lp.market_id,),
        ).fetchone()
    assert row is None, "market_state must NOT be written when flag is False"
