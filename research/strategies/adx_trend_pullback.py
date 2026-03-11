"""
Atlas Adx Trend Pullback Strategy
========================================
ADX > 25 (strong trend) + pullback to 20-EMA. Enter on bounce from EMA. Exit: trailing ATR stop.

Reference: Welles Wilder 'New Concepts in Technical Trading' (1978)
Generated: 2026-03-10T07:18:31.203810+00:00

Config Section: strategies.adx_trend_pullback

Core logic:
  1. Strong trend filter: ADX > adx_threshold (default 25) AND +DI > -DI (bullish direction)
  2. EMA pullback: price touched or dipped below 20-EMA within the last pullback_lookback bars
  3. Bounce confirmation: current close is above the 20-EMA (recovery)
  4. Uptrend filter: close > SMA-200 (optional)
  5. Stop: ATR-based stop (atr_stop_mult * ATR below entry)
  6. Exit: stop hit OR time-based exit
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from strategies.base import BaseStrategy, Signal
from utils.helpers import calc_atr, calc_rsi, calc_position_size

logger = logging.getLogger(__name__)


def _calc_adx(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculate ADX, +DI, and -DI using Wilder's smoothing.

    Args:
        high:   Series of high prices.
        low:    Series of low prices.
        close:  Series of close prices.
        period: Wilder smoothing period (default 14).

    Returns:
        (adx, plus_di, minus_di) — all pd.Series aligned to close.index.
    """
    high_diff = high.diff()
    low_diff = -low.diff()  # positive when price drops

    plus_dm = pd.Series(
        np.where((high_diff > low_diff) & (high_diff > 0), high_diff, 0.0),
        index=close.index,
    )
    minus_dm = pd.Series(
        np.where((low_diff > high_diff) & (low_diff > 0), low_diff, 0.0),
        index=close.index,
    )

    # True range
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)

    alpha = 1.0 / period
    smoothed_tr = tr.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smoothed_plus_dm = plus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    smoothed_minus_dm = minus_dm.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    # Avoid divide-by-zero
    plus_di = 100.0 * smoothed_plus_dm / smoothed_tr.replace(0, np.nan)
    minus_di = 100.0 * smoothed_minus_dm / smoothed_tr.replace(0, np.nan)

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / di_sum

    adx = dx.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    return adx, plus_di, minus_di


