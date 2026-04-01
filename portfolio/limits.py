"""
portfolio/limits.py — Per-universe position and equity-exposure caps.

UNIVERSE_LIMITS defines hard ceilings for each of the six Atlas universes.
The PortfolioConstructor enforces these limits when selecting signals.

Keys
----
max_positions   Maximum simultaneous open positions within the universe.
max_pct_equity  Maximum fraction of total portfolio equity deployed in the
                universe (e.g. 0.60 = 60 %).
"""
from __future__ import annotations

from typing import TypedDict


class UniverseLimit(TypedDict):
    max_positions: int
    max_pct_equity: float


UNIVERSE_LIMITS: dict[str, UniverseLimit] = {
    "sp500":          {"max_positions": 5, "max_pct_equity": 0.60},
    "sector_etfs":    {"max_positions": 3, "max_pct_equity": 0.30},
    "treasury_etfs":  {"max_positions": 2, "max_pct_equity": 0.40},
    "commodity_etfs": {"max_positions": 3, "max_pct_equity": 0.30},
    "gold_etfs":      {"max_positions": 2, "max_pct_equity": 0.20},
    "defensive_etfs": {"max_positions": 2, "max_pct_equity": 0.30},
}

# Fallback used when a signal's universe is not in UNIVERSE_LIMITS.
_DEFAULT_LIMIT: UniverseLimit = {"max_positions": 3, "max_pct_equity": 0.30}


def get_limit(universe: str) -> UniverseLimit:
    """Return the limit config for *universe*, falling back to a safe default."""
    return UNIVERSE_LIMITS.get(universe, _DEFAULT_LIMIT)
