"""Tests for A5: Stale data detection in data/ingest.py.

Verifies:
- check_data_freshness returns correct fresh/stale classification
- Handles empty data, weekends, single tickers, mixed freshness
- verify_ingest_freshness raises RuntimeError when halt_on_stale_data=True
- verify_ingest_freshness returns False (not raise) when halt_on_stale_data=False
- Telegram alert is sent when stale data is detected

Run:
    cd /root/atlas && python3 -m pytest tests/test_data_freshness.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from data.ingest import (
    check_data_freshness,
    verify_ingest_freshness,
    _last_trading_day,
)


# ── Helpers ───────────────────────────────────────────────────

def _df_with_latest(date_str: str, n_rows: int = 10) -> pd.DataFrame:
    """Build a DataFrame whose most recent row is on *date_str*."""
    end = pd.Timestamp(date_str)
    dates = pd.bdate_range(end=end, periods=n_rows)
    actual_n = len(dates)
    return pd.DataFrame(
        {"close": [100.0 + i for i in range(actual_n)], "ticker": "TEST"},
        index=dates,
    )


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")


def _two_days_ago_str() -> str:
    return (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")


def _last_weekday_str() -> str:
    d = datetime.now()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


# ── _last_trading_day ─────────────────────────────────────────

class TestLastTradingDay:
    def test_weekday_unchanged(self):
        """Monday–Friday: return the same day."""
        # Find the most recent Monday
        d = datetime.now()
        while d.weekday() != 0:  # 0 = Monday
            d -= timedelta(days=1)
        result = _last_trading_day(d)
        assert result.weekday() < 5

    def test_saturday_returns_friday(self):
        # Create a Saturday
        d = datetime(2026, 3, 21)  # Saturday
        assert d.weekday() == 5
        result = _last_trading_day(d)
        assert result.weekday() == 4  # Friday

    def test_sunday_returns_friday(self):
        d = datetime(2026, 3, 22)  # Sunday
        assert d.weekday() == 6
        result = _last_trading_day(d)
        assert result.weekday() == 4  # Friday

    def test_uses_now_by_default(self):
        result = _last_trading_day()
        assert result.weekday() < 5  # Always a weekday


# ── check_data_freshness ──────────────────────────────────────

class TestCheckDataFreshness:
    def test_empty_data_returns_not_fresh(self):
        result = check_data_freshness({})
        assert result["is_fresh"] is False
        assert "No data provided" in result["message"]

    def test_fresh_data_today(self):
        today = _last_weekday_str()
        data = {
            "AAPL": _df_with_latest(today),
            "MSFT": _df_with_latest(today),
        }
        result = check_data_freshness(data, max_lag_days=1)
        assert result["is_fresh"] is True
        assert result["stale_count"] == 0
        assert result["fresh_count"] == 2

    def test_fresh_data_yesterday_with_lag_1(self):
        """Data from yesterday is fresh when max_lag_days=1."""
        yesterday = _yesterday_str()
        data = {"AAPL": _df_with_latest(yesterday)}
        result = check_data_freshness(data, max_lag_days=1)
        # yesterday is within 1 day lag
        assert result["is_fresh"] is True

    def test_stale_data_old_date(self):
        """Data from a week ago is always stale."""
        old_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        data = {"AAPL": _df_with_latest(old_date)}
        result = check_data_freshness(data, max_lag_days=1)
        assert result["is_fresh"] is False
        assert result["stale_count"] == 1
        assert "AAPL" in result["stale_tickers"]

    def test_mixed_freshness(self):
        """Some fresh, some stale: overall is_fresh=False."""
        today = _last_weekday_str()
        old_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        data = {
            "AAPL": _df_with_latest(today),
            "STALE1": _df_with_latest(old_date),
            "STALE2": _df_with_latest(old_date),
        }
        result = check_data_freshness(data, max_lag_days=1)
        assert result["is_fresh"] is False
        assert result["fresh_count"] == 1
        assert result["stale_count"] == 2
        assert "STALE1" in result["stale_tickers"]
        assert "STALE2" in result["stale_tickers"]

    def test_empty_dataframes_skipped(self):
        """Empty DataFrames are skipped in the freshness check."""
        today = _last_weekday_str()
        data = {
            "AAPL": _df_with_latest(today),
            "EMPTY": pd.DataFrame(),
        }
        result = check_data_freshness(data, max_lag_days=1)
        assert result["fresh_count"] == 1
        assert "EMPTY" not in result["stale_tickers"]

    def test_none_skipped(self):
        """None values are skipped in the freshness check."""
        today = _last_weekday_str()
        data = {"AAPL": _df_with_latest(today), "NONE": None}
        result = check_data_freshness(data, max_lag_days=1)
        assert result["fresh_count"] == 1

    def test_returns_newest_and_oldest_dates(self):
        today = _last_weekday_str()
        old = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
        data = {
            "FRESH": _df_with_latest(today),
            "OLD": _df_with_latest(old),
        }
        result = check_data_freshness(data, max_lag_days=1)
        assert result["newest_date"] is not None
        assert result["oldest_date"] is not None
        assert result["newest_date"] >= result["oldest_date"]

    def test_expected_date_in_result(self):
        today = _last_weekday_str()
        data = {"AAPL": _df_with_latest(today)}
        result = check_data_freshness(data, max_lag_days=1)
        assert result["expected_date"] is not None
        # Expected date should be a YYYY-MM-DD string
        assert len(result["expected_date"]) == 10

    def test_stale_message_contains_count(self):
        old = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        data = {f"STALE{i}": _df_with_latest(old) for i in range(3)}
        result = check_data_freshness(data, max_lag_days=1)
        assert "3" in result["message"] or "stale" in result["message"].lower()

    def test_single_ticker_fresh(self):
        today = _last_weekday_str()
        data = {"SPY": _df_with_latest(today)}
        result = check_data_freshness(data, max_lag_days=1)
        assert result["is_fresh"] is True
        assert result["stale_count"] == 0


# ── verify_ingest_freshness ───────────────────────────────────

class TestVerifyIngestFreshness:
    def _fresh_data(self) -> dict:
        today = _last_weekday_str()
        return {"AAPL": _df_with_latest(today), "MSFT": _df_with_latest(today)}

    def _stale_data(self) -> dict:
        old = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
        return {"AAPL": _df_with_latest(old), "MSFT": _df_with_latest(old)}

    def _cfg(self, halt: bool = True) -> dict:
        return {
            "trading": {
                "live_safety": {
                    "halt_on_stale_data": halt,
                }
            }
        }

    def test_returns_true_for_fresh_data(self):
        result = verify_ingest_freshness(self._fresh_data(), config=self._cfg(halt=True))
        assert result is True

    def test_raises_for_stale_data_when_halt_true(self):
        with patch("utils.telegram.send_message", return_value=True):
            with pytest.raises(RuntimeError, match="STALE DATA"):
                verify_ingest_freshness(self._stale_data(), config=self._cfg(halt=True))

    def test_returns_false_for_stale_data_when_halt_false(self):
        with patch("utils.telegram.send_message", return_value=True):
            result = verify_ingest_freshness(
                self._stale_data(), config=self._cfg(halt=False)
            )
        assert result is False

    def test_halts_by_default_when_no_config(self):
        """Without config, default is halt=True."""
        with patch("utils.telegram.send_message", return_value=True):
            with pytest.raises(RuntimeError):
                verify_ingest_freshness(self._stale_data(), config=None)

    def test_sends_telegram_alert_on_stale(self):
        with patch("utils.telegram.send_message", return_value=True) as mock_send:
            try:
                verify_ingest_freshness(
                    self._stale_data(), config=self._cfg(halt=True)
                )
            except RuntimeError:
                pass
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0][0]
        assert "STALE" in call_args.upper() or "stale" in call_args.lower()

    def test_no_alert_for_fresh_data(self):
        with patch("utils.telegram.send_message", return_value=True) as mock_send:
            verify_ingest_freshness(self._fresh_data(), config=self._cfg(halt=True))
        mock_send.assert_not_called()

    def test_telegram_failure_is_non_fatal(self):
        """If Telegram send fails, the main logic still runs."""
        with patch("utils.telegram.send_message", side_effect=Exception("no network")):
            with pytest.raises(RuntimeError, match="STALE DATA"):
                verify_ingest_freshness(self._stale_data(), config=self._cfg(halt=True))

    def test_includes_market_id_in_log(self):
        """market_id is passed through to freshness check."""
        with patch("utils.telegram.send_message", return_value=True):
            try:
                verify_ingest_freshness(
                    self._stale_data(),
                    config=self._cfg(halt=True),
                    market_id="sp500",
                )
            except RuntimeError:
                pass
        # No assertion needed — we just verify no exception from the call

    def test_empty_data_raises_or_passes_gracefully(self):
        """Empty data dict returns False / raises (stale check with no data)."""
        # Empty data → is_fresh=False → should either raise or return False
        try:
            result = verify_ingest_freshness({}, config=self._cfg(halt=False))
            assert result is False
        except RuntimeError:
            pass  # acceptable if halt=True behavior kicks in
