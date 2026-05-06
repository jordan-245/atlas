"""Tests for monitor.lifecycle.StrategyLifecycleManager.force_to_watch / force_to_state.

Run:
    python3 -m pytest tests/test_lifecycle_force_to_state.py -v --timeout=30
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

from monitor.lifecycle import StrategyLifecycleManager, LifecycleState


@pytest.fixture
def manager(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> StrategyLifecycleManager:
    """Isolated manager with lifecycle state file redirected to tmp_path."""
    monkeypatch.setattr(StrategyLifecycleManager, "LIFECYCLE_FILE", tmp_path / "lifecycle.json")
    cfg = {
        "strategies": {"momentum": {"enabled": True}},
        "allocation": {"pools": {"momentum": {"max_positions": 3}}},
    }
    return StrategyLifecycleManager(cfg, market_id="sp500")


def test_force_to_watch_active_transitions(manager: StrategyLifecycleManager) -> None:
    """momentum starts ACTIVE (auto-init in _load_state) → force_to_watch → WATCH."""
    assert manager.get_state("momentum") == LifecycleState.ACTIVE
    assert manager.force_to_watch("momentum", "test reason") is True
    assert manager.get_state("momentum") == LifecycleState.WATCH
    assert manager.records["momentum"].pool_cap_override == 2  # max(1, 3-1)


def test_force_to_watch_already_watch_noop(manager: StrategyLifecycleManager) -> None:
    """Second call when already in WATCH returns False (no-op)."""
    manager.force_to_watch("momentum", "first")
    assert manager.force_to_watch("momentum", "second") is False
    assert manager.get_state("momentum") == LifecycleState.WATCH


def test_force_to_watch_unknown_strategy_initializes_then_demotes(
    manager: StrategyLifecycleManager,
) -> None:
    """Unknown strategy not in config: auto-initialized as ACTIVE then → WATCH."""
    assert manager.force_to_watch("never_seen", "test") is True
    assert manager.get_state("never_seen") == LifecycleState.WATCH


def test_force_to_state_respects_allowed_transitions(manager: StrategyLifecycleManager) -> None:
    """ACTIVE → SUSPENDED allowed; SUSPENDED → ACTIVE refused (must go via PROBATION)."""
    # ACTIVE → SUSPENDED is allowed
    assert manager.force_to_state("momentum", LifecycleState.SUSPENDED, "kill") is True
    # SUSPENDED → ACTIVE is NOT allowed (must go via PROBATION)
    assert manager.force_to_state("momentum", LifecycleState.ACTIVE, "revive") is False
    assert manager.get_state("momentum") == LifecycleState.SUSPENDED


def test_force_to_state_history_logged(manager: StrategyLifecycleManager) -> None:
    """Transition appends a history entry with correct from/to/reason fields."""
    manager.force_to_watch("momentum", "divergence breach 5d")
    rec = manager.records["momentum"]
    assert len(rec.history) == 1
    h = rec.history[0]
    assert h["from"] == "ACTIVE"
    assert h["to"] == "WATCH"
    assert "divergence breach 5d" in h["reason"]


def test_force_to_state_persists_to_disk(
    manager: StrategyLifecycleManager, tmp_path: Path
) -> None:
    """After force_to_watch, a fresh manager loaded from same file sees WATCH state."""
    manager.force_to_watch("momentum", "test")
    cfg = {
        "strategies": {"momentum": {"enabled": True}},
        "allocation": {"pools": {"momentum": {"max_positions": 3}}},
    }
    fresh = StrategyLifecycleManager(cfg, market_id="sp500")
    assert fresh.get_state("momentum") == LifecycleState.WATCH
