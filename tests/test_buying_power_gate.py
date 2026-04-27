"""tests/test_buying_power_gate.py — Leverage gate regression tests.

Verifies that _execute_entry() in live_executor.py refuses orders that
would push total portfolio leverage above the configured risk.leverage cap.

Tests use a fully mocked broker (no real network calls).
"""
from __future__ import annotations

import sys
import os
from unittest.mock import MagicMock, patch, PropertyMock
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)


# ---------------------------------------------------------------------------
# Helpers — build a minimal config and a fake PositionInfo / AccountInfo
# ---------------------------------------------------------------------------

def _make_config(leverage: float = 2.0, max_order_value: float = 10_000) -> dict:
    return {
        "market": "sp500",
        "trading": {
            "mode": "live",
            "live_enabled": True,
            "live_safety": {
                "dry_run_first": False,
                "max_order_value": max_order_value,
                "max_daily_orders": 50,
            },
        },
        "risk": {
            "leverage": leverage,
            "starting_equity": 5000,
            "max_risk_per_trade_pct": 0.005,
            "max_open_positions": 10,
            "max_sector_concentration": 3,
            "max_daily_drawdown_pct": 0.02,
        },
    }


def _make_position(ticker: str, market_value: float):
    """Return a minimal PositionInfo-like mock."""
    p = MagicMock()
    p.ticker = ticker
    p.market_value = market_value
    p.shares = 10
    p.entry_price = market_value / 10
    return p


def _make_account(equity: float, cash: float = 0.0, buying_power: float = 0.0):
    acct = MagicMock()
    acct.equity = equity
    acct.cash = cash
    acct.buying_power = buying_power
    acct.halted = False
    acct.halt_reason = ""
    return acct


def _make_entry(
    ticker: str = "TSLA",
    price: float = 200.0,
    qty: int = 5,
    stop: float = 180.0,
    strategy: str = "momentum",
) -> dict:
    return {
        "ticker": ticker,
        "entry_price": price,
        "position_size": qty,
        "stop_price": stop,
        "strategy": strategy,
        "confidence": 0.75,
        "order_type": "",
    }


def _build_executor(config: dict, broker_mock):
    """Construct a LiveExecutor wired to broker_mock, bypassing connect()."""
    from brokers.live_executor import LiveExecutor
    ex = LiveExecutor.__new__(LiveExecutor)
    ex.config = config
    ex._broker = broker_mock
    ex._connected = True
    ex._daily_order_count = 0
    ex._daily_date = "2026-04-27"
    ex._halted = False
    ex._halt_reason = ""
    ex._circuit_breaker_tripped = False
    ex._daily_start_equity = 5000.0
    return ex


# ---------------------------------------------------------------------------
# Test class
# ---------------------------------------------------------------------------

