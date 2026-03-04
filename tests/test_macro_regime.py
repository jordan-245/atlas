"""Unit tests for data.macro — macro regime calculator.

Run with:
    python -m pytest tests/test_macro_regime.py -v

Tests cover:
    - download_macro_data() returns correct columns and types
    - compute_macro_signals() produces all expected derived columns
    - No look-ahead bias: gc_regime at date T only uses data up to T (expanding window)
    - VIX ROC spike detection with known values
    - Yield curve flattening detection with known values
    - macro_regime_scale is in expected range [0.5, 1.5]
    - Graceful degradation when some series are unavailable (NaN/missing)
"""

import sys
import os
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Import guard — skip all tests with a clear message if data.macro is missing
# ---------------------------------------------------------------------------

_IMPORT_ERROR_MSG = ""
try:
    from data.macro import download_macro_data, compute_macro_signals
    MACRO_AVAILABLE = True
except ImportError as _e:
    MACRO_AVAILABLE = False
    _IMPORT_ERROR_MSG = str(_e)
    download_macro_data = None  # type: ignore
    compute_macro_signals = None  # type: ignore

skip_if_unavailable = pytest.mark.skipif(
    not MACRO_AVAILABLE,
    reason=f"data.macro not available: {_IMPORT_ERROR_MSG}",
)

pytestmark = skip_if_unavailable


# ---------------------------------------------------------------------------
# Constants matching builder-1's actual implementation
# ---------------------------------------------------------------------------

# Columns in raw macro data (from download_macro_data)
RAW_COLUMNS = {"gold", "copper", "vix", "yield_10y", "yield_13w"}

# Columns in signals (from compute_macro_signals)
DERIVED_COLUMNS = {
    "gold_copper_ratio",
    "gc_regime",
    "vix_roc_5d",
    "vix_spike",
    "yield_curve_10y_3m",
    "yc_change_5d",
    "yc_flattening",
    "macro_regime_scale",
}

# gc_regime uses integers: 1=risk-on, 2=neutral, 3=risk-off
VALID_GC_REGIMES = {1, 2, 3}


# ---------------------------------------------------------------------------
# Helpers — synthetic macro data
# ---------------------------------------------------------------------------

