"""P1-A: Integration tests — DynamicSizer wired into live strategy paths.

Tests verify that:
1. When ``dynamic_sizing.enabled=True`` a high-volatility ticker receives a
   smaller ``position_size`` (and hence fewer shares) than a low-volatility
   ticker given the same risk_budget.
2. When ``dynamic_sizing.enabled=False`` both tickers use the flat risk_pct
   so risk_amounts are the same.
3. At least 2 of the 9 wired strategies are exercised end-to-end through
   their ``generate_signals`` path (ConnorsRSI2 and MomentumBreakout).

No network calls — all data is synthetic.
"""
from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict

import numpy as np
import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Shared config / data helpers
# ---------------------------------------------------------------------------

def _base_config(dynamic_enabled: bool = True) -> Dict[str, Any]:
    """Minimal full config that works for all 9 strategy constructors."""
    return {
        "dynamic_sizing": {
            "enabled": dynamic_enabled,
            "base_risk_pct": 0.005,
            "min_risk_pct": 0.003,
            "max_risk_pct": 0.008,
            "confidence_scaling": {"enabled": False},
            "volatility_scaling": {
                "enabled": True,
                "low_vol_threshold":  0.02,  # ATR/price < 2% → low vol mult
                "high_vol_threshold": 0.05,  # ATR/price > 5% → high vol mult
                "low_vol_mult":  1.2,
                "high_vol_mult": 0.7,
            },
            "equity_curve_scaling": {"enabled": False},
        },
        "risk": {
            "starting_equity":         100_000.0,
            "leverage":                1.0,
            "max_risk_per_trade_pct":  0.005,
            "max_open_positions":      20,
            "max_sector_concentration": 5,
            "max_daily_drawdown_pct":  0.02,
            "require_stop_loss":       True,
            "require_planned_exit":    True,
            "min_confidence":          0.50,
            "trailing_stop": {"enabled": False, "activation_pct": 0.02, "atr_multiplier": 2.0},
        },
        "fees": {
            "commission_per_trade": 0.0,
            "commission_pct":       0.0,
            "min_position_value":   0.0,
        },
        "trading": {"live_safety": {"max_order_value": 0.0}},
        "sector_map": {},
        "strategy": {
            # ConnorsRSI2 / generic
            "ibs_filter":         False,
            "sma200_filter":      False,
            # MeanReversion
            "earnings_blackout_enabled": False,
        },
    }


def _make_connors_data(atr_level: float, n_bars: int = 250) -> Dict[str, pd.DataFrame]:
    """Synthetic data that reliably triggers a ConnorsRSI2 entry.

    *atr_level* controls the daily high-low range (ATR proxy).
    High value → high volatility.  Low value → low volatility.

    Pattern: steady uptrend for most bars (keeps price above SMA-200),
    then a sharp 2-day drop to force RSI(2) well below the entry threshold.
    """
    rng = np.random.default_rng(42)
    dates = pd.date_range(end=datetime(2026, 4, 24), periods=n_bars, freq="B")

    # Uptrend
    base = np.linspace(50.0, 150.0, n_bars - 2)
    # Sharp 2-bar drop (forces RSI(2) < 10)
    drop = [base[-1] * 0.94, base[-1] * 0.90]
    closes = np.concatenate([base, drop])

    highs  = closes + atr_level * 0.6
    lows   = closes - atr_level * 0.6
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    volume = rng.integers(2_000_000, 5_000_000, n_bars).astype(float)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows,
         "close": closes, "adj_close": closes, "volume": volume},
        index=dates,
    )
    df.index.name = "date"
    return {"CR_TICK": df}


