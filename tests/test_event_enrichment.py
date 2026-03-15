"""Tests for inject_event_features() in backtest/enrichment.py.

Verifies that event proximity data is correctly stamped onto signal
features without filtering, rejecting, or mutating confidence values.

Run with:  python -m pytest tests/test_event_enrichment.py -v --tb=short
All tests are offline (no network calls) and complete in < 5 seconds.
"""
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backtest.enrichment import inject_event_features  # noqa: E402
from data.events import EventCalendar  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(ticker="AAPL", features=None):
    """Minimal signal-like object with a features dict."""
    sig = SimpleNamespace(
        ticker=ticker,
        strategy="test_strategy",
        direction="long",
        confidence=0.7,
        features=features if features is not None else {},
    )
    return sig


def _make_calendar_with_proximity(proximity_dict):
    """Return a mock EventCalendar whose get_event_proximity returns a fixed dict."""
    cal = MagicMock(spec=EventCalendar)
    cal.get_event_proximity.return_value = proximity_dict
    return cal


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def real_ec():
    """Real EventCalendar for integration-style tests."""
    return EventCalendar()


# ---------------------------------------------------------------------------
# No-op cases
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesNoOp:
    def test_none_calendar_is_noop(self):
        """When event_calendar is None, signals are untouched."""
        sig = _make_signal()
        original_features = dict(sig.features)
        inject_event_features([sig], date(2026, 3, 15), event_calendar=None)
        assert sig.features == original_features

    def test_empty_signals_list_is_noop(self):
        """Empty signals list returns cleanly without calling the calendar."""
        cal = MagicMock(spec=EventCalendar)
        inject_event_features([], date(2026, 3, 15), event_calendar=cal)
        cal.get_event_proximity.assert_not_called()

    def test_none_calendar_empty_signals_is_noop(self):
        """Both None calendar and empty signals → clean no-op."""
        inject_event_features([], date(2026, 3, 15), event_calendar=None)


