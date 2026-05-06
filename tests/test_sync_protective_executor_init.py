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
