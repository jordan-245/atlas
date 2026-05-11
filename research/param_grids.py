"""Parameter grids for research sweeps.

Extracted from research/archive/sweep.py on 2026-05-11 as part of
#refactor-audit cleanup.  This is the canonical location for PARAM_GRIDS
— research/archive/sweep.py re-exports from here for backward compat.
"""
from __future__ import annotations

from typing import Dict

# ─── Parameter Grids ─────────────────────────────────────────────────────────

# Each strategy has a grid of parameters to sweep.
# Only scalar params — nested dicts handled separately.
# Values are ordered from most likely to least likely improvement.

PARAM_GRIDS: Dict[str, Dict[str, list]] = {
    # ── Tier 1 / Core ────────────────────────────────────────────────────
    "mean_reversion": {
        "rsi_period": [7, 10, 14, 21, 5],
        "rsi_oversold": [30, 35, 40],  # tightened: removed 20 (min=-5.63) and 25 (min=-2.64) — both seed exploration of oversold<30 region, empirically negative Sharpe
        "zscore_lookback": [15, 20, 30, 10],
        "zscore_entry": [-1.5, -2.0, -2.5, -1.0],
        "atr_period": [10, 14, 20, 7],
        "atr_stop_mult": [2.0, 2.5, 3.0, 1.5],
        "profit_target_atr_mult": [1.5, 2.0, 2.5, 1.0, 3.0],
        "max_hold_days": [5, 7, 10, 15, 20],
        "sma200_filter": [True, False],
        "ibs_max": [0.3, 0.5, 0.7, 1.0],
    },
    "trend_following": {
        "fast_ma": [10, 15, 30, 50],  # tightened: removed 20 — equals min slow_ma=20, seeds degenerate zero-spread MA exploration
        "slow_ma": [20, 50, 100, 200],
        "pullback_pct": [0.02, 0.03, 0.04, 0.05],  # tightened: removed 0.06 (avg_sharpe=-0.60, min=-2.95) — 6% pullback fires only into continuation moves
        "atr_period": [10, 14, 20],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "trailing_stop_atr_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [10, 15, 20, 30],
        "sma200_filter": [True, False],
    },
    "opening_gap": {
        "gap_threshold": [-0.01, -0.015, -0.02],  # tightened: removed -0.025,-0.03 (min=-11.24 both) — deep gaps in sp500 large-caps lack statistical power
        "ibs_confirm": [0.3, 0.4, 0.5, 0.6],
        "rsi14_max": [20, 25, 30, 35],
        "vol_surge_threshold": [1.0, 1.2, 1.5, 2.0],
        "atr_period": [10, 14, 20],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    # ── Tier 2 / Dormant core ─────────────────────────────────────────────
    "connors_rsi2": {
        "rsi_period": [2, 3, 4, 5],
        "rsi_entry": [5, 10, 15, 20],
        "sma_trend_period": [100, 150, 200],
        "sma200_filter": [True, False],
        "min_consecutive_down": [0, 1, 2, 3],
        "ibs_max": [0.3, 0.5, 0.7, 1.0],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
    },
    "momentum_breakout": {
        "breakout_period": [10, 20, 30, 40, 60],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 10, 15, 20],
        "sma200_filter": [True, False],
        "signal_mode": ["raw", "risk_adjusted", "idiosyncratic"],
        "momentum_lookback": [126, 252],
        "momentum_skip": [0, 21, 42],
    },
    "short_term_mr": {
        "rsi_period": [2, 3, 4, 5],
        "rsi_oversold": [10, 15, 20, 25],
        "max_hold_days": [2, 3, 5, 7],
        "atr_stop_mult": [1.5, 2.0, 2.5],
    },
    "bb_squeeze": {
        "bb_period": [10, 15],  # tightened: removed 20 (min=-2.39) and 30 (min=-7.67) — wide bands with short holds are noise-fitting
        "bb_std": [1.5, 2.0],  # tightened: removed 2.5 (min=-1.09) — over-wide bands miss squeeze signals
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 10, 15],
    },
    # ── Tier 3 / Research strategies ─────────────────────────────────────
    "adx_trend_pullback": {
        "adx_period": [7, 10, 14, 21],
        "adx_threshold": [20.0, 25.0, 30.0, 35.0],
        "ema_touch_pct": [0.005, 0.01, 0.015, 0.02],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "consecutive_down_days": {
        "min_down_days": [2, 3, 4],  # tightened: removed 5 (avg=-4.48, min=-17.88) — 5-day down streak fires into momentum crashes, not mean-reversion
        "ibs_threshold": [0.2, 0.3, 0.5, 1.0],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    "demark_sequential": {
        "setup_bars": [7, 9],  # tightened: removed 13 (avg=-4.96, min=-6.48) — DeMark countdown extension lacks statistical power in sp500
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "donchian_breakout": {
        "entry_period": [10, 20, 30, 50],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [10, 15, 20, 30],
        "sma200_filter": [True, False],
    },
    "stochastic_oversold": {
        "stoch_period": [5, 10, 14],  # tightened: removed 21 (avg=-3.46, min=-3.98) — 21-bar stochastic lags too far for short-term MR
        "stoch_smooth": [3],  # tightened: removed 5 (avg=-3.73, min=-4.47) — over-smoothing makes stochastic a lagging indicator, catastrophically bad
        "stoch_entry": [10, 15, 20, 25],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "williams_percent_r": {
        "wr_period": [10, 14, 21],
        "wr_entry": [-80, -85, -95],  # tightened: removed -90 (avg=-5.16, min=-13.86) — fires into sustained downtrends, knife-catching overfit
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "lower_band_reversion": {
        "band_mult": [1.0, 1.5, 2.0, 2.5],
        "ibs_threshold": [0.2, 0.5],  # tightened: removed 0.3 (avg=-1.60, min=-3.09) — mid-point threshold captures neither clean lower-band touches nor confirmed reversals
        "range_lookback": [10, 15, 20, 25],
        "max_hold_days": [5, 7, 10],  # tightened: removed 3 (avg=-2.93, min=-3.72) — 3-day hold exits before mean reversion completes
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "sma200_filter": [True, False],
    },
    "triple_rsi": {
        "rsi_period": [3, 5, 7],
        "rsi_entry": [30, 35],  # tightened: removed 20 (avg=-5.28, min=-7.91) and 25 (avg=-2.51, min=-5.15) — triple-RSI<25 fires only during crashes
        "decline_days": [2, 3],  # tightened: removed 4 (avg=-4.46, min=-5.70) — 4-day decline + triple-RSI fires only in deep downtrends
        "max_hold_days": [3, 5, 7, 10],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "sma200_filter": [True, False],
    },
    "keltner_reversion": {
        "ema_period": [15, 20],  # tightened: removed 10 (avg=-33.49, min=-66.56) — short EMA creates hairline bands, catastrophic overtrading on sp500
        "atr_mult": [1.5, 2.0],  # tightened: removed 2.5 (avg=-8.90, min=-15.90) — over-wide Keltner bands miss the reversion window
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "inside_bar_nr7": {
        "nr_lookback": [5, 7, 10],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    "volume_climax": {
        "volume_mult": [1.5, 2.0, 2.5, 3.0, 4.0],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    "gap_and_go": {
        "gap_threshold": [0.02, 0.03, 0.04, 0.05],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    "heikin_ashi_reversal": {
        "reversal_bars": [1, 2, 3, 4],
        "min_red_bars": [2, 3, 4, 5],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "macd_divergence": {
        "macd_fast": [8, 12, 16],
        "macd_slow": [20, 26, 30],
        "macd_signal": [7, 9, 11],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "overnight_return": {
        "ibs_min": [0.3, 0.4, 0.5, 0.6],
        "momentum_min": [0.0, 0.005, 0.01, 0.015],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [1, 2, 3, 5],
        "sma200_filter": [True, False],
    },
    "pead_earnings_drift": {
        "min_jump_pct": [0.02, 0.03, 0.04, 0.05],
        "max_days_after_event": [1, 2, 3],
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [10, 15, 20, 30],
        "sma200_filter": [True, False],
    },
    # ── Tier 4 / New Builder-1 strategies ────────────────────────────────
    "relative_strength_pullback": {
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "rsi_divergence": {
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [5, 7, 10, 15],
        "sma200_filter": [True, False],
    },
    "vwap_reversion": {
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
    "monthly_rotation": {
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [10, 15, 20, 30],
        "sma200_filter": [True, False],
    },
    "put_call_vix_proxy": {
        "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
        "max_hold_days": [3, 5, 7, 10],
        "sma200_filter": [True, False],
    },
}
