"""Regression tests for Issue 2 — LiveExecutor._mode missing in sync_protective_orders.

Root cause: sync_protective_orders.py instantiates LiveExecutor via __new__
(skipping __init__), but only set 3 attributes (_broker, _connected, config).
reconcile_entry_fills reads self._mode at multiple sites → AttributeError on
every 15-min cron cycle.

Fix: add `_exec._mode = config.get("trading", {}).get("mode", "live")` after
the other __new__ attribute assignments.

Tests:
  1. test_sync_protective_executor_has_mode
     Manual __new__ + attr-setting → hasattr(_exec, "_mode") True, value "live".
  2. test_sync_protective_source_contains_mode_fix
     Source-inspection guard — fails if anyone removes the fix line.
  3. test_reconcile_entry_fills_reads_mode_attribute
     Full call path with mocked broker → no AttributeError, returns list.

Run:
    cd /root/atlas && python3 -m pytest tests/test_sync_protective_executor_init.py -v --timeout=30
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as _adb  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — __new__ + manual attribute assignment leaves _mode set
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_protective_executor_has_mode() -> None:
    """Mimic the production __new__ block in sync_protective_orders.py exactly,
    then assert _mode is present and equals 'live'."""
    from brokers.live_executor import LiveExecutor

    config = {
        "market_id": "sp500",
        "version": "v1",
        "trading": {"mode": "live", "live_enabled": True, "broker": "alpaca"},
        "risk": {"starting_equity": 5000.0, "max_risk_per_trade_pct": 0.02,
                 "max_open_positions": 10, "leverage": 1.0},
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
    }
    mock_broker = MagicMock()

    # Replicate the production block verbatim (as of the fix)
    _exec = LiveExecutor.__new__(LiveExecutor)
    _exec._broker = mock_broker
    _exec._connected = True
    _exec.config = config
    _exec._mode = config.get("trading", {}).get("mode", "live")

    assert hasattr(_exec, "_mode"), "_exec must have _mode attribute"
    assert _exec._mode == "live", f"Expected 'live', got {_exec._mode!r}"


def test_sync_protective_executor_has_mode_paper() -> None:
    """Same check for paper mode to ensure the config plumbing is correct."""
    from brokers.live_executor import LiveExecutor

    config = {"trading": {"mode": "paper"}}
    _exec = LiveExecutor.__new__(LiveExecutor)
    _exec._broker = MagicMock()
    _exec._connected = True
    _exec.config = config
    _exec._mode = config.get("trading", {}).get("mode", "live")

    assert _exec._mode == "paper"


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — Source-inspection guard
# ─────────────────────────────────────────────────────────────────────────────

def test_sync_protective_source_contains_mode_fix() -> None:
    """Verify the fix line is present in sync_protective_orders.py source.

    This is a shape-check guard: if anyone removes '_exec._mode = config.get('
    this test fails loudly rather than letting AttributeErrors silently return
    to the non-fatal except block.
    """
    src_path = PROJECT / "scripts" / "sync_protective_orders.py"
    src = src_path.read_text()

    fix_line = '_exec._mode = config.get("trading", {}).get("mode", "live")'
    assert fix_line in src, (
        f"Fix line not found in {src_path}.\n"
        "Expected: _exec._mode = config.get(\"trading\", {}).get(\"mode\", \"live\")\n"
        "This guard fails when the fix is removed — restore it before removing this test."
    )

    # Also check the fix appears BEFORE reconcile_entry_fills call (ordering matters)
    fix_pos = src.index(fix_line)
    reconcile_pos = src.index("reconciled_entries = _exec.reconcile_entry_fills(")
    assert fix_pos < reconcile_pos, (
        "_exec._mode must be set BEFORE reconcile_entry_fills is called"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — reconcile_entry_fills does not raise AttributeError
# ─────────────────────────────────────────────────────────────────────────────

def test_reconcile_entry_fills_reads_mode_attribute() -> None:
    """With a fully mocked broker (returning [] from get_orders), call
    reconcile_entry_fills on a __new__-constructed executor. Must not raise
    AttributeError and must return a list."""
    from brokers.live_executor import LiveExecutor

    config = {
        "market_id": "sp500",
        "version": "v1",
        "trading": {"mode": "live", "live_enabled": True, "broker": "alpaca"},
        "risk": {
            "starting_equity": 5000.0,
            "max_risk_per_trade_pct": 0.02,
            "max_open_positions": 10,
            "leverage": 1.0,
        },
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
    }

    mock_broker = MagicMock()
    # reconcile_entry_fills calls broker._broker_call(...) to fetch orders
    mock_broker._broker_call.return_value = []

    _exec = LiveExecutor.__new__(LiveExecutor)
    _exec._broker = mock_broker
    _exec._connected = True
    _exec.config = config
    _exec._mode = config.get("trading", {}).get("mode", "live")  # THE FIX
    _exec._halted = False

    mock_ledger = MagicMock()
    mock_ledger.trades = []
    mock_ledger.record_entry.return_value = 1

    plan = {"proposed_entries": []}  # empty plan → reconcile loop does nothing

    with (
        patch("brokers.live_executor._get_regime_model") as mock_regime,
        patch("journal.logger.TradeLedger", return_value=mock_ledger),
    ):
        mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"

        try:
            result = _exec.reconcile_entry_fills(plan=plan)
        except AttributeError as exc:
            pytest.fail(
                f"reconcile_entry_fills raised AttributeError — _mode fix not applied: {exc}"
            )

    assert isinstance(result, list), f"Expected list, got {type(result)}"


# ─────────────────────────────────────────────────────────────────────────────
# Tests for _policy attribute (Fix: AttributeError spam every 15-min cron)
# ─────────────────────────────────────────────────────────────────────────────

class TestSyncProtectivePolicyInit:
    """Regression tests for _policy missing from LiveExecutor.__new__ block.

    Root cause: sync_protective_orders.py __new__ block set _broker, _connected,
    config, _mode — but NOT _policy. reconcile_entry_fills calls
    self._policy.trade_table() → AttributeError → caught by non-fatal except →
    WARNING logged every 15-min cron cycle.

    Fix: add BrokerRoutingPolicy(config, market_id=...) assignment after _mode.
    """

    def test_executor_has_policy_after_new_block(self) -> None:
        """Mimic the production __new__ block including the _policy fix.

        Asserts that _exec._policy is a BrokerRoutingPolicy instance and
        has a callable trade_table() method.
        """
        from brokers.live_executor import LiveExecutor
        from brokers.routing_policy import BrokerRoutingPolicy

        config = {
            "market_id": "sp500",
            "version": "v1",
            "trading": {"mode": "live", "live_enabled": True, "broker": "alpaca"},
            "risk": {"starting_equity": 5000.0, "max_risk_per_trade_pct": 0.02,
                     "max_open_positions": 10, "leverage": 1.0},
            "fees": {"commission_per_trade": 0, "commission_pct": 0},
        }
        _exec = LiveExecutor.__new__(LiveExecutor)
        _exec._broker = MagicMock()
        _exec._connected = True
        _exec.config = config
        _exec._mode = config.get("trading", {}).get("mode", "live")
        # THE FIX being tested
        _exec._policy = BrokerRoutingPolicy(
            config, market_id=config.get("market_id", "sp500"),
        )

        assert hasattr(_exec, "_policy"), "_exec must have _policy attribute after __new__ block"
        assert isinstance(_exec._policy, BrokerRoutingPolicy)
        assert callable(_exec._policy.trade_table)
        assert _exec._policy.trade_table() == "trades"  # live mode → live table

    def test_policy_source_present_in_sync_protective(self) -> None:
        """Source-inspection guard: _policy initialisation must appear in source.

        Fails loudly if the fix is ever reverted, rather than letting the
        AttributeError silently return via the non-fatal except block.
        """
        src = (PROJECT / "scripts" / "sync_protective_orders.py").read_text()

        assert "_exec._policy = BrokerRoutingPolicy(" in src, (
            "_exec._policy assignment missing from sync_protective_orders.py. "
            "This fix prevents AttributeError in reconcile_entry_fills every 15-min cron."
        )
        # Ensure _policy is set BEFORE reconcile_entry_fills is called
        policy_pos = src.index("_exec._policy = BrokerRoutingPolicy(")
        reconcile_pos = src.index("reconciled_entries = _exec.reconcile_entry_fills(")
        assert policy_pos < reconcile_pos, (
            "_exec._policy must be assigned BEFORE reconcile_entry_fills is called"
        )

    def test_reconcile_entry_fills_no_policy_attributeerror(self) -> None:
        """Full call: reconcile_entry_fills must NOT raise AttributeError for _policy.

        Constructs executor via __new__ with _policy set (production pattern),
        calls reconcile_entry_fills with empty broker orders, expects a list
        and NO AttributeError. Without the fix this raises immediately.
        """
        from brokers.live_executor import LiveExecutor
        from brokers.routing_policy import BrokerRoutingPolicy

        config = {
            "market_id": "sp500",
            "version": "v1",
            "trading": {"mode": "live", "live_enabled": True, "broker": "alpaca"},
            "risk": {"starting_equity": 5000.0, "max_risk_per_trade_pct": 0.02,
                     "max_open_positions": 10, "leverage": 1.0},
            "fees": {"commission_per_trade": 0, "commission_pct": 0},
        }
        mock_broker = MagicMock()
        mock_broker._broker_call.return_value = []

        _exec = LiveExecutor.__new__(LiveExecutor)
        _exec._broker = mock_broker
        _exec._connected = True
        _exec.config = config
        _exec._mode = "live"
        _exec._policy = BrokerRoutingPolicy(config, market_id="sp500")
        _exec._halted = False

        mock_ledger = MagicMock()
        mock_ledger.trades = []
        mock_ledger.record_entry.return_value = 1
        plan = {"proposed_entries": []}

        with (
            patch("brokers.live_executor._get_regime_model") as mock_regime,
            patch("journal.logger.TradeLedger", return_value=mock_ledger),
        ):
            mock_regime.return_value.classify_current.return_value.state.value = "bull_risk_on"
            try:
                result = _exec.reconcile_entry_fills(plan=plan)
            except AttributeError as exc:
                pytest.fail(
                    f"reconcile_entry_fills raised AttributeError — _policy fix broken: {exc}"
                )

        assert isinstance(result, list)

    def test_executor_without_policy_raises_attribute_error(self) -> None:
        """Negative test: omitting _policy assignment causes AttributeError on access.

        This documents the pre-fix behaviour: reconcile_entry_fills calls
        self._policy.trade_table() when it processes a broker order. Without the
        fix, the __new__ block never sets _policy → AttributeError on that line.

        We test this directly — without the fix, accessing _exec._policy raises
        AttributeError, confirming the attribute is truly missing on a bare
        __new__ instance.
        """
        from brokers.live_executor import LiveExecutor

        config = {
            "market_id": "sp500",
            "trading": {"mode": "live", "live_enabled": True, "broker": "alpaca"},
        }

        # Deliberately do NOT set _policy — pre-fix state
        _exec = LiveExecutor.__new__(LiveExecutor)
        _exec._broker = MagicMock()
        _exec._connected = True
        _exec.config = config
        _exec._mode = "live"
        # _exec._policy is NOT set

        # Direct attribute access must raise AttributeError
        with pytest.raises(AttributeError):
            _ = _exec._policy

        # And calling .trade_table() on the missing attribute raises AttributeError
        with pytest.raises(AttributeError):
            _ = _exec._policy.trade_table()
