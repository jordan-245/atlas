"""Tests for scripts/check_signal_writes.py — signal-write divergence watchdog.

All tests are fully offline: no real plan files, no real SQLite DB, no
Telegram.  We use tmp_path for file I/O and unittest.mock for alert patching.

Run with:  python -m pytest tests/test_signal_write_monitor.py -v
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path bootstrap ──────────────────────────────────────────────────────────
_ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

from scripts.check_signal_writes import check_signal_writes, main  # noqa: E402


# ── Helpers ─────────────────────────────────────────────────────────────────

CHECK_DATE = datetime.date(2026, 4, 14)
CHECK_DATE_STR = "2026-04-14"
UNIVERSE = "sp500"


def _write_plan(plans_dir: Path, universe: str, date_str: str, n_proposed: int) -> None:
    """Write a minimal plan JSON with ``n_proposed`` proposed_entries."""
    entries = [
        {
            "ticker": f"TICK{i}",
            "strategy": "mean_reversion",
            "entry_price": 100.0 + i,
            "stop_price": 95.0,
            "take_profit": 110.0,
            "position_size": 1,
            "position_value": 100.0,
            "risk_amount": 5.0,
            "confidence": 0.75,
            "rationale": "test",
            "features": {},
            "sector": "Technology",
            "market_id": universe,
        }
        for i in range(n_proposed)
    ]
    plan = {
        "trade_date": date_str,
        "generated_at": f"{date_str}T18:00:00",
        "market_id": universe,
        "config_version": "test",
        "status": "approved",
        "proposed_entries": entries,
        "rejected_entries": [],
        "total_signals_generated": n_proposed,
    }
    fname = plans_dir / f"plan_{universe}_{date_str}.json"
    fname.write_text(json.dumps(plan))


def _make_db(db_path: Path, universe: str, date_str: str, n_signals: int) -> None:
    """Create a minimal SQLite DB with ``n_signals`` proposed-action rows."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE signals (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            ticker    TEXT NOT NULL,
            strategy  TEXT NOT NULL,
            universe  TEXT NOT NULL,
            direction TEXT DEFAULT 'long',
            entry_price   REAL NOT NULL,
            stop_price    REAL NOT NULL,
            take_profit   REAL,
            position_size INTEGER NOT NULL,
            position_value REAL NOT NULL,
            risk_amount   REAL NOT NULL,
            confidence    REAL NOT NULL,
            rationale TEXT,
            features  TEXT,
            sector    TEXT,
            regime_state TEXT,
            action    TEXT NOT NULL,
            action_reason TEXT,
            config_version TEXT,
            market_id TEXT
        )
        """
    )
    for i in range(n_signals):
        cur.execute(
            """
            INSERT INTO signals
                (timestamp, ticker, strategy, universe, entry_price, stop_price,
                 position_size, position_value, risk_amount, confidence, action, market_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'proposed', ?)
            """,
            (
                f"{date_str}T18:00:{i:02d}",
                f"TICK{i}",
                "mean_reversion",
                universe,
                100.0 + i,
                95.0,
                1,
                100.0,
                5.0,
                0.75,
                universe,
            ),
        )
    conn.commit()
    conn.close()


# ── Tests: check_signal_writes() ────────────────────────────────────────────

class TestCheckSignalWrites:
    """Unit-level tests for the core divergence-detection function."""

    def test_no_plan_files_returns_empty(self, tmp_path):
        """No plan JSON → nothing to check → no divergences."""
        db_path = tmp_path / "atlas.db"
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 0)
        result = check_signal_writes(
            date=CHECK_DATE,
            plans_dir=tmp_path,
            db_path=db_path,
        )
        assert result == []

    def test_matching_counts_returns_empty(self, tmp_path):
        """JSON proposed=5, SQLite proposed=5 → no divergence (exit 0)."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"

        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 5)

        result = check_signal_writes(
            date=CHECK_DATE,
            plans_dir=plans_dir,
            db_path=db_path,
            tolerance=2,
        )
        assert result == [], f"Expected no divergence, got: {result}"

    def test_within_tolerance_returns_empty(self, tmp_path):
        """JSON=5, SQLite=3 → diff=2 ≤ tolerance=2 → no divergence."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"

        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 3)

        result = check_signal_writes(
            date=CHECK_DATE,
            plans_dir=plans_dir,
            db_path=db_path,
            tolerance=2,
        )
        assert result == []

    def test_zero_sqlite_rows_returns_divergence(self, tmp_path):
        """JSON=5, SQLite=0 → silent-failure scenario → divergence."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"

        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 0)

        result = check_signal_writes(
            date=CHECK_DATE,
            plans_dir=plans_dir,
            db_path=db_path,
            tolerance=2,
        )
        assert len(result) == 1
        universe_r, date_r, json_n, sqlite_n = result[0]
        assert universe_r == UNIVERSE
        assert date_r == CHECK_DATE_STR
        assert json_n == 5
        assert sqlite_n == 0

    def test_above_tolerance_returns_divergence(self, tmp_path):
        """JSON=5, SQLite=2 → diff=3 > tolerance=2 → divergence."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"

        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 2)

        result = check_signal_writes(
            date=CHECK_DATE,
            plans_dir=plans_dir,
            db_path=db_path,
            tolerance=2,
        )
        assert len(result) == 1
        _, _, json_n, sqlite_n = result[0]
        assert json_n == 5
        assert sqlite_n == 2

    def test_multiple_universes_ok(self, tmp_path):
        """Two universes, both matching → no divergences."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"

        _write_plan(plans_dir, "sp500", CHECK_DATE_STR, 4)
        _write_plan(plans_dir, "commodity_etfs", CHECK_DATE_STR, 2)

        # Build DB with both universes
        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            """
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL, ticker TEXT NOT NULL,
                strategy TEXT NOT NULL, universe TEXT NOT NULL,
                direction TEXT DEFAULT 'long',
                entry_price REAL NOT NULL, stop_price REAL NOT NULL,
                take_profit REAL, position_size INTEGER NOT NULL,
                position_value REAL NOT NULL, risk_amount REAL NOT NULL,
                confidence REAL NOT NULL, rationale TEXT, features TEXT,
                sector TEXT, regime_state TEXT, action TEXT NOT NULL,
                action_reason TEXT, config_version TEXT, market_id TEXT
            )
            """
        )
        for univ, n in [("sp500", 4), ("commodity_etfs", 2)]:
            for i in range(n):
                cur.execute(
                    "INSERT INTO signals (timestamp, ticker, strategy, universe, "
                    "entry_price, stop_price, position_size, position_value, "
                    "risk_amount, confidence, action, market_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,'proposed',?)",
                    (f"{CHECK_DATE_STR}T18:00:{i:02d}", f"T{i}", "mr", univ,
                     100.0, 95.0, 1, 100.0, 5.0, 0.7, univ),
                )
        conn.commit()
        conn.close()

        result = check_signal_writes(
            date=CHECK_DATE, plans_dir=plans_dir, db_path=db_path, tolerance=2,
        )
        assert result == []