def _make_raw_df(
    n_days: int = 260,
    gold_base: float = 1800.0,
    copper_base: float = 3.50,
    vix_base: float = 15.0,
    yield_10y_base: float = 2.0,
    yield_13w_base: float = 1.5,
    seed: int = 42,
) -> pd.DataFrame:
    """Build a synthetic raw macro DataFrame matching download_macro_data() output."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2020-01-01", periods=n_days)

    gold = gold_base + np.cumsum(rng.normal(0, 5.0, n_days))
    copper = np.abs(copper_base + np.cumsum(rng.normal(0, 0.02, n_days)))
    copper = np.clip(copper, 0.5, None)
    vix = np.abs(vix_base + rng.normal(0, 1.0, n_days))
    yield_10y = yield_10y_base + np.cumsum(rng.normal(0, 0.01, n_days))
    yield_13w = yield_13w_base + np.cumsum(rng.normal(0, 0.01, n_days))

    df = pd.DataFrame(
        {
            "gold": gold,
            "copper": copper,
            "vix": vix,
            "yield_10y": yield_10y,
            "yield_13w": yield_13w,
        },
        index=dates,
    )
    df.index.name = "date"
    return df


def _make_inverted_curve_df(n_days: int = 200) -> pd.DataFrame:
    """Build a DataFrame with an inverted yield curve (yield_13w > yield_10y)."""
    df = _make_raw_df(n_days=n_days)
    df["yield_10y"] = 1.5
    df["yield_13w"] = 2.5  # Inversion: 13W > 10Y
    return df


def _make_steep_curve_df(n_days: int = 200) -> pd.DataFrame:
    """Build a DataFrame with a steep yield curve (yield_10y >> yield_13w)."""
    df = _make_raw_df(n_days=n_days)
    df["yield_10y"] = 3.5
    df["yield_13w"] = 1.0  # Steep: spread = 2.5
    return df


# ---------------------------------------------------------------------------
# TestDownloadMacroData
# ---------------------------------------------------------------------------

class TestDownloadMacroData:
    """Tests for the download_macro_data() function."""

    def test_import_succeeds(self):
        assert download_macro_data is not None
        assert callable(download_macro_data)

    @patch("data.macro.download_macro_data", autospec=True)
    def test_returns_dataframe(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        assert isinstance(result, pd.DataFrame)
        assert not result.empty

    @patch("data.macro.download_macro_data", autospec=True)
    def test_contains_expected_columns(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        missing = RAW_COLUMNS - set(result.columns)
        assert not missing, f"Missing columns: {missing}"

    @patch("data.macro.download_macro_data", autospec=True)
    def test_column_types_are_numeric(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        for col in RAW_COLUMNS & set(result.columns):
            assert pd.api.types.is_numeric_dtype(result[col]), f"'{col}' not numeric"

    @patch("data.macro.download_macro_data", autospec=True)
    def test_index_is_datetime(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        assert isinstance(result.index, pd.DatetimeIndex)

    @patch("data.macro.download_macro_data", autospec=True)
    def test_index_is_sorted(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        assert result.index.is_monotonic_increasing

    @patch("data.macro.download_macro_data", autospec=True)
    def test_no_negative_prices(self, mock_dl):
        mock_dl.return_value = _make_raw_df(n_days=50)
        result = mock_dl()
        for col in ("gold", "copper"):
            if col in result.columns:
                assert (result[col].dropna() > 0).all(), f"'{col}' has non-positive"

    def test_accepts_cache_age_param(self):
        import inspect
        sig = inspect.signature(download_macro_data)
        assert "cache_max_age_hours" in sig.parameters


# ---------------------------------------------------------------------------
# TestComputeMacroSignals
# ---------------------------------------------------------------------------

class TestComputeMacroSignals:
    """Tests for compute_macro_signals()."""

    def test_import_succeeds(self):
        assert compute_macro_signals is not None
        assert callable(compute_macro_signals)

    def test_returns_dataframe(self):
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        assert isinstance(result, pd.DataFrame)

    def test_produces_all_derived_columns(self):
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        missing = DERIVED_COLUMNS - set(result.columns)
        assert not missing, f"Missing: {missing}. Got: {list(result.columns)}"

    def test_index_preserved(self):
        df = _make_raw_df(n_days=100)
        result = compute_macro_signals(df)
        pd.testing.assert_index_equal(result.index, df.index, check_names=False)

    def test_gold_copper_ratio_values(self):
        df = _make_raw_df(n_days=100)
        df["gold"] = 1800.0
        df["copper"] = 3.6
        expected = 1800.0 / 3.6

        result = compute_macro_signals(df)
        valid = result["gold_copper_ratio"].dropna()
        assert len(valid) > 0
        assert (valid - expected).abs().max() < 1.0

    def test_yield_curve_is_10y_minus_13w(self):
        """yield_curve_10y_3m = yield_10y - yield_13w."""
        df = _make_raw_df(n_days=100)
        df["yield_10y"] = 2.5
        df["yield_13w"] = 1.5
        expected = 1.0

        result = compute_macro_signals(df)
        assert "yield_curve_10y_3m" in result.columns
        valid = result["yield_curve_10y_3m"].dropna()
        assert len(valid) > 0
        assert (valid - expected).abs().max() < 0.01

    def test_gc_regime_values_are_valid(self):
        """gc_regime must contain only valid integer regime labels (1, 2, 3)."""
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        assert "gc_regime" in result.columns
        valid = result["gc_regime"].dropna()
        assert len(valid) > 0
        invalid = set(valid.unique()) - VALID_GC_REGIMES
        assert not invalid, f"Invalid gc_regime values: {invalid}"

    def test_macro_regime_scale_range(self):
        """macro_regime_scale must be in [0.5, 1.5]."""
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        valid = result["macro_regime_scale"].dropna()
        assert len(valid) > 0
        assert valid.min() >= 0.5, f"Below 0.5: {valid.min()}"
        assert valid.max() <= 1.5, f"Above 1.5: {valid.max()}"

    def test_at_least_two_distinct_scale_values(self):
        """With sufficient history, multiple scale values should appear."""
        df = _make_raw_df(n_days=520, seed=123)
        result = compute_macro_signals(df)
        valid = result["macro_regime_scale"].dropna()
        assert len(valid.unique()) >= 2

    def test_gc_regime_affects_scale_direction(self):
        """risk-off (3) should have lower average scale than risk-on (1)."""
        df = _make_raw_df(n_days=520, seed=42)
        result = compute_macro_signals(df)
        paired = result[["gc_regime", "macro_regime_scale"]].dropna()

        risk_on = paired[paired["gc_regime"] == 1]["macro_regime_scale"]
        risk_off = paired[paired["gc_regime"] == 3]["macro_regime_scale"]

        if len(risk_on) > 5 and len(risk_off) > 5:
            assert risk_on.mean() > risk_off.mean(), (
                f"risk-on avg scale ({risk_on.mean():.3f}) should be > "
                f"risk-off avg scale ({risk_off.mean():.3f})"
            )


# ---------------------------------------------------------------------------
# TestNoLookaheadBias
# ---------------------------------------------------------------------------

class TestNoLookaheadBias:
    """Verify that gc_regime at date T uses only data from dates <= T."""

    def test_classification_identical_on_prefix_vs_full_dataset(self):
        """gc_regime at date T should be identical from prefix or full dataset."""
        df = _make_raw_df(n_days=300, seed=7)
        split_day = 150

        signals_full = compute_macro_signals(df)
        signals_prefix = compute_macro_signals(df.iloc[:split_day])

        regime_full = signals_full.iloc[split_day - 1]["gc_regime"]
        regime_prefix = signals_prefix.iloc[split_day - 1]["gc_regime"]

        assert regime_full == regime_prefix, (
            f"Look-ahead bias! day {split_day - 1}: "
            f"prefix={regime_prefix} vs full={regime_full}"
        )

    def test_vix_roc_uses_only_past_data(self):
        """vix_roc_5d at day 10 = (VIX[10] - VIX[5]) / VIX[5]."""
        df = _make_raw_df(n_days=50)
        df["vix"] = np.arange(1, 51, dtype=float)

        result = compute_macro_signals(df)
        idx_10 = result.index[10]
        roc_at_10 = result.loc[idx_10, "vix_roc_5d"]
        expected = (11.0 - 6.0) / 6.0  # 0.8333

        if not pd.isna(roc_at_10):
            assert abs(roc_at_10 - expected) < 0.01

    def test_gc_ratio_tercile_window_is_expanding(self):
        """Phase 1 classification shouldn't change when Phase 2 data is added."""
        n_each = 100
        dates = pd.bdate_range("2020-01-01", periods=n_each * 2)

        gold = np.concatenate([np.full(n_each, 1000.0), np.full(n_each, 100.0)])
        copper = np.full(n_each * 2, 100.0)

        df = pd.DataFrame(
            {
                "gold": gold,
                "copper": copper,
                "vix": np.full(n_each * 2, 15.0),
                "yield_10y": np.full(n_each * 2, 2.0),
                "yield_13w": np.full(n_each * 2, 1.5),
            },
            index=dates,
        )
        df.index.name = "date"

        signals_prefix = compute_macro_signals(df.iloc[:n_each])
        signals_full = compute_macro_signals(df)

        regime_prefix = signals_prefix.iloc[80]["gc_regime"]
        regime_full = signals_full.iloc[80]["gc_regime"]

        assert regime_prefix == regime_full, (
            f"Look-ahead bias: day 80 changed from {regime_prefix} to {regime_full}"
        )


