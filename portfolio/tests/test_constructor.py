"""
portfolio/tests/test_constructor.py — Unit tests for the portfolio constructor.

Run with:
    cd /root/atlas && python3 -m pytest portfolio/tests/test_constructor.py -v
"""
from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from typing import Optional

from portfolio.constructor import ConstructedPortfolio, PortfolioConstructor
from portfolio.limits import UNIVERSE_LIMITS, get_limit
from portfolio.correlation import (
    CORRELATION_GROUPS,
    MAX_PER_GROUP,
    check_correlation_conflicts,
)
from regime.states import RegimeState, REGIME_CONFIGS
from regime.model import RegimeClassification


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def make_signal(
    ticker: str,
    universe: str = "sp500",
    confidence: float = 0.75,
    entry: float = 100.0,
    stop: float = None,
    size: int = 10,
):
    """Return a minimal Signal-compatible object."""
    from strategies.base import Signal

    _stop = stop if stop is not None else round(entry * 0.95, 2)
    return Signal(
        ticker=ticker,
        strategy="test_strategy",
        direction="long",
        entry_price=entry,
        stop_price=_stop,
        take_profit=entry * 1.1,
        position_size=size,
        position_value=round(entry * size, 2),
        risk_amount=round((entry - _stop) * size, 2),
        confidence=confidence,
        rationale="test",
        universe=universe,
    )


def make_position(ticker: str, universe: str = "sp500", value: float = 1000.0):
    """Return a minimal position-like object."""

    @dataclass
    class FakePosition:
        ticker: str
        universe: str
        position_value: float = 1000.0

    return FakePosition(ticker=ticker, universe=universe, position_value=value)


def make_bull_regime(
    universes=None,
    sizing=1.0,
    max_pos=5,
) -> RegimeClassification:
    universes = universes or ["sp500", "sector_etfs", "commodity_etfs"]
    return RegimeClassification(
        state=RegimeState.BULL_RISK_ON,
        scores={"composite": 0.7},
        active_universes=universes,
        sizing_multiplier=sizing,
        max_positions=max_pos,
        enabled_strategies=["all"],
        reasoning="test",
        model_version="v1",
        date="2026-01-01",
    )


def make_bear_regime() -> RegimeClassification:
    cfg = REGIME_CONFIGS[RegimeState.BEAR_RISK_OFF]
    return RegimeClassification(
        state=RegimeState.BEAR_RISK_OFF,
        scores={"composite": -0.6},
        active_universes=cfg["active_universes"],
        sizing_multiplier=cfg["sizing_multiplier"],
        max_positions=cfg["max_positions"],
        enabled_strategies=cfg["strategy_types"],
        reasoning="test",
        model_version="v1",
        date="2026-01-01",
    )


# ---------------------------------------------------------------------------
# 1. Backward compatibility — no regime
# ---------------------------------------------------------------------------


class TestNoRegime:
    def test_all_sp500_signals_pass_through(self):
        """Without a regime, SP500 signals should all pass through (up to limits)."""
        constructor = PortfolioConstructor()
        signals = [make_signal(f"TICK{i}") for i in range(3)]
        result = constructor.construct(signals, equity=50_000)

        assert isinstance(result, ConstructedPortfolio)
        assert len(result.signals) == 3
        assert len(result.rejected) == 0

    def test_default_regime_state(self):
        constructor = PortfolioConstructor()
        result = constructor.construct([], equity=10_000)
        assert result.regime_state == "no_regime"

    def test_default_sizing_multiplier(self):
        constructor = PortfolioConstructor()
        result = constructor.construct([], equity=10_000)
        assert result.sizing_multiplier == 1.0

    def test_non_sp500_signals_rejected_without_regime(self):
        """Without regime, only sp500 is active — other universes rejected."""
        constructor = PortfolioConstructor()
        signals = [
            make_signal("SPY", universe="sp500"),
            make_signal("GLD", universe="gold_etfs"),
        ]
        result = constructor.construct(signals, equity=50_000)
        tickers = [s.ticker for s in result.signals]
        assert "SPY" in tickers
        assert "GLD" not in tickers
        assert len(result.rejected) == 1


