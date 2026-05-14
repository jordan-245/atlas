"""OHLCV query helpers — private loaders for signal modules.

Centralizes inline SQL previously duplicated across signals/. Provides
in-process TTL cache to avoid hammering the DB on repeated calls within
a cron cycle.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import pandas as pd

from db.atlas_db import get_db

logger = logging.getLogger(__name__)

_CACHE_TTL_SEC: int = 300  # 5-min default
_cache: dict[tuple, tuple[float, pd.DataFrame]] = {}


def _cache_get(key: tuple, ttl: int) -> Optional[pd.DataFrame]:
    """Return a cached DataFrame copy or None (on miss, expiry, or ttl<=0)."""
    if ttl <= 0:
        return None
    hit = _cache.get(key)
    if hit is None:
        return None
    ts, df = hit
    if time.time() - ts > ttl:
        _cache.pop(key, None)
        return None
    return df.copy()  # defensive copy so callers can't mutate the cache


def _cache_put(key: tuple, df: pd.DataFrame) -> None:
    _cache[key] = (time.time(), df.copy())


# ---------------------------------------------------------------------------
# Public loaders
# ---------------------------------------------------------------------------

def get_ohlcv_volume(
    tickers: list[str] | tuple[str, ...],
    start_date: str,
    end_date: str,
    *,
    cache_ttl: int = _CACHE_TTL_SEC,
) -> pd.DataFrame:
    """Return DataFrame with columns [date, ticker, volume] for the given range.

    Replaces inline SQL in signals/etf_flows.py.

    Args:
        tickers:    Tickers to query (list or tuple — order is normalised for
                    cache keying).
        start_date: ISO date string, lower bound inclusive (date >= start_date).
        end_date:   ISO date string, upper bound inclusive (date <= end_date).
        cache_ttl:  In-process TTL seconds.  Pass 0 to bypass the cache.

    Returns:
        DataFrame with columns [date, ticker, volume] ordered by (date, ticker).
        Empty DataFrame (correct column names) when no rows match.
    """
    if not tickers:
        return pd.DataFrame(columns=["date", "ticker", "volume"])

    key = ("get_ohlcv_volume", tuple(sorted(tickers)), start_date, end_date)
    cached = _cache_get(key, cache_ttl)
    if cached is not None:
        return cached

    placeholders = ",".join("?" * len(tickers))
    sql = (
        f"SELECT date, ticker, volume FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) AND date >= ? AND date <= ? "
        f"ORDER BY date, ticker"
    )
    with get_db() as conn:
        df = pd.read_sql_query(sql, conn, params=(*tickers, start_date, end_date))

    _cache_put(key, df)
    return df


def get_ohlcv_close(
    tickers: list[str] | tuple[str, ...],
    start_date: str,
    end_date: str,
    *,
    cache_ttl: int = _CACHE_TTL_SEC,
) -> pd.DataFrame:
    """Return DataFrame with columns [ticker, date, close] for the given range.

    Replaces inline SQL in signals/sector_rotation.py.

    Args:
        tickers:    Tickers to query.
        start_date: ISO date string, lower bound inclusive (date >= start_date).
        end_date:   ISO date string, upper bound inclusive (date <= end_date).
        cache_ttl:  In-process TTL seconds.  Pass 0 to bypass the cache.

    Returns:
        DataFrame with columns [ticker, date, close] ordered by (date, ticker).
        Empty DataFrame (correct column names) when no rows match.
    """
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close"])

    key = ("get_ohlcv_close", tuple(sorted(tickers)), start_date, end_date)
    cached = _cache_get(key, cache_ttl)
    if cached is not None:
        return cached

    placeholders = ",".join("?" * len(tickers))
    sql = (
        f"SELECT ticker, date, close FROM ohlcv "
        f"WHERE ticker IN ({placeholders}) AND date >= ? AND date <= ? "
        f"ORDER BY date, ticker"
    )
    with get_db() as conn:
        df = pd.read_sql_query(sql, conn, params=(*tickers, start_date, end_date))

    _cache_put(key, df)
    return df


def clear_cache() -> None:
    """Test helper — clear the in-process cache between unit tests."""
    _cache.clear()