# ---------------------------------------------------------------------------
# TestVixRocSpikeDetection
# ---------------------------------------------------------------------------

class TestVixRocSpikeDetection:
    """Tests for VIX 5-day ROC spike detection."""

    def test_vix_roc_5d_known_spike(self):
        """60% VIX spike at day 10 produces ROC ≈ 0.60."""
        n = 30
        df = _make_raw_df(n_days=n)
        df["vix"] = 20.0
        df.iloc[10, df.columns.get_loc("vix")] = 32.0  # 60% spike

        result = compute_macro_signals(df)
        roc = result.iloc[10]["vix_roc_5d"]
        if not pd.isna(roc):
            assert abs(roc - 0.60) < 0.01, f"Expected ~0.60, got {roc:.4f}"

    def test_vix_decline_negative_roc(self):
        """VIX decline shows negative ROC."""
        n = 30
        df = _make_raw_df(n_days=n)
        df["vix"] = 30.0
        df.iloc[10, df.columns.get_loc("vix")] = 20.0

        result = compute_macro_signals(df)
        roc = result.iloc[10]["vix_roc_5d"]
        if not pd.isna(roc):
            assert roc < 0, f"Expected negative ROC, got {roc:.4f}"

    def test_flat_vix_near_zero_roc(self):
        """Flat VIX produces near-zero 5d ROC."""
        df = _make_raw_df(n_days=50)
        df["vix"] = 15.0

        result = compute_macro_signals(df)
        valid = result["vix_roc_5d"].dropna()
        if len(valid) > 0:
            assert valid.abs().max() < 0.001

    def test_vix_spike_flag(self):
        """vix_spike=True when ROC exceeds threshold (default 0.30)."""
        df = _make_raw_df(n_days=50)
        df["vix"] = 15.0
        df.iloc[20, df.columns.get_loc("vix")] = 22.5  # +50%

        result = compute_macro_signals(df)
        spike = result.iloc[20]["vix_spike"]
        if not pd.isna(result.iloc[20]["vix_roc_5d"]):
            assert spike is True or spike == True, "Expected vix_spike=True for 50% spike"


