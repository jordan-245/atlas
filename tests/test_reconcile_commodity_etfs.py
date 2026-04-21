"""Regression tests for reconcile_positions.py commodity_etfs market support."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT = Path(__file__).resolve().parent.parent


def test_market_tuple_includes_commodity_etfs():
    """_MARKETS must include commodity_etfs (extension guard)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reconcile_positions",
        PROJECT / "scripts" / "reconcile_positions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "commodity_etfs" in mod._MARKETS
    assert mod._DEFAULT_BROKER.get("commodity_etfs") == "alpaca"


def test_cli_accepts_commodity_etfs_market():
    """argparse --market commodity_etfs must not error on choices validation."""
    # --help parses args; invoking with an invalid --market value exits 2
    result = subprocess.run(
        ["python3", str(PROJECT / "scripts" / "reconcile_positions.py"),
         "--market", "commodity_etfs", "--dry-run", "--no-telegram", "--quiet"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=str(PROJECT),
    )
    # Exit 0 (no discrepancies) or 1 (discrepancies found) both mean args were accepted.
    # Exit 2 would mean argparse rejected --market commodity_etfs.
    assert result.returncode in (0, 1), (
        f"reconcile_positions rejected --market commodity_etfs "
        f"(exit={result.returncode})\nstderr={result.stderr}\nstdout={result.stdout}"
    )


def test_load_internal_state_commodity_etfs():
    """load_internal_state reads brokers/state/live_commodity_etfs.json."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reconcile_positions",
        PROJECT / "scripts" / "reconcile_positions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    state = mod.load_internal_state("commodity_etfs")
    # Must not crash; must return a dict with 'positions' key
    assert isinstance(state, dict)
    assert "positions" in state


def test_load_config_commodity_etfs():
    """load_config reads config/active/commodity_etfs.json."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reconcile_positions",
        PROJECT / "scripts" / "reconcile_positions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    cfg = mod.load_config("commodity_etfs")
    assert isinstance(cfg, dict)


def test_reconcile_positions_commodity_etfs_broker_disconnected():
    """reconcile_positions gracefully handles broker errors for commodity_etfs."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "reconcile_positions",
        PROJECT / "scripts" / "reconcile_positions.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # Mock get_live_broker to return None (simulating disconnection)
    with patch("brokers.registry.get_live_broker", return_value=None):
        result = mod.reconcile_positions("commodity_etfs", fix=False, dry_run=True)
    assert result["market_id"] == "commodity_etfs"
    assert result.get("error")  # should have an error string set
