"""Tests for dynamic silent-failure threshold in autoresearch_nightly.

Covers _resolve_min_rows() — the function that computes thresholds based on the
number of enabled strategies in a universe's active config and the hand-calibrated
operator floors in MIN_ROWS_PER_UNIVERSE.

Semantics (max, not min — corrected 2026-05-12):
  threshold = max(operator_floor, enabled_strategies * MIN_ROWS_PER_STRATEGY)

Errors resolved by initial fix (2026-05-06): db.errors table ids 19, 20, 21, 27, 28, 29
  (all "Research sweep silent failure: universe=gold_etfs/commodity_etfs rows=0
   threshold=10/20" false-positive alerts).
Follow-up fix (2026-05-12, commit eb647724 follow-up): min() → max() so operator
  floors for large universes (sp500=50) are never silently weakened by the dynamic
  floor (2*3=6).
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
    DEFAULT_ROWS_PER_STRATEGY,
    MIN_ROWS_PER_STRATEGY,
    MIN_ROWS_PER_UNIVERSE,
    ROWS_PER_STRATEGY_BY_UNIVERSE,
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
    """Allow-list-aware behaviour (#392): threshold scales with enabled count.

    threshold = max(MIN_ROWS_PER_STRATEGY, enabled * ROWS_PER_STRATEGY_BY_UNIVERSE).
    Missing/retired configs fall back to the static MIN_ROWS_PER_UNIVERSE floor.
    """

    def test_resolve_min_rows_gold_etfs_missing_config_returns_operator_floor(self):
        """gold_etfs config is retired/missing → static operator floor (3)."""
        result = _resolve_min_rows("gold_etfs")
        assert result == MIN_ROWS_PER_UNIVERSE["gold_etfs"] == 3, (
            f"gold_etfs (no active config) should fall back to operator floor 3, got {result}"
        )

    def test_resolve_min_rows_commodity_etfs_3_strategies_returns_9(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """commodity_etfs with 3 enabled strategies (isolated config).

        Expected: max(3, 3 * ROWS_PER_STRATEGY_BY_UNIVERSE['commodity_etfs']=3) = 9.
        """
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        _write_config(
            tmp_path, "commodity_etfs",
            {"a": {"enabled": True}, "b": {"enabled": True}, "c": {"enabled": True}},
        )
        result = _resolve_min_rows("commodity_etfs")
        assert result == 9, (
            f"commodity_etfs with 3 enabled strategies should give 9, got {result}"
        )

    def test_resolve_min_rows_sp500_1_strategy_returns_25(self):
        """sp500 active config currently has ONE enabled strategy (momentum_breakout).

        Expected: max(3, 1 * ROWS_PER_STRATEGY_BY_UNIVERSE['sp500']=25) = 25.
        This is the #392 fix — the old flat operator floor of 50 produced a false
        DEGRADED warning every night for a healthy ~38-row single-strategy sweep.
        """
        result = _resolve_min_rows("sp500")
        assert result == 25, (
            f"sp500 with 1 enabled strategy should give threshold=25 "
            f"(allow-list-aware, #392), got {result}"
        )

    def test_resolve_min_rows_sp500_2_strategies_returns_50(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """sp500 with 2 enabled strategies (isolated config) → 50.

        Expected: max(3, 2 * 25) = 50 — sensitivity to a real collapse is preserved
        because the floor scales back up as strategies are re-enabled.
        """
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        _write_config(
            tmp_path, "sp500",
            {"momentum_breakout": {"enabled": True}, "connors_rsi2": {"enabled": True}},
        )
        result = _resolve_min_rows("sp500")
        assert result == 50, (
            f"sp500 with 2 enabled strategies should scale back to 50, got {result}"
        )


# ─── Tests using tmp_path (isolated from production configs) ─────────────────


class TestResolveMinRowsIsolated:
    """Tests 4-7: monkeypatch ATLAS_ROOT to a tmp dir for full isolation."""

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

    def test_resolve_min_rows_zero_enabled_returns_floor(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Universe with 0 enabled strategies -> static operator floor (not 0 or 3).

        We still want to alert if rows ARE somehow produced for a universe where
        no strategies are enabled -- so the operator floor is preserved.
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
        expected_floor = MIN_ROWS_PER_UNIVERSE["gold_etfs"]
        assert result == expected_floor, (
            f"0 enabled strategies should fall back to static operator floor "
            f"({expected_floor}), got {result}"
        )

    def test_resolve_min_rows_corrupt_config_falls_back(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ):
        """Corrupt JSON in config file -> WARNING log + fallback to static floor."""
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        cfg_dir = tmp_path / "config" / "active"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / "gold_etfs.json").write_text("THIS IS NOT VALID JSON {{{{")

        with caplog.at_level(logging.WARNING, logger="research.autoresearch_nightly"):
            result = _resolve_min_rows("gold_etfs")

        expected_floor = MIN_ROWS_PER_UNIVERSE["gold_etfs"]
        assert result == expected_floor, (
            f"Corrupt config should fall back to static operator floor ({expected_floor}), got {result}"
        )
        assert any(
            "_resolve_min_rows" in record.message and "falling back" in record.message
            for record in caplog.records
        ), f"Expected a WARNING with '_resolve_min_rows' and 'falling back', got: {caplog.records}"

    def test_resolve_min_rows_scales_with_high_enabled_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Floor scales linearly with the enabled-strategy count (#392).

        Synthesize treasury_etfs with 100 enabled strategies. Expected:
        max(MIN_ROWS_PER_STRATEGY, 100 * ROWS_PER_STRATEGY_BY_UNIVERSE['treasury_etfs']).
        """
        monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
        strategies = {f"strat_{i:03d}": {"enabled": True} for i in range(100)}
        _write_config(tmp_path, "treasury_etfs", strategies)

        result = _resolve_min_rows("treasury_etfs")
        expected = 100 * ROWS_PER_STRATEGY_BY_UNIVERSE["treasury_etfs"]  # 100*5 = 500
        assert result == expected, (
            f"100 enabled strategies should scale to {expected}, got {result}"
        )


# ─── Regression test: sp500 operator floor must not be weakened ──────────────


def test_sp500_threshold_scales_with_allow_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Regression test (#392): the sp500 floor tracks the active allow-list.

    1 enabled strategy → 25 (a healthy ~38-row sweep is NOT flagged DEGRADED);
    2 enabled strategies → 50 (sensitivity to a real collapse is preserved).
    Pre-#392 this was a flat operator floor of 50, which false-flagged every
    single-strategy nightly run.
    """
    monkeypatch.setattr(autoresearch_nightly, "ATLAS_ROOT", tmp_path)
    _write_config(tmp_path, "sp500", {"momentum_breakout": {"enabled": True}})
    assert _resolve_min_rows("sp500") == 25

    _write_config(
        tmp_path, "sp500",
        {"momentum_breakout": {"enabled": True}, "connors_rsi2": {"enabled": True}},
    )
    assert _resolve_min_rows("sp500") == 50


# --- Module-level constant sanity --------------------------------------------


class TestConstants:
    """Verify the constants are correctly defined."""

    def test_min_rows_per_strategy_is_3(self):
        assert MIN_ROWS_PER_STRATEGY == 3

    def test_default_min_rows_is_10(self):
        assert DEFAULT_MIN_ROWS == 10

    def test_rows_per_strategy_constants(self):
        """Allow-list-aware per-strategy expectations are defined (#392)."""
        assert ROWS_PER_STRATEGY_BY_UNIVERSE["sp500"] == 25
        assert DEFAULT_ROWS_PER_STRATEGY == 3

    def test_sp500_per_strategy_scaling_invariant(self):
        """Core #392 invariant: the floor scales with the enabled-strategy count.

        A single-strategy sp500 sweep (~38 rows) must NOT trip the floor (25),
        while two strategies scale it back to 50 to preserve collapse sensitivity.
        """
        per_strategy = ROWS_PER_STRATEGY_BY_UNIVERSE["sp500"]
        assert max(MIN_ROWS_PER_STRATEGY, 1 * per_strategy) == 25
        assert max(MIN_ROWS_PER_STRATEGY, 2 * per_strategy) == 50
