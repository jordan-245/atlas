"""tests/test_premature_closure.py — #FIX-PMEQ-002 regression tests.

Verifies that trade exit_date reflects the ACTUAL broker fill timestamp
(filled_at) rather than the detection wall-clock time (datetime.now()).

The bug: reconcile_fills() previously called db.record_trade_exit() without
an exit_date argument, so record_trade_exit always wrote datetime.now() as the
exit timestamp.  When a reconcile script detected a fill at premarket (08:00
AEST) that actually occurred at 18:00 AEST, the trade was stamped 10 hours
early — a "premature closure" (#FIX-PMEQ-002).

Real-world example: XLY id=167, entry 2026-04-22, exit recorded 2026-04-30
08:00:34 AEST but actual broker fill was ~18:00 AEST the same day.

Run with: python3 -m pytest tests/test_premature_closure.py -v --timeout=30
"""
from __future__ import annotations

import sqlite3
import sys
import types
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from brokers.base import OrderResult, OrderSide, OrderStatus  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_sell_order(
    ticker: str,
    fill_price: float,
    filled_at: str | None,
    status: str = "filled",
    order_id: str = "ord-sell-001",
) -> OrderResult:
    """Build a minimal OrderResult representing a broker SELL order."""
    is_filled = status == "filled"
    return OrderResult(
        success=True,
        order_id=order_id,
        ticker=ticker,
        side=OrderSide.SELL,
        status=OrderStatus.FILLED if is_filled else OrderStatus.SUBMITTED,
        requested_qty=5,
        filled_qty=5 if is_filled else 0,
        fill_price=fill_price if is_filled else 0.0,
        raw={
            "filled_at": filled_at,
            "status": status,
            "order_type": "market",
            "order_class": "simple",
            "parent_id": None,
            "submitted_at": "2026-04-30T07:00:00+00:00",
        },
    )


def _make_open_trade(
    ticker: str,
    trade_id: int = 167,
    strategy: str = "momentum_breakout",
    universe: str = "sector_etfs",
    entry_date: str = "2026-04-22",
) -> dict[str, Any]:
    """Build a minimal open-trade dict matching db.get_open_positions() output."""
    return {
        "id": trade_id,
        "ticker": ticker,
        "strategy": strategy,
        "universe": universe,
        "entry_date": entry_date,
        "entry_price": 116.0,
        "shares": 5,
        "status": "open",
        "stop_price": 110.0,
    }


def _make_mock_db(open_trades: list[dict]) -> MagicMock:
    """Mock the atlas_db module with get_open_positions + record_trade_exit."""
    mock_db = MagicMock()
    mock_db.get_open_positions.return_value = open_trades
    mock_db.record_trade_exit.return_value = None
    return mock_db


def _make_mock_broker(orders: list[OrderResult]) -> MagicMock:
    """Mock broker with get_history_orders."""
    broker = MagicMock()
    broker.get_history_orders.return_value = orders
    return broker


# ─────────────────────────────────────────────────────────────────────────────
# Patch helpers — keep reconcile_fills isolated from filesystem / DB
# ─────────────────────────────────────────────────────────────────────────────