# ---------------------------------------------------------------------------
# Feature injection — keys present
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesKeys:
    def test_all_expected_keys_injected(self):
        """All 5 expected keys are written into signal.features."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 8,
            "days_to_rebal": 20,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 1), event_calendar=cal)

        assert "days_to_fomc" in sig.features
        assert "days_to_cpi" in sig.features
        assert "days_to_nfp" in sig.features
        assert "is_opex_week" in sig.features
        assert "is_rebal_week" in sig.features

    def test_days_to_values_match_proximity(self):
        """days_to_* values are taken directly from get_event_proximity()."""
        proximity = {
            "days_to_fomc": 7,
            "days_to_cpi": 14,
            "days_to_nfp": 2,
            "days_to_opex": 12,
            "days_to_rebal": 30,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 1), event_calendar=cal)

        assert sig.features["days_to_fomc"] == 7
        assert sig.features["days_to_cpi"] == 14
        assert sig.features["days_to_nfp"] == 2

    def test_is_opex_week_true_when_opex_le_5(self):
        """is_opex_week is True when days_to_opex <= 5."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 4,  # within 5 days
            "days_to_rebal": 20,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 16), event_calendar=cal)
        assert sig.features["is_opex_week"] is True

    def test_is_opex_week_true_when_opex_eq_0(self):
        """is_opex_week is True on OPEX day itself (days_to_opex == 0)."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 0,
            "days_to_rebal": 20,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 20), event_calendar=cal)
        assert sig.features["is_opex_week"] is True

    def test_is_opex_week_false_when_opex_gt_5(self):
        """is_opex_week is False when days_to_opex > 5."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 10,  # far away
            "days_to_rebal": 20,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 10), event_calendar=cal)
        assert sig.features["is_opex_week"] is False

    def test_is_opex_week_false_when_opex_minus_one(self):
        """is_opex_week is False when days_to_opex == -1 (not found)."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": -1,
            "days_to_rebal": 20,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2028, 1, 1), event_calendar=cal)
        assert sig.features["is_opex_week"] is False

    def test_is_rebal_week_true_when_rebal_le_5(self):
        """is_rebal_week is True when days_to_rebal <= 5."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 15,
            "days_to_rebal": 3,  # within 5 days
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 17), event_calendar=cal)
        assert sig.features["is_rebal_week"] is True

    def test_is_rebal_week_false_when_rebal_gt_5(self):
        """is_rebal_week is False when days_to_rebal > 5."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 15,
            "days_to_rebal": 25,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 1), event_calendar=cal)
        assert sig.features["is_rebal_week"] is False

    def test_is_rebal_week_false_when_rebal_minus_one(self):
        """is_rebal_week is False when days_to_rebal == -1 (not found)."""
        proximity = {
            "days_to_fomc": -1,
            "days_to_cpi": -1,
            "days_to_nfp": -1,
            "days_to_opex": -1,
            "days_to_rebal": -1,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        sig = _make_signal()
        inject_event_features([sig], date(2030, 1, 1), event_calendar=cal)
        assert sig.features["is_rebal_week"] is False


# ---------------------------------------------------------------------------
# Multiple signals — all get same proximity values
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesMultipleSignals:
    def test_all_signals_receive_features(self):
        """All signals in the batch get identical proximity values."""
        proximity = {
            "days_to_fomc": 5,
            "days_to_cpi": 3,
            "days_to_nfp": 1,
            "days_to_opex": 4,
            "days_to_rebal": 4,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        signals = [_make_signal(ticker=t) for t in ["AAPL", "MSFT", "GOOG"]]
        inject_event_features(signals, date(2026, 3, 16), event_calendar=cal)

        for sig in signals:
            assert sig.features["days_to_fomc"] == 5
            assert sig.features["days_to_cpi"] == 3
            assert sig.features["days_to_nfp"] == 1
            assert sig.features["is_opex_week"] is True
            assert sig.features["is_rebal_week"] is True

    def test_calendar_queried_once_regardless_of_signal_count(self):
        """EventCalendar is called exactly once, not per-signal."""
        proximity = {
            "days_to_fomc": 10,
            "days_to_cpi": 5,
            "days_to_nfp": 3,
            "days_to_opex": 10,
            "days_to_rebal": 20,
            "is_opex_week": 0,
        }
        cal = _make_calendar_with_proximity(proximity)
        signals = [_make_signal(ticker=t) for t in ["AAPL", "MSFT", "GOOG", "AMZN"]]
        inject_event_features(signals, date(2026, 3, 1), event_calendar=cal)
        cal.get_event_proximity.assert_called_once()


# ---------------------------------------------------------------------------
# Signals with None features — auto-initialised
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesNoneFeatures:
    def test_none_features_initialised(self):
        """A signal with features=None gets a new dict created."""
        sig = SimpleNamespace(
            ticker="AAPL",
            features=None,
        )
        proximity = {
            "days_to_fomc": 5,
            "days_to_cpi": 5,
            "days_to_nfp": 5,
            "days_to_opex": 5,
            "days_to_rebal": 5,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        inject_event_features([sig], date(2026, 3, 15), event_calendar=cal)
        assert isinstance(sig.features, dict)
        assert "days_to_fomc" in sig.features

    def test_missing_features_attr_initialised(self):
        """A signal object without a features attribute gets one created."""
        # SimpleNamespace without features attribute
        sig = SimpleNamespace(ticker="AAPL")
        proximity = {
            "days_to_fomc": 5,
            "days_to_cpi": 5,
            "days_to_nfp": 5,
            "days_to_opex": 5,
            "days_to_rebal": 5,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        inject_event_features([sig], date(2026, 3, 15), event_calendar=cal)
        assert hasattr(sig, "features")
        assert "days_to_fomc" in sig.features


# ---------------------------------------------------------------------------
# Existing features are preserved — no clobber
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesPreservesExistingFeatures:
    def test_existing_features_not_overwritten(self):
        """Pre-existing keys in features that aren't event keys are left intact."""
        sig = _make_signal(features={"rsi_14": 0.65, "volume_ratio": 1.2})
        proximity = {
            "days_to_fomc": 7,
            "days_to_cpi": 3,
            "days_to_nfp": 1,
            "days_to_opex": 4,
            "days_to_rebal": 10,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        inject_event_features([sig], date(2026, 3, 16), event_calendar=cal)
        # Event features injected
        assert sig.features["days_to_fomc"] == 7
        # Pre-existing features preserved
        assert sig.features["rsi_14"] == 0.65
        assert sig.features["volume_ratio"] == 1.2

    def test_confidence_not_modified(self):
        """inject_event_features never modifies signal.confidence."""
        sig = _make_signal()
        original_confidence = sig.confidence
        proximity = {
            "days_to_fomc": 7,
            "days_to_cpi": 3,
            "days_to_nfp": 1,
            "days_to_opex": 4,
            "days_to_rebal": 10,
            "is_opex_week": 1,
        }
        cal = _make_calendar_with_proximity(proximity)
        inject_event_features([sig], date(2026, 3, 16), event_calendar=cal)
        assert sig.confidence == original_confidence


# ---------------------------------------------------------------------------
# Integration: real EventCalendar
# ---------------------------------------------------------------------------

class TestInjectEventFeaturesIntegration:
    def test_real_calendar_injects_valid_values(self, real_ec):
        """End-to-end: real EventCalendar injects reasonable values."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 15), event_calendar=real_ec)

        # days_to_* are integers and non-negative (or -1 for not found)
        for key in ("days_to_fomc", "days_to_cpi", "days_to_nfp"):
            val = sig.features[key]
            assert isinstance(val, int), f"{key} should be int, got {type(val)}"
            assert val >= 0 or val == -1, f"{key}={val} should be >= 0 or -1"

        # is_opex_week / is_rebal_week are booleans
        assert isinstance(sig.features["is_opex_week"], bool)
        assert isinstance(sig.features["is_rebal_week"], bool)

    def test_real_calendar_opex_week_march_2026(self, real_ec):
        """2026-03-16 is 4 days before OPEX (2026-03-20), so is_opex_week=True."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 16), event_calendar=real_ec)
        assert sig.features["is_opex_week"] is True

    def test_real_calendar_not_opex_week_after_march_opex(self, real_ec):
        """2026-03-26 is 6+ days after OPEX (2026-03-20), so is_opex_week=False."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 26), event_calendar=real_ec)
        assert sig.features["is_opex_week"] is False

    def test_real_calendar_rebal_week_march_2026(self, real_ec):
        """2026-03-15 is 5 days before Q1 REBAL (2026-03-20), so is_rebal_week=True."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 15), event_calendar=real_ec)
        assert sig.features["is_rebal_week"] is True

    def test_real_calendar_not_rebal_week_early_march(self, real_ec):
        """2026-03-01 is 19 days before Q1 REBAL (2026-03-20), so is_rebal_week=False."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 1), event_calendar=real_ec)
        assert sig.features["is_rebal_week"] is False

    def test_real_calendar_fomc_day_zero(self, real_ec):
        """On FOMC day (2026-03-18), days_to_fomc should be 0."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 18), event_calendar=real_ec)
        assert sig.features["days_to_fomc"] == 0

    def test_real_calendar_nfp_first_friday_march_2026(self, real_ec):
        """On March 2026 NFP day (2026-03-06), days_to_nfp should be 0."""
        sig = _make_signal()
        inject_event_features([sig], date(2026, 3, 6), event_calendar=real_ec)
        assert sig.features["days_to_nfp"] == 0
