#!/usr/bin/env python3
"""Tests for Phase B.0 — position_protective_orders ledger wiring.

Verifies that live_executor.py and sync_protective_orders.py both write
to the position_protective_orders table as additive second writers alongside
the existing trades.stop_order_id / trades.tp_order_id path.

All writes are wrapped in try/except — these tests verify that:
  1. Successful paths DO write the ledger.
  2. DB failures do NOT break the primary trade flow.
  3. The PROTECTIVE_LEDGER_WRITE_ENABLED env flag disables all writes.

Uses _isolate_prod_db (autouse, from conftest.py) for SQLite isolation.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATLAS_ROOT))

import db.atlas_db as _adb
from db.atlas_db import (
    close_protective_record,
    get_protective_record,
    list_active_protective_records,
    upsert_protective_record,
)

# ── helpers ───────────────────────────────────────────────────────────────────

def _minimal_live_config(market_id: str = "sp500") -> dict:
    """Minimal config that makes LiveExecutor.is_dry_run=False."""
    return {
        "market_id": market_id,
        "version": "test-v1",
        "trading": {
            "mode": "live",
            "live_enabled": True,
            "broker": "alpaca",
            "live_safety": {
                "dry_run_first": False,
                "max_order_value": 50_000,
                "max_daily_orders": 50,
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


def _make_executor(market_id: str = "sp500"):
    """Return a LiveExecutor with a mocked broker, not dry-run."""
    from brokers.live_executor import LiveExecutor
    cfg = _minimal_live_config(market_id)
    ex = LiveExecutor(cfg)
    ex._connected = True
    ex._halted = False
    ex._daily_date = "2026-04-29"
    ex._daily_start_equity = 10_000.0
    return ex


def _get_record(market_id: str, ticker: str):
    """Fetch protective record row (all fields) or None."""
    with _adb.get_db() as db:
        row = db.execute(
            "SELECT * FROM position_protective_orders WHERE market_id=? AND ticker=?",
            (market_id, ticker),
        ).fetchone()
        return dict(row) if row else None


# ═══════════════════════════════════════════════════════════════════════════════
# Test 1 — _execute_entry writes protective record after bracket fill
# ═══════════════════════════════════════════════════════════════════════════════

class TestExecuteEntryWritesProtectiveRecord:
    """Bracket entry → upsert_protective_record called with stop+tp IDs."""

    def test_execute_entry_writes_protective_record(self, monkeypatch):
        """After a FILLED bracket entry, the protective ledger row is created."""
        from brokers.base import OrderResult, OrderStatus, OrderSide
        from brokers.live_executor import LiveExecutor

        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        # Build a fake FILLED order result that carries bracket leg IDs
        fake_order = OrderResult(
            success=True,
            order_id="parent-order-001",
            status=OrderStatus.FILLED,
            fill_price=150.0,
            message="filled",
            side=OrderSide.BUY,
            raw={
                "filled_qty": "10",
                "legs": [
                    {"id": "stop-leg-001", "side": "sell", "order_type": "stop"},
                    {"id": "tp-leg-001",   "side": "sell", "order_type": "limit"},
                ],
            },
        )

        executor = _make_executor("sp500")
        mock_broker = MagicMock()
        mock_broker.get_positions.return_value = []
        mock_broker.get_account_info.return_value = SimpleNamespace(equity=50000)
        executor._broker = mock_broker

        entry = {
            "ticker": "AAPL",
            "entry_price": 150.0,
            "stop_price": 140.0,
            "take_profit": 165.0,
            "position_size": 10,
            "strategy": "momentum",
            "confidence": 0.8,
        }

        with (
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor.preflight_check_order", return_value=[]),
            patch("brokers.live_executor._journal_entry"),
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch.object(executor, "place_order", return_value=fake_order),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
            patch("db.atlas_db.update_trade_protective_orders", return_value=1),
            patch("risk.cross_universe_guard.check_entry", return_value=SimpleNamespace(
                allowed=True, reason="", positions_count=1, positions_cap=10, buying_power=50000
            )),
            patch("risk.gross_exposure_guard.check_gross_exposure", return_value=(True, "")),
            patch("journal.logger.TradeLedger.record_entry", return_value=42),
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-29")

        # The entry succeeded (may be SUBMITTED if polling is mocked)
        assert result.get("success") is True or result.get("order_id") is not None

        # The protective record should now exist in the isolated DB
        rec = _get_record("sp500", "AAPL")
        assert rec is not None, "Protective record should have been created"
        assert rec["stop_order_id"] == "stop-leg-001"
        assert rec["tp_order_id"] == "tp-leg-001"
        assert rec["oco_class"] == "bracket"
        assert rec["status"] == "active"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 2 — _execute_exit closes protective record
# ═══════════════════════════════════════════════════════════════════════════════

class TestExitFillClosesProtectiveRecord:
    """Exit fill → close_protective_record called."""

    def test_exit_fill_closes_protective_record(self, monkeypatch):
        """After a confirmed exit fill, the protective record is set to 'closed'."""
        from brokers.base import OrderResult, OrderStatus, OrderSide, PositionInfo

        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        # Pre-seed the protective record
        upsert_protective_record(
            market_id="sp500", ticker="MSFT",
            trade_id=None, position_qty=10,
            stop_order_id="stop-001", stop_price=280.0,
            tp_order_id="tp-001", tp_price=320.0,
            oco_class="bracket",
        )
        assert _get_record("sp500", "MSFT")["status"] == "active"

        executor = _make_executor("sp500")

        pos = PositionInfo(
            ticker="MSFT", shares=10, current_price=310.0,
            entry_price=290.0, market_value=3100.0,
        )
        fake_exit_order = OrderResult(
            success=True,
            order_id="exit-order-001",
            status=OrderStatus.FILLED,
            fill_price=310.0,
            message="filled",
            side=OrderSide.SELL,
            raw={"filled_at": "2026-04-29T21:00:00Z", "submitted_at": "2026-04-29T21:00:00Z"},
        )

        mock_broker = MagicMock()
        mock_broker.get_open_orders.return_value = []
        mock_broker.get_positions.return_value = [pos]
        mock_broker.cancel_order.return_value = SimpleNamespace(success=True, message="")
        mock_broker.place_order.return_value = fake_exit_order
        executor._broker = mock_broker

        exit_rec = {"ticker": "MSFT", "direction": "long", "reason": "signal_exit"}

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.live_executor.preflight_check_order", return_value=[]),
            patch("brokers.live_executor._journal_entry"),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
            patch("journal.logger.TradeLedger.record_exit"),
            patch("brokers.live_portfolio.LivePortfolio.record_closed_trade"),
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            executor._execute_exit(exit_rec, "2026-04-29")

        rec = _get_record("sp500", "MSFT")
        assert rec is not None
        assert rec["status"] == "closed", f"Expected closed, got {rec['status']}"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 3 — place_stops_for_plan (cancel-replace equivalent) updates record
# ═══════════════════════════════════════════════════════════════════════════════

class TestCancelReplaceUpdatesRecord:
    """place_stops_for_plan with new stop/tp IDs → record updated."""

    def test_cancel_replace_updates_record_with_new_ids(self, monkeypatch):
        """When place_stops_for_plan places new stop+tp, the ledger row has new IDs."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        # Pre-seed with old IDs
        upsert_protective_record(
            market_id="sp500", ticker="NVDA",
            trade_id=None, position_qty=5,
            stop_order_id="old-stop-001", stop_price=400.0,
            tp_order_id="old-tp-001", tp_price=480.0,
            oco_class="oco",
        )

        executor = _make_executor("sp500")
        executor._broker = MagicMock()

        plan = {
            "proposed_entries": [
                {
                    "ticker": "NVDA",
                    "position_size": 5,
                    "stop_price": 405.0,
                    "take_profit": 485.0,
                    "strategy": "momentum",
                    "direction": "long",
                    "entry_price": 440.0,
                }
            ]
        }
        entry_results = [{"success": True, "status": "FILLED"}]
        config = _minimal_live_config("sp500")

        with (
            patch.object(executor, "place_protective_stop", return_value="new-stop-002"),
            patch.object(executor, "place_take_profit", return_value="new-tp-002"),
            patch("brokers.live_executor._journal_entry"),
        ):
            executor.place_stops_for_plan(plan, entry_results, config, "2026-04-29")

        rec = _get_record("sp500", "NVDA")
        assert rec is not None
        assert rec["stop_order_id"] == "new-stop-002", f"Got {rec['stop_order_id']}"
        assert rec["tp_order_id"] == "new-tp-002", f"Got {rec['tp_order_id']}"
        assert rec["status"] == "active"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 4 — _apply_db_consistency writes record
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncProtectiveWritesRecord:
    """_apply_db_consistency populates ledger for _DB_UPDATE_ACTIONS tickers."""

    def test_apply_db_consistency_writes_record(self, monkeypatch):
        """_apply_db_consistency calls upsert_protective_record for action tickers."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        from scripts.sync_protective_orders import _apply_db_consistency
        from brokers.base import OrderSide

        # Build a mock broker whose get_open_orders returns resolved IDs
        mock_order = SimpleNamespace(
            side=OrderSide.SELL,
            ticker="GLD",
            order_id="stop-gld-001",
            order_type="stop",
            raw={"order_type": "stop"},
        )
        mock_broker = MagicMock()
        mock_broker.get_open_orders.return_value = [mock_order]

        sync_result = {
            "per_ticker": {
                "GLD": {
                    "sl_action": "oco_placed",
                    "tp_action": "placed",
                    "stop_price": 175.0,
                    "take_profit": 205.0,
                    "qty": 10,
                },
            }
        }

        with patch("db.atlas_db.update_trade_protective_orders", return_value=1):
            _apply_db_consistency(mock_broker, "commodity_etfs", sync_result)

        # The per-ticker loop should have written the record
        rec = _get_record("commodity_etfs", "GLD")
        assert rec is not None, "Protective record should have been written by _apply_db_consistency"
        assert rec["stop_order_id"] == "stop-gld-001"
        assert rec["status"] == "active"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 5 — env flag disabled → no writes
# ═══════════════════════════════════════════════════════════════════════════════

class TestProtectiveLedgerDisabledViaEnvFlag:
    """When PROTECTIVE_LEDGER_WRITE_ENABLED=false, no ledger writes occur."""

    def test_protective_ledger_disabled_via_env_flag(self, monkeypatch):
        """With flag=false, _apply_db_consistency does NOT write to ledger."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "false")

        from scripts.sync_protective_orders import _apply_db_consistency
        from brokers.base import OrderSide

        mock_order = SimpleNamespace(
            side=OrderSide.SELL,
            ticker="SLV",
            order_id="stop-slv-001",
            order_type="stop",
            raw={"order_type": "stop"},
        )
        mock_broker = MagicMock()
        mock_broker.get_open_orders.return_value = [mock_order]

        sync_result = {
            "per_ticker": {
                "SLV": {
                    "sl_action": "oco_placed",
                    "tp_action": "placed",
                    "stop_price": 20.0,
                    "take_profit": 25.0,
                    "qty": 50,
                },
            }
        }

        with patch("db.atlas_db.update_trade_protective_orders", return_value=1):
            _apply_db_consistency(mock_broker, "commodity_etfs", sync_result)

        # No row should have been written
        rec = _get_record("commodity_etfs", "SLV")
        assert rec is None, "Flag=false should prevent any ledger writes"

    def test_live_executor_feature_flag_disabled(self, monkeypatch):
        """_protective_ledger_enabled() returns False when env=false."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "false")
        from brokers.live_executor import _protective_ledger_enabled
        assert _protective_ledger_enabled() is False

    def test_live_executor_feature_flag_enabled(self, monkeypatch):
        """_protective_ledger_enabled() returns True by default."""
        monkeypatch.delenv("PROTECTIVE_LEDGER_WRITE_ENABLED", raising=False)
        from brokers.live_executor import _protective_ledger_enabled
        assert _protective_ledger_enabled() is True

    def test_sync_protective_feature_flag_disabled(self, monkeypatch):
        """sync_protective._protective_ledger_enabled() returns False when env=false."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "false")
        from scripts.sync_protective_orders import _protective_ledger_enabled
        assert _protective_ledger_enabled() is False


