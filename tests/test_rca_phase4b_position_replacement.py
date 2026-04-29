"""Tests for RCA #4B — Position replacement at limit (brokers/plan.py).

All tests are isolated: no real DB, no broker calls, no file I/O.
"""
from __future__ import annotations

import logging
import types
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest


# ── Minimal stubs so we can import TradePlanGenerator without the full stack ──

def _make_signal(
    ticker: str,
    confidence: float,
    entry_price: float = 100.0,
    stop_price: float = 90.0,
    take_profit: float = 120.0,
    position_size: int = 10,
    strategy: str = "momentum",
    rationale: str = "",
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        ticker=ticker,
        confidence=confidence,
        entry_price=entry_price,
        stop_price=stop_price,
        take_profit=take_profit,
        position_size=position_size,
        strategy=strategy,
        rationale=rationale,
        sector="Tech",
        features={},
        market_id="sp500",
    )


def _make_position(
    ticker: str,
    confidence: float,
    entry_price: float = 100.0,
    shares: int = 10,
    pnl: float = 0.0,
) -> types.SimpleNamespace:
    """Mimics brokers.position.Position — unrealized_pnl is a callable method."""
    pos = types.SimpleNamespace(
        ticker=ticker,
        confidence=confidence,
        entry_price=entry_price,
        shares=shares,
        strategy="momentum",
        sector="Tech",
        stop_price=entry_price * 0.9,
        take_profit=entry_price * 1.2,
    )
    # unrealized_pnl is a method: pos.unrealized_pnl(current_price) -> float
    pos.unrealized_pnl = lambda price: pnl
    return pos


def _make_portfolio(positions: list, cash: float = 10_000.0, equity_val: float = 10_000.0):
    """Minimal portfolio mock."""
    port = MagicMock()
    port.positions = positions
    port.cash = cash
    port.equity.return_value = equity_val
    port.portfolio_summary.return_value = {
        "open_positions": [{"ticker": p.ticker, "strategy": "momentum"} for p in positions],
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
    }
    port.check_risk_limits.return_value = (True, "")
    port.atlas_positions = positions
    return port


def _make_config(
    max_positions: int,
    enable_replacement: bool,
    min_confidence: float = 0.0,
) -> dict:
    return {
        "market": "sp500",
        "version": "test",
        "risk": {
            "max_open_positions": max_positions,
            "enable_position_replacement": enable_replacement,
            "min_confidence": min_confidence,
            "max_sector_concentration": 999,
            "max_gross_exposure_pct": 999,
        },
        "allocation": {"enabled": False},
        "intraday": {"entry_refinement": False},
        "event_calendar": {"enabled": False},
    }