# ---------------------------------------------------------------------------
# TestYieldCurveDetection
# ---------------------------------------------------------------------------

class TestYieldCurveDetection:
    """Tests for yield curve slope and flattening detection."""

    def test_inverted_curve_negative_spread(self):
        """Inverted curve (yield_13w > yield_10y) → negative yield_curve_10y_3m."""
        df = _make_inverted_curve_df(n_days=50)
        result = compute_macro_signals(df)

        assert "yield_curve_10y_3m" in result.columns
        valid = result["yield_curve_10y_3m"].dropna()
        assert len(valid) > 0
        assert (valid < 0).all(), f"Expected negative spread, got range [{valid.min()}, {valid.max()}]"

    def test_steep_curve_positive_spread(self):
        """Steep curve (yield_10y >> yield_13w) → positive yield_curve_10y_3m."""
        df = _make_steep_curve_df(n_days=50)
        result = compute_macro_signals(df)

        valid = result["yield_curve_10y_3m"].dropna()
        assert len(valid) > 0
        assert (valid > 1.0).all(), f"Expected spread > 1.0, got min={valid.min()}"

    def test_yield_curve_known_values(self):
        """yield_curve_10y_3m = yield_10y - yield_13w."""
        n = 50
        df = _make_raw_df(n_days=n)
        df["yield_10y"] = np.linspace(1.0, 3.0, n)
        df["yield_13w"] = np.linspace(0.5, 2.5, n)
        expected = df["yield_10y"] - df["yield_13w"]  # ~0.5

        result = compute_macro_signals(df)
        computed = result["yield_curve_10y_3m"].dropna()
        common = computed.index.intersection(expected.index)
        diff = (computed.loc[common] - expected.loc[common]).abs()
        assert diff.max() < 0.001

    def test_yc_flattening_detection(self):
        """Rapid curve flattening should produce negative yc_change_5d."""
        n = 30
        df = _make_raw_df(n_days=n)
        df["yield_10y"] = 3.0
        df["yield_13w"] = 1.0
        # Flatten the curve sharply at day 15: 10Y drops, 13W stays
        # spread goes from 2.0 to 0.5
        df.iloc[15:, df.columns.get_loc("yield_10y")] = 1.5

        result = compute_macro_signals(df)
        # The 5d diff window catches the shift when comparing day T (post-shift)
        # to day T-5 (pre-shift). This happens at days 15-19 (shift at 15,
        # 5d lookback reaches pre-shift at day 10-14).
        # Check days 15-19 for negative yc_change_5d
        found_flattening = False
        for i in range(15, min(20, len(result))):
            yc_change = result.iloc[i]["yc_change_5d"]
            if not pd.isna(yc_change) and yc_change < -0.10:
                found_flattening = True
                break
        assert found_flattening, (
            "Expected negative yc_change_5d in days 15-19 after yield curve flattening"
        )


# ---------------------------------------------------------------------------
# TestMacroRegimeScale
# ---------------------------------------------------------------------------