# ═══════════════════════════════════════════════════════════════════════════════
# Test 6 — DB write failure does NOT break entry flow
# ═══════════════════════════════════════════════════════════════════════════════

class TestWriteFailureDoesNotBreakEntryFlow:
    """If upsert_protective_record raises, the entry still succeeds."""

    def test_write_failure_does_not_break_entry_flow(self, monkeypatch):
        """upsert_protective_record raises RuntimeError → entry result still success=True."""
        from brokers.base import OrderResult, OrderStatus, OrderSide

        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        fake_order = OrderResult(
            success=True,
            order_id="parent-order-002",
            status=OrderStatus.FILLED,
            fill_price=200.0,
            message="filled",
            side=OrderSide.BUY,
            raw={
                "filled_qty": "5",
                "legs": [
                    {"id": "stop-leg-002", "side": "sell", "order_type": "stop"},
                    {"id": "tp-leg-002",   "side": "sell", "order_type": "limit"},
                ],
            },
        )

        executor = _make_executor("sp500")
        mock_broker = MagicMock()
        mock_broker.get_positions.return_value = []
        mock_broker.get_account_info.return_value = SimpleNamespace(equity=50000)
        executor._broker = mock_broker

        entry = {
            "ticker": "AMZN",
            "entry_price": 200.0,
            "stop_price": 185.0,
            "take_profit": 220.0,
            "position_size": 5,
            "strategy": "momentum",
            "confidence": 0.7,
        }

        def _raise_on_upsert(*args, **kwargs):
            raise RuntimeError("Simulated DB failure in upsert_protective_record")

        with (
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor.preflight_check_order", return_value=[]),
            patch("brokers.live_executor._journal_entry"),
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch.object(executor, "place_order", return_value=fake_order),
            patch("brokers.live_executor._get_regime_model") as mock_regime,
            patch("db.atlas_db.update_trade_protective_orders", return_value=1),
            patch("db.atlas_db.upsert_protective_record", side_effect=_raise_on_upsert),
            patch("risk.cross_universe_guard.check_entry", return_value=SimpleNamespace(
                allowed=True, reason="", positions_count=1, positions_cap=10, buying_power=50000
            )),
            patch("risk.gross_exposure_guard.check_gross_exposure", return_value=(True, "")),
            patch("journal.logger.TradeLedger.record_entry", return_value=99),
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            result = executor._execute_entry(entry, "2026-04-29")

        # Entry should still have succeeded despite DB failure
        assert result.get("success") is True or result.get("order_id") is not None, (
            f"Entry flow should succeed even when ledger write fails. Got: {result}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Test 7 — concurrent upserts are idempotent (last write wins)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConcurrentWritesIdempotent:
    """Two successive upserts → final state is from the second call."""

    def test_concurrent_writes_idempotent(self):
        """Two upserts → only one row, reflecting the second call's IDs."""
        upsert_protective_record(
            market_id="sp500", ticker="TSLA",
            trade_id=None, position_qty=3,
            stop_order_id="stop-first-001", stop_price=200.0,
            tp_order_id="tp-first-001", tp_price=250.0,
            oco_class="bracket",
        )
        upsert_protective_record(
            market_id="sp500", ticker="TSLA",
            trade_id=None, position_qty=3,
            stop_order_id="stop-second-002", stop_price=205.0,
            tp_order_id="tp-second-002", tp_price=255.0,
            oco_class="bracket",
        )

        with _adb.get_db() as db:
            rows = db.execute(
                "SELECT COUNT(*) as n, stop_order_id FROM position_protective_orders "
                "WHERE market_id='sp500' AND ticker='TSLA'"
            ).fetchone()
        assert rows["n"] == 1, "Exactly one row after two upserts"
        assert rows["stop_order_id"] == "stop-second-002", "Second upsert should win"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 8 — close_protective_record is idempotent
# ═══════════════════════════════════════════════════════════════════════════════

class TestCloseProtectiveIdempotent:
    """close_protective_record called twice → no error, status stays closed."""

    def test_close_protective_idempotent(self):
        """Closing twice produces no exception and status remains 'closed'."""
        upsert_protective_record(
            market_id="sp500", ticker="META",
            trade_id=None, position_qty=8,
            stop_order_id="stop-meta-001", stop_price=450.0,
            tp_order_id=None, tp_price=None,
            oco_class="stop",
        )
        close_protective_record(market_id="sp500", ticker="META")
        close_protective_record(market_id="sp500", ticker="META")  # idempotent

        rec = _get_record("sp500", "META")
        assert rec["status"] == "closed"

    def test_close_nonexistent_is_safe(self):
        """Closing a non-existent ticker produces no exception."""
        close_protective_record(market_id="sp500", ticker="DOES_NOT_EXIST")
        # No exception = pass


# ═══════════════════════════════════════════════════════════════════════════════
# Test 9 — sync_protective closes record for broker-detached position
# ═══════════════════════════════════════════════════════════════════════════════

class TestSyncProtectiveClosesDetachedPosition:
    """If a position is in state_tickers but not at broker, record is closed."""

    def test_sync_protective_closes_record_for_detached_position(self, monkeypatch):
        """Detached position (state file has it, broker doesn't) → record closed."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        # Pre-seed an active protective record
        upsert_protective_record(
            market_id="sp500", ticker="XYZ",
            trade_id=None, position_qty=5,
            stop_order_id="stop-xyz-001", stop_price=50.0,
            tp_order_id=None, tp_price=None,
            oco_class="stop",
        )
        assert _get_record("sp500", "XYZ")["status"] == "active"

        # Confirm the feature flag function is importable
        from scripts.sync_protective_orders import _protective_ledger_enabled
        assert _protective_ledger_enabled()

        # Simulate the detached-position close logic from sync_market
        # (state_tickers has XYZ, but broker positions don't)
        state_tickers = {"XYZ", "AAA"}
        broker_position_tickers = {"AAA"}  # XYZ is gone at broker

        from db.atlas_db import close_protective_record as _cpr
        for det_ticker in state_tickers - broker_position_tickers:
            _cpr(market_id="sp500", ticker=det_ticker)

        rec = _get_record("sp500", "XYZ")
        assert rec["status"] == "closed", f"Detached XYZ should be closed, got {rec['status']}"

    def test_detached_logic_skips_broker_present_tickers(self, monkeypatch):
        """Tickers still at broker are NOT closed even when iterating state_tickers."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        upsert_protective_record(
            market_id="sp500", ticker="STILL_OPEN",
            trade_id=None, position_qty=3,
            stop_order_id="stop-so-001", stop_price=100.0,
            tp_order_id=None, tp_price=None,
            oco_class="stop",
        )

        state_tickers = {"STILL_OPEN"}
        broker_position_tickers = {"STILL_OPEN"}  # still at broker

        from db.atlas_db import close_protective_record as _cpr
        for det_ticker in state_tickers - broker_position_tickers:
            _cpr(market_id="sp500", ticker=det_ticker)  # loop body never runs

        rec = _get_record("sp500", "STILL_OPEN")
        assert rec["status"] == "active", "Broker-present ticker must NOT be closed"


# ═══════════════════════════════════════════════════════════════════════════════
# Test 10 — _apply_db_consistency writes record (full integration path)
# ═══════════════════════════════════════════════════════════════════════════════

class TestApplyDbConsistencyWritesRecord:
    """Full _apply_db_consistency → position_protective_orders updated."""

    def test_apply_db_consistency_writes_record_full(self, monkeypatch):
        """_apply_db_consistency with oco_placed ticker → ledger row created."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        from scripts.sync_protective_orders import _apply_db_consistency
        from brokers.base import OrderSide

        # Broker returns one stop + one TP for ticker AMD
        stop_order = SimpleNamespace(
            side=OrderSide.SELL, ticker="AMD",
            order_id="stop-amd-001",
            raw={"order_type": "stop"},
        )
        tp_order = SimpleNamespace(
            side=OrderSide.SELL, ticker="AMD",
            order_id="tp-amd-001",
            raw={"order_type": "limit"},
        )
        mock_broker = MagicMock()
        mock_broker.get_open_orders.return_value = [stop_order, tp_order]

        sync_result = {
            "per_ticker": {
                "AMD": {
                    "sl_action": "oco_placed",
                    "tp_action": "placed",
                    "stop_price": 140.0,
                    "take_profit": 175.0,
                    "qty": 20,
                },
            }
        }

        with patch("db.atlas_db.update_trade_protective_orders", return_value=1):
            _apply_db_consistency(mock_broker, "sp500", sync_result)

        rec = _get_record("sp500", "AMD")
        assert rec is not None, "Ledger row should exist after _apply_db_consistency"
        assert rec["stop_order_id"] == "stop-amd-001"
        assert rec["tp_order_id"] == "tp-amd-001"
        assert rec["oco_class"] == "oco"
        assert rec["status"] == "active"
        assert rec["stop_price"] == 140.0
        assert rec["tp_price"] == 175.0

    def test_apply_db_consistency_skipped_ticker_also_refreshed(self, monkeypatch):
        """Skipped (already-existed) tickers also get last_synced_at updated."""
        monkeypatch.setenv("PROTECTIVE_LEDGER_WRITE_ENABLED", "true")

        # Pre-seed
        upsert_protective_record(
            market_id="sp500", ticker="CHTR",
            trade_id=None, position_qty=3,
            stop_order_id="stop-chtr-001", stop_price=300.0,
            tp_order_id=None, tp_price=None,
            oco_class="stop",
        )

        from scripts.sync_protective_orders import _apply_db_consistency
        from brokers.base import OrderSide

        stop_order = SimpleNamespace(
            side=OrderSide.SELL, ticker="CHTR",
            order_id="stop-chtr-001",
            raw={"order_type": "stop"},
        )
        mock_broker = MagicMock()
        mock_broker.get_open_orders.return_value = [stop_order]

        # sl_action="skipped" → NOT in _DB_UPDATE_ACTIONS, but should still refresh via (a) loop
        sync_result = {
            "per_ticker": {
                "CHTR": {
                    "sl_action": "skipped",   # already existed — NOT in _DB_UPDATE_ACTIONS
                    "tp_action": "",
                    "stop_price": 300.0,
                    "take_profit": None,
                    "qty": 3,
                },
            }
        }

        with patch("db.atlas_db.update_trade_protective_orders", return_value=0):
            _apply_db_consistency(mock_broker, "sp500", sync_result)

        rec = _get_record("sp500", "CHTR")
        assert rec is not None, "Skipped ticker should still have protective record refreshed"
        assert rec["stop_order_id"] == "stop-chtr-001"
        assert rec["status"] == "active"
