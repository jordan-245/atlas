"""Tests for data.macro_query — macro_indicators loader helpers with TTL cache.

All tests use a tmp SQLite DB with seed data; no production DB writes.
"""
from __future__ import annotations

import sqlite3
import time
from unittest.mock import patch

import pandas as pd
import pytest

import db.atlas_db as _adb
from data.macro_query import (
    _MACRO_COL_ALLOWLIST,
    clear_cache,
    get_macro_indicators_cols,
    get_vix_term_structure,
)
from data import macro_query as _mod


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
    """Tiny isolated DB with macro_indicators rows (5 dates, last has NULL vix)."""
    db_path = tmp_path / "test_macro.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE macro_indicators (
            date TEXT PRIMARY KEY,
            vix REAL, vix3m REAL, vix_term_ratio REAL,
            yield_10y REAL, yield_2y REAL, yield_3m REAL,
            yield_curve_10y2y REAL, yield_curve_10y3m REAL,
            credit_oas REAL, dxy REAL, gold REAL, copper REAL,
            gold_copper_ratio REAL, fed_funds REAL,
            unemployment_claims INTEGER,
            spy_close REAL, spy_200dma REAL,
            spy_above_200dma INTEGER, spy_200dma_slope REAL,
            updated_at TEXT
        )"""
    )
    rows = [
        ("2024-01-02", 14.5, 15.0, 0.967, 4.0, 4.5, 5.2, -0.5, -1.2,
         1.2, 103.0, 2050.0, 3.8, 540.0, 5.33, 220000,
         470.0, 455.0, 1, 0.05, "2024-01-02"),
        ("2024-01-03", 13.8, 14.5, 0.952, 4.1, 4.6, 5.3, -0.5, -1.2,
         1.1, 103.5, 2060.0, 3.9, 545.0, 5.33, 215000,
         472.0, 456.0, 1, 0.06, "2024-01-03"),
        ("2024-01-04", 15.2, 14.8, 1.027, 3.9, 4.4, 5.1, -0.5, -1.2,
         1.3, 102.5, 2040.0, 3.7, 535.0, 5.33, 225000,
         468.0, 455.0, 1, 0.04, "2024-01-04"),
        ("2024-01-05", 16.0, 14.5, 1.103, 3.8, 4.3, 5.0, -0.5, -1.2,
         1.4, 102.0, 2035.0, 3.6, 532.0, 5.33, 230000,
         465.0, 454.0, 1, 0.03, "2024-01-05"),
        # Row with NULL vix/vix3m to test NULL filtering
        ("2024-01-08", None, None, None, 3.7, 4.2, 4.9, -0.5, -1.2,
         1.5, 101.0, 2030.0, 3.5, 530.0, 5.33, 235000,
         462.0, 453.0, 1, 0.02, "2024-01-08"),
    ]
    conn.executemany(
        "INSERT INTO macro_indicators VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(_adb, "_db_path_override", str(db_path))
    yield str(db_path)


# ---------------------------------------------------------------------------
# Allowlist
# ---------------------------------------------------------------------------

class TestAllowlist:
    def test_date_is_in_allowlist(self):
        assert "date" in _MACRO_COL_ALLOWLIST

    def test_vix_is_in_allowlist(self):
        assert "vix" in _MACRO_COL_ALLOWLIST

    def test_vix3m_is_in_allowlist(self):
        assert "vix3m" in _MACRO_COL_ALLOWLIST


# ---------------------------------------------------------------------------
# get_macro_indicators_cols
# ---------------------------------------------------------------------------

class TestGetMacroIndicatorsCols:
    def test_date_always_in_result(self, seed_db):
        """date is prepended automatically even when not in cols."""
        df = get_macro_indicators_cols(["vix"], "2024-01-08", 10, cache_ttl=0)
        assert "date" in df.columns

    def test_requested_col_in_result(self, seed_db):
        df = get_macro_indicators_cols(["vix", "vix3m"], "2024-01-08", 10, cache_ttl=0)
        assert "vix" in df.columns
        assert "vix3m" in df.columns

    def test_rows_returned_newest_first(self, seed_db):
        """ORDER BY date DESC: first row should be latest date."""
        df = get_macro_indicators_cols(["vix"], "2024-01-08", 10, cache_ttl=0)
        assert df.iloc[0]["date"] > df.iloc[-1]["date"]

    def test_limit_respected(self, seed_db):
        df = get_macro_indicators_cols(["vix"], "2024-01-08", 2, cache_ttl=0)
        assert len(df) <= 2

    def test_end_date_upper_bound(self, seed_db):
        """Rows strictly after end_date must not appear."""
        df = get_macro_indicators_cols(["vix"], "2024-01-03", 10, cache_ttl=0)
        dates = df["date"].tolist()
        assert all(d <= "2024-01-03" for d in dates)

    def test_invalid_column_raises_value_error(self, seed_db):
        with pytest.raises(ValueError, match="allowlist"):
            get_macro_indicators_cols(["bad_col"], "2024-01-08", 10, cache_ttl=0)

    def test_sql_injection_attempt_blocked(self, seed_db):
        with pytest.raises(ValueError, match="allowlist"):
            get_macro_indicators_cols(
                ["vix; DROP TABLE macro_indicators--"],
                "2024-01-08", 10, cache_ttl=0,
            )

    def test_cache_hit_skips_db(self, seed_db):
        df1 = get_macro_indicators_cols(["vix"], "2024-01-08", 10, cache_ttl=60)
        with patch("data.macro_query.get_db") as mock_db:
            df2 = get_macro_indicators_cols(["vix"], "2024-01-08", 10, cache_ttl=60)
            mock_db.assert_not_called()
        pd.testing.assert_frame_equal(df1, df2)

    def test_cache_bypass_with_zero_ttl(self, seed_db):
        """cache_ttl=0 must always hit the DB even if cache is populated."""
        key = ("get_macro_indicators_cols", ("date", "vix"), "2024-01-08", 10)
        fake_df = pd.DataFrame({"date": ["fake"], "vix": [999.0]})
        _mod._cache[key] = (time.time(), fake_df)

        result = get_macro_indicators_cols(["vix"], "2024-01-08", 10, cache_ttl=0)
        # Real DB has real rows, not the 1-row fake
        assert 999.0 not in result["vix"].values


# ---------------------------------------------------------------------------
# get_vix_term_structure
# ---------------------------------------------------------------------------

class TestGetVixTermStructure:
    def test_returns_correct_columns(self, seed_db):
        df = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=0)
        assert list(df.columns) == ["date", "vix", "vix3m"]

    def test_null_rows_excluded(self, seed_db):
        """Rows with NULL vix or vix3m must not appear."""
        df = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=0)
        assert not df["vix"].isna().any()
        assert not df["vix3m"].isna().any()
        assert "2024-01-08" not in df["date"].values

    def test_ordered_ascending_by_date(self, seed_db):
        df = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=0)
        dates = df["date"].tolist()
        assert dates == sorted(dates)

    def test_date_range_inclusive_bounds(self, seed_db):
        df = get_vix_term_structure("2024-01-03", "2024-01-04", cache_ttl=0)
        assert set(df["date"]) == {"2024-01-03", "2024-01-04"}

    def test_empty_range_returns_empty_df(self, seed_db):
        df = get_vix_term_structure("2020-01-01", "2020-01-02", cache_ttl=0)
        assert df.empty

    def test_cache_hit_skips_db(self, seed_db):
        df1 = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=60)
        with patch("data.macro_query.get_db") as mock_db:
            df2 = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=60)
            mock_db.assert_not_called()
        pd.testing.assert_frame_equal(df1, df2)

    def test_ttl_expiry_triggers_fresh_fetch(self, seed_db):
        df1 = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=3600)
        key = ("get_vix_term_structure", "2024-01-02", "2024-01-08")
        ts, cached_df = _mod._cache[key]
        _mod._cache[key] = (0.0, cached_df)

        df2 = get_vix_term_structure("2024-01-02", "2024-01-08", cache_ttl=3600)
        pd.testing.assert_frame_equal(df1, df2)
        assert _mod._cache[key][0] > 0.0