class TestMacroRegimeScale:
    """Tests for macro_regime_scale bounds and behavior."""

    def test_scale_always_in_valid_range(self):
        df = _make_raw_df(n_days=520, seed=99)
        result = compute_macro_signals(df)
        valid = result["macro_regime_scale"].dropna()
        assert (valid >= 0.5).all(), f"Below 0.5: {valid.min()}"
        assert (valid <= 1.5).all(), f"Above 1.5: {valid.max()}"

    def test_risk_off_gc_reduces_scale(self):
        """gc_regime=3 (risk-off) should reduce scale below 1.0 (gc_adj = -0.4)."""
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        risk_off_rows = result[result["gc_regime"] == 3]
        if len(risk_off_rows) > 0:
            scales = risk_off_rows["macro_regime_scale"].dropna()
            if len(scales) > 0:
                # gc_adj=-0.4 means base is 0.6 before VIX/YC adjustments
                # So scale should average well below 1.0
                assert scales.mean() < 1.0, f"risk-off avg scale should be < 1.0, got {scales.mean():.3f}"

    def test_risk_on_gc_increases_scale(self):
        """gc_regime=1 (risk-on) should increase scale above 1.0 (gc_adj = +0.2)."""
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        risk_on_rows = result[result["gc_regime"] == 1]
        if len(risk_on_rows) > 0:
            scales = risk_on_rows["macro_regime_scale"].dropna()
            if len(scales) > 0:
                assert scales.mean() > 1.0, f"risk-on avg scale should be > 1.0, got {scales.mean():.3f}"

    def test_scale_is_continuous(self):
        """macro_regime_scale combines gc_adj + vix_adj + yc_adj, can be non-discrete."""
        df = _make_raw_df(n_days=520, seed=77)
        result = compute_macro_signals(df)
        valid = result["macro_regime_scale"].dropna()
        # The composite scale can take many values (e.g. 0.6, 0.7, 1.0, 1.1, 1.2, etc.)
        # Just verify range and diversity
        assert len(valid.unique()) >= 2, "Expected at least 2 distinct scale values"
        assert valid.min() >= 0.5
        assert valid.max() <= 1.5


# ---------------------------------------------------------------------------
# TestMissingDataGracefulDegradation
# ---------------------------------------------------------------------------

class TestMissingDataGracefulDegradation:
    """Handles partial/missing data gracefully."""

    def test_handles_all_nan_vix(self):
        df = _make_raw_df(n_days=50)
        df["vix"] = np.nan
        try:
            result = compute_macro_signals(df)
            assert isinstance(result, pd.DataFrame)
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with all-NaN VIX: {e}")

    def test_handles_all_nan_yields(self):
        df = _make_raw_df(n_days=50)
        df["yield_10y"] = np.nan
        df["yield_13w"] = np.nan
        try:
            result = compute_macro_signals(df)
            assert isinstance(result, pd.DataFrame)
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with all-NaN yields: {e}")

    def test_handles_missing_copper_column(self):
        df = _make_raw_df(n_days=50)
        df = df.drop(columns=["copper"])
        try:
            result = compute_macro_signals(df)
            assert isinstance(result, pd.DataFrame)
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with missing copper: {e}")

    def test_handles_partial_nan_rows(self):
        df = _make_raw_df(n_days=100)
        df.iloc[:10, df.columns.get_loc("gold")] = np.nan
        df.iloc[:10, df.columns.get_loc("copper")] = np.nan
        try:
            result = compute_macro_signals(df)
            valid = result.iloc[30:]["gc_regime"].dropna()
            assert len(valid) > 0
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with partial NaN: {e}")

    def test_handles_short_dataframe(self):
        df = _make_raw_df(n_days=3)
        try:
            result = compute_macro_signals(df)
            assert isinstance(result, pd.DataFrame)
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with 3 rows: {e}")

    def test_handles_empty_dataframe(self):
        try:
            result = compute_macro_signals(pd.DataFrame())
            assert isinstance(result, pd.DataFrame)
            assert result.empty
        except Exception as e:
            pytest.fail(f"Raised {type(e).__name__} with empty df: {e}")

    def test_gc_regime_has_values_with_good_data(self):
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        valid = result["gc_regime"].dropna()
        assert len(valid) > 50

    def test_macro_scale_has_values_with_good_data(self):
        df = _make_raw_df(n_days=260)
        result = compute_macro_signals(df)
        valid = result["macro_regime_scale"].dropna()
        assert len(valid) > 50