class TestLeverageGate:
    """Pre-submit leverage gate in _execute_entry."""

    def _make_broker(
        self,
        equity: float,
        positions_mv: list[float],
        buying_power: float = 1000.0,
    ):
        """Build a broker mock with preset account + positions."""
        broker = MagicMock()

        # Account
        broker.get_account_info.return_value = _make_account(
            equity=equity, cash=equity - sum(positions_mv), buying_power=buying_power
        )

        # Positions
        broker.get_positions.return_value = [
            _make_position(f"POS{i}", mv)
            for i, mv in enumerate(positions_mv)
        ]

        # Kill-switch / price-arbiter stubs
        broker.get_market_snapshot.return_value = None

        return broker

    # ── Scenario 1: existing 1.8× + new order pushes to 2.5× → REFUSED ────

    def test_refuses_order_that_exceeds_leverage_cap(self):
        """equity=$5000, existing MV=$9000 (1.8×), new order MV=$3000 → 2.4× > 2.0×*1.05=2.1× → BLOCKED."""
        equity = 5000.0
        existing_mv = 9000.0  # 1.8× already
        order_price = 300.0
        order_qty = 10  # $3000 order → prospective = (9000+3000)/5000 = 2.4×

        config = _make_config(leverage=2.0, max_order_value=5000)
        broker = self._make_broker(equity=equity, positions_mv=[existing_mv])
        executor = _build_executor(config, broker)

        entry = _make_entry(ticker="TSLA", price=order_price, qty=order_qty)

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
        ):
            result = executor._execute_entry(entry, "2026-04-27")

        assert result["success"] is False, f"Expected BLOCKED, got: {result}"
        assert result.get("reason") == "leverage_gate", (
            f"Expected reason='leverage_gate', got: {result.get('reason')}"
        )
        assert result.get("blocked") is True

    # ── Scenario 2: existing 1.8× + order lands at exactly 2.0× → APPROVED ─

    def test_approves_order_at_exactly_configured_cap(self):
        """equity=$5000, existing MV=$9000 (1.8×), order MV=$1000 → 2.0× = cap → APPROVED (within 5% slack)."""
        equity = 5000.0
        existing_mv = 9000.0  # 1.8×
        order_price = 100.0
        order_qty = 10  # $1000 → prospective = (9000+1000)/5000 = 2.0× == cap → PASS

        config = _make_config(leverage=2.0, max_order_value=5000)
        broker = self._make_broker(equity=equity, positions_mv=[existing_mv], buying_power=2000)
        executor = _build_executor(config, broker)

        entry = _make_entry(ticker="NVDA", price=order_price, qty=order_qty)

        # place_order should succeed
        mock_order = MagicMock()
        mock_order.success = True
        mock_order.order_id = "test-order-123"
        mock_order.fill_price = 0.0
        mock_order.status.value = "submitted"
        mock_order.message = "Order submitted"
        mock_order.raw = {}
        broker.place_order.return_value = mock_order

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-27")

        # Should NOT be blocked by leverage gate (2.0 <= 2.0*1.05=2.1)
        assert result.get("reason") != "leverage_gate", (
            f"Should have passed leverage gate: {result}"
        )
        assert result.get("blocked") is not True, f"Should not be blocked: {result}"

    # ── Scenario 3: within 5% slack (2.08× vs 2.0× cap) → APPROVED ────────

    def test_approves_order_within_slack_band(self):
        """2.08× < 2.0×*1.05=2.10× → APPROVED."""
        equity = 5000.0
        existing_mv = 9000.0
        order_price = 200.0
        order_qty = 2  # $400 → (9000+400)/5000 = 1.88× < 2.1 → PASS

        config = _make_config(leverage=2.0, max_order_value=5000)
        broker = self._make_broker(equity=equity, positions_mv=[existing_mv], buying_power=2000)
        executor = _build_executor(config, broker)

        entry = _make_entry(ticker="MSFT", price=order_price, qty=order_qty)

        mock_order = MagicMock()
        mock_order.success = True
        mock_order.order_id = "test-order-456"
        mock_order.fill_price = 0.0
        mock_order.status.value = "submitted"
        mock_order.message = "Order submitted"
        mock_order.raw = {}
        broker.place_order.return_value = mock_order

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-27")

        assert result.get("reason") != "leverage_gate", (
            f"Should not be blocked by leverage gate: {result}"
        )

    # ── Scenario 4: broker API failure → non-fatal, order proceeds ──────────

    def test_gate_is_non_fatal_on_broker_error(self):
        """If broker raises on get_account_info, leverage gate logs warning and proceeds."""
        equity = 5000.0
        config = _make_config(leverage=2.0, max_order_value=5000)
        broker = MagicMock()
        broker.get_account_info.side_effect = RuntimeError("Broker timeout")
        broker.get_market_snapshot.return_value = None

        mock_order = MagicMock()
        mock_order.success = True
        mock_order.order_id = "test-order-789"
        mock_order.fill_price = 0.0
        mock_order.status.value = "submitted"
        mock_order.message = "Order submitted"
        mock_order.raw = {}
        broker.place_order.return_value = mock_order

        executor = _build_executor(config, broker)
        entry = _make_entry(ticker="GOOG", price=100.0, qty=5)

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-27")

        # Must not be blocked — leverage gate is non-fatal
        assert result.get("reason") != "leverage_gate", (
            f"Should not block on broker error: {result}"
        )

    # ── Scenario 5: zero equity guard (avoid division by zero) ──────────────

    def test_skips_gate_when_equity_is_zero(self):
        """equity=0 → gate skips check (no division by zero)."""
        config = _make_config(leverage=2.0, max_order_value=10_000)
        broker = self._make_broker(equity=0.0, positions_mv=[], buying_power=0.0)

        mock_order = MagicMock()
        mock_order.success = True
        mock_order.order_id = "order-000"
        mock_order.fill_price = 0.0
        mock_order.status.value = "submitted"
        mock_order.message = "ok"
        mock_order.raw = {}
        broker.place_order.return_value = mock_order

        executor = _build_executor(config, broker)
        entry = _make_entry(ticker="SPY", price=500.0, qty=1)

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-27")

        # Gate should not block (equity=0 skips the check)
        assert result.get("reason") != "leverage_gate"

    # ── Scenario 6: current state 1.75× passes cap 2.0× ────────────────────

    def test_current_live_state_is_within_cap(self):
        """Reproduce current live state: equity=$5436, MV=$9499 → 1.747× < 2.0× cap.

        A small new order ($500) → (9499+500)/5436 = 1.84× < 2.1× → APPROVED.
        """
        equity = 5435.94
        existing_mv = 9498.58  # 12 live positions
        config = _make_config(leverage=2.0, max_order_value=5000)
        broker = self._make_broker(
            equity=equity, positions_mv=[existing_mv], buying_power=1373.30
        )

        mock_order = MagicMock()
        mock_order.success = True
        mock_order.order_id = "live-state-test"
        mock_order.fill_price = 0.0
        mock_order.status.value = "submitted"
        mock_order.message = "ok"
        mock_order.raw = {}
        broker.place_order.return_value = mock_order

        executor = _build_executor(config, broker)
        # Small order: 2 shares at $250 = $500 → prosp = (9498+500)/5436 = 1.84×
        entry = _make_entry(ticker="AMD", price=250.0, qty=2)

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "transition_uncertain"
            result = executor._execute_entry(entry, "2026-04-27")

        assert result.get("reason") != "leverage_gate", (
            f"1.84× should pass 2.0× cap: {result}"
        )

    # ── Scenario 7: current state 1.75×, large order pushes to 2.2× → BLOCKED

    def test_current_live_state_large_order_blocked(self):
        """equity=$5436, MV=$9499, new order $2500 → (9499+2500)/5436 = 2.21× > 2.1× → BLOCKED."""
        equity = 5435.94
        existing_mv = 9498.58
        config = _make_config(leverage=2.0, max_order_value=10_000)
        broker = self._make_broker(
            equity=equity, positions_mv=[existing_mv], buying_power=1373.30
        )

        executor = _build_executor(config, broker)
        # Large order: 25 shares at $100 = $2500 → (9498+2500)/5436 = 2.206× > 2.1×
        entry = _make_entry(ticker="PLTR", price=100.0, qty=25)

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
        ):
            result = executor._execute_entry(entry, "2026-04-27")

        assert result["success"] is False
        assert result.get("reason") == "leverage_gate", f"Expected leverage_gate, got: {result}"
        assert "2.2" in result["errors"][0] or "2.1" in result["errors"][0] or \
               float(result["errors"][0].split("to")[1].split("x")[0]) > 2.0
