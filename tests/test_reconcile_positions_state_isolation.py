"""Regression tests for scripts.reconcile_positions state-file isolation.

Discovered 2026-04-30: test_reconcile_positions_fix_idempotent called
save_internal_state() with broker=[] + fix=True, writing positions=[] to
/root/atlas/brokers/state/live_sp500.json (wiped CAT/FCX/MU).

Root cause: scripts/reconcile_positions.py used a hardcoded
PROJECT / "brokers" / "state" path with no monkeypatch hook.

Fix (Task #295):
  - Added _STATE_DIR module constant + _state_path() helper.
  - Added session+function autouse fixtures in conftest.py mirroring
    brokers.live_portfolio._STATE_DIR isolation (commit 4ea328fa).
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROD_SP500_PATH = "/root/atlas/brokers/state/live_sp500.json"


# ---------------------------------------------------------------------------
# Test 1 — save_internal_state() writes to isolated dir, NOT prod
# ---------------------------------------------------------------------------

def test_save_internal_state_writes_to_isolated_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """After the fixture, save_internal_state writes to tmp, not prod."""
    import scripts.reconcile_positions as _rp  # noqa: PLC0415

    # The conftest autouse fixture already redirected _STATE_DIR to a per-test
    # tmp dir.  Confirm save_internal_state() writes there, not to prod.
    state = {"positions": [{"ticker": "FAKE", "shares": 1, "entry_price": 100.0}]}
    _rp.save_internal_state("sp500", state)

    # Written to isolated dir
    written_path = _rp._STATE_DIR / "live_sp500.json"
    assert written_path.exists(), f"Expected file at {written_path}"

    loaded = json.loads(written_path.read_text())
    assert any(p["ticker"] == "FAKE" for p in loaded["positions"])

    # NOT written to prod
    prod_path = Path(PROD_SP500_PATH)
    if prod_path.exists():
        prod_content = json.loads(prod_path.read_text())
        prod_tickers = [p["ticker"] for p in prod_content.get("positions", [])]
        assert "FAKE" not in prod_tickers, (
            "save_internal_state() leaked FAKE ticker into prod live_sp500.json — "
            "_STATE_DIR isolation is broken!"
        )


# ---------------------------------------------------------------------------
# Test 2 — reconcile_positions --fix does NOT pollute prod
# ---------------------------------------------------------------------------

def test_reconcile_positions_fix_does_not_pollute_prod(tmp_path: Path) -> None:
    """Simulate the exact bug pattern: broker=[], fix=True, market=sp500.

    The autouse fixture in conftest.py must prevent any write to prod.
    """
    prod_path = PROD_SP500_PATH
    pre_mtime = os.path.getmtime(prod_path) if os.path.exists(prod_path) else None
    pre_size = os.path.getsize(prod_path) if os.path.exists(prod_path) else None

    import scripts.reconcile_positions as _rp  # noqa: PLC0415

    # Mock a broker that returns empty positions (the exact bug trigger)
    mock_broker = MagicMock()
    mock_broker.connect.return_value = True
    mock_broker.get_positions.return_value = []  # <-- the dangerous input
    mock_broker.disconnect.return_value = None

    with patch("brokers.registry.get_live_broker", return_value=mock_broker):
        result = _rp.reconcile_positions("sp500", fix=True, dry_run=False)

    # The function should have run without error
    assert result["market_id"] == "sp500"

    # Prod file must be UNTOUCHED
    if pre_mtime is not None and os.path.exists(prod_path):
        cur_mtime = os.path.getmtime(prod_path)
        cur_size = os.path.getsize(prod_path)
        assert cur_mtime == pre_mtime, (
            f"REGRESSION: live_sp500.json mtime changed after reconcile --fix with "
            f"broker=[] (mtime {pre_mtime:.3f} → {cur_mtime:.3f}). "
            "_STATE_DIR isolation is broken!"
        )
        assert cur_size == pre_size, (
            f"REGRESSION: live_sp500.json size changed after reconcile --fix with "
            f"broker=[] (size {pre_size} → {cur_size} bytes). "
            "_STATE_DIR isolation is broken!"
        )


# ---------------------------------------------------------------------------
# Test 3 — module has _STATE_DIR constant of type Path
# ---------------------------------------------------------------------------

def test_module_has_state_dir_constant() -> None:
    """scripts.reconcile_positions must expose _STATE_DIR as a Path."""
    import scripts.reconcile_positions as _rp  # noqa: PLC0415

    assert hasattr(_rp, "_STATE_DIR"), (
        "scripts.reconcile_positions missing _STATE_DIR constant — "
        "the test isolation fixture cannot patch it."
    )
    assert isinstance(_rp._STATE_DIR, Path), (
        f"_STATE_DIR must be a pathlib.Path, got {type(_rp._STATE_DIR)}"
    )
    # Also confirm _state_path() exists and returns a Path
    assert hasattr(_rp, "_state_path"), "scripts.reconcile_positions missing _state_path() helper"
    result = _rp._state_path("sp500")
    assert isinstance(result, Path)
    assert result.name == "live_sp500.json"