def _run_reconcile_fills(
    market_id: str,
    broker: MagicMock,
    mock_db: MagicMock,
    market_tickers: set[str] | None = None,
    existing_broker_orders: dict | None = None,
    dry_run: bool = False,
):
    """Run core.reconcile.reconcile_fills with all filesystem dependencies patched."""
    from core.reconcile import reconcile_fills

    market_tickers = market_tickers or {"XLY", "XLI", "GLD"}
    existing_broker_orders = existing_broker_orders or {}  # all orders are "new"

    with (
        patch("core.reconcile._get_market_tickers", return_value=market_tickers),
        patch("core.reconcile._load_existing_broker_orders", return_value=existing_broker_orders),
        patch("core.reconcile._upsert_broker_order_row"),  # suppress DB writes for broker_orders
    ):
        return reconcile_fills(
            market_id=market_id,
            broker=broker,
            db=mock_db,
            dry_run=dry_run,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestExitDateUsesBrokerFillTimestamp:
    """#FIX-PMEQ-002: exit_date must come from broker filled_at, not datetime.now()."""

    def test_exit_date_uses_broker_fill_timestamp(self):
        """When broker order has filled_at, exit_date must match filled_at, not wall-clock.

        Scenario:
          - Broker fill occurred at 2026-04-30T18:00:00+10:00 (AEST afternoon)
          - Reconcile script ran at 2026-04-30T20:00:00+10:00 (2 hours later)
          - Previously: exit_date = 2026-04-30T20:00:00 (wrong — detection time)
          - Expected:   exit_date = 2026-04-30T18:00:00+10:00 (correct — fill time)
        """
        broker_fill_ts = "2026-04-30T18:00:00+10:00"
        order = _make_sell_order("XLY", fill_price=116.71, filled_at=broker_fill_ts)
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        # Freeze wall-clock to a time DIFFERENT from the broker fill
        frozen_now = "2026-04-30T20:00:00.000000"
        with patch("db.atlas_db.datetime") as mock_dt:
            mock_dt.now.return_value.isoformat.return_value = frozen_now
            _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        # record_trade_exit must have been called with exit_date=broker_fill_ts
        assert mock_db.record_trade_exit.called, "record_trade_exit was not called"
        kwargs = mock_db.record_trade_exit.call_args.kwargs
        assert kwargs.get("exit_date") == broker_fill_ts, (
            f"exit_date={kwargs.get('exit_date')!r} should be broker filled_at={broker_fill_ts!r}, "
            "not the detection wall-clock time"
        )

    def test_exit_date_not_detection_wallclock(self):
        """Complementary: confirm exit_date is NOT the wall-clock detection time.

        If exit_date equals the mocked datetime.now(), the bug has regressed.
        """
        broker_fill_ts = "2026-04-30T08:00:00+00:00"
        detection_time = "2026-04-30T18:00:00.000000"  # detection 10h AFTER fill

        order = _make_sell_order("XLY", fill_price=116.71, filled_at=broker_fill_ts)
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        kwargs = mock_db.record_trade_exit.call_args.kwargs
        actual_exit_date = kwargs.get("exit_date")
        assert actual_exit_date == broker_fill_ts
        assert actual_exit_date != detection_time, (
            "exit_date must not be the detection wall-clock time"
        )

    def test_exit_date_falls_back_when_no_filled_at(self):
        """When broker order has NO filled_at, exit_date parameter is None.

        record_trade_exit defaults to datetime.now() when exit_date=None;
        the fallback is acceptable when no broker timestamp is available.
        """
        order = _make_sell_order("XLY", fill_price=116.71, filled_at=None)
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        kwargs = mock_db.record_trade_exit.call_args.kwargs
        # exit_date=None signals record_trade_exit to use datetime.now() internally
        assert kwargs.get("exit_date") is None, (
            "When no filled_at is available, exit_date should be None "
            "(not a hardcoded datetime.now() string from reconcile_fills)"
        )

    def test_exit_date_none_for_empty_string_filled_at(self):
        """Empty string filled_at normalised to None (not passed as exit_date)."""
        order = _make_sell_order("XLY", fill_price=116.71, filled_at="")
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        kwargs = mock_db.record_trade_exit.call_args.kwargs
        assert kwargs.get("exit_date") is None


class TestNoPrematureCloseWithoutBrokerFill:
    """Trades must NOT be closed when there is no confirmed broker SELL fill."""

    def test_no_premature_close_without_broker_fill(self):
        """SELL order present in history but NOT filled → no trade closure.

        Scenario: broker order status='new'/'pending_new' (e.g. limit order
        in queue during after-hours).  Reconcile should not close the trade.
        """
        order = _make_sell_order(
            "XLY", fill_price=0.0, filled_at=None, status="new"
        )
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        report = _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        # Trade must NOT be closed
        assert not mock_db.record_trade_exit.called, (
            "record_trade_exit must NOT be called when broker SELL is not filled"
        )
        assert report.trades_closed == [], (
            "No trades should be closed for an unfilled SELL order"
        )

    def test_no_premature_close_for_submitted_order(self):
        """SELL order with status='submitted'/'accepted' → no closure."""
        order = _make_sell_order(
            "XLY", fill_price=0.0, filled_at=None, status="accepted"
        )
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        report = _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        assert not mock_db.record_trade_exit.called
        assert len(report.trades_closed) == 0

    def test_dry_run_does_not_close_trade(self):
        """In dry_run mode, record_trade_exit is never called regardless of fill."""
        broker_fill_ts = "2026-04-30T18:00:00+10:00"
        order = _make_sell_order("XLY", fill_price=116.71, filled_at=broker_fill_ts)
        mock_db = _make_mock_db(open_trades=[_make_open_trade("XLY")])
        broker = _make_mock_broker([order])

        report = _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=True)

        assert not mock_db.record_trade_exit.called, (
            "dry_run=True: record_trade_exit must never be called"
        )
        # dry_run reports what WOULD have happened
        assert report.trades_closed == [0]


