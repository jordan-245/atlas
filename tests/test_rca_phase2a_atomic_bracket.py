"""Regression tests — RCA Phase 2A: atomic bracket placement.

Verifies that `_execute_entry` always submits a single BRACKET (or OTO) order
via `place_order`, never calling `place_protective_stop` and `place_take_profit`
sequentially, which would open a race window between SL and TP placement.

Also verifies the 2:1 R/R fallback TP synthesis when the entry signal carries
a stop but no take-profit.

Tests:
  1. test_entry_with_stop_and_tp_uses_single_bracket_call
  2. test_entry_with_stop_but_no_tp_synthesizes_2to1_rr_tp
  3. test_entry_with_no_stop_no_tp_uses_plain_limit_no_bracket
  4. test_no_sequential_sl_then_tp_call_pattern
  5. test_bracket_legs_persist_to_trades_table

Run:
    cd /root/atlas && python3 -m pytest tests/test_rca_phase2a_atomic_bracket.py -v --timeout=30
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from brokers.live_executor import LiveExecutor
from brokers.base import (
    AccountInfo,
    OrderResult,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionInfo,
)


# ─── Shared helpers ──────────────────────────────────────────────────────────


def _minimal_config() -> dict:
    return {
        "version": "test-v1.0",
        "market_id": "sp500",
        "trading": {
            "mode": "live",
            "live_enabled": True,
            "live_safety": {
                "max_order_value": 50_000,
                "max_daily_orders": 50,
                "dry_run_first": False,
                "max_daily_loss_pct": 0.05,
            },
        },
        "risk": {
            "starting_equity": 10_000.0,
            "max_risk_per_trade_pct": 0.02,
            "max_open_positions": 10,
            "leverage": 2.0,
        },
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
    }


def _make_executor() -> LiveExecutor:
    """Return a pre-connected LiveExecutor with a mock broker attached."""
    cfg = _minimal_config()
    ex = LiveExecutor(cfg)
    ex._connected = True
    ex._halted = False
    ex._daily_date = "2026-04-29"
    ex._daily_start_equity = 10_000.0
    return ex


def _mock_broker(equity: float = 10_000.0) -> MagicMock:
    broker = MagicMock()
    broker.get_account_info.return_value = AccountInfo(equity=equity, cash=5_000.0)
    broker.get_positions.return_value = []
    return broker


def _submitted_result(ticker: str = "AAPL") -> OrderResult:
    return OrderResult(
        success=True,
        order_id=f"ORD-{ticker}-001",
        ticker=ticker,
        side=OrderSide.BUY,
        status=OrderStatus.SUBMITTED,
        requested_qty=10,
        filled_qty=0,
        fill_price=0.0,
        raw={},
    )


def _filled_result(ticker: str = "AAPL", price: float = 100.0, qty: int = 10,
                   legs: list | None = None) -> OrderResult:
    return OrderResult(
        success=True,
        order_id=f"ORD-{ticker}-001",
        ticker=ticker,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        requested_qty=qty,
        filled_qty=qty,
        fill_price=price,
        raw={"legs": legs or [], "filled_qty": str(qty)},
    )


def _entry(ticker: str = "AAPL",
           entry_price: float = 100.0,
           stop_price: float = 95.0,
           take_profit: float | None = None,
           qty: int = 10) -> dict:
    return {
        "ticker": ticker,
        "entry_price": entry_price,
        "position_size": qty,
        "strategy": "mtf_momentum",
        "confidence": 0.75,
        "stop_price": stop_price,
        "take_profit": take_profit,
    }


# Patch targets
_KILL_SWITCH   = "brokers.kill_switch.is_halted"
_PRICE_ARB     = "brokers.price_arbiter.is_ticker_halted"
_PREFLIGHT     = "brokers.live_executor.preflight_check_order"
_JOURNAL       = "brokers.live_executor._journal_entry"
_REGIME_MODEL  = "brokers.live_executor._get_regime_model"
_CUG_CHECK     = "risk.cross_universe_guard.check_entry"
_GEG_CHECK     = "risk.gross_exposure_guard.check_gross_exposure"
_TELEGRAM      = "utils.telegram.send_message"
_TRADE_LEDGER  = "journal.logger.TradeLedger"
_UPDATE_ORDERS = "db.atlas_db.update_trade_protective_orders"


def _mock_allowed_guard():
    """Return a GuardDecision-like object with allowed=True."""
    from risk.cross_universe_guard import GuardDecision
    return GuardDecision(allowed=True, reason="test")


def _standard_patches(extra_patches=None):
    """Return a list of (target, value) tuples for standard entry patches."""
    regime = MagicMock()
    regime.classify_current.return_value.state.value = "bull_risk_on"
    patches = [
        (_KILL_SWITCH, False),
        (_PRICE_ARB, False),
        (_PREFLIGHT, []),          # no preflight errors → proceed
        (_JOURNAL, MagicMock()),
        (_TELEGRAM, MagicMock()),
    ]
    return patches


# ═══════════════════════════════════════════════════════════════════════════
# 1. Real TP present → single BRACKET via place_order, no synthesis needed
# ═══════════════════════════════════════════════════════════════════════════

class TestAtomicBracketWithRealTp:
    """Case B baseline: entry has stop AND take_profit — bracket fires, no synthesis."""

    def test_entry_with_stop_and_tp_uses_single_bracket_call(self):
        """When entry has both stop_price and take_profit, place_order is called ONCE
        with both stop_loss_price and take_profit_price populated — no second call."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("AAPL")
        ex._broker = broker

        entry = _entry(
            ticker="AAPL",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,  # real TP provided
        )

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            result = ex._execute_entry(entry, "2026-04-29")

        # Exactly one call to broker.place_order (the LIMIT/BRACKET entry)
        assert broker.place_order.call_count == 1, (
            f"Expected 1 place_order call, got {broker.place_order.call_count}"
        )

        call_kwargs = broker.place_order.call_args[1]
        assert call_kwargs.get("stop_loss_price") == 95.0, (
            f"Expected stop_loss_price=95.0, got {call_kwargs.get('stop_loss_price')}"
        )
        assert call_kwargs.get("take_profit_price") == 110.0, (
            f"Expected take_profit_price=110.0, got {call_kwargs.get('take_profit_price')}"
        )
        assert result.get("success") is True


