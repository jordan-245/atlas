"""Regression test for Bug B — ResearchSession crashed on non-sp500 universes.

Validated-strategies audit 2026-05-01 found research/loop.py:556 called
_find_latest_snapshot() unconditionally; for non-sp500 universes with no
snapshot file, this raised RuntimeError and aborted the session.

Fix: gracefully fall back to live-cache via universe.builder.build_from_definition()
for non-sp500 universes that have no snapshot.
"""
import pytest
from unittest.mock import patch, MagicMock


def test_snapshot_fallback_for_sector_etfs():
    """ResearchSession should instantiate cleanly for sector_etfs even with no snapshot."""
    from research.loop import ResearchSession
    # sector_etfs has NO snapshot in data/snapshots/ — must fall back
    s = ResearchSession("mean_reversion", market="sector_etfs", snapshot_id=None)
    assert s.market == "sector_etfs"
    assert s.snapshot_id is None  # confirms fallback path was taken
    assert s._data is not None
    assert len(s._data) > 0  # got tickers from live cache


def test_snapshot_fallback_for_defensive_etfs():
    from research.loop import ResearchSession
    s = ResearchSession("mean_reversion", market="defensive_etfs", snapshot_id=None)
    assert s.snapshot_id is None
    assert s._data is not None


def test_snapshot_fallback_for_gold_etfs():
    from research.loop import ResearchSession
    s = ResearchSession("mean_reversion", market="gold_etfs", snapshot_id=None)
    assert s.snapshot_id is None
    assert s._data is not None


def test_snapshot_fallback_for_treasury_etfs():
    from research.loop import ResearchSession
    s = ResearchSession("mean_reversion", market="treasury_etfs", snapshot_id=None)
    assert s.snapshot_id is None
    assert s._data is not None


def test_sp500_still_requires_snapshot():
    """sp500 must NOT fall back — snapshot is required for reproducibility."""
    from research.loop import ResearchSession
    # sp500 has snapshots — instantiation should succeed and resolve snapshot_id
    s = ResearchSession("mean_reversion", market="sp500", snapshot_id=None)
    assert s.snapshot_id is not None
    assert "sp500" in s.snapshot_id.lower()


def test_explicit_snapshot_id_honored():
    """Passing snapshot_id explicitly should override the fallback path."""
    from research.loop import ResearchSession
    s = ResearchSession(
        "mean_reversion",
        market="commodity_etfs",
        snapshot_id="commodity_etfs_20260417_7yr",
    )
    assert s.snapshot_id == "commodity_etfs_20260417_7yr"
