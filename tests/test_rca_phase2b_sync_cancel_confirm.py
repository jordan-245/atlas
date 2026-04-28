"""Tests for the cancel-confirm-then-place race fix (Phase 2B / RCA #2B).

Verifies that _wait_for_cancel_confirm polls broker.get_order_status until
the cancel is confirmed, and that _handle_held_stops integrates correctly
so no replacement order can be placed while the old order is still settling.

Failure mode being fixed:
  Alpaca 40310000 "insufficient qty" occurs when a cancel is issued and a
  new SELL order is placed before the cancel settles — the old order still
  "holds" position shares at Alpaca's side.  Observed tickers: AVGO, XLK,
  CCJ, MU (Apr 22-28 logs).
"""
from __future__ import annotations

import json
import logging
import time
from unittest.mock import MagicMock, call

import pytest

# ---------------------------------------------------------------------------
# Path bootstrap — allow running as a standalone file or via pytest
# ---------------------------------------------------------------------------
import sys
from pathlib import Path

_ATLAS = Path(__file__).resolve().parent.parent
if str(_ATLAS) not in sys.path:
    sys.path.insert(0, str(_ATLAS))

from scripts.sync_protective_orders import (
    _handle_held_stops,
    _wait_for_cancel_confirm,
)
from brokers.base import OrderResult, OrderStatus


# ---------------------------------------------------------------------------
# Shared clock mock helpers
# ---------------------------------------------------------------------------

class _FakeClock:
    """Controllable monotonic clock + sleep for deterministic timeout tests."""

    def __init__(self):
        self._t = 0.0

    def monotonic(self) -> float:
        return self._t

    def sleep(self, s: float) -> None:
        self._t += s


# ---------------------------------------------------------------------------
# Tests: _wait_for_cancel_confirm — unit
# ---------------------------------------------------------------------------