# ---------------------------------------------------------------------------
# 2. Regime filtering — active universes
# ---------------------------------------------------------------------------


class TestRegimeFiltering:
    def test_signals_outside_active_universes_rejected(self):
        """Bear regime only allows treasury/gold/defensive; sp500 signals rejected."""
        regime = make_bear_regime()  # active: treasury_etfs, gold_etfs, defensive_etfs
        constructor = PortfolioConstructor(regime_classification=regime)

        signals = [
            make_signal("AAPL", universe="sp500"),
            make_signal("TLT", universe="treasury_etfs"),
            make_signal("GLD", universe="gold_etfs"),
        ]
        result = constructor.construct(signals, equity=50_000)
        tickers = [s.ticker for s in result.signals]

        assert "AAPL" not in tickers
        assert "TLT" in tickers
        assert "GLD" in tickers

    def test_all_active_universe_signals_accepted(self):
        regime = make_bull_regime(universes=["sp500"])
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [make_signal(f"T{i}", universe="sp500") for i in range(4)]
        result = constructor.construct(signals, equity=200_000)
        # sp500 limit is 5, max_positions is 5 — all 4 should pass
        assert len(result.signals) == 4

    def test_regime_state_recorded(self):
        regime = make_bull_regime()
        constructor = PortfolioConstructor(regime_classification=regime)
        result = constructor.construct([], equity=10_000)
        assert result.regime_state == RegimeState.BULL_RISK_ON.value


# ---------------------------------------------------------------------------
# 3. Per-universe position limits
# ---------------------------------------------------------------------------


class TestUniversePositionLimits:
    def test_sp500_capped_at_max_positions(self):
        """sp500 limit is 5 positions — 6th signal should be rejected."""
        regime = make_bull_regime(universes=["sp500"], max_pos=10)
        constructor = PortfolioConstructor(regime_classification=regime)
        # 6 sp500 signals, no existing positions
        signals = [make_signal(f"TICK{i}", universe="sp500") for i in range(6)]
        result = constructor.construct(signals, equity=500_000)

        sp500_limit = UNIVERSE_LIMITS["sp500"]["max_positions"]
        assert len(result.signals) <= sp500_limit

    def test_sector_etfs_capped_at_3(self):
        regime = make_bull_regime(universes=["sector_etfs"], max_pos=10)
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [
            make_signal("XLE", universe="sector_etfs"),
            make_signal("XLK", universe="sector_etfs"),
            make_signal("XLF", universe="sector_etfs"),
            make_signal("XLV", universe="sector_etfs"),  # 4th — should be rejected
        ]
        result = constructor.construct(signals, equity=200_000)
        sector_tickers = [s.ticker for s in result.signals]
        # Only 3 sector_etfs positions allowed
        assert len(sector_tickers) <= 3

    def test_existing_positions_count_toward_limit(self):
        """If 3 sp500 positions are already open, only 2 more can be added."""
        regime = make_bull_regime(universes=["sp500"], max_pos=10)
        constructor = PortfolioConstructor(regime_classification=regime)

        existing = [make_position(f"EXIST{i}", universe="sp500") for i in range(3)]
        signals = [make_signal(f"NEW{i}", universe="sp500") for i in range(4)]

        result = constructor.construct(signals, equity=500_000, existing_positions=existing)
        # sp500 limit = 5, 3 existing → 2 slots remain
        assert len(result.signals) <= 2

    def test_equity_exposure_cap_enforced(self):
        """If a signal would push universe equity above max_pct_equity, reject it."""
        regime = make_bull_regime(universes=["gold_etfs"], max_pos=10)
        constructor = PortfolioConstructor(regime_classification=regime)

        equity = 10_000
        # gold_etfs max_pct_equity = 0.20 → max $2,000 exposure
        # Each signal is worth $1,500 → 2nd should be rejected (would be $3,000 = 30%)
        signals = [
            make_signal("GLD", universe="gold_etfs", entry=150.0, size=10),  # $1,500
            make_signal("IAU", universe="gold_etfs", entry=150.0, size=10),  # $1,500 → would exceed
        ]
        result = constructor.construct(signals, equity=equity)
        assert len(result.signals) <= 1