# ═══════════════════════════════════════════════════════════════════════════
# 2. No TP provided → synthesize 2:1 R/R take_profit
# ═══════════════════════════════════════════════════════════════════════════

class TestAtomicBracketSynthesizedTp:
    """RCA #2A fix: when signal has stop but no TP, synthesize TP = entry + 2×risk."""

    def test_entry_with_stop_but_no_tp_synthesizes_2to1_rr_tp(self):
        """Signal has stop=95, no TP → synthesized TP = 100 + 2×(100-95) = 110."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("TSLA")
        ex._broker = broker

        entry = _entry(
            ticker="TSLA",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=None,  # no TP from strategy
        )

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            result = ex._execute_entry(entry, "2026-04-29")

        # Expected synthesized TP = 100 + 2*(100 - 95) = 110.0
        expected_tp = 100.0 + 2.0 * (100.0 - 95.0)  # = 110.0

        assert broker.place_order.call_count == 1
        call_kwargs = broker.place_order.call_args[1]
        assert call_kwargs.get("stop_loss_price") == 95.0, (
            f"Expected stop_loss_price=95.0, got {call_kwargs.get('stop_loss_price')}"
        )
        assert call_kwargs.get("take_profit_price") == expected_tp, (
            f"Expected synthesized take_profit_price={expected_tp}, "
            f"got {call_kwargs.get('take_profit_price')}"
        )

    def test_entry_with_stop_and_zero_tp_synthesizes_2to1_rr_tp(self):
        """take_profit=0 (explicit zero, same as missing) → synthesis fires."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("MSFT")
        ex._broker = broker

        entry = _entry(
            ticker="MSFT",
            entry_price=200.0,
            stop_price=190.0,
            take_profit=0,  # explicit zero
        )

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "transition_uncertain"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            result = ex._execute_entry(entry, "2026-04-29")

        # entry=200, stop=190 → risk=10 → tp = 200 + 2×10 = 220
        expected_tp = 200.0 + 2.0 * (200.0 - 190.0)  # = 220.0

        call_kwargs = broker.place_order.call_args[1]
        assert call_kwargs.get("take_profit_price") == expected_tp, (
            f"Expected synthesized take_profit_price={expected_tp}, "
            f"got {call_kwargs.get('take_profit_price')}"
        )

    def test_synthesis_uses_refined_order_price_not_signal_price(self):
        """When entry has limit_price refinement, synthesis uses _order_price (refined)."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("NVDA")
        ex._broker = broker

        # Refined limit price differs from entry_price
        entry = {
            "ticker": "NVDA",
            "entry_price": 500.0,       # signal price
            "limit_price": 498.0,       # refined limit price (used for order)
            "order_type": "limit",
            "position_size": 5,
            "strategy": "mtf_momentum",
            "confidence": 0.80,
            "stop_price": 490.0,
            "take_profit": None,
        }

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            ex._execute_entry(entry, "2026-04-29")

        # _order_price = 498.0 (refined), stop=490 → risk=8 → tp = 498 + 16 = 514
        expected_tp = round(498.0 + 2.0 * (498.0 - 490.0), 2)  # = 514.0

        call_kwargs = broker.place_order.call_args[1]
        assert call_kwargs.get("take_profit_price") == expected_tp, (
            f"Expected tp={expected_tp} based on refined price, "
            f"got {call_kwargs.get('take_profit_price')}"
        )


# ═══════════════════════════════════════════════════════════════════════════
# 3. No stop, no TP → plain LIMIT, no bracket, no synthesis
# ═══════════════════════════════════════════════════════════════════════════

class TestNoStopNoTp:
    """Sanity: when entry has no stop, synthesis is skipped and no bracket is placed."""

    def test_entry_with_no_stop_no_tp_uses_plain_limit_no_bracket(self):
        """No stop_price → no synthesis, place_order called with both None."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("SPY")
        ex._broker = broker

        entry = _entry(
            ticker="SPY",
            entry_price=500.0,
            stop_price=0,      # no stop
            take_profit=None,  # no TP either
        )

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            result = ex._execute_entry(entry, "2026-04-29")

        assert broker.place_order.call_count == 1
        call_kwargs = broker.place_order.call_args[1]
        # Neither stop_loss_price nor take_profit_price should be set
        assert call_kwargs.get("stop_loss_price") is None, (
            f"Expected stop_loss_price=None, got {call_kwargs.get('stop_loss_price')}"
        )
        assert call_kwargs.get("take_profit_price") is None, (
            f"Expected take_profit_price=None, got {call_kwargs.get('take_profit_price')}"
        )
        # Still a LIMIT order
        assert call_kwargs.get("order_type") == OrderType.LIMIT