class AdxTrendPullback(BaseStrategy):
    """ADX > 25 (strong trend) + pullback to 20-EMA. Enter on bounce from EMA."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        strat_cfg = config.get("strategies", {}).get("adx_trend_pullback", {})

        # ADX parameters
        self.adx_period = strat_cfg.get("adx_period", 14)
        self.adx_threshold = strat_cfg.get("adx_threshold", 25.0)

        # EMA pullback parameters
        self.ema_period = strat_cfg.get("ema_period", 20)
        self.pullback_lookback = strat_cfg.get("pullback_lookback", 5)  # bars to look back for EMA touch
        self.ema_touch_pct = strat_cfg.get("ema_touch_pct", 0.01)       # how close to EMA counts as touch (1%)

        # Trend filter
        self.sma200_filter = strat_cfg.get("sma200_filter", True)

        # Risk management
        self.atr_period = strat_cfg.get("atr_period", 14)
        self.atr_stop_mult = strat_cfg.get("atr_stop_mult", 2.0)

        # Exit parameters
        self.max_hold_days = strat_cfg.get("max_hold_days", 10)

        self._logger.info(
            f"AdxTrendPullback initialized: adx>{self.adx_threshold}, "
            f"EMA{self.ema_period}, pullback_lookback={self.pullback_lookback}, "
            f"sma200={'ON' if self.sma200_filter else 'OFF'}, "
            f"atr_stop_mult={self.atr_stop_mult}, max_hold={self.max_hold_days}"
        )

    @property
    def name(self) -> str:
        return "adx_trend_pullback"

    def generate_signals(
        self,
        data: Dict[str, pd.DataFrame],
        equity: float,
        existing_positions: List[Dict[str, Any]],
    ) -> List[Signal]:
        """Generate ADX trend-pullback entry signals.

        Signal conditions (all must be true):
          1. ADX > adx_threshold (strong trend)
          2. +DI > -DI (bullish directional bias)
          3. Price is above SMA-200 (uptrend, optional)
          4. Pullback detected: low touched within ema_touch_pct of EMA in last pullback_lookback bars
          5. Bounce confirmed: current close > EMA
        """
        signals: List[Signal] = []
        held_tickers = self._get_held_tickers(existing_positions)
        risk_pct = self.risk_config.get("max_risk_per_trade_pct", 0.01)
        commission_per_trade = self.fees_config.get("commission_per_trade", 0.0)
        commission_pct = self.fees_config.get("commission_pct", 0.0)
        min_position_value = self.fees_config.get("min_position_value", 0.0)
        max_position_value = self.config.get("trading", {}).get(
            "live_safety", {}
        ).get("max_order_value", 0.0)

        min_rows = max(200 + self.adx_period + 10, self.ema_period + self.pullback_lookback + 10)

        for ticker, df in data.items():
            try:
                if ticker in held_tickers:
                    continue
                if not self._can_open_position(existing_positions):
                    self._logger.debug("Max positions reached, stopping scan")
                    break
                if not self._has_sufficient_data(df, min_rows):
                    continue

                close = df["close"]
                high = df["high"]
                low = df["low"]

                # ── 20-EMA ──────────────────────────────────────────────────
                ema = close.ewm(span=self.ema_period, adjust=False).mean()
                ema_val = float(ema.iloc[-1])
                current_close = float(close.iloc[-1])

                if np.isnan(ema_val) or ema_val <= 0:
                    continue

                # ── Bounce: current close must be above EMA ─────────────────
                if current_close <= ema_val:
                    continue

                # ── Pullback to EMA: any of the last N bars touched EMA ──────
                # A bar "touched" EMA if its low was <= EMA * (1 + ema_touch_pct)
                # and its high was >= EMA * (1 - ema_touch_pct)
                # We look at a window ending one bar ago (not today, since today is the bounce)
                window_low = low.iloc[-(self.pullback_lookback + 1):-1]
                window_ema = ema.iloc[-(self.pullback_lookback + 1):-1]

                # Pullback: at least one bar's LOW was at or below EMA
                pullback_mask = window_low <= window_ema * (1.0 + self.ema_touch_pct)
                if not pullback_mask.any():
                    continue

                # ── ADX: strong trend ────────────────────────────────────────
                adx, plus_di, minus_di = _calc_adx(high, low, close, self.adx_period)
                adx_val = float(adx.iloc[-1])
                plus_di_val = float(plus_di.iloc[-1])
                minus_di_val = float(minus_di.iloc[-1])

                if np.isnan(adx_val) or adx_val < self.adx_threshold:
                    continue

                # Bullish directional bias: +DI > -DI
                if plus_di_val <= minus_di_val:
                    continue

                # ── SMA-200 uptrend filter ───────────────────────────────────
                if self.sma200_filter:
                    sma200 = close.rolling(200).mean()
                    sma200_val = float(sma200.iloc[-1])
                    if np.isnan(sma200_val) or current_close <= sma200_val:
                        continue

                # ── ATR and position sizing ──────────────────────────────────
                atr = calc_atr(high, low, close, self.atr_period)
                atr_val = float(atr.iloc[-1])

                if atr_val <= 0 or np.isnan(atr_val):
                    continue

                entry_price = current_close
                stop_price = entry_price - self.atr_stop_mult * atr_val

                if stop_price <= 0 or stop_price >= entry_price:
                    continue

                pos_result = calc_position_size(
                    entry_price=entry_price,
                    stop_price=stop_price,
                    equity=equity,
                    risk_pct=risk_pct,
                    commission_per_trade=commission_per_trade,
                    commission_pct=commission_pct,
                    min_position_value=min_position_value,
                    max_position_value=max_position_value,
                )
                shares = pos_result["shares"]
                if shares <= 0:
                    continue

                # ── Confidence score ─────────────────────────────────────────
                # Base confidence from ADX strength above threshold
                adx_excess = min((adx_val - self.adx_threshold) / 25.0, 1.0)
                # Boost from DI spread
                di_spread = plus_di_val - minus_di_val
                di_bonus = min(di_spread / 50.0, 0.20)
                confidence = round(min(0.92, 0.55 + adx_excess * 0.25 + di_bonus), 3)

                sma200_str = ""
                if self.sma200_filter:
                    sma200_val = float(close.rolling(200).mean().iloc[-1])
                    sma200_str = f", SMA200={sma200_val:.2f}"

                pullback_low = float(window_low.min())
                rationale = (
                    f"{ticker}: ADX pullback bounce — ADX={adx_val:.1f}>{self.adx_threshold}, "
                    f"+DI={plus_di_val:.1f}>-DI={minus_di_val:.1f}, "
                    f"EMA{self.ema_period}={ema_val:.2f}, pullback_low={pullback_low:.2f}, "
                    f"close={current_close:.2f}, ATR={atr_val:.2f}, stop={stop_price:.2f}"
                    f"{sma200_str}"
                )

                signals.append(Signal(
                    ticker=ticker,
                    strategy=self.name,
                    direction="long",
                    entry_price=entry_price,
                    stop_price=round(stop_price, 2),
                    take_profit=None,
                    position_size=shares,
                    position_value=round(shares * entry_price, 2),
                    risk_amount=round(pos_result["total_risk"], 2),
                    confidence=confidence,
                    rationale=rationale,
                    features={
                        "adx": round(adx_val, 2),
                        "plus_di": round(plus_di_val, 2),
                        "minus_di": round(minus_di_val, 2),
                        "ema": round(ema_val, 2),
                        "atr": round(atr_val, 3),
                        "pullback_low": round(pullback_low, 2),
                    },
                    market_id=self.config.get("market", "sp500"),
                ))

            except Exception as e:
                self._logger.warning(f"{ticker}: signal generation failed: {e}")
                continue

        self._logger.info(
            f"AdxTrendPullback: {len(signals)} signals from {len(data)} tickers"
        )
        return signals

    def check_exits(
        self,
        data: Dict[str, pd.DataFrame],
        positions: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Check positions for exit conditions.

        Exit rules (priority order):
          1. ATR trailing stop hit: close <= stop_price
          2. Time exit: held >= max_hold_days
          3. EMA breakdown: close falls below EMA again (trend invalidated)
        """
        exits: List[Dict[str, Any]] = []

        for pos in positions:
            if pos.get("strategy") != self.name:
                continue
            ticker = pos.get("ticker")
            if not ticker or ticker not in data:
                continue

            df = data[ticker]
            if not self._has_sufficient_data(df, self.ema_period + 2):
                continue

            current_price = float(df["close"].iloc[-1])
            stop_price = pos.get("stop_price", 0)

            # Days held
            entry_date = pos.get("entry_date")
            days_held = 0
            if entry_date:
                if isinstance(entry_date, str):
                    entry_date = pd.Timestamp(entry_date)
                days_held = (df.index[-1] - pd.Timestamp(entry_date)).days

            reason = None
            details = None

            # 1. ATR trailing stop hit
            if stop_price and current_price <= stop_price:
                reason = "stop_hit"
                details = (
                    f"{ticker} ATR stop hit: close {current_price:.2f} <= stop {stop_price:.2f}, "
                    f"held {days_held}d"
                )

            # 2. Time exit
            elif days_held >= self.max_hold_days:
                reason = "time_exit"
                details = f"{ticker} time exit: held {days_held}d >= max {self.max_hold_days}d"

            # 3. EMA breakdown — trend structure broken
            else:
                ema = df["close"].ewm(span=self.ema_period, adjust=False).mean()
                ema_val = float(ema.iloc[-1])
                if not np.isnan(ema_val) and current_price < ema_val * 0.99:
                    reason = "signal_exit"
                    details = (
                        f"{ticker} EMA breakdown: close {current_price:.2f} < "
                        f"EMA{self.ema_period}={ema_val:.2f}, held {days_held}d"
                    )

            if reason:
                exits.append({
                    "ticker": ticker,
                    "reason": reason,
                    "exit_price": current_price,
                    "details": details or reason,
                })

        self._logger.debug(
            f"AdxTrendPullback: {len(exits)} exits from {len(positions)} positions"
        )
        return exits


# Default parameter grid for optimization
PARAM_GRID = {
    "adx_threshold": [20.0, 25.0, 30.0],
    "ema_period": [10, 20, 30],
    "atr_stop_mult": [1.5, 2.0, 2.5, 3.0],
    "max_hold_days": [5, 10, 15, 20],
    "pullback_lookback": [3, 5, 8],
    "sma200_filter": [True, False],
}