def _run_plan(
    positions: list,
    signals: list,
    max_positions: int,
    enable_replacement: bool,
    prices: dict | None = None,
    min_confidence: float = 0.0,
):
    """Run generate_plan() and return the resulting plan dict."""
    from brokers.plan import TradePlanGenerator

    config = _make_config(max_positions, enable_replacement, min_confidence)
    portfolio = _make_portfolio(positions)
    gen = TradePlanGenerator(portfolio, config)

    _prices = prices or {p.ticker: p.entry_price for p in positions}
    for sig in signals:
        _prices.setdefault(sig.ticker, sig.entry_price)

    # Patch heavy optional dependencies so they don't blow up
    with (
        patch("brokers.plan.build_allocation_pool") as mock_pool,
        patch("brokers.plan._get_latest_overlay", return_value=None),
    ):
        alloc = MagicMock()
        alloc.is_enabled.return_value = False
        alloc.counts_summary.return_value = {}
        mock_pool.return_value = alloc

        plan = gen.generate_plan(
            signals=signals,
            exit_recommendations=[],
            prices=_prices,
            trade_date="2026-04-29",
        )
    return plan


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — flag OFF: at limit, candidate must be rejected (current behaviour)
# ─────────────────────────────────────────────────────────────────────────────
class TestDisabledByDefault:
    def test_disabled_by_default_at_limit_rejects_new_signal(self):
        """When flag is OFF and at limit, signal is rejected — no replacement."""
        positions = [
            _make_position("AAPL", confidence=0.7, pnl=-50.0),
            _make_position("MSFT", confidence=0.6, pnl=100.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.9)]  # higher conf than worst

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=2,  # already at limit
            enable_replacement=False,
        )

        # Entry must be rejected
        assert len(plan["proposed_entries"]) == 0
        assert len(plan["rejected_entries"]) == 1
        assert plan["rejected_entries"][0]["ticker"] == "NVDA"
        assert "Max positions" in plan["rejected_entries"][0]["rejection_reason"]

        # No synthetic exits added
        assert len(plan["proposed_exits"]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — flag ON: at limit, candidate has HIGHER confidence → replacement
# ─────────────────────────────────────────────────────────────────────────────
class TestEnabledReplacement:
    def test_enabled_at_limit_replaces_worst_pnl_with_higher_conf(self):
        """Flag ON, at limit, new candidate has higher conf → replacement queued."""
        positions = [
            _make_position("AAPL", confidence=0.7, pnl=-50.0),  # worst PnL
            _make_position("MSFT", confidence=0.6, pnl=100.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.9)]  # higher than worst (0.7)

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=2,
            enable_replacement=True,
        )

        # Exit of worst-PnL position queued
        assert len(plan["proposed_exits"]) == 1
        exit_item = plan["proposed_exits"][0]
        assert exit_item["ticker"] == "AAPL"
        assert exit_item["reason"] == "position_replacement"

        # New candidate admitted as an entry
        assert len(plan["proposed_entries"]) == 1
        assert plan["proposed_entries"][0]["ticker"] == "NVDA"

        # Nothing wrongly rejected
        assert len(plan["rejected_entries"]) == 0

    def test_worst_pnl_position_is_selected_for_exit(self):
        """When multiple positions, the one with lowest PnL is always replaced."""
        positions = [
            _make_position("AAPL", confidence=0.5, pnl=200.0),
            _make_position("GOOGL", confidence=0.6, pnl=-150.0),  # worst PnL
            _make_position("MSFT", confidence=0.7, pnl=50.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.9)]

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=3,
            enable_replacement=True,
        )

        assert len(plan["proposed_exits"]) == 1
        assert plan["proposed_exits"][0]["ticker"] == "GOOGL"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — flag ON: candidate has LOWER confidence → no replacement
