"""Regression tests: reconcile_ledger stop-price fallback chain P1→P2→P3→P4.

Root cause: OCO bracket stops have status='held' and are NOT returned by
broker.get_open_orders() — the old guard skipped FSLR because it only
checked the live order list.  The fix adds a 4-level fallback chain:

  P1 — broker_orders table (includes held/oco rows)
  P2 — position_protective_orders table
  P3 — most recent plan JSON file (entry_price within ±2%)
  P4 — defer backfill, log warning, add to errors

All four tests mock broker.get_open_orders() → [] to prove we are NOT
relying on the old live-order lookup.

Run:
    cd /root/atlas && python3 -m pytest tests/test_reconcile_ledger_backfill_fallback.py -v --timeout=30
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as _adb
from db.atlas_db import get_db, record_trade_entry


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

_TICKER = "FSLR"
_MARKET = "sp500"
_ENTRY_PRICE = 218.16
_SHARES = 2


def _make_broker_position(
    ticker: str = _TICKER,
    entry_price: float = _ENTRY_PRICE,
    shares: int = _SHARES,
) -> MagicMock:
    pos = MagicMock()
    pos.ticker = ticker
    pos.entry_price = entry_price
    pos.shares = shares
    return pos


def _make_mock_broker(positions: list | None = None) -> MagicMock:
    """Return a mock broker suitable for passing to reconcile_ledger()."""
    broker = MagicMock()
    broker.get_positions.return_value = positions or [_make_broker_position()]
    broker.get_open_orders.return_value = []          # ← always empty; forces fallback chain
    broker.get_history_orders.return_value = []       # no fill history → entry_price from bp
    return broker


def _open_count(ticker: str = _TICKER, universe: str = _MARKET) -> int:
    with get_db() as db:
        row = db.execute(
            "SELECT COUNT(*) FROM trades WHERE ticker=? AND universe=? AND exit_date IS NULL",
            (ticker, universe),
        ).fetchone()
    return row[0] if row else 0


def _open_stop_price(ticker: str = _TICKER, universe: str = _MARKET) -> float | None:
    with get_db() as db:
        row = db.execute(
            "SELECT stop_price FROM trades WHERE ticker=? AND universe=? AND exit_date IS NULL",
            (ticker, universe),
        ).fetchone()
    return float(row[0]) if (row and row[0] is not None) else None


def _insert_broker_order_stop(
    stop_price: float,
    ticker: str = _TICKER,
    order_id: str = "test-stop-oco-001",
    order_class: str = "oco",
    status: str = "held",
) -> None:
    """Insert a SELL stop row into broker_orders with a raw_alpaca_json stop_price."""
    raw = json.dumps({
        "id": order_id,
        "symbol": ticker,
        "side": "OrderSide.SELL",
        "order_class": f"OrderClass.{order_class.upper()}",
        "order_type": "OrderType.STOP",
        "type": "OrderType.STOP",
        "stop_price": str(stop_price),
        "status": f"OrderStatus.{status.upper()}",
        "qty": "2",
        "filled_qty": "0",
        "submitted_at": "2026-05-06 13:46:06.437854+00:00",
    })
    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO broker_orders
                (order_id, symbol, side, qty, filled_qty, fill_price,
                 status, submitted_at, filled_at, order_class,
                 parent_id, raw_alpaca_json, last_synced_at)
            VALUES (?, ?, 'sell', 2, 0, NULL, ?, '2026-05-06 13:46:06', NULL,
                    ?, NULL, ?, datetime('now'))
            """,
            (order_id, ticker, status, order_class, raw),
        )


