"""Tests for dynamic silent-failure threshold in autoresearch_nightly.

Covers _resolve_min_rows() — the function that replaces the static
MIN_ROWS_PER_UNIVERSE.get() call and scales thresholds based on the number of
enabled strategies in a universe's active config.

Errors resolved by this fix: db.errors table ids 19, 20, 21, 27, 28, 29
  (all "Research sweep silent failure: universe=gold_etfs/commodity_etfs rows=0
   threshold=10/20" false-positive alerts).
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pytest

# Ensure atlas root is on sys.path
ATLAS_ROOT = Path(__file__).resolve().parents[1]
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

import research.autoresearch_nightly as autoresearch_nightly
from research.autoresearch_nightly import (
    DEFAULT_MIN_ROWS,
    MIN_ROWS_PER_STRATEGY,
    MIN_ROWS_PER_UNIVERSE,
    _resolve_min_rows,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, universe: str, strategies: dict) -> Path:
    """Write a minimal active-config JSON for *universe* under *tmp_path*."""
    cfg_dir = tmp_path / "config" / "active"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / f"{universe}.json"
    cfg_path.write_text(json.dumps({"strategies": strategies}))
    return cfg_path


# ─── Tests using PRODUCTION configs (assert live behaviour) ──────────────────


class TestResolveMinRowsLiveConfigs:
    """Tests 1-3: use the real config/active/*.json files to assert live behaviour."""

    def test_resolve_min_rows_gold_etfs_1_strategy_returns_3(self):
        """gold_etfs has 1 enabled strategy (connors_rsi2).

        Expected: min(ceiling=10, dynamic=max(3, 1*3)=3) = 3
        """
        result = _resolve_min_rows("gold_etfs")
        assert result == 3, (
            f"gold_etfs with 1 enabled strategy should give threshold=3, got {result}"
        )

    def test_resolve_min_rows_commodity_etfs_3_strategies_returns_9(self):
        """commodity_etfs has 3 enabled strategies.

        Expected: min(ceiling=20, dynamic=max(3, 3*3)=9) = 9
        """
        result = _resolve_min_rows("commodity_etfs")
        assert result == 9, (
            f"commodity_etfs with 3 enabled strategies should give threshold=9, got {result}"
        )

    def test_resolve_min_rows_sp500_2_strategies_returns_6(self):
        """sp500 has 2 enabled strategies (momentum_breakout + connors_rsi2).

        Expected: min(ceiling=50, dynamic=max(3, 2*3)=6) = 6
        """
        result = _resolve_min_rows("sp500")
        assert result == 6, (
            f"sp500 with 2 enabled strategies should give threshold=6, got {result}"
        )


# ─── Tests using tmp_path (isolated from production configs) ─────────────────


class TestResolveMinRowsIsolated:
    """Tests 4-6: monkeypatch ATLAS_ROOT to a tmp dir for full isolation."""

    def test_resolve_min_rows_unknown_universe_returns_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Universe not in MIN_ROWS_PER_UNIVERSE + no config file -> DEFAULT_MIN_ROWS."""
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        # No config file written for "phantom_universe"
        result = _resolve_min_rows("phantom_universe")
        assert result == DEFAULT_MIN_ROWS, (
            f"Unknown universe with no config should return DEFAULT_MIN_ROWS={DEFAULT_MIN_ROWS}, "
            f"got {result}"
        )

    def test_resolve_min_rows_zero_enabled_returns_ceiling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Universe with 0 enabled strategies -> static ceiling (not 0 or 3).

        We still want to alert if rows ARE somehow produced for a universe where
        no strategies are enabled -- so the ceiling is preserved.
        """
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        _write_config(
            tmp_path,
            "gold_etfs",
            {
                "connors_rsi2": {"enabled": False},
                "momentum_breakout": {"enabled": False},
            },
        )
        result = _resolve_min_rows("gold_etfs")
        expected_ceiling = MIN_ROWS_PER_UNIVERSE["gold_etfs"]
        assert result == expected_ceiling, (
            f"0 enabled strategies should fall back to static ceiling "
            f"({expected_ceiling}), got {result}"
        )

    def test_resolve_min_rows_corrupt_config_falls_back(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Corrupt JSON in config file -> WARNING log + fallback to static ceiling."""
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        cfg_dir = tmp_path / "config" / "active"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "gold_etfs.json").write_text("THIS IS NOT VALID JSON {{{{")

        with caplog.at_level(logging.WARNING, logger="research.autoresearch_nightly"):
            result = _resolve_min_rows("gold_etfs")

        expected_ceiling = MIN_ROWS_PER_UNIVERSE["gold_etfs"]
        assert result == expected_ceiling, (
            f"Corrupt config should fall back to static ceiling ({expected_ceiling}), got {result}"
        )
        assert any(
            "_resolve_min_rows" in record.message and "falling back" in record.message
            for record in caplog.records
        ), f"Expected a WARNING with '_resolve_min_rows' and 'falling back', got: {caplog.records}"


# --- Module-level constant sanity --------------------------------------------


class TestConstants:
    """Verify the new constants are correctly defined."""

    def test_min_rows_per_strategy_is_3(self):
        assert MIN_ROWS_PER_STRATEGY == 3

    def test_default_min_rows_is_10(self):
        assert DEFAULT_MIN_ROWS == 10

    def test_dynamic_is_lower_than_static_ceiling_for_gold_etfs(self):
        """Confirm that the dynamic calculation beats the static ceiling for gold_etfs.

        This is the core business invariant -- the fix only helps if dynamic < ceiling.
        """
        ceiling = MIN_ROWS_PER_UNIVERSE["gold_etfs"]  # 10
        dynamic = max(3, 1 * MIN_ROWS_PER_STRATEGY)   # max(3, 3) = 3
        assert min(ceiling, dynamic) < ceiling, (
            "Dynamic threshold must be strictly lower than ceiling for gold_etfs (1 strategy)"
        )
