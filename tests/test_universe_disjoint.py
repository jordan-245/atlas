"""tests/test_universe_disjoint.py — Universe disjointness enforcement.

Tests for:
1. FCX is in sp500 only (not in commodity_etfs)
2. assert_universes_disjoint() passes on the current universe configuration
3. assert_universes_disjoint() catches newly introduced violations (synthetic test)

Run with: python3 -m pytest tests/test_universe_disjoint.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))


# ─── Fixtures & helpers ────────────────────────────────────────────────────────

def _get_commodity_tickers() -> set[str]:
    """Return the commodity_etfs static ticker set."""
    from universe.definitions import get_universe_tickers
    return set(get_universe_tickers("commodity_etfs"))


def _get_sp500_market_tickers() -> set[str]:
    """Return sp500 tickers from the market profile (not dynamic builder)."""
    from markets.registry import MarketRegistry
    market = MarketRegistry.get("sp500")
    return set(market.get_universe_tickers())


# ─── Task 4 tests ─────────────────────────────────────────────────────────────

class TestFCXUniverse:
    """FCX canonical universe = sp500 only."""

    def test_fcx_only_in_sp500(self) -> None:
        """FCX appears in sp500 universe but NOT in commodity_etfs.

        Canonical classification: FCX (Freeport-McMoRan) is an S&P 500 equity
        — a copper miner — not a commodity ETF. It was removed from commodity_etfs
        on 2026-05-14 to eliminate the contamination.
        """
        commodity_tickers = _get_commodity_tickers()
        sp500_tickers = _get_sp500_market_tickers()

        assert "FCX" not in commodity_tickers, (
            "FCX must NOT be in commodity_etfs — it is an S&P 500 equity (Freeport-McMoRan). "
            "Canonical universe is sp500 only. If it re-appears here, check "
            "markets/etf_markets.py and universe/definitions.py."
        )
        assert "FCX" in sp500_tickers, (
            "FCX must be in sp500 — it is an S&P 500 constituent (copper miner). "
            "Check markets/sp500.py if this assertion fails."
        )

    def test_fcx_membership_via_derive_universe(self) -> None:
        """derive_universe('FCX') returns 'sp500' (not commodity_etfs)."""
        from universe.membership import derive_universe, clear_cache

        # Clear cache so we pick up any in-process changes
        clear_cache()

        result = derive_universe("FCX")
        assert result == "sp500", (
            f"derive_universe('FCX') returned {result!r}, expected 'sp500'. "
            "FCX should be uniquely in sp500 after the 2026-05-14 cleanup. "
            "If it's still in commodity_etfs, check universe/definitions.py."
        )


class TestUniverseDisjointness:
    """assert_universes_disjoint() validation."""

    def test_universes_disjoint_or_whitelisted(self) -> None:
        """Call assert_universes_disjoint() — must pass without raising.

        The current universe configuration should have no unexpected overlaps.
        Known intentional overlaps (GLD, XLP, XLU) are whitelisted in
        universe/builder._UNIVERSE_KNOWN_OVERLAPS.
        """
        from universe.builder import assert_universes_disjoint

        # Should not raise
        assert_universes_disjoint()

    def test_disjointness_catches_new_violation(self) -> None:
        """Inject a synthetic violation into UNIVERSES; assert_universes_disjoint() must raise.

        This test verifies the guard is actually checking for violations,
        not just silently passing.
        """
        from universe import definitions, builder
        from universe.builder import assert_universes_disjoint

        # Inject a fake duplicate into treasury_etfs (TLT exists there)
        # We'll temporarily add TLT to gold_etfs as well
        original_gold_tickers = list(definitions.UNIVERSES["gold_etfs"]["tickers"])
        original_builder_overlaps = builder._UNIVERSE_KNOWN_OVERLAPS

        try:
            # Add TLT to gold_etfs to create a violation with treasury_etfs
            definitions.UNIVERSES["gold_etfs"]["tickers"] = original_gold_tickers + ["TLT"]
            # Ensure TLT is NOT in the whitelist
            builder._UNIVERSE_KNOWN_OVERLAPS = frozenset(
                e for e in original_builder_overlaps
                if e[0] != "TLT"  # exclude any hypothetical TLT entry
            )

            with pytest.raises(AssertionError) as exc_info:
                assert_universes_disjoint()

            error_msg = str(exc_info.value)
            assert "TLT" in error_msg, (
                f"Expected AssertionError to mention 'TLT', got: {error_msg}"
            )
            assert "gold_etfs" in error_msg or "treasury_etfs" in error_msg, (
                f"Expected AssertionError to mention the markets, got: {error_msg}"
            )

        finally:
            # Always restore original state
            definitions.UNIVERSES["gold_etfs"]["tickers"] = original_gold_tickers
            builder._UNIVERSE_KNOWN_OVERLAPS = original_builder_overlaps

    def test_known_overlaps_still_match_reality(self) -> None:
        """The whitelist _UNIVERSE_KNOWN_OVERLAPS must have no stale entries.

        If an overlap is resolved (e.g., GLD removed from commodity_etfs),
        the corresponding whitelist entry must be removed too — otherwise it
        silently masks potential future bugs.
        """
        from universe.definitions import UNIVERSES, get_universe_tickers
        from universe.builder import _UNIVERSE_KNOWN_OVERLAPS

        stale = []
        for ticker, universe_a, universe_b in _UNIVERSE_KNOWN_OVERLAPS:
            try:
                tickers_a = set(get_universe_tickers(universe_a))
                tickers_b = set(get_universe_tickers(universe_b))
            except (KeyError, ValueError):
                stale.append(
                    f"({ticker!r}, {universe_a!r}, {universe_b!r}) — universe not found"
                )
                continue

            if ticker not in tickers_a or ticker not in tickers_b:
                stale.append(
                    f"({ticker!r}, {universe_a!r}, {universe_b!r}) — "
                    f"ticker in_a={ticker in tickers_a}, in_b={ticker in tickers_b} "
                    f"(stale whitelist entry)"
                )

        if stale:
            pytest.fail(
                "Stale entries in _UNIVERSE_KNOWN_OVERLAPS:\n  "
                + "\n  ".join(stale)
                + "\nRemove entries where the overlap no longer exists."
            )