class TestXly167Regression:
    """Regression test for the exact XLY id=167 premature closure scenario.

    Facts:
      - trade entry: 2026-04-22T09:00:09
      - exit recorded: 2026-04-30T08:00:34 (AEST premarket — was datetime.now() at detection)
      - actual broker fill: ~2026-04-30T18:00:00 AEST (10 hours AFTER recorded exit)
      - exit_reason: reconcile_fill
      - exit_price: 116.7134

    After the fix, exit_date must equal the broker's filled_at, not the
    detection timestamp.
    """

    _BROKER_FILL_TS = "2026-04-30T18:00:00+10:00"   # actual Alpaca filled_at
    _DETECTION_TS   = "2026-04-30T08:00:34.278815"   # what datetime.now() returned

    def _xly_open_trade(self) -> dict:
        return _make_open_trade(
            "XLY",
            trade_id=167,
            strategy="momentum_breakout",
            universe="sector_etfs",
            entry_date="2026-04-22",
        )

    def test_xly_167_regression(self):
        """Synthesise XLY id=167: fill at 18:00, detection at 08:00 → exit_date=18:00."""
        order = _make_sell_order(
            "XLY",
            fill_price=116.7134,
            filled_at=self._BROKER_FILL_TS,
            order_id="xly-ord-167",
        )
        mock_db = _make_mock_db(open_trades=[self._xly_open_trade()])
        broker = _make_mock_broker([order])

        # Simulate reconcile running at 08:00:34 AEST
        with patch("db.atlas_db.datetime") as mock_dt:
            mock_dt.now.return_value.isoformat.return_value = self._DETECTION_TS
            _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        assert mock_db.record_trade_exit.called
        kwargs = mock_db.record_trade_exit.call_args.kwargs

        assert kwargs["exit_date"] == self._BROKER_FILL_TS, (
            f"XLY id=167 regression: exit_date={kwargs['exit_date']!r} "
            f"should equal broker filled_at={self._BROKER_FILL_TS!r}, "
            f"not detection time {self._DETECTION_TS!r}"
        )
        assert kwargs["exit_price"] == pytest.approx(116.7134)
        assert kwargs["exit_reason"] in ("reconcile_fill", "market_exit", "stop_fill")

    def test_xly_167_exit_date_not_detection_time(self):
        """Ensure the old broken behaviour (detection time as exit_date) does not return."""
        order = _make_sell_order(
            "XLY", fill_price=116.7134, filled_at=self._BROKER_FILL_TS
        )
        mock_db = _make_mock_db(open_trades=[self._xly_open_trade()])
        broker = _make_mock_broker([order])

        _run_reconcile_fills("sector_etfs", broker, mock_db, dry_run=False)

        kwargs = mock_db.record_trade_exit.call_args.kwargs
        exit_date = kwargs.get("exit_date", "")
        # The old bug: exit_date = "2026-04-30T08:00:34..." (detection time)
        assert not exit_date.startswith("2026-04-30T08"), (
            "exit_date must not be the 08:xx detection time; "
            "it should be the broker fill time ~18:xx"
        )


