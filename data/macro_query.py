"""Macro-indicator query helpers — private loaders for signal modules.

Centralizes inline SQL previously duplicated across signals/. Provides
in-process TTL cache to avoid hammering the DB on repeated calls within
a cron cycle.

Deviation from spec design
--------------------------
``get_macro_indicators_cols`` uses signature ``(cols, end_date, limit)`` rather
than the spec's ``(cols, start_date, end_date)``.  The actual SQL pattern in
``signals/macro_surprise.py`` is::

    WHERE date <= end_date ORDER BY date DESC LIMIT limit

which retrieves the *N most-recent rows up to a date*, not a date range.
Forcing a start_date into that query would change semantics; creating a
specific function that matches the real usage is safer.
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

# ---------------------------------------------------------------------------
# Column allowlist — all columns that exist in macro_indicators.
# Used to validate caller-supplied column names and prevent SQL injection.
# Derived from db/schema.sql CREATE TABLE macro_indicators.
# ---------------------------------------------------------------------------
_MACRO_COL_ALLOWLIST: frozenset[str] = frozenset(
    {
        "date",
        "vix",
        "vix3m",
        "vix_term_ratio",
        "yield_10y",
        "yield_2y",
        "yield_3m",
        "yield_curve_10y2y",
        "yield_curve_10y3m",
        "credit_oas",
        "dxy",
        "gold",
        "copper",
        "gold_copper_ratio",
        "fed_funds",
        "unemployment_claims",
        "spy_close",
        "spy_200dma",
        "spy_above_200dma",
        "spy_200dma_slope",
    }
)


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

def get_macro_indicators_cols(
    cols: list[str],
    end_date: str,
    limit: int,
    *,
    cache_ttl: int = _CACHE_TTL_SEC,
) -> pd.DataFrame:
    """Query macro_indicators for specific columns, date-descending with LIMIT.

    Replaces inline SQL in signals/macro_surprise.py::compute_macro_surprises.

    The underlying SQL is::

        SELECT date, <cols> FROM macro_indicators
        WHERE date <= end_date ORDER BY date DESC LIMIT limit

    Rows are returned newest-first (caller should sort if chronological order
    is needed).

    Args:
        cols:      Data column names to SELECT.  ``"date"`` is always prepended
                   automatically — do not include it in *cols*.  Every name must
                   be in ``_MACRO_COL_ALLOWLIST`` or ValueError is raised.
        end_date:  ISO date string upper bound (inclusive, ``date <= end_date``).
        limit:     Maximum number of rows to return.
        cache_ttl: In-process TTL seconds.  Pass 0 to bypass.

    Returns:
        DataFrame with columns ``["date"] + cols`` ordered newest-first.
        Empty DataFrame when no rows match.

    Raises:
        ValueError: if any column name is not in the allowlist.
    """
    # Prepend "date" once, deduplicating in case the caller included it.
    all_cols: list[str] = ["date"] + [c for c in cols if c != "date"]

    # Validate every requested column against the allowlist.
    for col in all_cols:
        if col not in _MACRO_COL_ALLOWLIST:
            raise ValueError(
                f"Column {col!r} is not in the macro_indicators allowlist. "
                f"Allowed: {sorted(_MACRO_COL_ALLOWLIST)}"
            )

    key = ("get_macro_indicators_cols", tuple(all_cols), end_date, limit)
    cached = _cache_get(key, cache_ttl)
    if cached is not None:
        return cached

    col_clause = ", ".join(all_cols)
    sql = (
        f"SELECT {col_clause} FROM macro_indicators "
        f"WHERE date <= ? ORDER BY date DESC LIMIT ?"
    )
    with get_db() as conn:
        df = pd.read_sql_query(sql, conn, params=(end_date, limit))

    _cache_put(key, df)
    return df


def get_vix_term_structure(
    start_date: str,
    end_date: str,
    *,
    cache_ttl: int = _CACHE_TTL_SEC,
) -> pd.DataFrame:
    """Return raw DataFrame with columns [date, vix, vix3m] in the given range.

    Replaces the inline SQL in signals/vix_term_structure.py.
    Only rows where both vix and vix3m are non-NULL are returned.
    The ratio computation and regime classification remain in the signal file.

    Args:
        start_date: ISO date string (inclusive lower bound, BETWEEN lower).
        end_date:   ISO date string (inclusive upper bound, BETWEEN upper).
        cache_ttl:  In-process TTL seconds.  Pass 0 to bypass.

    Returns:
        DataFrame with columns [date, vix, vix3m] ordered ascending by date.
        Empty DataFrame when no rows match.
    """
    key = ("get_vix_term_structure", start_date, end_date)
    cached = _cache_get(key, cache_ttl)
    if cached is not None:
        return cached

    sql = (
        "SELECT date, vix, vix3m FROM macro_indicators "
        "WHERE date BETWEEN ? AND ? AND vix IS NOT NULL AND vix3m IS NOT NULL "
        "ORDER BY date"
    )
    with get_db() as conn:
        df = pd.read_sql_query(sql, conn, params=(start_date, end_date))

    _cache_put(key, df)
    return df


def clear_cache() -> None:
    """Test helper — clear the in-process cache between unit tests."""
    _cache.clear()