def _make_momentum_data(atr_level: float, n_bars: int = 200) -> Dict[str, pd.DataFrame]:
    """Synthetic data that reliably triggers a MomentumBreakout entry.

    Pattern: steady uptrend, today's price marginally above the 20-day high.
    *atr_level* controls ATR magnitude.
    """
    rng = np.random.default_rng(7)
    dates = pd.date_range(end=datetime(2026, 4, 24), periods=n_bars, freq="B")

    closes = np.linspace(50.0, 120.0, n_bars)
    # Force today's close above all 20 prior closes → breakout
    closes[-1] = max(closes[-22:-1]) + 2.0

    highs  = closes + atr_level * 0.6
    lows   = closes - atr_level * 0.6
    opens  = np.roll(closes, 1); opens[0] = closes[0]
    volume = rng.integers(2_000_000, 5_000_000, n_bars).astype(float)

    df = pd.DataFrame(
        {"open": opens, "high": highs, "low": lows,
         "close": closes, "adj_close": closes, "volume": volume},
        index=dates,
    )
    df.index.name = "date"
    return {"MB_TICK": df}


# ---------------------------------------------------------------------------
# Unit tests: get_risk_pct_for_config (module-level helper)
# ---------------------------------------------------------------------------

class TestGetRiskPctForConfig:
    """Unit tests for the utility function added to utils/dynamic_sizing."""

    def test_high_vol_smaller_than_low_vol(self):
        """High ATR/price ratio → smaller risk_pct than low ATR/price ratio."""
        from utils.dynamic_sizing import get_risk_pct_for_config
        cfg = _base_config(dynamic_enabled=True)
        price = 100.0
        # ATR/price = 0.01 (< low_vol_thresh 0.02) → multiplied by 1.2
        rp_low  = get_risk_pct_for_config(cfg, atr=1.0, entry_price=price)
        # ATR/price = 0.06 (> high_vol_thresh 0.05) → multiplied by 0.7
        rp_high = get_risk_pct_for_config(cfg, atr=6.0, entry_price=price)
        assert rp_high < rp_low, (
            f"High-vol risk_pct {rp_high:.5f} should be < low-vol {rp_low:.5f}"
        )

    def test_disabled_returns_flat_pct(self):
        """Disabled toggle → always returns the flat max_risk_per_trade_pct."""
        from utils.dynamic_sizing import get_risk_pct_for_config
        cfg = _base_config(dynamic_enabled=False)
        flat = cfg["risk"]["max_risk_per_trade_pct"]
        rp1 = get_risk_pct_for_config(cfg, atr=1.0, entry_price=100.0)
        rp2 = get_risk_pct_for_config(cfg, atr=6.0, entry_price=100.0)
        assert rp1 == flat
        assert rp2 == flat

    def test_disabled_both_tickers_same(self):
        """Disabled → same risk_pct for wildly different ATRs."""
        from utils.dynamic_sizing import get_risk_pct_for_config
        cfg = _base_config(dynamic_enabled=False)
        rp1 = get_risk_pct_for_config(cfg, atr=0.5,  entry_price=100.0)
        rp2 = get_risk_pct_for_config(cfg, atr=10.0, entry_price=100.0)
        assert rp1 == rp2

    def test_enabled_result_clamped_in_range(self):
        """Result must lie in [min_risk_pct, max_risk_pct]."""
        from utils.dynamic_sizing import get_risk_pct_for_config
        cfg = _base_config(dynamic_enabled=True)
        min_r = cfg["dynamic_sizing"]["min_risk_pct"]
        max_r = cfg["dynamic_sizing"]["max_risk_pct"]
        for atr in [0.001, 0.5, 1.0, 5.0, 20.0]:
            rp = get_risk_pct_for_config(cfg, atr=atr, entry_price=100.0)
            assert min_r <= rp <= max_r, (
                f"atr={atr}: risk_pct={rp} not in [{min_r}, {max_r}]"
            )


# ---------------------------------------------------------------------------
# Strategy integration tests: ConnorsRSI2
# ---------------------------------------------------------------------------

