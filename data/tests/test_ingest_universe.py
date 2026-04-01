"""
Tests for data.ingest — ingest_universe() and ingest_all_etf_universes().

All tests use a temporary SQLite database and mock yfinance so no real API
calls are made.
"""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_ohlcv_df(tickers=None, n_rows=10, start="2023-01-01"):
    """Build a minimal OHLCV DataFrame that looks like yfinance output."""
    dates = pd.date_range(start, periods=n_rows, freq="B")
    df = pd.DataFrame(
        {
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "volume": 1_000_000,
        },
        index=dates,
    )
    df.index.name = "date"
    if tickers:
        df["ticker"] = tickers[0]
    return df


def _init_test_db(tmp_path: Path) -> str:
    """Initialise a fresh SQLite test DB and return its path."""
    db_path = str(tmp_path / "test_atlas.db")
    # Minimal schema — only the ohlcv table is needed for these tests.
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv (
            ticker    TEXT NOT NULL,
            date      TEXT NOT NULL,
            open      REAL,
            high      REAL,
            low       REAL,
            close     REAL,
            adj_close REAL,
            volume    INTEGER,
            universe  TEXT,
            source    TEXT,
            PRIMARY KEY (ticker, date)
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Provide a fresh test DB path and patch atlas_db to use it."""
    db_path = _init_test_db(tmp_path)
    import db.atlas_db as atlas_db
    original_override = atlas_db._db_path_override
    atlas_db._db_path_override = db_path
    yield db_path
    atlas_db._db_path_override = original_override


# ── ingest_universe() tests ──────────────────────────────────────────────────

class TestIngestUniverse:
    """Unit tests for data.ingest.ingest_universe()."""

    # Shared patcher helpers — all tests bypass yfinance and cache
    @staticmethod
    def _patch_yf(df):
        """Context manager: patch _download_via_yfinance to return df."""
        return patch("data.ingest._download_via_yfinance", return_value=df)

    @staticmethod
    def _patch_cache_miss():
        """Context manager: always simulate a cache miss."""
        return patch("data.ingest._load_cache", return_value=None)

    @staticmethod
    def _patch_save_cache():
        """Context manager: silence _save_cache side effects."""
        return patch("data.ingest._save_cache")

    def test_returns_expected_keys(self, tmp_db):
        """Result dict must contain all documented keys."""
        from data.ingest import ingest_universe

        sample_df = _make_ohlcv_df(n_rows=5)

        with self._patch_yf(sample_df), self._patch_cache_miss(), self._patch_save_cache():
            result = ingest_universe("sector_etfs", start_date="2023-01-01", end_date="2023-01-31")

        assert "universe" in result
        assert "tickers_fetched" in result
        assert "tickers_failed" in result
        assert "rows_written" in result
        assert "start_date" in result
        assert "end_date" in result

    def test_universe_name_tag_in_sqlite(self, tmp_db):
        """Rows written to SQLite must have universe=universe_name."""
        from data.ingest import ingest_universe
        from db.atlas_db import get_db

        sample_df = _make_ohlcv_df(n_rows=5)

        with self._patch_yf(sample_df), self._patch_cache_miss(), self._patch_save_cache():
            ingest_universe("gold_etfs", start_date="2023-01-01", end_date="2023-01-31")

        with get_db() as db:
            rows = db.execute(
                "SELECT DISTINCT universe FROM ohlcv WHERE ticker='GLD'"
            ).fetchall()

        assert len(rows) == 1
        assert rows[0][0] == "gold_etfs"

    def test_cross_universe_ticker_tagged_correctly(self, tmp_db):
        """GLD ingested via commodity_etfs then gold_etfs — last write wins."""
        from data.ingest import ingest_universe
        from db.atlas_db import get_db

        sample_df = _make_ohlcv_df(n_rows=5)

        with self._patch_yf(sample_df), self._patch_cache_miss(), self._patch_save_cache():
            ingest_universe("commodity_etfs", start_date="2023-01-01", end_date="2023-01-31")
            ingest_universe("gold_etfs", start_date="2023-01-01", end_date="2023-01-31")

        with get_db() as db:
            rows = db.execute(
                "SELECT DISTINCT universe FROM ohlcv WHERE ticker='GLD'"
            ).fetchall()

        # Only ONE row per (ticker, date) — last universe wins
        assert len(rows) == 1
        assert rows[0][0] == "gold_etfs"

    def test_failed_ticker_excluded_from_fetched(self, tmp_db):
        """Tickers returning empty DataFrame must appear in tickers_failed."""
        from data.ingest import ingest_universe

        with self._patch_yf(pd.DataFrame()), self._patch_cache_miss(), self._patch_save_cache():
            result = ingest_universe(
                "treasury_etfs", start_date="2023-01-01", end_date="2023-01-31"
            )

        assert len(result["tickers_fetched"]) == 0
        assert len(result["tickers_failed"]) > 0

    def test_exception_in_download_goes_to_failed(self, tmp_db):
        """If _download_via_yfinance raises, the ticker must end up in tickers_failed."""
        from data.ingest import ingest_universe

        with patch("data.ingest._download_via_yfinance", side_effect=RuntimeError("network error")), \
             self._patch_cache_miss(), self._patch_save_cache():
            result = ingest_universe(
                "treasury_etfs", start_date="2023-01-01", end_date="2023-01-31"
            )

        assert len(result["tickers_fetched"]) == 0
        assert len(result["tickers_failed"]) > 0

    def test_rows_written_count_matches_df_length(self, tmp_db):
        """rows_written must equal total rows across all successfully fetched tickers."""
        from data.ingest import ingest_universe
        from universe.definitions import get_universe_tickers

        n_rows = 7
        sample_df = _make_ohlcv_df(n_rows=n_rows)

        with self._patch_yf(sample_df), self._patch_cache_miss(), self._patch_save_cache():
            result = ingest_universe(
                "sector_etfs", start_date="2023-01-01", end_date="2023-01-31"
            )

        expected_rows = len(get_universe_tickers("sector_etfs")) * n_rows
        assert result["rows_written"] == expected_rows

    def test_raises_for_sp500_universe(self, tmp_db):
        """ingest_universe('sp500') must raise ValueError (dynamic universe)."""
        from data.ingest import ingest_universe

        with pytest.raises(ValueError, match="sp500"):
            ingest_universe("sp500")

    def test_raises_for_unknown_universe(self, tmp_db):
        """ingest_universe with an unrecognised name must raise KeyError."""
        from data.ingest import ingest_universe

        with pytest.raises(KeyError):
            ingest_universe("nonexistent_universe")

    def test_default_start_date_is_7_years_ago(self, tmp_db):
        """When start_date is None, the result start_date must be ~7 years ago."""
        from data.ingest import ingest_universe
        from datetime import datetime, timedelta

        with self._patch_yf(_make_ohlcv_df(n_rows=3)), \
             self._patch_cache_miss(), self._patch_save_cache():
            result = ingest_universe("gold_etfs")

        expected_year = (datetime.now() - timedelta(days=7 * 365)).year
        actual_year = int(result["start_date"][:4])
        assert actual_year == expected_year

    def test_force_bypasses_cache(self, tmp_db, tmp_path):
        """When force=True, _load_cache should not be consulted."""
        from data.ingest import ingest_universe

        cache_calls = []
        download_calls = []

        def mock_load_cache(ticker, market_id=None):
            cache_calls.append(ticker)
            # Return data so we can confirm it's ignored when force=True
            return _make_ohlcv_df(n_rows=3)

        def mock_yf_download(ticker, start_str, end_str):
            download_calls.append(ticker)
            return _make_ohlcv_df(n_rows=3)

        with patch("data.ingest._load_cache", side_effect=mock_load_cache), \
             patch("data.ingest._download_via_yfinance", side_effect=mock_yf_download), \
             patch("data.ingest._save_cache"):
            ingest_universe("gold_etfs", start_date="2023-01-01", force=True)

        # force=True: _load_cache must NOT be called; yfinance must be called
        assert len(cache_calls) == 0, f"Cache should not be consulted with force=True, got: {cache_calls}"
        assert len(download_calls) > 0, "yfinance should be called when force=True"


# ── ingest_all_etf_universes() tests ─────────────────────────────────────────

class TestIngestAllEtfUniverses:
    """Unit tests for data.ingest.ingest_all_etf_universes()."""

    def test_calls_ingest_universe_for_each_etf_universe(self, tmp_db):
        """ingest_all_etf_universes must call ingest_universe once per ETF universe."""
        from data.ingest import ingest_all_etf_universes
        from universe.definitions import list_universes

        expected_universes = [u for u in list_universes() if u != "sp500"]
        called_with = []

        def mock_ingest(universe_name, **kwargs):
            called_with.append(universe_name)
            return {
                "universe": universe_name,
                "tickers_fetched": ["FAKE"],
                "tickers_failed": [],
                "rows_written": 5,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            }

        with patch("data.ingest.ingest_universe", side_effect=mock_ingest):
            result = ingest_all_etf_universes(start_date="2023-01-01")

        assert sorted(called_with) == sorted(expected_universes)

    def test_sp500_excluded(self, tmp_db):
        """sp500 must NOT be included in the ingested universe list."""
        from data.ingest import ingest_all_etf_universes

        called_with = []

        def mock_ingest(universe_name, **kwargs):
            called_with.append(universe_name)
            return {
                "universe": universe_name,
                "tickers_fetched": [],
                "tickers_failed": [],
                "rows_written": 0,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            }

        with patch("data.ingest.ingest_universe", side_effect=mock_ingest):
            ingest_all_etf_universes()

        assert "sp500" not in called_with

    def test_aggregate_rows_written_is_sum(self, tmp_db):
        """total_rows_written must be the sum across all universe calls."""
        from data.ingest import ingest_all_etf_universes
        from universe.definitions import list_universes

        etf_universes = [u for u in list_universes() if u != "sp500"]
        per_universe_rows = 42

        def mock_ingest(universe_name, **kwargs):
            return {
                "universe": universe_name,
                "tickers_fetched": ["A"],
                "tickers_failed": [],
                "rows_written": per_universe_rows,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            }

        with patch("data.ingest.ingest_universe", side_effect=mock_ingest):
            result = ingest_all_etf_universes()

        assert result["total_rows_written"] == per_universe_rows * len(etf_universes)

    def test_failed_tickers_deduplicated(self, tmp_db):
        """Cross-universe failed tickers should appear only once in total_tickers_failed."""
        from data.ingest import ingest_all_etf_universes

        def mock_ingest(universe_name, **kwargs):
            return {
                "universe": universe_name,
                "tickers_fetched": [],
                "tickers_failed": ["GLD"],  # GLD fails in every universe
                "rows_written": 0,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            }

        with patch("data.ingest.ingest_universe", side_effect=mock_ingest):
            result = ingest_all_etf_universes()

        assert result["total_tickers_failed"].count("GLD") == 1

    def test_results_keyed_by_universe_name(self, tmp_db):
        """The 'results' sub-dict must be keyed by universe name."""
        from data.ingest import ingest_all_etf_universes
        from universe.definitions import list_universes

        etf_universes = [u for u in list_universes() if u != "sp500"]

        def mock_ingest(universe_name, **kwargs):
            return {
                "universe": universe_name,
                "tickers_fetched": [],
                "tickers_failed": [],
                "rows_written": 0,
                "start_date": "2023-01-01",
                "end_date": "2023-12-31",
            }

        with patch("data.ingest.ingest_universe", side_effect=mock_ingest):
            result = ingest_all_etf_universes()

        for u in etf_universes:
            assert u in result["results"]


# ── get_universe_data() tests ─────────────────────────────────────────────────

class TestGetUniverseData:
    """Tests for the updated db.atlas_db.get_universe_data() function."""

    def test_static_universe_uses_ticker_list(self, tmp_db):
        """get_universe_data for a static universe must query by ticker list, not universe col."""
        from db.atlas_db import get_db, get_universe_data

        # Write GLD with universe='commodity_etfs'
        with get_db() as db:
            db.execute(
                """INSERT OR REPLACE INTO ohlcv
                   (ticker, date, open, high, low, close, adj_close, volume, universe, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("GLD", "2023-01-03", 170.0, 172.0, 169.0, 171.0, None, 5000000, "commodity_etfs", "yfinance"),
            )
            # Also write IAU, GDX, GDXJ for gold_etfs universe
            for ticker in ["IAU", "GDX", "GDXJ"]:
                db.execute(
                    """INSERT OR REPLACE INTO ohlcv
                       (ticker, date, open, high, low, close, adj_close, volume, universe, source)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (ticker, "2023-01-03", 30.0, 31.0, 29.0, 30.5, None, 1000000, "gold_etfs", "yfinance"),
                )

        # get_universe_data("gold_etfs") should return GLD even though it's
        # tagged as commodity_etfs in the DB — because definitions.py lists it
        data = get_universe_data("gold_etfs", start_date="2023-01-01")
        assert "GLD" in data
        assert not data["GLD"].empty

    def test_sp500_falls_back_to_universe_column(self, tmp_db):
        """get_universe_data('sp500') falls back to WHERE universe=? query."""
        from db.atlas_db import get_db, get_universe_data

        with get_db() as db:
            db.execute(
                """INSERT OR REPLACE INTO ohlcv
                   (ticker, date, open, high, low, close, adj_close, volume, universe, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                ("AAPL", "2023-01-03", 130.0, 132.0, 129.0, 131.0, None, 80000000, "sp500", "alpaca"),
            )

        data = get_universe_data("sp500")
        assert "AAPL" in data
