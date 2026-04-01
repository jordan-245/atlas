"""
portfolio/correlation.py — Static cross-universe correlation conflict detection.

Design
------
Rather than computing live price correlations (which would require price data
inside the constructor), we maintain a curated map of "correlation groups" —
known clusters of tickers that move together and therefore should not all be
held simultaneously.

Within each group the constructor keeps at most MAX_PER_GROUP positions,
preferring the signal with the highest confidence score when trimming.

Usage
-----
    from portfolio.correlation import check_correlation_conflicts, CORRELATION_GROUPS
    from strategies.base import Signal

    filtered = check_correlation_conflicts(signals)
"""
from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known high-correlation ticker groups
# ---------------------------------------------------------------------------

#: Map of group-name → list of tickers that are highly correlated.
#: Tickers may appear in multiple groups if they cross themes.
CORRELATION_GROUPS: dict[str, list[str]] = {
    "energy":    ["XLE", "XOP", "USO", "UNG"],
    "gold":      ["GLD", "IAU", "GDX", "GDXJ"],
    "defensive": ["SH", "PSQ", "XLU", "XLP"],
    "bonds":     ["TLT", "IEF", "SHY", "TIP", "BND"],
}

#: Maximum simultaneous positions from any single correlation group.
MAX_PER_GROUP: int = 2

# Pre-build a reverse map: ticker → list[group_name] for O(1) lookups.
_TICKER_TO_GROUPS: dict[str, list[str]] = defaultdict(list)
for _group, _tickers in CORRELATION_GROUPS.items():
    for _ticker in _tickers:
        _TICKER_TO_GROUPS[_ticker].append(_group)


def check_correlation_conflicts(signals: list) -> list:
    """Filter *signals* to avoid over-concentration in correlated groups.

    Algorithm
    ---------
    1. For every correlation group, collect all signals whose ticker belongs
       to that group.
    2. Sort by confidence descending; keep at most MAX_PER_GROUP.
    3. Mark the rest as rejected (they are not returned).

    Signals whose tickers are not in any correlation group pass through
    unchanged.

    Parameters
    ----------
    signals:
        List of :class:`strategies.base.Signal` objects.

    Returns
    -------
    list
        Filtered list of signals with at most MAX_PER_GROUP per correlation
        group (highest confidence retained).
    """
    if not signals:
        return []

    # Track which signals are *rejected* due to correlation limits.
    rejected_ids: set[int] = set()

    # Group signals by correlation group.
    group_signals: dict[str, list] = defaultdict(list)
    for sig in signals:
        for group in _TICKER_TO_GROUPS.get(sig.ticker, []):
            group_signals[group].append(sig)

    for group, group_sigs in group_signals.items():
        if len(group_sigs) <= MAX_PER_GROUP:
            continue  # no conflict

        # Sort best-confidence first; reject the tail.
        ranked = sorted(group_sigs, key=lambda s: s.confidence, reverse=True)
        to_reject = ranked[MAX_PER_GROUP:]
        for sig in to_reject:
            if id(sig) not in rejected_ids:
                logger.debug(
                    "Correlation filter: rejecting %s (group=%s, confidence=%.3f)",
                    sig.ticker, group, sig.confidence,
                )
            rejected_ids.add(id(sig))

    filtered = [s for s in signals if id(s) not in rejected_ids]
    return filtered