class TestWaitForCancelConfirm:
    """Unit tests for _wait_for_cancel_confirm helper."""

    def _ok(self, status: OrderStatus, oid: str = "oid-test") -> OrderResult:
        return OrderResult(success=True, order_id=oid, status=status)

    # ── True-returning cases ────────────────────────────────────────────────

    def test_cancel_confirm_returns_true_when_status_canceled(self, monkeypatch):
        """CANCELLED status on first poll → True."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        broker.get_order_status.return_value = self._ok(OrderStatus.CANCELLED, "oid-1")

        result = _wait_for_cancel_confirm(broker, "oid-1", timeout_sec=5.0)
        assert result is True
        assert broker.get_order_status.call_count == 1

    def test_cancel_confirm_returns_true_when_status_expired(self, monkeypatch):
        """Alpaca 'expired' maps to OrderStatus.CANCELLED — must return True."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        # 'expired' → CANCELLED in the Alpaca status map
        broker.get_order_status.return_value = self._ok(OrderStatus.CANCELLED, "oid-2")

        result = _wait_for_cancel_confirm(broker, "oid-2", timeout_sec=5.0)
        assert result is True

    def test_cancel_confirm_returns_true_when_status_rejected(self, monkeypatch):
        """Alpaca 'rejected' maps to OrderStatus.FAILED — also a terminal confirmed state."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        broker.get_order_status.return_value = self._ok(OrderStatus.FAILED, "oid-3")

        result = _wait_for_cancel_confirm(broker, "oid-3", timeout_sec=5.0)
        assert result is True

    def test_cancel_confirm_polls_until_confirmed_with_200ms_delay(self, monkeypatch):
        """First 2 polls return SUBMITTED (pending_cancel), 3rd returns CANCELLED.

        Verifies the helper waits and retries rather than returning early on
        non-terminal status.  Each 'sleep' advances the fake clock by 0.1 s so
        we stay within the 5 s timeout for all 3 polls.
        """
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        call_count = [0]

        def side_effect(oid):
            call_count[0] += 1
            if call_count[0] < 3:
                return OrderResult(success=True, order_id=oid, status=OrderStatus.SUBMITTED)
            return OrderResult(success=True, order_id=oid, status=OrderStatus.CANCELLED)

        broker.get_order_status.side_effect = side_effect

        result = _wait_for_cancel_confirm(
            broker, "oid-4", timeout_sec=5.0, poll_interval_sec=0.1
        )
        assert result is True
        # Should have polled exactly 3 times: 2×SUBMITTED + 1×CANCELLED
        assert call_count[0] == 3
        # Should have slept twice (once after each SUBMITTED poll)
        assert broker.get_order_status.call_count == 3

    # ── False-returning cases ───────────────────────────────────────────────

    def test_cancel_confirm_returns_false_on_timeout(self, monkeypatch):
        """Never confirmed within timeout → False."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        # Always returns SUBMITTED (pending_cancel — never settles)
        broker.get_order_status.return_value = OrderResult(
            success=True, order_id="oid-5", status=OrderStatus.SUBMITTED
        )

        # timeout=0.5s, poll_interval=1.0s → deadline=0.5, after first sleep(1.0)
        # clock=1.0 > deadline=0.5 → exits loop → False
        result = _wait_for_cancel_confirm(
            broker, "oid-5", timeout_sec=0.5, poll_interval_sec=1.0
        )
        assert result is False
        # Only polled once before timeout
        assert broker.get_order_status.call_count == 1

    def test_cancel_confirm_returns_false_on_filled(self, monkeypatch, caplog):
        """FILLED status (race lost) → False AND WARNING logged."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        broker.get_order_status.return_value = OrderResult(
            success=True, order_id="oid-6", status=OrderStatus.FILLED
        )

        with caplog.at_level(logging.WARNING, logger="atlas.sync_protective_orders"):
            result = _wait_for_cancel_confirm(broker, "oid-6", timeout_sec=5.0)

        assert result is False
        filled_msgs = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING
            and "FILLED" in r.message
            and "oid-6" in r.message
        ]
        assert filled_msgs, (
            "Expected a WARNING mentioning 'FILLED' and 'oid-6', "
            f"got records: {[r.message for r in caplog.records]}"
        )

    def test_cancel_confirm_handles_get_status_exception_and_retries(self, monkeypatch):
        """If get_order_status raises, helper logs WARNING and retries."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        broker = MagicMock()
        call_count = [0]

        def side_effect(oid):
            call_count[0] += 1
            if call_count[0] == 1:
                raise ConnectionError("network blip")
            return OrderResult(success=True, order_id=oid, status=OrderStatus.CANCELLED)

        broker.get_order_status.side_effect = side_effect

        result = _wait_for_cancel_confirm(
            broker, "oid-7", timeout_sec=5.0, poll_interval_sec=0.1
        )
        assert result is True
        assert call_count[0] == 2


# ---------------------------------------------------------------------------
# Tests: integration with _handle_held_stops
# ---------------------------------------------------------------------------

