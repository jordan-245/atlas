"""Regression tests: PDT backoff at every order-submit site (AVGO/CCJ path).

Covers the 2026-04-24 incident: AVGO and CCJ were re-submitted 40+ times
despite being PDT-denied earlier the same day because the re-submit path
didn't consult or update the expiry-based backoff state.

Test matrix
-----------
1. place_order PDT denial on AVGO → set_pdt_deferred called; expiry = 21:00 UTC
2. Second AVGO place_order before 21:00 UTC → submit_order NOT called (pre-check)
3. place_order for ON (not deferred) → submit_order IS called (unaffected)
4. Past 21:00 UTC → is_pdt_deferred returns False; clear_expired removes entry
5. sync_market() with AVGO deferred → NOT passed to sync_all_protective_orders
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import date, datetime, time, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brokers.base import OrderSide, OrderType
from brokers.pdt_state import (
    _rth_close_today,
    clear_expired,
    is_pdt_deferred,
    set_pdt_deferred,
)
from scripts.sync_protective_orders import sync_market


_PDT_ERROR_MSG = (
    '{"code":40310100,"message":"trade denied due to pattern day trading protection"}'
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _make_broker():
    """Construct AlpacaBroker bypassing __init__ (no API keys required)."""
    from brokers.alpaca.broker import AlpacaBroker
    broker = object.__new__(AlpacaBroker)
    broker._trade_client = MagicMock()
    broker._tif = "gtc"
    broker._live = False
    broker._paper = True   # paper mode → is_live returns False
    broker._order_map = {}
    return broker


def _pos(ticker: str, stop: float = 95.0) -> SimpleNamespace:
    p = SimpleNamespace()
    p.ticker = ticker
    p.shares = 10
    p.current_price = 100.0
    p.stop_price = stop
    p.take_profit = 0.0
    p.strategy = "momentum_breakout"
    p.entry_date = "2026-04-24"
    p.entry_price = 100.0
    return p


def _write_state(tmp_path: Path, market: str, tickers: list[str]) -> None:
    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"live_{market}.json").write_text(json.dumps({
        "market_id": market, "mode": "live",
        "positions": [
            {"ticker": t, "stop_price": 95.0, "strategy": "momentum_breakout",
             "entry_date": "2026-04-24", "entry_price": 100.0, "shares": 10}
            for t in tickers
        ],
        "closed_trades": [], "equity_history": [],
    }))


def _write_config(tmp_path: Path, market: str) -> None:
    cfg_dir = tmp_path / "config" / "active"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / f"{market}.json").write_text(json.dumps({
        "trading": {"broker": "alpaca", "live_enabled": True},
        "risk": {},
    }))


def _mock_broker(positions: list) -> MagicMock:
    b = MagicMock()
    b.connect.return_value = True
    b.disconnect.return_value = None
    b.get_open_orders.return_value = []
    b.get_positions.return_value = positions
    b.sync_all_protective_orders.return_value = {
        "sl_placed": 0, "tp_placed": 0, "sl_already_exists": 0,
        "tp_already_exists": 0, "errors": 0, "pdt_deferred": 0,
        "per_ticker": {},
    }
    return b


def _make_le_class(inst: MagicMock):
    class _LE:
        def __new__(cls, *a, **kw):
            return inst
    return _LE


def _synced_tickers(broker: MagicMock) -> set[str]:
    """Extract tickers from broker.sync_all_protective_orders call args."""
    call_args = broker.sync_all_protective_orders.call_args
    if call_args is None:
        return set()
    positions = (call_args.kwargs.get("positions")
                 or (call_args.args[0] if call_args.args else []))
    return {p.ticker for p in positions}


def _future_expiry() -> datetime:
    """Return a guaranteed future expiry (today 21:00 UTC, or +2h if already past)."""
    candidate = _rth_close_today()
    if candidate <= datetime.now(tz=timezone.utc):
        candidate = datetime.now(tz=timezone.utc) + timedelta(hours=2)
    return candidate


# ═══════════════════════════════════════════════════════════════
# 1–4: broker.place_order() + pdt_state helpers
# ═══════════════════════════════════════════════════════════════

class TestBrokerPDTBackoff:

    def test_pdt_denial_records_state_with_rth_close_expiry(self, tmp_path: Path) -> None:
        """PDT denial on AVGO place_order → set_pdt_deferred called with today's 21:00 UTC."""
        broker = _make_broker()
        broker._trade_client.submit_order.side_effect = Exception(_PDT_ERROR_MSG)

        expected_expiry = _rth_close_today()

        with patch("brokers.alpaca.broker._is_pdt_deferred_new", return_value=False), \
             patch("brokers.alpaca.broker._set_pdt_deferred_new") as mock_set, \
             patch("brokers.alpaca.broker._pdt_rth_close", return_value=expected_expiry), \
             patch.object(broker, "_require_connected"):
            result = broker.place_order(
                "AVGO", OrderSide.SELL, 1, 0.0,
                OrderType.STOP, stop_price=400.0, tif="gtc",
            )

        # Order must fail (PDT denied)
        assert result.success is False, "PDT-denied order must not succeed"

        # set_pdt_deferred_new must be called once with ("AVGO", today's 21:00 UTC)
        mock_set.assert_called_once_with("AVGO", expected_expiry)

        # The result message must indicate this was a PDT denial
        assert "40310100" in result.message or "pdt" in result.message.lower(), (
            f"Expected PDT error code in result.message, got: {result.message!r}"
        )

    def test_second_submit_skipped_by_precheck(self, tmp_path: Path) -> None:
        """AVGO deferred → place_order pre-check blocks submit_order (not called)."""
        broker = _make_broker()
        broker._trade_client.submit_order.side_effect = AssertionError(
            "submit_order must NOT be called — pre-check should block it"
        )

        with patch("brokers.alpaca.broker._is_pdt_deferred_new", return_value=True), \
             patch.object(broker, "_require_connected"):
            result = broker.place_order(
                "AVGO", OrderSide.SELL, 1, 0.0,
                OrderType.STOP, stop_price=400.0, tif="gtc",
            )

        # submit_order must NOT have been called
        broker._trade_client.submit_order.assert_not_called()
        assert result.success is False
        assert "pdt_deferred" in result.message.lower(), (
            f"Expected 'pdt_deferred' in result.message, got: {result.message!r}"
        )

    def test_different_ticker_not_blocked(self, tmp_path: Path) -> None:
        """ON (not PDT-deferred) is submitted normally even when AVGO would be blocked."""
        broker = _make_broker()
        # ON submit raises a generic error — we only verify submit_order was called
        broker._trade_client.submit_order.side_effect = Exception("generic error")

        # Simulate: AVGO deferred, ON not
        def _is_deferred(t: str) -> bool:
            return t == "AVGO"

        with patch("brokers.alpaca.broker._is_pdt_deferred_new", side_effect=_is_deferred), \
             patch("brokers.alpaca.broker._set_pdt_deferred_new"), \
             patch("brokers.alpaca.broker._pdt_rth_close", return_value=_future_expiry()), \
             patch.object(broker, "_require_connected"):
            broker.place_order(
                "ON", OrderSide.SELL, 10, 0.0,
                OrderType.STOP, stop_price=95.0, tif="gtc",
            )

        # submit_order must have been called once (ON was not blocked by pre-check)
        broker._trade_client.submit_order.assert_called_once()

    def test_expired_state_cleared_and_submit_proceeds(self, tmp_path: Path) -> None:
        """Past 21:00 UTC: is_pdt_deferred returns False; clear_expired removes entry."""
        pdt_file = tmp_path / "pdt_state.json"
        past_expiry = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat()
        pdt_file.write_text(json.dumps({"AVGO": past_expiry}))

        # Expired → not deferred
        assert not is_pdt_deferred("AVGO", _path=pdt_file), (
            "Expired AVGO entry must NOT be considered deferred"
        )

        # clear_expired removes the entry
        cleared = clear_expired(_path=pdt_file)
        assert "AVGO" in cleared, "AVGO must appear in cleared list"

        state = json.loads(pdt_file.read_text())
        assert "AVGO" not in state, (
            "AVGO must be removed from pdt_state.json after expiry"
        )

        # A future entry for a different ticker is preserved
        future_expiry = _future_expiry()
        pdt_file.write_text(json.dumps({
            "AVGO": past_expiry,
            "CCJ": future_expiry.isoformat(),
        }))
        cleared2 = clear_expired(_path=pdt_file)
        assert "AVGO" in cleared2
        assert "CCJ" not in cleared2
        state2 = json.loads(pdt_file.read_text())
        assert "CCJ" in state2, "CCJ (future expiry) must NOT be cleared"
        assert "AVGO" not in state2