class TestConnorsRSI2DynamicSizing:
    """Exercise ConnorsRSI2.generate_signals with enabled / disabled toggle."""

    def _get_strategy(self, enabled: bool):
        from strategies.connors_rsi2 import ConnorsRSI2
        cfg = _base_config(dynamic_enabled=enabled)
        return ConnorsRSI2(cfg)

    def test_enabled_high_vol_smaller_position(self):
        """High ATR → DynamicSizer reduces risk_pct → fewer shares than low ATR."""
        strat = self._get_strategy(enabled=True)

        # low vol: ATR ≈ 1.0 on a ~135 price → ATR/price ≈ 0.007 → low_vol_mult=1.2
        data_low  = _make_connors_data(atr_level=1.0)
        # high vol: ATR ≈ 8.0 on a ~135 price → ATR/price ≈ 0.059 → high_vol_mult=0.7
        data_high = _make_connors_data(atr_level=8.0)

        equity = 100_000.0
        strat.precompute(data_low)
        sigs_low  = strat.generate_signals(data_low,  equity, [])

        # Reset precompute flag for second dataset
        strat._precomputed = False
        strat.precompute(data_high)
        sigs_high = strat.generate_signals(data_high, equity, [])

        assert sigs_low,  "Expected signal for low-vol ConnorsRSI2 ticker"
        assert sigs_high, "Expected signal for high-vol ConnorsRSI2 ticker"

        size_low  = sigs_low[0].position_size
        size_high = sigs_high[0].position_size
        assert size_high < size_low, (
            f"High-vol size {size_high} should be < low-vol size {size_low} "
            f"(dynamic sizing should scale down for high vol)"
        )

    def test_disabled_same_risk_budget_proportion(self):
        """Disabled toggle → both tickers use the same flat risk_pct."""
        strat = self._get_strategy(enabled=False)

        data_low  = _make_connors_data(atr_level=1.0)
        data_high = _make_connors_data(atr_level=8.0)

        equity = 100_000.0
        flat_budget = equity * 0.005  # max_risk_per_trade_pct

        strat.precompute(data_low)
        sigs_low  = strat.generate_signals(data_low,  equity, [])
        strat._precomputed = False
        strat.precompute(data_high)
        sigs_high = strat.generate_signals(data_high, equity, [])

        assert sigs_low,  "Expected signal for low-vol (disabled)"
        assert sigs_high, "Expected signal for high-vol (disabled)"

        # Both used flat 0.5% risk → risk_amount ≈ flat_budget (within 5%)
        for label, sigs in [("low", sigs_low), ("high", sigs_high)]:
            ra = sigs[0].risk_amount
            assert abs(ra - flat_budget) < flat_budget * 0.05, (
                f"{label}-vol risk_amount={ra:.2f} far from flat budget {flat_budget:.2f}"
            )


# ---------------------------------------------------------------------------
# Strategy integration tests: MomentumBreakout
# ---------------------------------------------------------------------------

class TestMomentumBreakoutDynamicSizing:
    """Exercise MomentumBreakout.generate_signals with both toggle states."""

    def _get_strategy(self, enabled: bool):
        from strategies.momentum_breakout import MomentumBreakout
        cfg = _base_config(dynamic_enabled=enabled)
        return MomentumBreakout(cfg)

    def test_enabled_high_vol_smaller_position(self):
        """High ATR → smaller qty under the same equity/risk_budget."""
        strat = self._get_strategy(enabled=True)

        # low vol: ATR ≈ 0.3 on ~120 price → ATR/price ≈ 0.0025 → low_vol_mult
        data_low  = _make_momentum_data(atr_level=0.3)
        # high vol: ATR ≈ 7.0 on ~120 price → ATR/price ≈ 0.058 → high_vol_mult
        data_high = _make_momentum_data(atr_level=7.0)

        equity = 100_000.0
        strat.precompute(data_low)
        sigs_low = strat.generate_signals(data_low, equity, [])

        strat._precomputed = False
        strat.precompute(data_high)
        sigs_high = strat.generate_signals(data_high, equity, [])

        assert sigs_low,  "Expected signal for low-vol MomentumBreakout"
        assert sigs_high, "Expected signal for high-vol MomentumBreakout"

        size_low  = sigs_low[0].position_size
        size_high = sigs_high[0].position_size
        assert size_high < size_low, (
            f"High-vol size {size_high} should be < low-vol size {size_low}"
        )

    def test_disabled_both_generate_signals(self):
        """Disabled path still generates signals; uses flat risk_pct."""
        strat = self._get_strategy(enabled=False)

        data_low  = _make_momentum_data(atr_level=0.3)
        data_high = _make_momentum_data(atr_level=7.0)

        equity = 100_000.0
        flat_budget = equity * 0.005

        strat.precompute(data_low)
        sigs_low  = strat.generate_signals(data_low,  equity, [])
        strat._precomputed = False
        strat.precompute(data_high)
        sigs_high = strat.generate_signals(data_high, equity, [])

        assert sigs_low,  "Expected signal for low-vol MB (disabled)"
        assert sigs_high, "Expected signal for high-vol MB (disabled)"

        for label, sigs in [("low", sigs_low), ("high", sigs_high)]:
            ra = sigs[0].risk_amount
            assert abs(ra - flat_budget) < flat_budget * 0.05, (
                f"{label}-vol risk_amount={ra:.2f} far from flat budget {flat_budget:.2f}"
            )


