"""Regression tests for Fix 1: broker_data_valid=False blocks state-file writes.

Root cause: reconcile_exit_fills() constructed LivePortfolio fresh inside the
per-order loop, with broker_data_valid=False on init → save_state() silently
skipped every time → live_*.json files 2-3 days stale.

Fix: hoist LivePortfolio construction BEFORE the loop, inject the connected
broker, call _refresh_from_broker() so broker_data_valid is set correctly.

Reference: brokers/live_executor.py::reconcile_exit_fills
See also: brokers/live_portfolio.py::save_state / _refresh_from_broker
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_mock_order(
    order_id: str = "ord-001",
    symbol: str = "AAPL",
    side: str = "sell",
    status: str = "filled",
    filled_avg_price="150.00",
    filled_qty: str = "10",
    client_order_id: str = "atlas_exit_AAPL_001",
    filled_at: str = "2026-05-04T14:30:00Z",
):
    """Build a minimal Alpaca order mock for test use."""
    order = MagicMock()
    order.id = order_id
    order.symbol = symbol
    order.side = MagicMock()
    order.side.value = side
    order.status = MagicMock()
    order.status.value = status
    order.filled_avg_price = filled_avg_price
    order.filled_qty = filled_qty
    order.qty = filled_qty
    order.client_order_id = client_order_id
    order.filled_at = filled_at
    return order


def _minimal_config(market_id: str = "sp500") -> dict:
    return {
        "market_id": market_id,
        "risk": {
            "starting_equity": 5000,
            "max_risk_per_trade_pct": 0.005,
            "max_open_positions": 10,
            "max_sector_concentration": 2,
            "max_daily_drawdown_pct": 0.02,
            "leverage": 1.0,
        },
        "fees": {},
        # Disable SQLite dual-write to keep tests simple
        "dual_write_market_state": False,
    }


# ---------------------------------------------------------------------------
# Test 1: fresh LivePortfolio → broker_data_valid=False → save_state() no-op
# ---------------------------------------------------------------------------

class TestFreshPortfolioSaveStateNoOp:
    """A freshly-constructed LivePortfolio must have broker_data_valid=False
    and save_state() must be a no-op (no file written)."""

    def test_fresh_portfolio_has_broker_data_valid_false(self, tmp_path):
        """LivePortfolio.__init__ must initialise broker_data_valid=False."""
        from brokers.live_portfolio import LivePortfolio
        import brokers.live_portfolio as _lp

        with patch.object(LivePortfolio, "_load_local_state", return_value=None):
            lp = LivePortfolio(_minimal_config(), market_id="sp500")

        assert lp.broker_data_valid is False, (
            "Expected broker_data_valid=False on fresh init, got True — "
            "the save_state() guard will be ineffective."
        )

    def test_save_state_no_op_when_broker_data_invalid(self, tmp_path, caplog):
        """save_state() must emit a warning and NOT create the file when
        broker_data_valid=False."""
        from brokers.live_portfolio import LivePortfolio
        import brokers.live_portfolio as _lp

        # The autouse fixture has redirected _STATE_DIR; confirm it's isolated
        state_file = _lp._STATE_DIR / "live_sp500.json"
        assert not state_file.exists(), "State file should not exist at test start"

        with patch.object(LivePortfolio, "_load_local_state", return_value=None):
            lp = LivePortfolio(_minimal_config(), market_id="sp500")

        # broker_data_valid=False by default — save_state() must be a no-op
        with caplog.at_level(logging.WARNING, logger="atlas.live_portfolio"):
            lp.save_state()

        assert not state_file.exists(), (
            "save_state() must NOT write the file when broker_data_valid=False"
        )
        # Confirm the warning fires (so operators know what's happening)
        warning_msgs = [r.message for r in caplog.records if "broker_data_valid" in r.message]
        assert warning_msgs, (
            "Expected at least one 'broker_data_valid is False' warning — none found"
        )


# ---------------------------------------------------------------------------
# Test 2: after injecting broker + _refresh_from_broker → write succeeds
# ---------------------------------------------------------------------------

class TestRefreshFromBrokerEnablesWrite:
    """After _refresh_from_broker() with a healthy broker, broker_data_valid
    must be True and save_state() must write the file."""

    def _make_valid_mock_broker(self):
        """Broker that returns valid account data (non-zero equity/cash)."""
        from brokers.base import AccountInfo
        broker = MagicMock()
        broker.get_account_info.return_value = AccountInfo(
            equity=5000.0, cash=1000.0, market_value=4000.0
        )
        broker.get_positions.return_value = []
        broker.get_open_orders.return_value = []
        return broker

    def test_refresh_sets_broker_data_valid_true(self, tmp_path):
        """Injecting a healthy broker + calling _refresh_from_broker()
        must flip broker_data_valid to True."""
        from brokers.live_portfolio import LivePortfolio
        import brokers.live_portfolio as _lp

        with patch.object(LivePortfolio, "_load_local_state", return_value=None):
            lp = LivePortfolio(_minimal_config(), market_id="sp500")

        assert lp.broker_data_valid is False, "Pre-condition: should start as False"

        mock_broker = self._make_valid_mock_broker()
        lp._broker = mock_broker
        lp._connected = True

        lp._refresh_from_broker()

        assert lp.broker_data_valid is True, (
            "_refresh_from_broker() with valid account data must set "
            "broker_data_valid=True"
        )

    def test_save_state_writes_file_after_refresh(self, tmp_path):
        """After broker_data_valid=True, save_state() must create the file."""
        from brokers.live_portfolio import LivePortfolio
        import brokers.live_portfolio as _lp

        state_file = _lp._STATE_DIR / "live_sp500.json"
        assert not state_file.exists(), "Pre-condition: file should not exist"

        with patch.object(LivePortfolio, "_load_local_state", return_value=None):
            lp = LivePortfolio(_minimal_config(), market_id="sp500")

        mock_broker = self._make_valid_mock_broker()
        lp._broker = mock_broker
        lp._connected = True
        lp._refresh_from_broker()

        assert lp.broker_data_valid is True, "Pre-condition after refresh"

        with patch.object(lp, "_trigger_dashboard_refresh"):
            lp.save_state()

        assert state_file.exists(), (
            "save_state() must write the state file when broker_data_valid=True"
        )
        # Verify it's valid JSON with expected keys
        state = json.loads(state_file.read_text())
        assert "positions" in state
        assert "closed_trades" in state
        assert state["market_id"] == "sp500"


# ---------------------------------------------------------------------------
# Test 3: regression-of-record — reconcile_exit_fills writes state JSON
# ---------------------------------------------------------------------------

class TestReconcileExitFillsWritesState:
    """Regression test: reconcile_exit_fills must write the LivePortfolio
    state file when the broker is healthy.

    Before the fix: LivePortfolio constructed fresh inside the loop with
    broker_data_valid=False → save_state() silently skipped → state stale.
    After the fix: broker injected before the loop → broker_data_valid=True
    → save_state() writes → state file is updated.
    """

    def _make_executor_with_broker(self):
        """Build a LiveExecutor via __new__ (mirrors sync_protective_orders.py
        line ~940 pattern) with a mock broker that returns valid data."""
        from brokers.live_executor import LiveExecutor
        from brokers.base import AccountInfo

        config = _minimal_config(market_id="sp500")

        executor = object.__new__(LiveExecutor)
        executor.config = config
        executor._connected = True

        # Mock broker: valid account + one filled SELL order
        mock_broker = MagicMock()
        mock_broker.get_account_info.return_value = AccountInfo(
            equity=5000.0, cash=1000.0, market_value=4000.0
        )
        mock_broker.get_positions.return_value = []
        mock_broker.get_open_orders.return_value = []

        filled_order = _make_mock_order(
            order_id="ord-regression-001",
            symbol="AAPL",
            side="sell",
            status="filled",
            filled_avg_price="150.00",
            filled_qty="10",
            client_order_id="atlas_exit_AAPL_regression",
        )
        mock_broker._broker_call.return_value = [filled_order]
        executor._broker = mock_broker

        return executor

    def test_state_file_written_after_reconcile(self, tmp_path, caplog):
        """The state JSON file must be NEWER after reconcile_exit_fills()
        completes successfully with a healthy broker.

        This is the regression-of-record: before the fix, mtime never changed
        because save_state() was always silently skipped.
        """
        import brokers.live_portfolio as _lp

        state_file = _lp._STATE_DIR / "live_sp500.json"
        # File should not exist before the call
        pre_mtime = state_file.stat().st_mtime if state_file.exists() else 0.0

        executor = self._make_executor_with_broker()

        with patch("journal.logger.TradeLedger") as mock_ledger_cls, \
             patch("brokers.live_executor._get_regime_model") as mock_regime, \
             patch("brokers.live_portfolio.LivePortfolio._trigger_dashboard_refresh"), \
             patch("db.atlas_db.record_trade_exit", return_value=None), \
             patch("brokers.live_executor._protective_ledger_enabled",
                   return_value=False):

            mock_ledger = MagicMock()
            mock_ledger.trades = []   # no prior exits → order not deduped
            mock_ledger_cls.return_value = mock_ledger
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"

            with caplog.at_level(logging.WARNING, logger="atlas.live_portfolio"):
                result = executor.reconcile_exit_fills()

        # 1. One reconciled exit recorded
        assert len(result) == 1, (
            f"Expected 1 reconciled exit, got {len(result)}: {result}"
        )
        assert result[0]["ticker"] == "AAPL"

        # 2. State file must now exist and be newer
        assert state_file.exists(), (
            f"State file was not written to {state_file} — "
            "reconcile_exit_fills() did not call save_state()"
        )
        post_mtime = state_file.stat().st_mtime
        assert post_mtime > pre_mtime, (
            f"State file mtime did not advance: before={pre_mtime:.3f}, "
            f"after={post_mtime:.3f} — save_state() was silently skipped"
        )

        # 3. No "broker_data_valid is False" skip-warnings must appear
        skip_warnings = [
            r for r in caplog.records
            if "broker_data_valid is False" in r.message
        ]
        assert not skip_warnings, (
            "Unexpected save_state() skip warnings — broker_data_valid should "
            f"have been True after _refresh_from_broker(): {skip_warnings}"
        )

        # 4. State file contains the closed trade
        state = json.loads(state_file.read_text())
        closed = state.get("closed_trades", [])
        aapl_closes = [t for t in closed if t.get("ticker") == "AAPL"]
        assert aapl_closes, (
            f"Expected AAPL in closed_trades of state file, got: {closed}"
        )