# ═══════════════════════════════════════════════════════════════
# 5: sync_market() integration — the AVGO/CCJ re-submit path
# ═══════════════════════════════════════════════════════════════

class TestSyncMarketPDTBackoff:

    def test_avgo_skipped_by_new_pdt_check_in_sync_market(self, tmp_path: Path) -> None:
        """AVGO in pdt_state → sync_market does NOT pass AVGO to sync_all_protective_orders.

        Simulates the exact failure mode from 2026-04-24:
        - _is_pdt_retry_window=True (07:45 UTC is pre-market → old check does NOT skip)
        - is_pdt_deferred("AVGO")=True (new expiry-based check → DOES skip)
        Result: AVGO is excluded; ON is synced normally.
        """
        _write_state(tmp_path, "sp500", ["AVGO", "ON"])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker([_pos("AVGO"), _pos("ON")])
        le_inst = MagicMock()
        le_inst.reconcile_entry_fills.return_value = []
        le_inst.reconcile_exit_fills.return_value = []

        with ExitStack() as stack:
            stack.enter_context(patch("scripts.sync_protective_orders.PROJECT", tmp_path))
            stack.enter_context(patch("brokers.registry.get_live_broker", return_value=broker))
            stack.enter_context(
                patch("brokers.live_executor.LiveExecutor", _make_le_class(le_inst))
            )
            # Patch legacy PDT state helpers so they don't interfere
            stack.enter_context(
                patch("scripts.sync_protective_orders._load_pdt_state", return_value={})
            )
            stack.enter_context(patch("scripts.sync_protective_orders._save_pdt_state"))
            # Simulate 07:45 UTC (pre-market): old retry-window check would NOT skip AVGO
            stack.enter_context(
                patch("scripts.sync_protective_orders._is_pdt_retry_window",
                      return_value=True)
            )
            # New expiry-based check: AVGO deferred, ON is not
            stack.enter_context(
                patch(
                    "scripts.sync_protective_orders.is_pdt_deferred",
                    side_effect=lambda t: t == "AVGO",
                )
            )
            # Patch clear_expired and set_pdt_deferred to avoid real file I/O
            stack.enter_context(
                patch("scripts.sync_protective_orders._clear_pdt_expired")
            )
            stack.enter_context(
                patch("scripts.sync_protective_orders.set_pdt_deferred")
            )

            result = sync_market("sp500", "2026-04-24", dry_run=True)

        assert result.get("error") == "", (
            f"Unexpected sync_market error: {result.get('error')!r}"
        )

        tickers = _synced_tickers(broker)
        assert "AVGO" not in tickers, (
            "AVGO (PDT-deferred via expiry-based check) must NOT be passed "
            "to sync_all_protective_orders even during pre-market hours"
        )
        assert "ON" in tickers, (
            "ON (not PDT-deferred) must still be synced"
        )