# ---------------------------------------------------------------------------
# Smoke tests: all 9 strategies have _get_dynamic_risk_pct
# ---------------------------------------------------------------------------

STRATEGY_CLASSES = [
    ("strategies.mtf_momentum",     "MTFMomentum"),
    ("strategies.connors_rsi2",     "ConnorsRSI2"),
    ("strategies.bb_squeeze",       "BBSqueeze"),
    ("strategies.sector_rotation",  "SectorRotation"),
    ("strategies.trend_following",  "TrendFollowing"),
    ("strategies.opening_gap",      "OpeningGap"),
    ("strategies.short_term_mr",    "ShortTermMR"),
    ("strategies.mean_reversion",   "MeanReversion"),
    ("strategies.momentum_breakout","MomentumBreakout"),
]


class TestAllStrategiesHaveMethod:
    """Ensure every wired strategy exposes _get_dynamic_risk_pct."""

    def _find_cls(self, mod_path: str):
        import importlib
        from strategies.base import BaseStrategy
        mod = importlib.import_module(mod_path)
        for attr in dir(mod):
            obj = getattr(mod, attr)
            try:
                if (isinstance(obj, type)
                        and issubclass(obj, BaseStrategy)
                        and obj is not BaseStrategy):
                    return obj
            except TypeError:
                pass
        raise RuntimeError(f"No BaseStrategy subclass found in {mod_path}")

    def test_method_exists_and_callable(self):
        cfg = _base_config(dynamic_enabled=True)
        missing = []
        for mod_path, _ in STRATEGY_CLASSES:
            cls = self._find_cls(mod_path)
            instance = cls(cfg)
            if not callable(getattr(instance, "_get_dynamic_risk_pct", None)):
                missing.append(mod_path)
        assert not missing, f"Missing _get_dynamic_risk_pct: {missing}"

    def test_method_returns_float_in_range(self):
        cfg = _base_config(dynamic_enabled=True)
        min_r = cfg["dynamic_sizing"]["min_risk_pct"]
        max_r = cfg["dynamic_sizing"]["max_risk_pct"]
        for mod_path, _ in STRATEGY_CLASSES:
            cls = self._find_cls(mod_path)
            instance = cls(cfg)
            rp = instance._get_dynamic_risk_pct(atr=1.0, entry_price=50.0)
            assert isinstance(rp, float), f"{cls.__name__}: expected float, got {type(rp)}"
            assert min_r <= rp <= max_r, (
                f"{cls.__name__}: risk_pct {rp} not in [{min_r}, {max_r}]"
            )

    def test_disabled_returns_flat_pct(self):
        cfg_off = _base_config(dynamic_enabled=False)
        flat = cfg_off["risk"]["max_risk_per_trade_pct"]
        for mod_path, _ in STRATEGY_CLASSES:
            cls = self._find_cls(mod_path)
            instance = cls(cfg_off)
            rp = instance._get_dynamic_risk_pct(atr=1.0, entry_price=50.0)
            assert rp == flat, f"{cls.__name__} (disabled): got {rp}, expected {flat}"