# ─────────────────────────────────────────────────────────────────────────────
class TestNoReplacementWhenCandidateWeaker:
    def test_enabled_at_limit_does_not_replace_if_candidate_conf_lower(self):
        """Flag ON, at limit, but candidate conf ≤ worst position conf → reject."""
        positions = [
            _make_position("AAPL", confidence=0.8, pnl=-50.0),  # worst PnL but high conf
            _make_position("MSFT", confidence=0.6, pnl=100.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.75)]  # lower than worst (0.8)

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=2,
            enable_replacement=True,
        )

        # No replacement
        assert len(plan["proposed_exits"]) == 0
        assert len(plan["proposed_entries"]) == 0
        assert len(plan["rejected_entries"]) == 1
        assert plan["rejected_entries"][0]["ticker"] == "NVDA"

    def test_enabled_equal_confidence_does_not_replace(self):
        """Exactly equal confidence → no replacement (strictly greater required)."""
        positions = [
            _make_position("AAPL", confidence=0.75, pnl=-50.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.75)]

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=1,
            enable_replacement=True,
        )

        assert len(plan["proposed_exits"]) == 0
        assert len(plan["proposed_entries"]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — flag ON, BELOW limit → no replacement, normal entry
# ─────────────────────────────────────────────────────────────────────────────
class TestBelowLimit:
    def test_enabled_below_limit_no_replacement_just_normal_entry(self):
        """Flag ON but below limit → replacement logic not triggered, entry added."""
        positions = [_make_position("AAPL", confidence=0.7, pnl=-50.0)]
        signals = [_make_signal("NVDA", confidence=0.9)]

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=3,  # 1 position, max=3 → 2 slots free
            enable_replacement=True,
        )

        # Normal entry, no synthetic exit
        assert len(plan["proposed_entries"]) == 1
        assert plan["proposed_entries"][0]["ticker"] == "NVDA"
        assert len(plan["proposed_exits"]) == 0

    def test_enabled_no_existing_positions_no_replacement(self):
        """No existing positions → replacement never fires."""
        plan = _run_plan(
            positions=[],
            signals=[_make_signal("NVDA", confidence=0.9)],
            max_positions=2,
            enable_replacement=True,
        )

        assert len(plan["proposed_entries"]) == 1
        assert len(plan["proposed_exits"]) == 0


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — proposed_exits dict shape verification
# ─────────────────────────────────────────────────────────────────────────────
class TestReplacementExitShape:
    def test_replacement_queued_in_proposed_exits_with_correct_reason(self):
        """Synthetic exit dict must have reason='position_replacement', correct fields."""
        positions = [
            _make_position("AAPL", confidence=0.5, pnl=-100.0, shares=15),
        ]
        signals = [_make_signal("NVDA", confidence=0.9)]

        plan = _run_plan(
            positions=positions,
            signals=signals,
            max_positions=1,
            enable_replacement=True,
        )

        assert len(plan["proposed_exits"]) == 1
        ex = plan["proposed_exits"][0]

        assert ex["ticker"] == "AAPL"
        assert ex["reason"] == "position_replacement"
        assert ex["shares"] == 15
        assert "details" in ex
        assert "NVDA" in ex["details"]
        assert "0.90" in ex["details"] or "0.9" in ex["details"]


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — WARNING log includes both tickers + confidence values
# ─────────────────────────────────────────────────────────────────────────────
class TestWarningLog:
    def test_warning_logged_with_full_context(self, caplog):
        """WARNING log must contain both tickers, both confidence values, and 'POSITION_REPLACEMENT'."""
        positions = [
            _make_position("AAPL", confidence=0.6, pnl=-75.0),
        ]
        signals = [_make_signal("NVDA", confidence=0.88)]

        with caplog.at_level(logging.WARNING, logger="brokers.plan"):
            _run_plan(
                positions=positions,
                signals=signals,
                max_positions=1,
                enable_replacement=True,
            )

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("POSITION_REPLACEMENT" in m for m in warning_msgs), (
            f"Expected 'POSITION_REPLACEMENT' in warnings. Got: {warning_msgs}"
        )
        replacement_msg = next(m for m in warning_msgs if "POSITION_REPLACEMENT" in m)
        assert "AAPL" in replacement_msg, f"Expected 'AAPL' in: {replacement_msg}"
        assert "NVDA" in replacement_msg, f"Expected 'NVDA' in: {replacement_msg}"
        # Both confidence values present (0.6 and 0.88)
        assert "0.60" in replacement_msg or "0.6" in replacement_msg, replacement_msg
        assert "0.88" in replacement_msg, replacement_msg

    def test_no_warning_when_flag_disabled(self, caplog):
        """No POSITION_REPLACEMENT warning when flag is OFF."""
        positions = [_make_position("AAPL", confidence=0.5, pnl=-50.0)]
        signals = [_make_signal("NVDA", confidence=0.9)]

        with caplog.at_level(logging.WARNING, logger="brokers.plan"):
            _run_plan(
                positions=positions,
                signals=signals,
                max_positions=1,
                enable_replacement=False,
            )

        warning_msgs = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert not any("POSITION_REPLACEMENT" in m for m in warning_msgs)