# ═══════════════════════════════════════════════════════════════════════════
# 4. Regression guard: no sequential place_protective_stop → place_take_profit
# ═══════════════════════════════════════════════════════════════════════════

class TestNoSequentialSlThenTpPattern:
    """RCA regression guard: _execute_entry must NEVER call place_protective_stop
    followed by place_take_profit for the same ticker.  This test fails if the
    sequential pattern is reintroduced (e.g. someone refactors to call those
    methods instead of using bracket kwargs)."""

    def _run_entry(self, ex: LiveExecutor, entry: dict, broker: MagicMock) -> dict:
        broker.place_order.return_value = _submitted_result(entry["ticker"])
        ex._broker = broker
        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"
        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")):
            return ex._execute_entry(entry, "2026-04-29")

    def test_no_sequential_sl_then_tp_call_pattern_with_both(self):
        """Entry with stop + TP: place_protective_stop and place_take_profit never called."""
        ex = _make_executor()
        broker = _mock_broker()

        # Spy on the executor's place_protective_stop and place_take_profit methods
        with patch.object(ex, "place_protective_stop", wraps=None) as mock_pps, \
             patch.object(ex, "place_take_profit", wraps=None) as mock_ptp:
            mock_pps.return_value = None
            mock_ptp.return_value = None

            entry = _entry(ticker="AMZN", entry_price=180.0, stop_price=170.0, take_profit=200.0)
            result = self._run_entry(ex, entry, broker)

        # Neither method should have been called — bracket fires via place_order() kwargs
        assert mock_pps.call_count == 0, (
            f"place_protective_stop was called {mock_pps.call_count} times — "
            "sequential SL/TP pattern detected! (RCA #2A regression)"
        )
        assert mock_ptp.call_count == 0, (
            f"place_take_profit was called {mock_ptp.call_count} times — "
            "sequential SL/TP pattern detected! (RCA #2A regression)"
        )

    def test_no_sequential_sl_then_tp_call_pattern_no_tp(self):
        """Entry with stop but no TP (synthesized): still no sequential method calls."""
        ex = _make_executor()
        broker = _mock_broker()

        with patch.object(ex, "place_protective_stop", wraps=None) as mock_pps, \
             patch.object(ex, "place_take_profit", wraps=None) as mock_ptp:
            mock_pps.return_value = None
            mock_ptp.return_value = None

            entry = _entry(ticker="GOOG", entry_price=150.0, stop_price=142.0, take_profit=None)
            result = self._run_entry(ex, entry, broker)

        assert mock_pps.call_count == 0, (
            f"place_protective_stop called {mock_pps.call_count}× after RCA #2A fix — "
            "synthesis should have used broker.place_order kwargs, not this method"
        )
        assert mock_ptp.call_count == 0, (
            f"place_take_profit called {mock_ptp.call_count}× after RCA #2A fix"
        )

    def test_no_sequential_sl_then_tp_call_pattern_no_stop(self):
        """No stop, no TP: neither sequential method is called."""
        ex = _make_executor()
        broker = _mock_broker()

        with patch.object(ex, "place_protective_stop", wraps=None) as mock_pps, \
             patch.object(ex, "place_take_profit", wraps=None) as mock_ptp:
            mock_pps.return_value = None
            mock_ptp.return_value = None

            entry = _entry(ticker="META", entry_price=500.0, stop_price=0, take_profit=None)
            result = self._run_entry(ex, entry, broker)

        assert mock_pps.call_count == 0
        assert mock_ptp.call_count == 0


