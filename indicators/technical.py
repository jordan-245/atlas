"""Technical indicator functions: RSI, ATR, Z-score, volume ratio, WVF, IBS.

Pure-pandas/numpy implementations. No I/O, no broker dependencies.
Previously located at utils/helpers.py; relocated 2026-05-11 to align
package naming with module purpose.
"""
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def calc_atr(high: pd.Series, low: pd.Series, close: pd.Series,
             period: int = 14) -> pd.Series:
    """Calculate Average True Range (ATR).

    ATR measures market volatility by decomposing the entire range of
    an asset price for a given period.

    Args:
        high: Series of high prices.
        low: Series of low prices.
        close: Series of close prices.
        period: Lookback period (default 14).

    Returns:
        pd.Series of ATR values (NaN for first `period` rows).
    """
    if len(close) < period + 1:
        logger.warning(f"Insufficient data for ATR({period}): got {len(close)} rows")
        return pd.Series(np.nan, index=close.index)

    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Wilder's smoothing (EMA with alpha = 1/period)
    atr = true_range.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Calculate Relative Strength Index (RSI).

    Uses Wilder's smoothing method (exponential moving average).

    Args:
        close: Series of close prices.
        period: Lookback period (default 14).

    Returns:
        pd.Series of RSI values (0-100). NaN for insufficient data.
    """
    if len(close) < period + 1:
        logger.warning(f"Insufficient data for RSI({period}): got {len(close)} rows")
        return pd.Series(np.nan, index=close.index)

    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)

    # Wilder's smoothing
    alpha = 1.0 / period
    avg_gain = gains.ewm(alpha=alpha, min_periods=period, adjust=False).mean()
    avg_loss = losses.ewm(alpha=alpha, min_periods=period, adjust=False).mean()

    # Avoid division by zero
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    return rsi


def calc_zscore(series: pd.Series, lookback: int = 20) -> pd.Series:
    """Calculate rolling Z-score.

    Z-score = (value - rolling_mean) / rolling_std

    Useful for mean-reversion signals: values below -2 suggest
    the price is significantly below its recent average.

    Args:
        series: Input price or return series.
        lookback: Rolling window size (default 20).

    Returns:
        pd.Series of Z-score values.
    """
    if len(series) < lookback:
        logger.warning(f"Insufficient data for Z-score({lookback}): got {len(series)} rows")
        return pd.Series(np.nan, index=series.index)

    rolling_mean = series.rolling(window=lookback).mean()
    rolling_std = series.rolling(window=lookback).std(ddof=1)

    # Avoid division by zero
    zscore = (series - rolling_mean) / rolling_std.replace(0, np.nan)
    return zscore


def calc_volume_ratio(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Calculate volume ratio (current volume / average volume).

    A ratio > 1.0 means above-average volume (conviction).
    A ratio < 1.0 means below-average volume (weak move).

    Args:
        volume: Series of volume data.
        lookback: Rolling window for average (default 20).

    Returns:
        pd.Series of volume ratios.
    """
    if len(volume) < lookback:
        logger.warning(f"Insufficient data for volume_ratio({lookback}): got {len(volume)} rows")
        return pd.Series(np.nan, index=volume.index)

    avg_volume = volume.rolling(window=lookback).mean()
    # Avoid division by zero
    ratio = volume / avg_volume.replace(0, np.nan)
    return ratio


def calc_wvf(close: pd.Series, low: pd.Series, period: int = 22) -> pd.Series:
    """Calculate Williams VIX Fix — synthetic fear gauge for individual stocks.

    WVF spikes during panic selling, making it a high-probability
    mean reversion entry signal. PF 1.78 on S&P 500 (QuantifiedStrategies).

    Formula: WVF = [(Highest Close over N periods - Low) / Highest Close] × 100

    Args:
        close: Series of close prices.
        low: Series of low prices.
        period: Lookback period (default 22 trading days ≈ 1 month).

    Returns:
        pd.Series of WVF values (higher = more fear/panic).
    """
    if len(close) < period:
        return pd.Series(np.nan, index=close.index)
    highest_close = close.rolling(period).max()
    wvf = ((highest_close - low) / highest_close) * 100
    return wvf


def calc_ibs(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Calculate Internal Bar Strength (IBS).

    IBS = (Close - Low) / (High - Low)
    Range: 0.0 (closed at low) to 1.0 (closed at high).
    Low IBS (< 0.2) suggests selling pressure exhaustion.

    Args:
        high: Series of high prices.
        low: Series of low prices.
        close: Series of close prices.

    Returns:
        pd.Series of IBS values (0.0 to 1.0). NaN where range is zero.
    """
    range_ = high - low
    ibs = (close - low) / range_.replace(0, np.nan)
    return ibs
