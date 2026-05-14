"""Tests for data.ohlcv_query — OHLCV loader helpers with TTL cache.

All tests use a tmp SQLite DB with seed data; no production DB writes.
"""
from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pandas as pd
import pytest

import db.atlas_db as _adb
from data.ohlcv_query import clear_cache, get_ohlcv_close, get_ohlcv_volume
from data import ohlcv_query as _mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cache():
    """Clear the query cache before and after every test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture()
def seed_db(tmp_path, monkeypatch):
    """Tiny isolated DB with OHLCV rows for 3 tickers x 3 days."""
    db_path = tmp_path / "test_ohlcv.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE ohlcv (
            date TEXT, ticker TEXT, open REAL, high REAL, low REAL,
            close REAL, volume INTEGER,
            PRIMARY KEY (date, ticker)
        )"""
    )
    rows = [
        ("2024-01-02", "XLK", 100.0, 102.0, 99.0, 101.0, 1_000_000),
        ("2024-01-03", "XLK", 101.0, 103.0, 100.0, 102.0, 1_100_000),
        ("2024-01-04", "XLK", 102.0, 104.0, 101.0, 103.0, 1_200_000),
        ("2024-01-02", "XLF", 40.0, 41.0, 39.5, 40.5, 500_000),
        ("2024-01-03", "XLF", 40.5, 41.5, 40.0, 41.0, 510_000),
        ("2024-01-04", "XLF", 41.0, 42.0, 40.5, 41.5, 520_000),
        ("2024-01-02", "XLU", 70.0, 71.0, 69.5, 70.5, 300_000),
        ("2024-01-03", "XLU", 70.5, 71.5, 70.0, 71.0, 310_000),
        ("2024-01-04", "XLU", 71.0, 72.0, 70.5, 71.5, 320_000),
    ]
    conn.executemany(
        "INSERT INTO ohlcv (date, ticker, open, high, low, close, volume) VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_adb, "_db_path_override", str(db_path))
    yield str(db_path)


# ---------------------------------------------------------------------------
# get_ohlcv_volume
# ---------------------------------------------------------------------------

class TestGetOhlcvVolume:
    def test_returns_correct_columns(self, seed_db):
        df = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert list(df.columns) == ["date", "ticker", "volume"]

    def test_single_ticker_row_count(self, seed_db):
        df = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert len(df) == 3
        assert set(df["ticker"]) == {"XLK"}

    def test_multiple_tickers(self, seed_db):
        df = get_ohlcv_volume(["XLK", "XLF"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert set(df["ticker"]) == {"XLK", "XLF"}
        assert len(df) == 6

    def test_date_range_filters(self, seed_db):
        df = get_ohlcv_volume(["XLK"], "2024-01-03", "2024-01-04", cache_ttl=0)
        assert "2024-01-02" not in df["date"].values
        assert "2024-01-03" in df["date"].values

    def test_cache_hit_skips_db(self, seed_db):
        """Second identical call must not touch the DB."""
        df1 = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=60)
        with patch("data.ohlcv_query.get_db") as mock_db:
            df2 = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=60)
            mock_db.assert_not_called()
        pd.testing.assert_frame_equal(df1, df2)

    def test_cache_bypass_with_zero_ttl(self, seed_db):
        """cache_ttl=0 must always hit the DB even if cache is populated."""
        key = ("get_ohlcv_volume", ("XLK",), "2024-01-02", "2024-01-04")
        fake_df = pd.DataFrame({"date": ["fake"], "ticker": ["XLK"], "volume": [999]})
        _mod._cache[key] = (time.time(), fake_df)

        result = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=0)
        # Real DB has 3 rows, not 1 fake row
        assert len(result) == 3
        assert 999 not in result["volume"].values

    def test_ttl_expiry_triggers_fresh_fetch(self, seed_db):
        """After TTL expires the DB is re-queried and cache is refreshed."""
        df1 = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=3600)
        key = ("get_ohlcv_volume", ("XLK",), "2024-01-02", "2024-01-04")
        # Backdate the cached timestamp so it looks stale
        ts, cached_df = _mod._cache[key]
        _mod._cache[key] = (0.0, cached_df)

        df2 = get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=3600)
        pd.testing.assert_frame_equal(df1, df2)
        # Cache entry should now have a fresh timestamp
        assert _mod._cache[key][0] > 0.0

    def test_empty_result_for_unknown_ticker(self, seed_db):
        df = get_ohlcv_volume(["ZZZZ"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert df.empty

    def test_empty_tickers_returns_typed_empty_df(self, seed_db):
        df = get_ohlcv_volume([], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert df.empty
        assert list(df.columns) == ["date", "ticker", "volume"]


# ---------------------------------------------------------------------------
# get_ohlcv_close
# ---------------------------------------------------------------------------

class TestGetOhlcvClose:
    def test_returns_correct_columns(self, seed_db):
        df = get_ohlcv_close(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert list(df.columns) == ["ticker", "date", "close"]

    def test_single_ticker_row_count(self, seed_db):
        df = get_ohlcv_close(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert len(df) == 3

    def test_multiple_tickers_all_returned(self, seed_db):
        df = get_ohlcv_close(["XLK", "XLF", "XLU"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert set(df["ticker"]) == {"XLK", "XLF", "XLU"}
        assert len(df) == 9

    def test_cache_hit_skips_db(self, seed_db):
        df1 = get_ohlcv_close(["XLF"], "2024-01-02", "2024-01-04", cache_ttl=60)
        with patch("data.ohlcv_query.get_db") as mock_db:
            df2 = get_ohlcv_close(["XLF"], "2024-01-02", "2024-01-04", cache_ttl=60)
            mock_db.assert_not_called()
        pd.testing.assert_frame_equal(df1, df2)

    def test_volume_and_close_use_independent_cache_keys(self, seed_db):
        """Populating get_ohlcv_volume cache must not satisfy get_ohlcv_close."""
        get_ohlcv_volume(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=60)
        # close key is different — should still hit DB on first call
        df_close = get_ohlcv_close(["XLK"], "2024-01-02", "2024-01-04", cache_ttl=60)
        assert "close" in df_close.columns
        assert len(df_close) == 3

    def test_empty_result_for_unknown_ticker(self, seed_db):
        df = get_ohlcv_close(["MISSING"], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert df.empty

    def test_empty_tickers_returns_typed_empty_df(self, seed_db):
        df = get_ohlcv_close([], "2024-01-02", "2024-01-04", cache_ttl=0)
        assert df.empty
        assert list(df.columns) == ["ticker", "date", "close"]