# ---------------------------------------------------------------------------
# 4. Correlation checks
# ---------------------------------------------------------------------------


class TestCorrelationCheck:
    def test_max_two_per_group(self):
        """Within a correlation group, at most MAX_PER_GROUP signals accepted."""
        # Bond group: TLT, IEF, SHY, TIP, BND
        signals = [
            make_signal("TLT", universe="treasury_etfs", confidence=0.9),
            make_signal("IEF", universe="treasury_etfs", confidence=0.8),
            make_signal("SHY", universe="treasury_etfs", confidence=0.7),
            make_signal("TIP", universe="treasury_etfs", confidence=0.6),
        ]
        filtered = check_correlation_conflicts(signals)
        assert len(filtered) <= MAX_PER_GROUP

    def test_highest_confidence_retained(self):
        """When filtering a group, the highest-confidence signals are kept."""
        signals = [
            make_signal("GLD", universe="gold_etfs", confidence=0.5),
            make_signal("IAU", universe="gold_etfs", confidence=0.9),
            make_signal("GDX", universe="gold_etfs", confidence=0.3),
        ]
        filtered = check_correlation_conflicts(signals)
        tickers = {s.ticker for s in filtered}
        assert "IAU" in tickers  # highest confidence
        assert "GLD" in tickers  # second highest
        assert "GDX" not in tickers  # lowest confidence, dropped

    def test_uncorrelated_tickers_pass_through(self):
        """Tickers not in any correlation group are never filtered."""
        signals = [
            make_signal("AAPL", universe="sp500"),
            make_signal("MSFT", universe="sp500"),
            make_signal("GOOG", universe="sp500"),
        ]
        filtered = check_correlation_conflicts(signals)
        assert len(filtered) == len(signals)

    def test_empty_signal_list(self):
        assert check_correlation_conflicts([]) == []

    def test_correlation_groups_defined(self):
        """Sanity-check that CORRELATION_GROUPS has expected keys."""
        assert "gold" in CORRELATION_GROUPS
        assert "bonds" in CORRELATION_GROUPS
        assert "energy" in CORRELATION_GROUPS
        assert "defensive" in CORRELATION_GROUPS

    def test_correlation_filter_in_construct(self):
        """End-to-end: correlation filter applied inside construct()."""
        regime = make_bull_regime(
            universes=["treasury_etfs"], max_pos=10
        )
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [
            make_signal("TLT", universe="treasury_etfs", confidence=0.9),
            make_signal("IEF", universe="treasury_etfs", confidence=0.8),
            make_signal("SHY", universe="treasury_etfs", confidence=0.7),
        ]
        result = constructor.construct(signals, equity=500_000)
        assert len(result.signals) <= MAX_PER_GROUP


# ---------------------------------------------------------------------------
# 5. Sizing multiplier
# ---------------------------------------------------------------------------


class TestSizingMultiplier:
    def test_half_sizing_applied(self):
        """A 0.5 sizing multiplier halves position_size."""
        regime = make_bull_regime(sizing=0.5)
        constructor = PortfolioConstructor(regime_classification=regime)
        sig = make_signal("AAPL", universe="sp500", size=10)
        result = constructor.construct([sig], equity=50_000)

        assert len(result.signals) == 1
        assert result.signals[0].position_size == 5
        assert result.sizing_multiplier == 0.5

    def test_full_sizing_unchanged(self):
        """A 1.0 sizing multiplier leaves position_size unchanged."""
        regime = make_bull_regime(sizing=1.0)
        constructor = PortfolioConstructor(regime_classification=regime)
        sig = make_signal("AAPL", universe="sp500", size=10)
        result = constructor.construct([sig], equity=50_000)

        assert result.signals[0].position_size == 10

    def test_sizing_recorded_in_result(self):
        regime = make_bull_regime(sizing=0.7)
        constructor = PortfolioConstructor(regime_classification=regime)
        result = constructor.construct([], equity=10_000)
        assert result.sizing_multiplier == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# 6. Confidence ranking
# ---------------------------------------------------------------------------