# ═══════════════════════════════════════════════════════════════════════════
# 5. Bracket child legs persisted to trades table
# ═══════════════════════════════════════════════════════════════════════════

class TestBracketLegsPersist:
    """After a FILLED bracket entry, stop_order_id and tp_order_id are written
    to the trades table via update_trade_protective_orders."""

    def test_bracket_legs_persist_to_trades_table(self, monkeypatch):
        """FILLED order with bracket legs → both stop_order_id and tp_order_id recorded."""
        ex = _make_executor()
        broker = _mock_broker()

        # Return a FILLED order with bracket child legs
        legs = [
            {"id": "SL-STOP-001", "order_type": "stop", "side": "sell"},
            {"id": "TP-LIMIT-001", "order_type": "limit", "side": "sell"},
        ]
        broker.place_order.return_value = _filled_result(
            ticker="AMD", price=120.0, qty=8, legs=legs
        )
        ex._broker = broker

        entry = _entry(
            ticker="AMD",
            entry_price=120.0,
            stop_price=113.0,
            take_profit=134.0,
            qty=8,
        )

        # Mock TradeLedger to return a fake trade_id so leg-recording path runs
        mock_ledger = MagicMock()
        mock_ledger.record_entry.return_value = 99  # trade_id = 99

        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        mock_update = MagicMock(return_value=1)

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")), \
             patch(_TRADE_LEDGER, return_value=mock_ledger), \
             patch("db.atlas_db.update_trade_protective_orders", mock_update):
            result = ex._execute_entry(entry, "2026-04-29")

        assert result.get("success") is True

        # update_trade_protective_orders must have been called once
        assert mock_update.call_count == 1, (
            f"Expected update_trade_protective_orders to be called once, "
            f"got {mock_update.call_count}"
        )

        call_kwargs = mock_update.call_args[1]
        assert call_kwargs.get("stop_order_id") == "SL-STOP-001", (
            f"Expected stop_order_id='SL-STOP-001', got {call_kwargs.get('stop_order_id')}"
        )
        assert call_kwargs.get("tp_order_id") == "TP-LIMIT-001", (
            f"Expected tp_order_id='TP-LIMIT-001', got {call_kwargs.get('tp_order_id')}"
        )

    def test_bracket_legs_not_persisted_for_submitted_order(self):
        """SUBMITTED (not filled) order → TradeLedger not called, no leg persistence."""
        ex = _make_executor()
        broker = _mock_broker()
        broker.place_order.return_value = _submitted_result("ON")
        ex._broker = broker

        entry = _entry(ticker="ON", entry_price=50.0, stop_price=47.0, take_profit=56.0)

        mock_ledger = MagicMock()
        mock_update = MagicMock(return_value=0)
        regime_mock = MagicMock()
        regime_mock.classify_current.return_value.state.value = "bull_risk_on"

        with patch(_KILL_SWITCH, return_value=False), \
             patch(_PRICE_ARB, return_value=False), \
             patch(_PREFLIGHT, return_value=[]), \
             patch(_JOURNAL), \
             patch(_TELEGRAM), \
             patch(_REGIME_MODEL, return_value=regime_mock), \
             patch(_CUG_CHECK, return_value=_mock_allowed_guard()), \
             patch(_GEG_CHECK, return_value=(True, "ok")), \
             patch(_TRADE_LEDGER, return_value=mock_ledger), \
             patch("db.atlas_db.update_trade_protective_orders", mock_update):
            result = ex._execute_entry(entry, "2026-04-29")

        # SUBMITTED → TradeLedger.record_entry not called (deferred to fill confirmation)
        mock_ledger.record_entry.assert_not_called()
        # No leg persistence either
        mock_update.assert_not_called()