class TestHandleHeldStopsWithCancelConfirm:
    """Integration tests ensuring _handle_held_stops waits for confirm before returning."""

    def _make_held_order(self, ticker: str, order_id: str) -> MagicMock:
        order = MagicMock()
        order.raw = {"status": "held", "order_type": "stop", "side": "sell"}
        order.ticker = ticker
        order.order_id = order_id
        return order

    def _prime_state(self, tmp_path: Path, ticker: str, market: str, retry_count: int = 0) -> Path:
        """Write an existing state file entry (branch 4 requires one)."""
        state_file = tmp_path / "held_state.json"
        state_file.write_text(json.dumps({
            f"{ticker}::{market}": {
                "first_seen": "2026-04-28T10:00:00",
                "order_id": "order-held-prev",
                "retry_count": retry_count,
                "last_alerted_date": "",
                "permanently_skipped": False,
                "skip_reason": "",
            }
        }))
        return state_file

    def test_sync_cancels_then_waits_then_places_oco(
        self, monkeypatch, tmp_path
    ):
        """KEY RACE TEST: place_order must only be called AFTER cancel is confirmed.

        Sequence:
          cancel_order(held_id)           → immediately returns success
          get_order_status(held_id) ×2   → SUBMITTED (pending_cancel)
          get_order_status(held_id) ×1   → CANCELLED  ← confirm gate opens
          [caller proceeds to place order]
        Assertion: cancel_confirmed[0] is True whenever place_order is called.
        """
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        cancel_confirmed = [False]
        poll_count = [0]
        place_calls = []

        broker = MagicMock()
        broker.get_open_orders.return_value = [
            self._make_held_order("AVGO", "order-held-avgo")
        ]
        broker.cancel_order.return_value = MagicMock(success=True)

        def mock_get_status(oid):
            poll_count[0] += 1
            if poll_count[0] <= 2:
                return OrderResult(
                    success=True, order_id=oid, status=OrderStatus.SUBMITTED
                )
            # 3rd poll: confirmed cancelled — gate opens
            cancel_confirmed[0] = True
            return OrderResult(
                success=True, order_id=oid, status=OrderStatus.CANCELLED
            )

        broker.get_order_status.side_effect = mock_get_status

        def mock_place(*args, **kwargs):
            assert cancel_confirmed[0], (
                "place_order called BEFORE cancel was confirmed — 40310 race!"
            )
            place_calls.append(kwargs)
            return MagicMock(success=True)

        broker.place_order.side_effect = mock_place

        state_file = self._prime_state(tmp_path, "AVGO", "sp500")
        result = _handle_held_stops(
            broker, "sp500",
            dry_run=False,
            send_telegram=False,
            state_file=state_file,
            state_tickers={"AVGO"},
        )

        # AVGO should be in resubmitted (cancel confirmed successfully)
        assert "AVGO" in result["resubmitted"], (
            f"Expected AVGO in resubmitted, got: {result}"
        )
        assert cancel_confirmed[0], "Cancel should have been confirmed by the helper"
        assert poll_count[0] == 3, f"Expected 3 polls, got {poll_count[0]}"

        # Simulate caller placing a replacement order — confirm gate must be True
        broker.place_order(ticker="AVGO", side="sell", qty=10)
        assert len(place_calls) == 1

    def test_sync_skips_ticker_if_cancel_times_out(
        self, monkeypatch, tmp_path, caplog
    ):
        """If cancel confirmation times out, ticker NOT in resubmitted + ERROR logged."""
        clock = _FakeClock()
        monkeypatch.setattr("time.monotonic", clock.monotonic)
        monkeypatch.setattr("time.sleep", clock.sleep)

        # Make the env-var default timeout very small so it times out after 1 poll
        monkeypatch.setenv("ATLAS_SYNC_PROTECTIVE_CANCEL_TIMEOUT_SEC", "0.5")

        broker = MagicMock()
        broker.get_open_orders.return_value = [
            self._make_held_order("XLK", "order-held-xlk")
        ]
        broker.cancel_order.return_value = MagicMock(success=True)
        # Always returns SUBMITTED → never confirms → timeout
        broker.get_order_status.return_value = OrderResult(
            success=True, order_id="order-held-xlk", status=OrderStatus.SUBMITTED
        )

        state_file = self._prime_state(tmp_path, "XLK", "sp500")

        with caplog.at_level(logging.ERROR, logger="atlas.sync_protective_orders"):
            result = _handle_held_stops(
                broker, "sp500",
                dry_run=False,
                send_telegram=False,
                state_file=state_file,
                state_tickers={"XLK"},
            )

        assert "XLK" not in result["resubmitted"], (
            f"Timed-out ticker XLK must NOT be in resubmitted: {result['resubmitted']}"
        )
        assert "XLK" in result["errors"], (
            f"Timed-out ticker XLK must be in errors: {result['errors']}"
        )
        error_msgs = [
            r for r in caplog.records
            if r.levelno >= logging.ERROR
            and ("XLK" in r.message or "cancel confirmation" in r.message.lower())
        ]
        assert error_msgs, (
            f"Expected ERROR log mentioning XLK or cancel confirmation timeout; "
            f"got: {[r.message for r in caplog.records]}"
        )
        # No place_order should have been attempted for this ticker
        broker.place_order.assert_not_called()
