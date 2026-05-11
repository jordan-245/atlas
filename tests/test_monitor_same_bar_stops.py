"""Tests for scripts/monitor_same_bar_stops.py.

5 required tests:
  1. Smoke       — import the module; assert main() exists
  2. Detection   — 10 trades, 3 same-bar → rate=30%, count=3
  3. Threshold   — rate >20% AND ≥5 events → telegram fires (mocked)
  4. Cooldown    — last alert <24h ago → telegram suppressed
  5. Quiet mode  — --quiet → no telegram regardless of threshold

All DB operations use the global _isolate_prod_db autouse fixture from conftest.py.
"""
from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

import db.atlas_db as _adb


# ── Helpers ────────────────────────────────────────────────────────────────────

def _load_module():
    """Fresh import of the monitor module (bypasses sys.modules cache)."""
    spec = importlib.util.spec_from_file_location(
        "monitor_same_bar_stops",
        ATLAS_ROOT / "scripts" / "monitor_same_bar_stops.py",
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _insert_trade(
    db,
    ticker: str,
    strategy: str,
    entry_date: str,
    exit_date: str | None,
    pnl: float = -5.0,
    status: str = "closed",
    exit_reason: str = "stop_loss",
) -> None:
    """Insert a minimal trade row into the test DB."""
    db.execute(
        """
        INSERT INTO trades
            (ticker, strategy, universe, direction, entry_date, entry_price,
             shares, exit_date, exit_price, pnl, pnl_pct, status, superseded)
        VALUES
            (?, ?, 'sp500', 'long', ?, 100.0, 10, ?, 95.0, ?, -5.0, ?, 0)
        """,
        (ticker, strategy, entry_date, exit_date, pnl, status),
    )
    db.commit()


def _date(days_ago: int) -> str:
    """Return an ISO date string N days ago."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")


# ── Test 1: Smoke ──────────────────────────────────────────────────────────────

class TestSmoke:
    def test_module_importable_and_main_exists(self):
        """The module must be importable and expose a main() callable."""
        mod = _load_module()
        assert hasattr(mod, "main"), "monitor_same_bar_stops must expose main()"
        assert callable(mod.main), "main must be callable"

    def test_run_monitor_function_exists(self):
        """run_monitor() is the core function used by tests."""
        mod = _load_module()
        assert hasattr(mod, "run_monitor"), "run_monitor() must exist"
        assert callable(mod.run_monitor)


# ── Test 2: Detection ──────────────────────────────────────────────────────────

class TestDetection:
    def test_three_same_bar_out_of_ten(self, tmp_path):
        """Insert 10 closed trades, 3 same-bar → rate=0.30, count=3."""
        mod = _load_module()

        with _adb.get_db() as db:
            # 7 normal trades (different entry/exit dates)
            for i in range(7):
                _insert_trade(
                    db,
                    ticker=f"NORM{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 5),
                    exit_date=_date(i + 3),
                )
            # 3 same-bar trades
            for j in range(3):
                same_date = _date(j + 1)
                _insert_trade(
                    db,
                    ticker=f"SBAR{j:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                    pnl=-3.0,
                )

        data = mod.query_same_bar_stops(days=30)

        assert data["same_bar_total"] == 3, f"Expected 3 same-bar, got {data['same_bar_total']}"
        assert data["total_round_trips"] == 10, f"Expected 10 total, got {data['total_round_trips']}"
        assert abs(data["rate"] - 0.30) < 0.001, f"Expected rate 0.30, got {data['rate']}"

    def test_zero_same_bar_when_none(self, tmp_path):
        """Zero same-bar trades → rate=0.0, count=0."""
        mod = _load_module()

        with _adb.get_db() as db:
            for i in range(4):
                _insert_trade(
                    db,
                    ticker=f"FINE{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 5),
                    exit_date=_date(i + 2),
                )

        data = mod.query_same_bar_stops(days=30)

        assert data["same_bar_total"] == 0
        assert data["rate"] == 0.0

    def test_no_trades_returns_zero_rate(self):
        """Empty DB → rate=0.0, no errors."""
        mod = _load_module()
        data = mod.query_same_bar_stops(days=30)

        assert data["same_bar_total"] == 0
        assert data["total_round_trips"] == 0
        assert data["rate"] == 0.0

    def test_superseded_excluded(self):
        """Superseded=1 trades must not be counted."""
        mod = _load_module()

        with _adb.get_db() as db:
            same_date = _date(2)
            db.execute(
                """
                INSERT INTO trades
                    (ticker, strategy, universe, direction, entry_date,
                     entry_price, shares, exit_date, exit_price, pnl,
                     pnl_pct, status, superseded)
                VALUES ('ZZZ', 'momentum_breakout', 'sp500', 'long',
                        ?, 100.0, 10, ?, 95.0, -5.0, -5.0, 'closed', 1)
                """,
                (same_date, same_date),
            )
            db.commit()

        data = mod.query_same_bar_stops(days=30)
        assert data["same_bar_total"] == 0, "superseded=1 should not be counted"


# ── Test 3: Threshold — alert fires when rate >20% AND ≥5 events ──────────────

class TestThreshold:
    def test_alert_fires_above_threshold(self, tmp_path):
        """rate=50% AND 6 same-bar events → telegram should fire (mocked)."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        with _adb.get_db() as db:
            # 12 trades total: 6 same-bar, 6 normal
            for i in range(6):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"ALERT{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                    pnl=-4.0,
                )
            for i in range(6):
                _insert_trade(
                    db,
                    ticker=f"NORM{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 10),
                    exit_date=_date(i + 8),
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert result == 1, "Should return 1 (alert fired)"
        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert "same_bar_stops" in str(call_kwargs), "Category must be 'same_bar_stops'"

    def test_no_alert_when_below_min_events(self, tmp_path):
        """rate=100% BUT only 2 same-bar events (< min_events=5) → no alert."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        with _adb.get_db() as db:
            for i in range(2):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"TINY{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert result == 0, "Should return 0 (no alert — below min_events)"
        mock_notify.assert_not_called()

    def test_no_alert_when_below_rate_threshold(self, tmp_path):
        """5 same-bar events BUT rate=5% (< threshold 20%) → no alert."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        with _adb.get_db() as db:
            # 100 total trades, 5 same-bar → 5%
            for i in range(5):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"FEW{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )
            for i in range(95):
                _insert_trade(
                    db,
                    ticker=f"LOTS{i:03d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i % 20 + 5),
                    exit_date=_date(i % 20 + 2),
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert result == 0
        mock_notify.assert_not_called()


# ── Test 4: Cooldown ───────────────────────────────────────────────────────────

class TestCooldown:
    def test_alert_suppressed_within_24h(self, tmp_path):
        """If last alert was <24h ago, no Telegram call even if threshold exceeded."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        # Pre-seed state with a recent alert (2 hours ago)
        recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        state_file.write_text(json.dumps({"last_alert_at": recent}))

        with _adb.get_db() as db:
            for i in range(6):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"COOL{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )
            for i in range(4):
                _insert_trade(
                    db,
                    ticker=f"NORM{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 10),
                    exit_date=_date(i + 8),
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert result == 1, "Should return 1 (alert-worthy condition) but telegram suppressed"
        mock_notify.assert_not_called()

    def test_alert_fires_after_cooldown_expires(self, tmp_path):
        """If last alert was >24h ago, alert should fire."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        # State with an old alert (25 hours ago)
        old = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        state_file.write_text(json.dumps({"last_alert_at": old}))

        with _adb.get_db() as db:
            for i in range(6):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"STALE{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )
            for i in range(4):
                _insert_trade(
                    db,
                    ticker=f"NORM{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 10),
                    exit_date=_date(i + 8),
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert result == 1
        mock_notify.assert_called_once()

    def test_state_file_updated_after_alert(self, tmp_path):
        """State file must be written with last_alert_at after a real alert fires."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        with _adb.get_db() as db:
            for i in range(6):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"UPDT{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )
            for i in range(4):
                _insert_trade(
                    db,
                    ticker=f"NORM{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=_date(i + 10),
                    exit_date=_date(i + 8),
                )

        before = datetime.now(timezone.utc)
        with patch.object(mod, "_tg_notify"):
            mod.run_monitor(
                days=30,
                threshold=0.20,
                min_events=5,
                quiet=False,
                state_file=state_file,
            )

        assert state_file.exists(), "State file must be created"
        state = json.loads(state_file.read_text())
        assert "last_alert_at" in state
        stored_dt = datetime.fromisoformat(state["last_alert_at"])
        if stored_dt.tzinfo is None:
            stored_dt = stored_dt.replace(tzinfo=timezone.utc)
        assert stored_dt >= before, "last_alert_at must be >= run start time"


# ── Test 5: Quiet mode ─────────────────────────────────────────────────────────

class TestQuietMode:
    def test_quiet_suppresses_telegram_regardless_of_threshold(self, tmp_path):
        """--quiet must prevent ALL Telegram sends regardless of rate/events."""
        mod = _load_module()
        state_file = tmp_path / "same_bar_state.json"

        with _adb.get_db() as db:
            for i in range(10):
                same_date = _date(i + 1)
                _insert_trade(
                    db,
                    ticker=f"LOUD{i:02d}",
                    strategy="momentum_breakout",
                    entry_date=same_date,
                    exit_date=same_date,
                )

        with patch.object(mod, "_tg_notify") as mock_notify:
            result = mod.run_monitor(
                days=30,
                threshold=0.01,   # ultra-low threshold — would fire without --quiet
                min_events=1,
                quiet=True,
                state_file=state_file,
            )

        mock_notify.assert_not_called()
        assert result == 1, "Should still return 1 (alert-worthy) even with --quiet"

    def test_quiet_via_main_argv(self, tmp_path):
        """main(['--quiet', '--days', '30']) must not call telegram."""
        mod = _load_module()
        # Patch STATE_FILE to tmp so cooldown state is fresh
        with patch.object(mod, "STATE_FILE", tmp_path / "state.json"), \
             patch.object(mod, "_tg_notify") as mock_notify:
            # Should succeed without errors
            rc = mod.main(["--quiet", "--days", "30"])

        mock_notify.assert_not_called()
        assert rc in (0, 1, 2), f"Unexpected exit code: {rc}"