def _insert_position_protective_order(
    stop_price: float,
    ticker: str = _TICKER,
    market_id: str = _MARKET,
) -> None:
    """Insert an active row into position_protective_orders."""
    with get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO position_protective_orders
                (market_id, ticker, trade_id, position_qty,
                 stop_order_id, stop_price, tp_order_id, tp_price,
                 oco_class, last_synced_at, status)
            VALUES (?, ?, NULL, 2.0,
                    'ppo-stop-test-001', ?, 'ppo-tp-test-001', 272.54,
                    'oco', datetime('now'), 'active')
            """,
            (market_id, ticker, stop_price),
        )


def _run_reconcile(broker: MagicMock) -> dict:
    """Run reconcile_ledger with universe/derive patches."""
    import scripts.reconcile_ledger as rl_mod
    with (
        patch("universe.builder.get_universe_tickers", return_value=[_TICKER, "AAPL"]),
        patch("universe.membership.derive_universe", return_value=_MARKET),
    ):
        return rl_mod.reconcile_ledger(
            market_id=_MARKET,
            dry_run=False,
            broker=broker,
        )


# ─────────────────────────────────────────────────────────────────────────────
# P1 — stop from broker_orders table
# ─────────────────────────────────────────────────────────────────────────────

class TestP1BrokerOrders:
    """P1: broker_orders has a held OCO stop → backfill fires with that stop_price."""

    def test_backfill_uses_broker_orders_stop_price(self):
        """FSLR OCO stop in broker_orders (status=held, stop_price=200.0) → INSERT with 200.0."""
        assert _open_count() == 0

        _insert_broker_order_stop(stop_price=200.0)

        broker = _make_mock_broker()
        result = _run_reconcile(broker)

        # get_open_orders is NOT called — new code uses DB tables instead (the whole fix)
        broker.get_open_orders.assert_not_called()

        assert _open_count() == 1, f"Expected backfill INSERT, got 0 rows. errors={result['errors']}"
        stop = _open_stop_price()
        assert stop == pytest.approx(200.0, rel=1e-4), f"Expected stop=200.0, got {stop}"
        assert _TICKER in result["backfilled"], f"Expected {_TICKER} in backfilled: {result}"

    def test_p1_ignores_tp_leg_with_null_stop_price(self):
        """TP leg (OrderType.LIMIT, stop_price=None) must NOT be used as the stop.

        Verifies that P1 correctly skips rows where stop_price='None' in the JSON
        and finds the real stop leg (stop_price=213.84, status=held).
        """
        assert _open_count() == 0

        # Insert TP leg first (later submitted_at → would be returned first by DESC sort)
        tp_raw = json.dumps({
            "id": "test-tp-oco-002",
            "symbol": _TICKER,
            "side": "OrderSide.SELL",
            "order_class": "OrderClass.OCO",
            "order_type": "OrderType.LIMIT",
            "type": "OrderType.LIMIT",
            "stop_price": "None",      # ← TP leg: no stop_price
            "status": "OrderStatus.NEW",
            "qty": "2",
            "filled_qty": "0",
            "submitted_at": "2026-05-06 13:46:06.500000+00:00",  # later → returned first
        })
        with get_db() as db:
            db.execute(
                """
                INSERT OR REPLACE INTO broker_orders
                    (order_id, symbol, side, qty, filled_qty, fill_price,
                     status, submitted_at, filled_at, order_class,
                     parent_id, raw_alpaca_json, last_synced_at)
                VALUES ('test-tp-oco-002', ?, 'sell', 2, 0, NULL,
                        'new', '2026-05-06 13:46:06.5', NULL, 'oco',
                        NULL, ?, datetime('now'))
                """,
                (_TICKER, tp_raw),
            )
        # Insert stop leg with earlier submitted_at (returned second by DESC)
        _insert_broker_order_stop(
            stop_price=213.84,
            order_id="test-stop-oco-003",
            status="held",
        )

        broker = _make_mock_broker()
        result = _run_reconcile(broker)

        assert _open_count() == 1, f"Expected backfill INSERT. errors={result['errors']}"
        stop = _open_stop_price()
        # Must use the stop leg's price (213.84), not 0 from the TP leg
        assert stop == pytest.approx(213.84, rel=1e-4), (
            f"Expected stop=213.84 (from stop leg, skipping TP leg). Got {stop}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P2 — stop from position_protective_orders
# ─────────────────────────────────────────────────────────────────────────────

class TestP2PositionProtectiveOrders:
    """P2: broker_orders empty, position_protective_orders has active row → use it."""

    def test_backfill_uses_position_protective_orders_stop_price(self):
        """P1 returns no matching row; P2 has stop_price=213.84 → INSERT with 213.84."""
        assert _open_count() == 0

        # No broker_orders row for FSLR → P1 finds nothing
        # Insert position_protective_orders row with stop_price=213.84
        _insert_position_protective_order(stop_price=213.84)

        broker = _make_mock_broker()
        result = _run_reconcile(broker)

        broker.get_open_orders.assert_not_called()

        assert _open_count() == 1, f"Expected backfill INSERT (P2 path). errors={result['errors']}"
        stop = _open_stop_price()
        assert stop == pytest.approx(213.84, rel=1e-4), (
            f"Expected stop=213.84 from position_protective_orders, got {stop}"
        )
        assert _TICKER in result["backfilled"]


# ─────────────────────────────────────────────────────────────────────────────
# P3 — stop from plan file
# ─────────────────────────────────────────────────────────────────────────────

class TestP3PlanFile:
    """P3: broker_orders and position_protective_orders both empty; plan has the stop."""

    def test_backfill_uses_plan_stop_price(self, tmp_path):
        """P1 empty, P2 empty, plan file has FSLR entry_price≈218.16 → stop=210.0."""
        assert _open_count() == 0

        # No broker_orders or position_protective_orders rows for FSLR

        # Create plan file in tmp_path plans/ dir
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        plan_file = plans_dir / f"plan_{_MARKET}_20260507_120000.json"
        plan_data = {
            "proposed_entries": [
                {
                    "ticker": _TICKER,
                    "strategy": "momentum_breakout",
                    "entry_price": 218.16,    # matches broker fill exactly
                    "stop_price": 210.0,
                    "take_profit": 260.0,
                    "confidence": 0.75,
                },
                {
                    "ticker": "AAPL",
                    "strategy": "momentum_breakout",
                    "entry_price": 175.0,
                    "stop_price": 168.0,
                    "confidence": 0.65,
                },
            ]
        }
        plan_file.write_text(json.dumps(plan_data))

        broker = _make_mock_broker()

        import scripts.reconcile_ledger as rl_mod
        with (
            patch("universe.builder.get_universe_tickers", return_value=[_TICKER, "AAPL"]),
            patch("universe.membership.derive_universe", return_value=_MARKET),
            patch("scripts.reconcile_ledger.PROJECT", tmp_path),
        ):
            result = rl_mod.reconcile_ledger(
                market_id=_MARKET,
                dry_run=False,
                broker=broker,
            )

        broker.get_open_orders.assert_not_called()

        assert _open_count() == 1, (
            f"Expected backfill INSERT (P3 plan path). errors={result['errors']}"
        )
        stop = _open_stop_price()
        assert stop == pytest.approx(210.0, rel=1e-4), (
            f"Expected stop=210.0 from plan file, got {stop}"
        )
        assert _TICKER in result["backfilled"]

    def test_p3_ignores_plan_when_entry_price_too_far(self, tmp_path):
        """P3 skips plan entry when plan_entry_price differs >2% from broker fill."""
        assert _open_count() == 0

        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()
        plan_file = plans_dir / f"plan_{_MARKET}_20260507_110000.json"
        plan_data = {
            "proposed_entries": [
                {
                    "ticker": _TICKER,
                    "strategy": "momentum_breakout",
                    "entry_price": 195.0,   # >2% away from 218.16 → should be ignored
                    "stop_price": 180.0,
                    "confidence": 0.5,
                }
            ]
        }
        plan_file.write_text(json.dumps(plan_data))

        broker = _make_mock_broker()

        import scripts.reconcile_ledger as rl_mod
        with (
            patch("universe.builder.get_universe_tickers", return_value=[_TICKER]),
            patch("universe.membership.derive_universe", return_value=_MARKET),
            patch("scripts.reconcile_ledger.PROJECT", tmp_path),
        ):
            result = rl_mod.reconcile_ledger(
                market_id=_MARKET,
                dry_run=False,
                broker=broker,
            )

        # Plan entry_price is >2% off → P3 skips it → falls to P4 → deferred
        assert _open_count() == 0, "No INSERT expected when plan entry_price is outside ±2%"
        assert any("backfill deferred" in str(e) for e in result["errors"]), (
            f"Expected 'backfill deferred' in errors: {result['errors']}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P4 — all sources empty: deferred warning, no INSERT
# ─────────────────────────────────────────────────────────────────────────────

class TestP4Deferred:
    """P4: broker_orders, position_protective_orders, and plan files all empty."""

    def test_deferred_warning_and_no_insert(self, tmp_path, caplog):
        """All three sources empty → warning logged, errors list updated, no DB row."""
        import logging
        assert _open_count() == 0

        # Empty plans dir
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()

        broker = _make_mock_broker()

        import scripts.reconcile_ledger as rl_mod
        with (
            patch("universe.builder.get_universe_tickers", return_value=[_TICKER]),
            patch("universe.membership.derive_universe", return_value=_MARKET),
            patch("scripts.reconcile_ledger.PROJECT", tmp_path),
            caplog.at_level(logging.WARNING),
        ):
            result = rl_mod.reconcile_ledger(
                market_id=_MARKET,
                dry_run=False,
                broker=broker,
            )

        broker.get_open_orders.assert_not_called()

        # No INSERT
        assert _open_count() == 0, "P4 must NOT insert any row"

        # Warning logged
        deferred_logs = [r for r in caplog.records if "backfill deferred" in r.message.lower()]
        assert deferred_logs, (
            f"Expected 'backfill deferred' warning in logs. "
            f"Log messages: {[r.message for r in caplog.records]}"
        )

        # errors list updated with "backfill deferred"
        assert any("backfill deferred" in str(e) for e in result["errors"]), (
            f"Expected 'backfill deferred' in errors: {result['errors']}"
        )

        # NOT in backfilled
        assert _TICKER not in result.get("backfilled", []), (
            f"{_TICKER} should NOT be in backfilled list on P4 path"
        )
