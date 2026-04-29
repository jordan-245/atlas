"""Regression test: no NEW ticker appears in more than one market universe.

Pre-existing intentional overlaps are documented in KNOWN_OVERLAPS below.
The test fails if a NEW overlap is introduced (e.g., FCX in both commodity_etfs
and sp500), guarding against state-pollution bugs in reconciliation and
protective-order sync.

Run with: python -m pytest tests/test_universe_disjointness.py -v
"""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path
from typing import Dict, FrozenSet, Set, Tuple

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from markets.registry import MarketRegistry  # noqa: E402


# ---------------------------------------------------------------------------
# Known intentional overlaps (pre-existing by design).
# Format: frozenset({market_a, market_b}) → frozenset({ticker, ...})
# ---------------------------------------------------------------------------
# These overlaps are INTENTIONAL:
#   asx ∩ sp500      — cross-listed companies (CCL, DOW, PRU, RMD, ALL)
#   commodity_etfs ∩ gold_etfs  — GLD is both a commodity and a gold ETF
#   defensive_etfs ∩ sector_etfs — XLP/XLU sit in both sector and defensive buckets
KNOWN_OVERLAPS: dict[FrozenSet[str], FrozenSet[str]] = {
    frozenset({"asx", "sp500"}): frozenset({"ALL", "CCL", "DOW", "PRU", "RMD"}),
    frozenset({"commodity_etfs", "gold_etfs"}): frozenset({"GLD"}),
    frozenset({"defensive_etfs", "sector_etfs"}): frozenset({"XLP", "XLU"}),
}


def _get_all_universe_tickers() -> Dict[str, Set[str]]:
    """Return {market_id: set(tickers)} for every registered market."""
    result: Dict[str, Set[str]] = {}
    for market_id in MarketRegistry.list_ids():
        market = MarketRegistry.get(market_id)
        try:
            tickers = market.get_universe_tickers()
        except NotImplementedError:
            continue
        if tickers:
            result[market_id] = set(tickers)
    return result


class TestUniverseDisjointness:
    """Every market pair must not have unexpected shared tickers."""

    def test_no_unexpected_cross_market_duplicates(self) -> None:
        """For every (mkt_a, mkt_b) pair, assert only known overlaps exist.

        Fails immediately if a NEW ticker appears in more than one market.
        Helpful failure message names the pair and the unexpected tickers.
        """
        universes = _get_all_universe_tickers()
        market_ids = sorted(universes.keys())

        failures = []
        for mkt_a, mkt_b in combinations(market_ids, 2):
            overlap = universes[mkt_a] & universes[mkt_b]
            if not overlap:
                continue
            key = frozenset({mkt_a, mkt_b})
            allowed = KNOWN_OVERLAPS.get(key, frozenset())
            unexpected = overlap - allowed
            if unexpected:
                failures.append(
                    f"Tickers {sorted(unexpected)} appear in both {mkt_a} and {mkt_b} "
                    f"(not in KNOWN_OVERLAPS — add intentionally or remove the duplicate)"
                )

        if failures:
            failure_msg = "\n".join(failures)
            pytest.fail(
                f"Unexpected universe overlap(s) detected:\n{failure_msg}\n\n"
                "Fix: assign each ticker to exactly one market, or add it to "
                "KNOWN_OVERLAPS in tests/test_universe_disjointness.py with a comment."
            )

    def test_no_fcx_in_commodity_etfs(self) -> None:
        """Explicit regression: FCX belongs to sp500 (connors_rsi2), not commodity_etfs."""
        commodity_etfs = MarketRegistry.get("commodity_etfs")
        tickers = set(commodity_etfs.get_universe_tickers())
        assert "FCX" not in tickers, (
            "FCX was re-added to commodity_etfs — it belongs to sp500 (connors_rsi2 strategy). "
            "See Task #282."
        )

    def test_no_fcx_in_both_sp500_and_commodity_etfs(self) -> None:
        """FCX must not appear simultaneously in sp500 AND commodity_etfs."""
        sp500 = set(MarketRegistry.get("sp500").get_universe_tickers())
        commodity = set(MarketRegistry.get("commodity_etfs").get_universe_tickers())
        overlap = sp500 & commodity
        assert "FCX" not in overlap, (
            "FCX found in both sp500 and commodity_etfs — cross-market duplicate causes "
            "state-pollution in reconciliation and protective-order sync."
        )

    def test_registered_markets_are_non_empty(self) -> None:
        """Sanity: every market must have at least one ticker."""
        universes = _get_all_universe_tickers()
        empty = [mid for mid, tickers in universes.items() if len(tickers) == 0]
        assert not empty, f"Markets with zero tickers: {empty}"

    def test_known_overlaps_still_exist(self) -> None:
        """Guard: if a known overlap is resolved, remove it from KNOWN_OVERLAPS.

        This prevents KNOWN_OVERLAPS from silently accumulating dead entries
        that could hide real bugs.
        """
        universes = _get_all_universe_tickers()
        stale_entries = []
        for pair_key, expected_overlap in KNOWN_OVERLAPS.items():
            mkt_a, mkt_b = sorted(pair_key)
            if mkt_a not in universes or mkt_b not in universes:
                stale_entries.append(f"Market {pair_key} no longer registered")
                continue
            actual_overlap = universes[mkt_a] & universes[mkt_b]
            missing = expected_overlap - actual_overlap
            if missing:
                stale_entries.append(
                    f"KNOWN_OVERLAPS entry {pair_key} lists {sorted(missing)} "
                    f"but those tickers no longer overlap — remove stale entry"
                )
        if stale_entries:
            pytest.fail(
                "Stale KNOWN_OVERLAPS entries:\n" + "\n".join(stale_entries) + "\n\n"
                "Update KNOWN_OVERLAPS in tests/test_universe_disjointness.py."
            )
