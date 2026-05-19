"""Regression: sma200_filter rejections must surface in plan.rejected_entries.

Without this observability, operators cannot validate the falling-knife guard
shipped 2026-05-19 is actually doing anything.

Design notes vs spec:
  1. _make_falling_knife_df uses close.iloc[-1] *= 0.70 (single-day crash) rather
     than the spec's close.iloc[-5:] *= 0.92.  Reason: calc_zscore uses ddof=1
     rolling std; with a pure linear decline the endpoint z-score is mathematically
     capped at ~1.65 (n=20 window), so ×0.92 over 5 days still gives z≈-1.75 which
     doesn't clear the -2.0 threshold.  A single 30%-day drop raises z to ≈ -4.

  2. test_disabled_strategies_logged_in_get_strategies uses caplog.at_level(INFO)
     (root-level capture) rather than logger="cli".  Reason: cli.py's logger is
     logging.getLogger("atlas.cli") — a different Python logger hierarchy than
     "cli", so the logger= kwarg would be a no-op, leaving root at WARNING and
     dropping INFO records from caplog.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


# ---------------------------------------------------------------------------
# Test data builder
# ---------------------------------------------------------------------------

def _make_falling_knife_df(n_days: int = 250) -> pd.DataFrame:
    """Build OHLCV where price is below sma200 and RSI is oversold + zscore extreme.

    Triggers entry conditions on every gate EXCEPT sma200 → filter must reject.

    Pattern:
      - Days 1..n_days/2: rising from 100 → 130 (builds SMA-200 well above current price)
      - Days n_days/2+1..n_days-1: falling from 130 → 78 (RSI goes deeply oversold)
      - Last day: single −30% crash that pushes zscore below −2.0
        (A gradual −8% over 5 days only gives z≈−1.75 with ddof=1; one large drop
        creates a true outlier in the 20-day window → z≈−4.0)
    """
    dates = pd.date_range("2025-01-01", periods=n_days, freq="B")
    # "High then collapse" pattern: rises then falls 40% from peak
    half = n_days // 2
    base = np.linspace(100, 130, half).tolist() + np.linspace(130, 78, n_days - half).tolist()
    base = base[:n_days]
    close = pd.Series(base, index=dates)
    # Single large crash on last day → pushes RSI oversold AND zscore extreme
    close.iloc[-1] = close.iloc[-1] * 0.70
    high = close * 1.01
    low = close * 0.99
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1_000_000] * n_days, index=dates)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=dates,
    )


# ---------------------------------------------------------------------------
# Tests: strategy-level filter rejection recording
# ---------------------------------------------------------------------------

class TestSma200FilterRejection:
    """strategy.last_filter_rejections captures candidates blocked by sma200_filter."""

    def _make_config(self, zscore_entry: float = -2.0, rsi_oversold: int = 35) -> dict:
        return {
            "strategies": {
                "mean_reversion": {
                    "enabled": True,
                    "sma200_filter": True,
                    "rsi_oversold": rsi_oversold,
                    "zscore_entry": zscore_entry,
                }
            },
            "risk": {
                "max_risk_per_trade_pct": 0.005,
                "max_open_positions": 10,
                "min_confidence": 0.65,
            },
            "fees": {},
        }

    def test_sma200_filter_records_rejection(self):
        """When sma200_filter trips, strategy records the rejection in last_filter_rejections.

        Acceptance: instance.last_filter_rejections has the candidate with
        rejection_reason='sma200_filter' and rejection_detail containing close + sma200.
        """
        from strategies.mean_reversion import MeanReversion

        strat = MeanReversion(self._make_config())
        data = {"FALL": _make_falling_knife_df()}
        strat.precompute(data)
        sigs = strat.generate_signals(data, equity=10_000.0, existing_positions=[])

        # Candidate should be filtered — not appear in signals
        assert all(s.ticker != "FALL" for s in sigs), "FALL should have been filtered by sma200"

        rejections = strat.last_filter_rejections
        assert len(rejections) >= 1, f"Expected ≥1 filter rejection, got {rejections}"

        fall_rej = next((r for r in rejections if r["ticker"] == "FALL"), None)
        assert fall_rej is not None, "FALL should be in last_filter_rejections"
        assert fall_rej["rejection_reason"] == "sma200_filter"
        assert "close" in fall_rej["rejection_detail"]
        assert "sma200" in fall_rej["rejection_detail"]
        assert fall_rej["strategy"] == "mean_reversion"
        assert fall_rej["confidence"] == 0.0  # filtered before confidence calc

    def test_rejection_detail_has_below_sma200_pct(self):
        """rejection_detail includes below_sma200_pct for operators who want the margin."""
        from strategies.mean_reversion import MeanReversion

        strat = MeanReversion(self._make_config())
        data = {"FALL": _make_falling_knife_df()}
        strat.precompute(data)
        strat.generate_signals(data, equity=10_000.0, existing_positions=[])

        rejs = [r for r in strat.last_filter_rejections if r["ticker"] == "FALL"]
        assert rejs, "No FALL rejection"
        pct = rejs[0]["rejection_detail"].get("below_sma200_pct")
        assert pct is not None, "below_sma200_pct should be populated when sma200 is not NaN"
        assert pct < 0, f"below_sma200_pct should be negative (price below SMA), got {pct}"

    def test_last_filter_rejections_cleared_per_call(self):
        """Each call to generate_signals starts with a fresh rejection list (no accumulation)."""
        from strategies.mean_reversion import MeanReversion

        strat = MeanReversion(self._make_config())
        data = {"FALL": _make_falling_knife_df()}
        strat.precompute(data)

        strat.generate_signals(data, equity=10_000.0, existing_positions=[])
        first_count = len(strat.last_filter_rejections)

        strat.generate_signals(data, equity=10_000.0, existing_positions=[])
        second_count = len(strat.last_filter_rejections)

        assert first_count == second_count, (
            f"Expected stable rejection count (no accumulation), "
            f"got {first_count} then {second_count}"
        )

    def test_attribute_initialised_at_construction(self):
        """last_filter_rejections exists on the instance immediately after __init__."""
        from strategies.mean_reversion import MeanReversion

        strat = MeanReversion(self._make_config())
        assert hasattr(strat, "last_filter_rejections"), (
            "last_filter_rejections must be set in __init__"
        )
        assert strat.last_filter_rejections == [], (
            "last_filter_rejections should be empty list at init"
        )

    def test_no_rejection_when_filter_disabled(self):
        """With sma200_filter=False, last_filter_rejections stays empty (no spurious records)."""
        from strategies.mean_reversion import MeanReversion

        cfg = self._make_config()
        cfg["strategies"]["mean_reversion"]["sma200_filter"] = False
        strat = MeanReversion(cfg)
        data = {"FALL": _make_falling_knife_df()}
        strat.precompute(data)
        strat.generate_signals(data, equity=10_000.0, existing_positions=[])

        assert strat.last_filter_rejections == [], (
            "With sma200_filter=False there should be no filter rejections"
        )

    def test_sma200_filter_info_log_emitted(self, caplog):
        """sma200_filter rejection emits a logger.info (not debug) so it shows in normal logs."""
        import logging
        from strategies.mean_reversion import MeanReversion

        strat = MeanReversion(self._make_config())
        data = {"FALL": _make_falling_knife_df()}
        strat.precompute(data)

        with caplog.at_level(logging.INFO):
            strat.generate_signals(data, equity=10_000.0, existing_positions=[])

        sma_logs = [
            r for r in caplog.records
            if "REJECTED by sma200_filter" in r.getMessage()
        ]
        assert sma_logs, (
            f"Expected 'REJECTED by sma200_filter' INFO log; got records: "
            f"{[r.getMessage() for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Tests: disabled-strategy observability in get_strategies()
# ---------------------------------------------------------------------------

class TestDisabledStrategiesLog:
    """get_strategies() logs strategies that are disabled in config (enabled=false)."""

    def test_disabled_strategies_logged_in_get_strategies(self, caplog):
        """When mean_reversion is disabled in config, get_strategies logs the absence.

        Note: caplog.at_level(INFO) (root-level) is used rather than logger="cli"
        because cli.py's logger is logging.getLogger("atlas.cli") — a separate
        hierarchy from "cli", so the logger= kwarg would not capture its records.
        """
        import logging
        from scripts.cli import get_strategies

        config = {
            "market": "sp500",
            "strategies": {
                "momentum_breakout": {"enabled": True},
                "mean_reversion": {"enabled": False},
                "trend_following": {"enabled": False},
            },
        }
        with caplog.at_level(logging.INFO):
            try:
                get_strategies(config)
            except Exception:
                pass  # paper-strategy lookup may error in test env; that's fine

        msgs = [r.getMessage() for r in caplog.records]
        disabled_logs = [m for m in msgs if "disabled in config" in m]
        assert disabled_logs, f"Expected 'disabled in config' log; got: {msgs}"

    def test_disabled_log_includes_strategy_names(self, caplog):
        """The disabled-strategy log message includes the strategy names."""
        import logging
        from scripts.cli import get_strategies

        config = {
            "market": "sp500",
            "strategies": {
                "momentum_breakout": {"enabled": True},
                "mean_reversion": {"enabled": False},
            },
        }
        with caplog.at_level(logging.INFO):
            try:
                get_strategies(config)
            except Exception:
                pass

        all_msgs = " ".join(r.getMessage() for r in caplog.records)
        # mean_reversion should appear as a disabled strategy name
        assert "mean_reversion" in all_msgs, (
            f"Expected 'mean_reversion' in disabled-strategies log; got: {all_msgs}"
        )

    def test_no_disabled_log_when_all_enabled(self, caplog):
        """When all known strategies are enabled or in paper, no 'disabled in config' log fires."""
        import logging
        from strategies.mean_reversion import MeanReversion
        from scripts.cli import _STRATEGY_REGISTRY

        # Build config that enables all registry strategies
        all_strats_cfg = {name: {"enabled": True} for name in _STRATEGY_REGISTRY}
        config = {"market": "sp500", "strategies": all_strats_cfg}

        with caplog.at_level(logging.INFO):
            try:
                from scripts.cli import get_strategies
                get_strategies(config)
            except Exception:
                pass

        disabled_logs = [r for r in caplog.records if "disabled in config" in r.getMessage()]
        # All strategies are enabled — no disabled log expected
        assert not disabled_logs, (
            f"Unexpected 'disabled in config' log when all strategies are enabled: {disabled_logs}"
        )
