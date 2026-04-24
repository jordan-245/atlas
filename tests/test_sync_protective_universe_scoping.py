"""Regression tests: sync_protective_orders — per-market universe scoping (P0-3)
and PDT-deferred backoff (P1-13).

P0-3 root cause: broker.get_positions() returned ALL 7 broker positions; BOTH
sp500 and commodity_etfs sync loops iterated all 7, causing races where e.g.
sp500 sync placed a stop on CCJ (a commodity_etfs position) at the same time
as the commodity_etfs sync → P0-1 duplicate inserts + 741 error lines.

P1-13 root cause: PDT-deferred tickers (same-day entry on <$25k account) were
retried every 15 min during RTH, generating repeated broker 40310100 rejections.

Tests
-----
1. sp500 sync processes only the 3 sp500 tickers (not 2 commodity_etfs tickers)
2. State file with 2 tickers → only those 2 processed even if broker has 10
3. Empty state file → no stops placed (positions_checked=0)
4. PDT-deferred ticker inside RTH → skipped
5. PDT-deferred ticker during pre-market window → retried (NOT skipped)
6. _handle_held_stops state_tickers filter: cross-market held stops ignored
7. _handle_held_stops resolved_keys: cross-market entries NOT deleted
8. _is_pdt_retry_window: boundary conditions
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.sync_protective_orders import (
    _handle_held_stops,
    _is_pdt_retry_window,
    _pdt_should_skip,
    sync_market,
)


# ── Shared helpers ────────────────────────────────────────────────────────────

def _pos(ticker: str, stop: float = 95.0) -> SimpleNamespace:
    """Minimal broker Position-like object."""
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


def _make_held_order(ticker: str, order_id: str = "ord-1", status: str = "held") -> MagicMock:
    o = MagicMock()
    o.ticker = ticker
    o.order_id = order_id
    o.raw = {"status": status, "order_type": "stop", "side": "sell"}
    return o


def _write_state(tmp_path: Path, market: str, tickers: list[str]) -> Path:
    """Write a minimal live_{market}.json with the given tickers."""
    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"live_{market}.json"
    state_path.write_text(json.dumps({
        "market_id": market,
        "mode": "live",
        "positions": [
            {"ticker": t, "stop_price": 95.0, "strategy": "momentum_breakout",
             "entry_date": "2026-04-24", "entry_price": 100.0, "shares": 10}
            for t in tickers
        ],
        "closed_trades": [],
        "equity_history": [],
    }))
    return state_path


def _write_config(tmp_path: Path, market: str) -> None:
    """Write a minimal config/active/{market}.json."""
    cfg_dir = tmp_path / "config" / "active"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / f"{market}.json").write_text(json.dumps({
        "trading": {"broker": "alpaca", "live_enabled": True},
        "risk": {},
    }))


def _mock_broker(positions: list) -> MagicMock:
    """Return a mock broker with *positions* from get_positions()."""
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
    """Return a stub class that yields *inst* from __new__ (mimics LiveExecutor.__new__ pattern)."""
    class _LE:
        def __new__(cls, *a, **kw):
            return inst
    return _LE


def _run_sync_market(
    tmp_path: Path,
    market: str,
    broker: MagicMock,
    *,
    pdt_state: dict | None = None,
    is_pdt_retry_window: bool | None = None,
) -> dict:
    """Run sync_market with PROJECT, broker registry, executor, and PDT state mocked."""
    le_inst = MagicMock()
    le_inst.reconcile_entry_fills.return_value = []
    le_inst.reconcile_exit_fills.return_value = []

    with ExitStack() as stack:
        stack.enter_context(
            patch("scripts.sync_protective_orders.PROJECT", tmp_path)
        )
        stack.enter_context(
            patch("brokers.registry.get_live_broker", return_value=broker)
        )
        # LiveExecutor is imported locally in sync_market; patch at its source module.
        # Use _make_le_class so __new__(cls) returns le_inst (MagicMock cannot set __new__).
        stack.enter_context(
            patch("brokers.live_executor.LiveExecutor", _make_le_class(le_inst))
        )

        stack.enter_context(
            patch("scripts.sync_protective_orders._load_pdt_state",
                  return_value=(pdt_state if pdt_state is not None else {}))
        )
        stack.enter_context(
            patch("scripts.sync_protective_orders._save_pdt_state")
        )

        if is_pdt_retry_window is not None:
            stack.enter_context(
                patch("scripts.sync_protective_orders._is_pdt_retry_window",
                      return_value=is_pdt_retry_window)
            )

        return sync_market(market, "2026-04-24", dry_run=True)


def _synced_tickers(broker: MagicMock) -> set[str]:
    """Extract ticker set from broker.sync_all_protective_orders call args."""
    call_args = broker.sync_all_protective_orders.call_args
    if call_args is None:
        return set()
    positions = (call_args.kwargs.get("positions")
                 or (call_args.args[0] if call_args.args else []))
    return {p.ticker for p in positions}


# ═══════════════════════════════════════════════════════════════
# 1. Universe scoping — only state-file tickers processed
# ═══════════════════════════════════════════════════════════════

class TestUniverseScoping:

    def test_sp500_processes_only_state_file_tickers(self, tmp_path: Path) -> None:
        """Broker returns 5 positions (3 sp500, 2 commodity_etfs) — sp500 sync passes only 3."""
        _write_state(tmp_path, "sp500", ["AMD", "CHTR", "ON"])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker(
            [_pos("AMD"), _pos("CHTR"), _pos("ON"),  # sp500
             _pos("GLD"), _pos("CCJ")]                # commodity_etfs
        )

        result = _run_sync_market(tmp_path, "sp500", broker)

        assert result.get("error") == "", f"Unexpected error: {result.get('error')}"
        assert broker.sync_all_protective_orders.called, "sync_all_protective_orders not called"
        tickers = _synced_tickers(broker)
        assert tickers == {"AMD", "CHTR", "ON"}, f"Expected only sp500 tickers; got {tickers}"
        assert "GLD" not in tickers, "GLD (commodity_etfs) must NOT be synced by sp500"
        assert "CCJ" not in tickers, "CCJ (commodity_etfs) must NOT be synced by sp500"

    def test_only_state_file_tickers_processed_when_broker_has_more(self, tmp_path: Path) -> None:
        """State file has 2 tickers — only those 2 passed even if broker has 10."""
        _write_state(tmp_path, "sp500", ["NFLX", "MRVL"])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker(
            [_pos(t) for t in ["NFLX", "MRVL", "AMD", "CHTR", "ON",
                                "GLD", "CCJ", "UNG", "FCX", "SLV"]]
        )

        result = _run_sync_market(tmp_path, "sp500", broker)

        assert result.get("error") == ""
        assert broker.sync_all_protective_orders.called
        tickers = _synced_tickers(broker)
        assert tickers == {"NFLX", "MRVL"}, f"Expected {{NFLX, MRVL}}, got {tickers}"

    def test_empty_state_file_no_stops_placed(self, tmp_path: Path) -> None:
        """Empty state file (no positions) → sync_all_protective_orders NOT called."""
        _write_state(tmp_path, "sp500", [])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker([_pos("AMD"), _pos("CHTR")])

        result = _run_sync_market(tmp_path, "sp500", broker)

        assert result.get("error") == ""
        broker.sync_all_protective_orders.assert_not_called()
        assert result["counts"].get("positions_checked", 0) == 0


# ═══════════════════════════════════════════════════════════════
# 2. PDT backoff — RTH skip / pre-market retry
# ═══════════════════════════════════════════════════════════════

class TestPDTBackoff:

    def test_pdt_deferred_ticker_skipped_during_rth(self, tmp_path: Path) -> None:
        """PDT-deferred ticker during RTH (_is_pdt_retry_window=False) → skipped."""
        _write_state(tmp_path, "sp500", ["CHTR", "AMD"])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker([_pos("CHTR"), _pos("AMD")])

        pdt_state = {
            "CHTR::sp500": {"first_seen": "2026-04-24T10:00:00",
                             "last_retry": "2026-04-24T15:00:00",
                             "retry_count": 1, "market_id": "sp500"}
        }

        result = _run_sync_market(
            tmp_path, "sp500", broker,
            pdt_state=pdt_state,
            is_pdt_retry_window=False,   # simulates RTH
        )

        assert result.get("error") == ""
        # AMD (not PDT-deferred) should still be synced
        assert broker.sync_all_protective_orders.called, (
            "sync_all_protective_orders must be called — AMD is not PDT-deferred"
        )
        tickers = _synced_tickers(broker)
        assert "CHTR" not in tickers, "CHTR (PDT-deferred) must be skipped during RTH"
        assert "AMD" in tickers, "AMD (not PDT-deferred) must still be synced"

    def test_pdt_deferred_ticker_retried_during_premarket(self, tmp_path: Path) -> None:
        """PDT-deferred ticker at pre-market (_is_pdt_retry_window=True) → included."""
        _write_state(tmp_path, "sp500", ["CHTR"])
        _write_config(tmp_path, "sp500")

        broker = _mock_broker([_pos("CHTR")])

        pdt_state = {
            "CHTR::sp500": {"first_seen": "2026-04-23T15:00:00",
                             "last_retry": "2026-04-23T15:00:00",
                             "retry_count": 2, "market_id": "sp500"}
        }

        result = _run_sync_market(
            tmp_path, "sp500", broker,
            pdt_state=pdt_state,
            is_pdt_retry_window=True,    # simulates pre-market window
        )

        assert result.get("error") == ""
        assert broker.sync_all_protective_orders.called, (
            "sync_all_protective_orders must be called — CHTR retried at pre-market"
        )
        tickers = _synced_tickers(broker)
        assert "CHTR" in tickers, (
            "CHTR (PDT-deferred) must be retried in pre-market window (hour < 14 UTC)"
        )


# ═══════════════════════════════════════════════════════════════
# 3. _handle_held_stops state_tickers filter
# ═══════════════════════════════════════════════════════════════

class TestHeldStopsStateTickers:

    def test_cross_market_held_stop_ignored_with_state_tickers(
        self, tmp_path: Path
    ) -> None:
        """CCJ is held but NOT in sp500 state_tickers → ignored by sp500 sync."""
        state_file = tmp_path / "held.json"
        broker = MagicMock()
        broker.get_open_orders.return_value = [
            _make_held_order("CCJ", "ord-ccj"),   # commodity_etfs position
            _make_held_order("AMD", "ord-amd"),   # sp500 position
        ]
        broker.cancel_order.return_value = MagicMock(success=True)

        result = _handle_held_stops(
            broker, "sp500",
            state_file=state_file,
            send_telegram=False,
            state_tickers={"AMD"},   # sp500 owns only AMD
        )

        assert "CCJ" not in result["newly_held"], "CCJ must be filtered by state_tickers"
        assert "CCJ" not in result["resubmitted"]
        assert "AMD" in result["newly_held"], "AMD (first cycle) must be recorded"

    def test_no_state_tickers_processes_all_held(self, tmp_path: Path) -> None:
        """state_tickers=None → all held stops processed (backward-compatible behaviour)."""
        state_file = tmp_path / "held.json"
        broker = MagicMock()
        broker.get_open_orders.return_value = [
            _make_held_order("CCJ", "ord-ccj"),
            _make_held_order("AMD", "ord-amd"),
        ]
        broker.cancel_order.return_value = MagicMock(success=True)

        result = _handle_held_stops(
            broker, "sp500",
            state_file=state_file,
            send_telegram=False,
            state_tickers=None,      # no filter → backward-compatible
        )

        assert "CCJ" in result["newly_held"]
        assert "AMD" in result["newly_held"]


# ═══════════════════════════════════════════════════════════════
# 4. resolved_keys does NOT wipe cross-market held state
# ═══════════════════════════════════════════════════════════════

class TestResolvedKeysCrossMarket:

    def test_other_market_held_entry_not_deleted(self, tmp_path: Path) -> None:
        """sp500 sync must NOT delete CCJ::commodity_etfs from the shared state file."""
        state_file = tmp_path / "held.json"
        state_file.write_text(json.dumps({
            "AMD::sp500": {
                "first_seen": "2026-04-24T09:00:00",
                "order_id": "ord-amd",
                "retry_count": 0,
                "last_alerted_date": "",
                "permanently_skipped": False,
                "skip_reason": "",
            },
            "CCJ::commodity_etfs": {
                "first_seen": "2026-04-24T09:00:00",
                "order_id": "ord-ccj",
                "retry_count": 0,
                "last_alerted_date": "",
                "permanently_skipped": False,
                "skip_reason": "",
            },
        }))

        # No held orders from broker → AMD is resolved
        broker = MagicMock()
        broker.get_open_orders.return_value = []
        broker.cancel_order.return_value = MagicMock(success=True)

        _handle_held_stops(
            broker, "sp500",
            state_file=state_file,
            send_telegram=False,
            state_tickers={"AMD"},
        )

        state = json.loads(state_file.read_text())
        assert "AMD::sp500" not in state, (
            "AMD::sp500 should be cleared (no longer in held orders)"
        )
        assert "CCJ::commodity_etfs" in state, (
            "CCJ::commodity_etfs must NOT be deleted by sp500 sync "
            "(it belongs to a different market)"
        )


# ═══════════════════════════════════════════════════════════════
# 5. _is_pdt_retry_window boundary tests
# ═══════════════════════════════════════════════════════════════

class TestIsPdtRetryWindow:

    @pytest.mark.parametrize("hour,expected", [
        (0,  True),   # midnight UTC = pre-market window ✓
        (13, True),   # 13:59 UTC = last pre-market hour ✓
        (14, False),  # 14:00 UTC = boundary (RTH begins) ✗
        (16, False),  # 16:00 UTC = deep RTH ✗
        (21, False),  # 21:00 UTC = after-hours but ≥14 ✗
    ])
    def test_retry_window_hours(self, hour: int, expected: bool) -> None:
        t = datetime(2026, 4, 24, hour, 30, 0)
        result = _is_pdt_retry_window(t)
        assert result == expected, (
            f"_is_pdt_retry_window({hour:02d}:30 UTC) expected {expected}, got {result}"
        )

    def test_pdt_should_skip_false_when_not_in_state(self) -> None:
        assert _pdt_should_skip("CHTR", "sp500", {}) is False

    def test_pdt_should_skip_true_when_in_state(self) -> None:
        state = {"CHTR::sp500": {"first_seen": "2026-04-24T15:00:00"}}
        assert _pdt_should_skip("CHTR", "sp500", state) is True

    def test_pdt_should_skip_false_for_different_market(self) -> None:
        state = {"CHTR::commodity_etfs": {"first_seen": "2026-04-24T15:00:00"}}
        assert _pdt_should_skip("CHTR", "sp500", state) is False

    def test_pdt_should_skip_false_empty_state(self) -> None:
        assert _pdt_should_skip("GLD", "commodity_etfs", {}) is False
