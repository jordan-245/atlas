"""Wave D1 cutover tests — verify JSON writes are retired, SQLite is sole writer.

TS (migration timestamp): 20260427_160601
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ATLAS_ROOT = Path(__file__).resolve().parent.parent
_HEALTHZ_PATH = (
    ATLAS_ROOT
    / "pi-package"
    / "atlas-ops"
    / "skills"
    / "atlas-healthz"
    / "scripts"
    / "healthz.py"
)


def _load_healthz():
    """Import healthz.py from its non-importable (hyphenated) directory path."""
    spec = importlib.util.spec_from_file_location("healthz", _HEALTHZ_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_signal(
    ticker="AAPL",
    strategy="momentum_breakout",
    entry_price=150.0,
    stop_price=140.0,
    take_profit=165.0,
    position_size=10,
    confidence=0.75,
    rationale="test signal",
    direction="long",
    market_id="sp500",
):
    """Create a minimal Signal-like object for DecisionJournal.record_signal."""
    sig = types.SimpleNamespace(
        ticker=ticker,
        strategy=strategy,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit=take_profit,
        position_size=position_size,
        confidence=confidence,
        rationale=rationale,
        direction=direction,
        features={"rsi": 55.0},
        sector="Technology",
        market_id=market_id,
    )
    return sig


def _make_fill(
    ticker="TSLA",
    strategy="momentum_breakout",
    market_id="sp500",
    fill_price=200.0,
    shares=5,
    stop_price=185.0,
    confidence=0.70,
):
    return {
        "ticker": ticker,
        "strategy": strategy,
        "market_id": market_id,
        "fill_price": fill_price,
        "shares": shares,
        "stop_price": stop_price,
        "confidence": confidence,
        "universe": "sp500",
    }


# ---------------------------------------------------------------------------
# Test 1 — DecisionJournal._save() is a no-op (no JSON written)
# ---------------------------------------------------------------------------


def test_decision_journal_save_no_json_write(tmp_path, monkeypatch):
    """DecisionJournal.record_signal must NOT produce a JSON file after Wave D1."""
    import journal.logger as jl

    # Redirect JOURNAL_DIR so any accidental write goes to tmp_path, not prod
    monkeypatch.setattr(jl, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(jl.DecisionJournal, "FILE", tmp_path / "decision_journal.json")

    dj = jl.DecisionJournal()
    sig = _make_signal()
    dj.record_signal(sig, action="accepted", reason="test", market_id="sp500")

    # No JSON file should appear in tmp_path
    assert not (tmp_path / "decision_journal.json").exists(), (
        "DecisionJournal._save() must not write a JSON file after Wave D1"
    )

    # SQLite signals table should have the row
    import db.atlas_db as _adb
    import sqlite3

    with sqlite3.connect(_adb._db_path_override) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE ticker = ?", ("AAPL",)
        ).fetchone()[0]
    assert count >= 1, "Signal row should exist in SQLite signals table"


# ---------------------------------------------------------------------------
# Test 2 — TradeLedger._save() is a no-op (no JSON written)
# ---------------------------------------------------------------------------


def test_trade_ledger_save_no_json_write(tmp_path, monkeypatch):
    """TradeLedger.record_entry must NOT produce a JSON file after Wave D1."""
    import journal.logger as jl

    monkeypatch.setattr(jl, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(jl.TradeLedger, "FILE", tmp_path / "trade_ledger.json")

    tl = jl.TradeLedger()
    fill = _make_fill(ticker="MSFT", strategy="trend_following")
    tl.record_entry(fill)

    # No JSON file
    assert not (tmp_path / "trade_ledger.json").exists(), (
        "TradeLedger._save() must not write a JSON file after Wave D1"
    )

    # SQLite trades table should have the row
    import db.atlas_db as _adb
    import sqlite3

    with sqlite3.connect(_adb._db_path_override) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker = ?", ("MSFT",)
        ).fetchone()[0]
    assert count >= 1, "Trade entry row should exist in SQLite trades table"


# ---------------------------------------------------------------------------
# Test 3 — healthz check_logging reads signals from SQLite
# ---------------------------------------------------------------------------


def test_healthz_signals_query_shape(tmp_path):
    """healthz.check_logging must return decision_journal check from SQLite."""
    import db.atlas_db as _adb
    from db.atlas_db import record_signal

    # Insert 3 known signals into the isolated DB
    for i in range(3):
        record_signal(
            timestamp=f"2026-04-28T01:0{i}:00",
            ticker=f"TICK{i}",
            strategy="momentum_breakout",
            universe="sp500",
            entry_price=100.0 + i,
            stop_price=90.0,
            position_size=10,
            position_value=1000.0,
            risk_amount=100.0,
            confidence=0.7,
            action="accepted",
            market_id="sp500",
        )

    # Build a fake project structure pointing check_logging at our isolated DB
    fake_project = tmp_path / "project"
    (fake_project / "data").mkdir(parents=True)
    # Symlink atlas.db → isolated test DB
    (fake_project / "data" / "atlas.db").symlink_to(_adb._db_path_override)

    healthz = _load_healthz()
    results = healthz.check_logging(fake_project)

    # Find the decision_journal entry
    dj_result = next((r for r in results if r["check"] == "decision_journal"), None)
    assert dj_result is not None, "check_logging must return a 'decision_journal' result"
    assert dj_result["verdict"] == "ok", f"verdict should be ok, got: {dj_result}"
    assert "signal entries (SQLite)" in dj_result["message"], (
        f"message should include '(SQLite)': {dj_result['message']}"
    )
    # Extract count from message "3 signal entries (SQLite)"
    count_str = dj_result["message"].split(" ")[0]
    assert int(count_str) >= 3, f"Expected at least 3 signals; got: {dj_result['message']}"


# ---------------------------------------------------------------------------
# Test 4 — execution.py has no TRADE_LEDGER constant and no _read_json helper
# ---------------------------------------------------------------------------


def test_execution_py_no_json_fallback_helpers():
    """research.brain.execution must NOT have TRADE_LEDGER or _read_json after Wave D1."""
    import research.brain.execution as execution

    assert not hasattr(execution, "TRADE_LEDGER"), (
        "TRADE_LEDGER constant must be removed from execution.py (Wave D1)"
    )
    assert not hasattr(execution, "_read_json"), (
        "_read_json helper must be removed from execution.py (Wave D1)"
    )


# ---------------------------------------------------------------------------
# Test 5 — End-to-end: signal + entry + exit; zero JSON files in JOURNAL_DIR
# ---------------------------------------------------------------------------


def test_end_to_end_decision_then_trade_no_json(tmp_path, monkeypatch):
    """Full signal→entry→exit cycle must leave NO JSON files in JOURNAL_DIR."""
    import journal.logger as jl

    monkeypatch.setattr(jl, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(jl.DecisionJournal, "FILE", tmp_path / "decision_journal.json")
    monkeypatch.setattr(jl.TradeLedger, "FILE", tmp_path / "trade_ledger.json")

    dj = jl.DecisionJournal()
    tl = jl.TradeLedger()

    sig = _make_signal(ticker="NVDA", strategy="momentum_breakout")
    dj.record_signal(sig, action="accepted", market_id="sp500")

    fill = _make_fill(ticker="NVDA", strategy="momentum_breakout", fill_price=500.0)
    tl.record_entry(fill)

    tl.record_exit({
        "ticker": "NVDA",
        "strategy": "momentum_breakout",
        "fill_price": 520.0,
        "exit_reason": "take_profit",
        "pnl": 100.0,
        "market_id": "sp500",
    })

    # Assert ZERO JSON files in tmp_path (the monkeypatched JOURNAL_DIR)
    json_files = list(tmp_path.glob("*.json"))
    assert json_files == [], (
        f"No JSON files should exist in JOURNAL_DIR after Wave D1; found: {json_files}"
    )

    # Assert SQLite has all three (signal + open→closed trade)
    import db.atlas_db as _adb
    import sqlite3

    with sqlite3.connect(_adb._db_path_override) as conn:
        sig_count = conn.execute(
            "SELECT COUNT(*) FROM signals WHERE ticker='NVDA'"
        ).fetchone()[0]
        trade_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker='NVDA'"
        ).fetchone()[0]

    assert sig_count >= 1, "Signal row missing from SQLite"
    assert trade_count >= 1, "Trade row missing from SQLite"


# ---------------------------------------------------------------------------
# Test 6 — Idempotent replay: second record_entry does not create duplicate open row
# ---------------------------------------------------------------------------


def test_idempotent_replay_no_double_write(tmp_path, monkeypatch):
    """Calling record_entry twice for same ticker/universe must not create two open rows."""
    import journal.logger as jl

    monkeypatch.setattr(jl, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(jl.TradeLedger, "FILE", tmp_path / "trade_ledger.json")

    tl = jl.TradeLedger()
    fill = _make_fill(ticker="AMD", strategy="momentum_breakout")

    # First insert — should succeed
    result1 = tl.record_entry(fill)

    # Second insert — UNIQUE partial index should block it; record_trade_entry returns None
    result2 = tl.record_entry(fill)

    import db.atlas_db as _adb
    import sqlite3

    with sqlite3.connect(_adb._db_path_override) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker='AMD' AND status='open'"
        ).fetchone()[0]

    # Exactly one open row regardless of duplicate call
    assert count == 1, (
        f"Expected exactly 1 open AMD trade; got {count}. "
        "The UNIQUE partial index must block duplicates."
    )
    # Second call returns None (IntegrityError caught internally)
    assert result2 is None, (
        f"record_trade_entry should return None on duplicate; got {result2!r}"
    )


# ---------------------------------------------------------------------------
# Test 7 — Rollback script passes bash -n syntax check
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="scripts/dual_write_d1_rollback.sh moved to _attic/2026-05/ on 2026-05-12 per docs/cleanup-plan-2026-05.md")
def test_rollback_script_bash_syntax_valid():
    """scripts/dual_write_d1_rollback.sh must pass bash -n (syntax only, no exec)."""
    rollback = ATLAS_ROOT / "scripts" / "dual_write_d1_rollback.sh"
    assert rollback.exists(), f"Rollback script not found at {rollback}"

    result = subprocess.run(
        ["bash", "-n", str(rollback)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"bash -n failed on rollback script:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
