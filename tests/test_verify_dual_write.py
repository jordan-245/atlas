"""
Tests for scripts/verify_dual_write.py

Covers the scope-refit fixes from task #192:
- Check 5 (check_equity): dedup broker equity_history by date + +1 tolerance
- Check 7 (check_equity_history): dedup json by date (last-write-wins) + ±$0.10 tolerance
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# ── Project root on path ─────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parent.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))


# ── Helpers to build lightweight in-memory test state ────────────────────────

def _make_broker_state(equity_history: list[dict]) -> dict:
    """Minimal live_sp500.json-like dict."""
    return {"equity_history": equity_history}


def _make_sqlite_db(
    *,
    equity_curve_rows: list[tuple],       # (date, equity)
    equity_history_rows: list[tuple],     # (date, equity)
    market_state_rows: list[tuple] | None = None,  # (market_id, halted, halt_reason, daily_high_water)
) -> sqlite3.Connection:
    """Return an in-memory SQLite connection with the tables used by the checks."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE equity_curve (
            market_id TEXT, date TEXT, equity REAL, cash REAL,
            PRIMARY KEY (market_id, date)
        )"""
    )
    conn.execute(
        """CREATE TABLE equity_history (
            market_id TEXT, date TEXT, equity REAL, pnl REAL,
            PRIMARY KEY (market_id, date)
        )"""
    )
    conn.execute(
        """CREATE TABLE market_state (
            market_id TEXT PRIMARY KEY,
            halted INTEGER, halt_reason TEXT, daily_high_water REAL
        )"""
    )
    for date, equity in equity_curve_rows:
        conn.execute(
            "INSERT INTO equity_curve VALUES ('sp500', ?, ?, NULL)", (date, equity)
        )
    for date, equity in equity_history_rows:
        conn.execute(
            "INSERT INTO equity_history VALUES ('sp500', ?, ?, NULL)", (date, equity)
        )
    if market_state_rows:
        for mkt, halted, reason, hwm in market_state_rows:
            conn.execute(
                "INSERT INTO market_state VALUES (?, ?, ?, ?)",
                (mkt, int(halted), reason, hwm),
            )
    conn.commit()
    return conn


# ── Import the functions under test ──────────────────────────────────────────
# We import the module-level functions directly but mock their DB and file I/O.

import importlib.util

_VDW_PATH = PROJECT / "scripts" / "verify_dual_write.py"

def _load_vdw():
    """Load verify_dual_write as a module (avoid __main__ guard)."""
    spec = importlib.util.spec_from_file_location("verify_dual_write", _VDW_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def vdw():
    return _load_vdw()


# ═══════════════════════════════════════════════════════════════════════════════
# Check 5 — Equity Curve
# ═══════════════════════════════════════════════════════════════════════════════

def _run_check_equity(vdw, broker_state: dict, conn: sqlite3.Connection) -> bool:
    """Run check_equity with a patched broker file and SQLite connection."""
    import contextlib

    @contextlib.contextmanager
    def _mock_get_db(*args, **kwargs):
        yield conn

    with (
        patch.object(vdw, "_load", return_value=(broker_state, None)),
        patch("db.atlas_db.get_db", _mock_get_db),
    ):
        return vdw.check_equity()


class TestCheckEquity:
    def test_check_equity_exact_match(self, vdw):
        """sqlite_count == deduped broker_count → PASS."""
        broker = _make_broker_state([
            {"date": "2026-05-12", "equity": 100.0},
            {"date": "2026-05-13", "equity": 110.0},
        ])
        conn = _make_sqlite_db(
            equity_curve_rows=[("2026-05-12", 100.0), ("2026-05-13", 110.0)],
            equity_history_rows=[],
        )
        assert _run_check_equity(vdw, broker, conn) is True

    def test_check_equity_tolerates_one_row_delta(self, vdw):
        """sqlite=37, broker=38 → PASS (latest entries match; one old historical row absent).

        The +1 tolerance covers the case where a historical row is missing from SQLite
        (e.g. early-deployment gap) but the live snapshot is fully in sync.
        """
        # Build 38 unique broker entries with proper ISO-like dates
        history = [
            {"date": f"2026-04-{i:02d}", "equity": float(1000 + i)}
            for i in range(1, 39)
        ]
        broker = _make_broker_state(history)
        # SQLite has 37 rows — missing the OLDEST (2026-04-01), but latest matches
        sqlite_rows = [(h["date"], h["equity"]) for h in history[1:]]  # skip first row
        conn = _make_sqlite_db(
            equity_curve_rows=sqlite_rows,
            equity_history_rows=[],
        )
        assert _run_check_equity(vdw, broker, conn) is True

    def test_check_equity_fails_two_rows_behind(self, vdw):
        """sqlite=36, broker=38 → FAIL (beyond the 1-row intraday tolerance)."""
        history = [{"date": f"2026-03-{i:02d}", "equity": float(100 + i)} for i in range(1, 39)]
        broker = _make_broker_state(history)
        # SQLite has only 36 rows
        sqlite_rows = [(h["date"], h["equity"]) for h in history[:36]]
        conn = _make_sqlite_db(
            equity_curve_rows=sqlite_rows,
            equity_history_rows=[],
        )
        assert _run_check_equity(vdw, broker, conn) is False

    def test_check_equity_dedupes_broker_dupes(self, vdw):
        """JSON has 2 entries for same date → counted as 1 unique date.
        sqlite=2, broker_raw=3 (but unique=2) → PASS.
        """
        broker = _make_broker_state([
            {"date": "2026-05-11", "equity": 1311.97},   # first write (duplicate)
            {"date": "2026-05-11", "equity": 1341.31},   # second write (duplicate)
            {"date": "2026-05-12", "equity": 1326.66},
        ])
        conn = _make_sqlite_db(
            equity_curve_rows=[("2026-05-11", 1341.31), ("2026-05-12", 1326.66)],
            equity_history_rows=[],
        )
        result = _run_check_equity(vdw, broker, conn)
        assert result is True, (
            "check_equity should dedupe broker entries by date so unique=2 ≤ sqlite=2"
        )

    def test_check_equity_three_dupes_same_date(self, vdw):
        """JSON has 3 entries for same date → counted as 1 unique date; PASS when sqlite=1."""
        broker = _make_broker_state([
            {"date": "2026-05-11", "equity": 1311.97},
            {"date": "2026-05-11", "equity": 1311.97},
            {"date": "2026-05-11", "equity": 1341.31},
        ])
        conn = _make_sqlite_db(
            equity_curve_rows=[("2026-05-11", 1341.31)],
            equity_history_rows=[],
        )
        result = _run_check_equity(vdw, broker, conn)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════════
# Check 7 — Equity History
# ═══════════════════════════════════════════════════════════════════════════════

def _run_check_equity_history(vdw, state_files_data: dict[str, dict], conn: sqlite3.Connection) -> bool:
    """Run check_equity_history with patched state files and DB."""
    import contextlib

    @contextlib.contextmanager
    def _mock_get_db(*args, **kwargs):
        yield conn

    def _mock_load(path):
        key = str(path)
        for stem, data in state_files_data.items():
            if stem in key:
                return data, None
        return {}, f"file not found: {path}"

    def _mock_is_live_market(market_id: str) -> bool:
        return market_id in state_files_data

    # Create mock Path objects for state files
    mock_paths = []
    for stem in state_files_data:
        p = MagicMock()
        p.stem = f"live_{stem}"
        p.__str__ = lambda self, s=stem: f"/tmp/live_{s}.json"
        mock_paths.append(p)

    with (
        patch.object(vdw, "BROKER_STATE_DIR", MagicMock(**{"glob.return_value": mock_paths})),
        patch.object(vdw, "_load", side_effect=_mock_load),
        patch.object(vdw, "_is_live_market", side_effect=_mock_is_live_market),
        patch("db.atlas_db.get_db", _mock_get_db),
    ):
        return vdw.check_equity_history(N=7)


class TestCheckEquityHistory:
    def test_check_equity_history_exact_match(self, vdw):
        """All entries match exactly → PASS."""
        history = [{"date": f"2026-05-{i:02d}", "equity": float(1300 + i)} for i in range(7, 14)]
        conn = _make_sqlite_db(
            equity_curve_rows=[],
            equity_history_rows=[(h["date"], h["equity"]) for h in history],
        )
        assert _run_check_equity_history(vdw, {"sp500": {"equity_history": history}}, conn) is True

    def test_check_equity_history_dedupes_json_dupes(self, vdw):
        """JSON has 2 entries for same date: dedupe keeps LAST, compare to SQLite last-write."""
        history = [
            {"date": "2026-05-11", "equity": 1311.97},   # first (stale)
            {"date": "2026-05-11", "equity": 1341.31},   # last (correct)
            {"date": "2026-05-12", "equity": 1326.66},
            {"date": "2026-05-13", "equity": 1316.82},
        ]
        # SQLite has the LAST value (INSERT OR REPLACE)
        conn = _make_sqlite_db(
            equity_curve_rows=[],
            equity_history_rows=[
                ("2026-05-11", 1341.31),
                ("2026-05-12", 1326.66),
                ("2026-05-13", 1316.82),
            ],
        )
        result = _run_check_equity_history(
            vdw, {"sp500": {"equity_history": history}}, conn
        )
        assert result is True, (
            "check_equity_history should dedupe json by date (keep last) so "
            "2026-05-11 compares 1341.31 vs 1341.31"
        )

    def test_check_equity_history_tolerates_10c_drift(self, vdw):
        """Equity values within ±$0.10 should pass (intraday vs EOD rounding drift)."""
        history = [
            {"date": "2026-05-13", "equity": 1316.82},
            {"date": "2026-05-12", "equity": 1326.66},
        ]
        # SQLite has values within 10 cents
        conn = _make_sqlite_db(
            equity_curve_rows=[],
            equity_history_rows=[
                ("2026-05-13", 1316.77),  # +$0.05 drift
                ("2026-05-12", 1326.73),  # +$0.07 drift
            ],
        )
        result = _run_check_equity_history(
            vdw, {"sp500": {"equity_history": history}}, conn
        )
        assert result is True, "Drift within ±$0.10 should be tolerated"

    def test_check_equity_history_fails_large_mismatch(self, vdw):
        """Equity values more than ±$0.10 apart → FAIL."""
        history = [{"date": "2026-05-13", "equity": 1316.82}]
        conn = _make_sqlite_db(
            equity_curve_rows=[],
            equity_history_rows=[("2026-05-13", 1317.00)],  # $0.18 drift
        )
        result = _run_check_equity_history(
            vdw, {"sp500": {"equity_history": history}}, conn
        )
        assert result is False, "Drift > $0.10 should FAIL"

    def test_check_equity_history_three_dupes_last_wins(self, vdw):
        """JSON has 3 entries for same date; dedup keeps the very last (1341.31)."""
        history = [
            {"date": "2026-05-11", "equity": 1311.97},
            {"date": "2026-05-11", "equity": 1311.97},  # same value dup
            {"date": "2026-05-11", "equity": 1341.31},  # last / latest value
        ]
        conn = _make_sqlite_db(
            equity_curve_rows=[],
            equity_history_rows=[("2026-05-11", 1341.31)],
        )
        result = _run_check_equity_history(
            vdw, {"sp500": {"equity_history": history}}, conn
        )
        assert result is True