class TestRecordTradeExitExitDateParam:
    """Direct unit tests for the exit_date parameter on record_trade_exit."""

    def _init_db(self, tmp_path: Path) -> str:
        """Create a minimal in-memory SQLite trades table."""
        import db.atlas_db as _adb
        _adb.init_db()

    def test_record_trade_exit_uses_provided_exit_date(self, tmp_path):
        """record_trade_exit stores the caller-supplied exit_date verbatim.

        The exit_date must be on or after the entry_date to pass the ghost-trade
        guard. We use a future timestamp so the test is stable regardless of when
        it runs.
        """
        import db.atlas_db as _adb

        _adb.init_db()
        # Insert an open trade with entry_date = today (record_trade_entry default)
        _adb.record_trade_entry(
            ticker="XLY",
            strategy="test_strategy",
            universe="sector_etfs",
            entry_price=110.0,
            shares=5,
            stop_price=100.0,
            take_profit=120.0,
            confidence=0.8,
            regime_state=None,
            direction="long",
        )

        # Backdate the entry_date in DB so that exit_date (2026-04-30) is AFTER it
        with _adb.get_db() as conn:
            conn.execute(
                "UPDATE trades SET entry_date=? WHERE ticker='XLY' AND status='open'",
                ("2026-04-22",),  # entry before the exit date below
            )

        explicit_exit_date = "2026-04-30T18:00:00+10:00"
        _adb.record_trade_exit(
            ticker="XLY",
            strategy="test_strategy",
            exit_price=116.71,
            exit_reason="reconcile_fill",
            regime_at_exit=None,
            exit_date=explicit_exit_date,
        )

        with _adb.get_db() as conn:
            row = conn.execute(
                "SELECT exit_date FROM trades WHERE ticker='XLY' AND status='closed'"
            ).fetchone()

        assert row is not None, "Closed trade not found"
        assert row["exit_date"] == explicit_exit_date, (
            f"exit_date={row['exit_date']!r} should be {explicit_exit_date!r}"
        )

    def test_record_trade_exit_defaults_to_now_when_no_exit_date(self, tmp_path):
        """Without exit_date parameter, record_trade_exit falls back to datetime.now()."""
        import db.atlas_db as _adb

        _adb.init_db()
        _adb.record_trade_entry(
            ticker="AAPL",
            strategy="test_strategy",
            universe="sp500",
            entry_price=150.0,
            shares=10,
            stop_price=140.0,
            take_profit=160.0,
            confidence=0.8,
            regime_state=None,
            direction="long",
        )

        before = datetime.now().isoformat()[:16]  # truncate to minute
        _adb.record_trade_exit(
            ticker="AAPL",
            strategy="test_strategy",
            exit_price=155.0,
            exit_reason="take_profit",
            regime_at_exit=None,
            # exit_date NOT provided → defaults to datetime.now()
        )
        after = datetime.now().isoformat()[:16]

        with _adb.get_db() as conn:
            row = conn.execute(
                "SELECT exit_date FROM trades WHERE ticker='AAPL' AND status='closed'"
            ).fetchone()

        assert row is not None
        exit_date_min = row["exit_date"][:16]
        assert before <= exit_date_min <= after, (
            f"exit_date={row['exit_date']!r} should be close to now (between {before} and {after})"
        )