class TestConfidenceRanking:
    def test_higher_confidence_selected_first(self):
        """When only 1 slot is available, the highest-confidence signal wins."""
        regime = make_bull_regime(universes=["sp500"], max_pos=1)
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [
            make_signal("LOW", universe="sp500", confidence=0.3),
            make_signal("HIGH", universe="sp500", confidence=0.9),
            make_signal("MID", universe="sp500", confidence=0.6),
        ]
        result = constructor.construct(signals, equity=500_000)

        assert len(result.signals) == 1
        assert result.signals[0].ticker == "HIGH"

    def test_ranking_across_universe_limit(self):
        """Confidence ranking is applied within each universe bucket."""
        regime = make_bull_regime(universes=["sector_etfs"], max_pos=10)
        constructor = PortfolioConstructor(regime_classification=regime)
        # sector_etfs limit = 3; send 5 signals, verify top-3 by confidence kept
        signals = [
            make_signal("XLE", universe="sector_etfs", confidence=0.9),
            make_signal("XLK", universe="sector_etfs", confidence=0.4),
            make_signal("XLF", universe="sector_etfs", confidence=0.8),
            make_signal("XLV", universe="sector_etfs", confidence=0.7),
            make_signal("XLY", universe="sector_etfs", confidence=0.2),
        ]
        result = constructor.construct(signals, equity=500_000)
        selected_tickers = {s.ticker for s in result.signals}
        # Top 3 by confidence: XLE (0.9), XLF (0.8), XLV (0.7)
        assert "XLE" in selected_tickers
        assert "XLF" in selected_tickers
        assert "XLV" in selected_tickers
        assert "XLK" not in selected_tickers
        assert "XLY" not in selected_tickers


# ---------------------------------------------------------------------------
# 7. Universe exposure summary
# ---------------------------------------------------------------------------


class TestUniverseExposure:
    def test_exposure_dict_populated(self):
        regime = make_bull_regime(universes=["sp500", "sector_etfs"])
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [
            make_signal("AAPL", universe="sp500", entry=100.0, size=10),
            make_signal("XLE", universe="sector_etfs", entry=80.0, stop=76.0, size=5),
        ]
        result = constructor.construct(signals, equity=10_000)

        assert "sp500" in result.universe_exposure
        assert "sector_etfs" in result.universe_exposure
        assert result.universe_exposure["sp500"]["positions"] == 1
        assert result.universe_exposure["sector_etfs"]["positions"] == 1

    def test_pct_equity_calculated(self):
        regime = make_bull_regime(universes=["sp500"])
        constructor = PortfolioConstructor(regime_classification=regime)
        # 10 shares at $100 = $1,000 value → 10% of $10k equity
        signals = [make_signal("AAPL", universe="sp500", entry=100.0, size=10)]
        result = constructor.construct(signals, equity=10_000)

        assert result.universe_exposure["sp500"]["pct_equity"] == pytest.approx(0.10)

    def test_empty_signals_no_exposure(self):
        constructor = PortfolioConstructor()
        result = constructor.construct([], equity=10_000)
        assert result.universe_exposure == {}
        assert result.total_positions == 0


# ---------------------------------------------------------------------------
# 8. ConstructedPortfolio shape
# ---------------------------------------------------------------------------


class TestPortfolioShape:
    def test_result_is_constructed_portfolio(self):
        constructor = PortfolioConstructor()
        result = constructor.construct([], equity=10_000)
        assert isinstance(result, ConstructedPortfolio)

    def test_reasoning_non_empty(self):
        constructor = PortfolioConstructor()
        signals = [make_signal("AAPL")]
        result = constructor.construct(signals, equity=10_000)
        assert len(result.reasoning) > 0

    def test_rejected_entries_have_reason(self):
        """Every rejected entry must be a (signal, reason_str) pair."""
        regime = make_bear_regime()  # active: treasury_etfs, gold_etfs, defensive_etfs
        constructor = PortfolioConstructor(regime_classification=regime)
        signals = [make_signal("AAPL", universe="sp500")]
        result = constructor.construct(signals, equity=50_000)

        assert len(result.rejected) == 1
        sig, reason = result.rejected[0]
        assert sig.ticker == "AAPL"
        assert isinstance(reason, str)
        assert len(reason) > 0
