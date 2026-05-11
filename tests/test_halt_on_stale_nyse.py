"""
tests/test_halt_on_stale_nyse.py

Regression tests for NYSE calendar awareness in _last_trading_day() and
verify_ingest_freshness() / check_data_freshness().

Task #295 — fix: previously _last_trading_day used naive weekday walk-back
so Monday pre-market with Friday data was incorrectly flagged as stale
(expected_date = Sunday > Friday data → halt).
"""
from __future__ import annotations

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import patch

from tests.conftest import make_ohlcv_df

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_df(last_date: str, n: int = 30) -> pd.DataFrame:
    """Return a minimal OHLCV DataFrame with the given last date.

    Uses flat_price=102.0 (close value only; tests care about date index, not OHLC).
    Delegates to conftest.make_ohlcv_df.
    """
    return make_ohlcv_df(n_days=n, flat_price=102.0, volumes=1_000_000, end_date=last_date)


def _make_data(last_date: str, tickers: list[str] | None = None) -> dict[str, pd.DataFrame]:
    tickers = tickers or ["AAPL", "MSFT", "GOOG"]
    return {t: _make_df(last_date) for t in tickers}


# ---------------------------------------------------------------------------
# Unit tests for _last_trading_day
# ---------------------------------------------------------------------------

class TestLastTradingDay:
    """_last_trading_day should return the previous NYSE trading session."""

    def _call(self, dt: datetime) -> str:
        from data.ingest import _last_trading_day
        return _last_trading_day(dt).strftime("%Y-%m-%d")

    def test_monday_returns_previous_friday(self):
        """Monday 2026-05-04 → previous trading day is Friday 2026-05-01."""
        result = self._call(datetime(2026, 5, 4, 9, 35))
        assert result == "2026-05-01", f"Expected 2026-05-01, got {result}"

    def test_tuesday_returns_monday(self):
        """Regular Tuesday 2026-05-05 → previous trading day is Monday 2026-05-04."""
        result = self._call(datetime(2026, 5, 5, 9, 35))
        assert result == "2026-05-04", f"Expected 2026-05-04, got {result}"

    def test_after_mlk_day_returns_friday(self):
        """Tuesday 2026-01-20 (day after MLK Day holiday) → Friday 2026-01-16.

        MLK Day falls on 2026-01-19 (3rd Monday of January) — NYSE closed.
        Previous trading day is Friday 2026-01-16.
        """
        result = self._call(datetime(2026, 1, 20, 9, 35))
        assert result == "2026-01-16", f"Expected 2026-01-16, got {result}"

    def test_saturday_returns_friday(self):
        """Saturday 2026-05-02 → Friday 2026-05-01."""
        result = self._call(datetime(2026, 5, 2, 10, 0))
        assert result == "2026-05-01"

    def test_sunday_returns_friday(self):
        """Sunday 2026-05-03 → Friday 2026-05-01."""
        result = self._call(datetime(2026, 5, 3, 10, 0))
        assert result == "2026-05-01"

    def test_wednesday_returns_tuesday(self):
        """Wednesday returns the previous trading day (Tuesday)."""
        result = self._call(datetime(2026, 5, 6, 9, 35))
        assert result == "2026-05-05"

    def test_fallback_when_mcal_unavailable(self):
        """Without pandas_market_calendars, fallback still handles weekends correctly."""
        import builtins
        real_import = builtins.__import__

        def _block_mcal(name, *args, **kwargs):
            if name == "pandas_market_calendars":
                raise ImportError("blocked for test")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_mcal):
            from data.ingest import _last_trading_day
            # Monday — fallback should walk back 1 day then skip no weekends → Friday
            result = _last_trading_day(datetime(2026, 5, 4, 9, 35))
            assert result.strftime("%Y-%m-%d") == "2026-05-01"

            # Sunday — fallback walks back through Sat, Sun → Friday
            result = _last_trading_day(datetime(2026, 5, 3, 10, 0))
            assert result.strftime("%Y-%m-%d") == "2026-05-01"


# ---------------------------------------------------------------------------
# Integration tests: check_data_freshness
# ---------------------------------------------------------------------------