# ── Tests: main() (CLI) ─────────────────────────────────────────────────────

class TestMain:
    """Integration tests for the CLI entry-point (main())."""

    def test_exit_0_when_counts_match(self, tmp_path):
        """JSON=5, SQLite=5 → exit 0, no alert."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"
        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 5)

        with patch("scripts.check_signal_writes._send_alert") as mock_alert:
            rc = main([
                "--date", CHECK_DATE_STR,
                "--plans-dir", str(plans_dir),
                "--db-path", str(db_path),
                "--tolerance", "2",
            ])

        assert rc == 0
        mock_alert.assert_not_called()

    def test_exit_1_and_alert_when_zero_sqlite(self, tmp_path):
        """JSON=5, SQLite=0 → exit 1 + alert called."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"
        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 0)

        with patch("scripts.check_signal_writes._send_alert") as mock_alert:
            rc = main([
                "--date", CHECK_DATE_STR,
                "--plans-dir", str(plans_dir),
                "--db-path", str(db_path),
                "--tolerance", "2",
            ])

        assert rc == 1
        mock_alert.assert_called_once()
        # Verify alert includes universe and counts
        alert_arg = mock_alert.call_args[0][0]
        assert UNIVERSE in str(alert_arg) or CHECK_DATE_STR in str(alert_arg)

    def test_exit_0_within_tolerance(self, tmp_path):
        """JSON=5, SQLite=3 → diff=2 ≤ tolerance=2 → exit 0, no alert."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"
        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 3)

        with patch("scripts.check_signal_writes._send_alert") as mock_alert:
            rc = main([
                "--date", CHECK_DATE_STR,
                "--plans-dir", str(plans_dir),
                "--db-path", str(db_path),
                "--tolerance", "2",
            ])

        assert rc == 0
        mock_alert.assert_not_called()

    def test_exit_1_just_above_tolerance(self, tmp_path):
        """JSON=5, SQLite=2 → diff=3 > tolerance=2 → exit 1."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"
        _write_plan(plans_dir, UNIVERSE, CHECK_DATE_STR, 5)
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 2)

        with patch("scripts.check_signal_writes._send_alert") as mock_alert:
            rc = main([
                "--date", CHECK_DATE_STR,
                "--plans-dir", str(plans_dir),
                "--db-path", str(db_path),
                "--tolerance", "2",
            ])

        assert rc == 1
        mock_alert.assert_called_once()

    def test_exit_0_no_plan_files(self, tmp_path):
        """No plan files for the date → exit 0 (nothing to check)."""
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        db_path = tmp_path / "atlas.db"
        _make_db(db_path, UNIVERSE, CHECK_DATE_STR, 0)

        with patch("scripts.check_signal_writes._send_alert") as mock_alert:
            rc = main([
                "--date", CHECK_DATE_STR,
                "--plans-dir", str(plans_dir),
                "--db-path", str(db_path),
            ])

        assert rc == 0
        mock_alert.assert_not_called()

    def test_invalid_date_exits_1(self, tmp_path):
        """Invalid --date argument → exit 1 immediately."""
        rc = main(["--date", "not-a-date"])
        assert rc == 1