class TestCheckDataFreshness:
    """check_data_freshness should NOT flag data as stale on Monday with Friday data."""

    def test_monday_morning_with_friday_data_is_fresh(self):
        """Core regression: Monday 09:35 ET, data as of Friday → fresh (not stale).

        Previously this would halt because expected_date was Sunday > Friday.
        """
        from data.ingest import check_data_freshness

        data = _make_data("2026-05-01")  # Friday's close
        monday = datetime(2026, 5, 4, 9, 35)

        with patch("data.ingest._last_trading_day", return_value=datetime(2026, 5, 1)):
            result = check_data_freshness(data, market_id="sp500")

        assert result["is_fresh"], (
            f"Monday with Friday data should be fresh. "
            f"expected_date={result['expected_date']}, "
            f"stale={result['stale_tickers']}"
        )
        assert result["stale_count"] == 0
        assert result["stale_tickers"] == []

    def test_tuesday_with_stale_data_halts(self):
        """Tuesday with data from previous Tuesday (7 cal days ago) → stale.

        Gap = 5 trading days >> max_lag_days=1, must flag as stale.
        """
        from data.ingest import check_data_freshness

        # Data from Apr 28 (Tue); running May 5 (Tue) with _last_trading_day = May 4 (Mon)
        data = _make_data("2026-04-28")  # last Tuesday

        with patch("data.ingest._last_trading_day", return_value=datetime(2026, 5, 4)):
            result = check_data_freshness(data, market_id="sp500")

        # expected_date = 2026-05-04 - 1 day = 2026-05-03
        # Apr 28 < May 3 → stale
        assert not result["is_fresh"], (
            f"Data from previous Tuesday should be stale. "
            f"expected_date={result['expected_date']}, newest={result['newest_date']}"
        )
        assert result["stale_count"] == 3

    def test_after_mlk_monday_with_friday_data_is_fresh(self):
        """Tuesday morning after MLK Day (Jan 20), data from Jan 16 → fresh.

        _last_trading_day returns Jan 16 (holiday-aware, MLK=Jan 19 closed).
        expected_date = Jan 16 - 1 = Jan 15. Data Jan 16 >= Jan 15 → fresh.
        """
        from data.ingest import check_data_freshness

        data = _make_data("2026-01-16")  # Friday before MLK weekend

        with patch("data.ingest._last_trading_day", return_value=datetime(2026, 1, 16)):
            result = check_data_freshness(data, market_id="sp500")

        assert result["is_fresh"], (
            f"Day after MLK with pre-holiday Friday data should be fresh. "
            f"expected_date={result['expected_date']}, stale={result['stale_tickers']}"
        )
        assert result["stale_count"] == 0

    def test_data_from_two_trading_days_ago_is_stale(self):
        """Data older than expected_date threshold is correctly flagged as stale."""
        from data.ingest import check_data_freshness

        # _last_trading_day = May 1 (Friday); expected_date = Apr 30 (Thursday)
        # Data from Apr 28 (Tuesday) < Apr 30 → stale
        data = _make_data("2026-04-28")

        with patch("data.ingest._last_trading_day", return_value=datetime(2026, 5, 1)):
            result = check_data_freshness(data, market_id="sp500")

        assert not result["is_fresh"]
        assert result["stale_count"] == 3


# ---------------------------------------------------------------------------
# Live _last_trading_day integration (uses real NYSE calendar)
# ---------------------------------------------------------------------------

class TestLastTradingDayLiveCalendar:
    """Verify the live calendar path produces sensible results today."""

    def test_returns_a_weekday(self):
        """Result should always be a weekday (Mon–Fri)."""
        from data.ingest import _last_trading_day
        result = _last_trading_day()
        assert result.weekday() < 5, f"Result {result} is not a weekday"

    def test_result_not_in_future(self):
        """Last trading day must not be in the future."""
        from data.ingest import _last_trading_day
        result = _last_trading_day()
        assert result <= datetime.now(), f"Result {result} is in the future"

    def test_result_within_7_days(self):
        """Result must be within the past 7 calendar days."""
        from data.ingest import _last_trading_day
        result = _last_trading_day()
        assert (datetime.now() - result).days <= 7, (
            f"Result {result} is more than 7 days old"
        )
