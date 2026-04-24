"""Tests for universe.json freshness and rebuild_universe.py.

Serves as a CI canary for universe staleness:
  - WARN (not fail) if built_at is 7-14 days ago
  - FAIL if built_at is > 14 days ago
  - FAIL if any universe.json is missing or unparseable

Also tests the rebuild_universe.py script logic.
"""
from __future__ import annotations

import datetime
import json
import sys
from pathlib import Path
from typing import Optional
from unittest.mock import patch, MagicMock

import pytest

_ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

PROCESSED_DIR = _ATLAS_ROOT / "data" / "processed"
ALL_UNIVERSES = [
    "sp500",
    "commodity_etfs",
    "sector_etfs",
    "defensive_etfs",
    "gold_etfs",
    "treasury_etfs",
]

WARN_DAYS = 7     # warn if built_at is older than this
FAIL_DAYS = 14    # hard fail if built_at is older than this


def _load_universe(universe_name: str) -> Optional[dict]:
    p = PROCESSED_DIR / universe_name / "universe.json"
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _age_days(universe_name: str) -> Optional[float]:
    """Return age of universe.json built_at in fractional days, or None."""
    d = _load_universe(universe_name)
    if not d:
        return None
    built_at_str = d.get("metadata", {}).get("built_at")
    if not built_at_str:
        return None
    try:
        built_at = datetime.datetime.fromisoformat(built_at_str)
        now = datetime.datetime.now()
        return (now - built_at).total_seconds() / 86400
    except ValueError:
        return None


class TestUniverseFilesExist:
    """All expected universe.json files must exist and be parseable."""

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_universe_json_exists(self, universe_name):
        p = PROCESSED_DIR / universe_name / "universe.json"
        assert p.exists(), (
            f"universe.json missing for {universe_name!r} at {p}. "
            f"Run: python3 scripts/rebuild_universe.py --universe {universe_name}"
        )

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_universe_json_parseable(self, universe_name):
        d = _load_universe(universe_name)
        assert d is not None, f"universe.json for {universe_name!r} is missing or unparseable"
        assert "tickers" in d, f"universe.json for {universe_name!r} has no 'tickers' key"
        assert isinstance(d["tickers"], list), f"tickers must be a list for {universe_name!r}"

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_universe_has_tickers(self, universe_name):
        d = _load_universe(universe_name)
        if d is None:
            pytest.skip(f"{universe_name} universe.json missing")
        tickers = d.get("tickers", [])
        assert len(tickers) > 0, f"universe.json for {universe_name!r} has 0 tickers"

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_universe_has_built_at(self, universe_name):
        d = _load_universe(universe_name)
        if d is None:
            pytest.skip(f"{universe_name} universe.json missing")
        built_at = d.get("metadata", {}).get("built_at")
        assert built_at, (
            f"universe.json for {universe_name!r} has no metadata.built_at timestamp"
        )


class TestUniverseFreshness:
    """universe.json built_at must be within FAIL_DAYS days.

    Warns (pytest.warns or print) if within WARN_DAYS..FAIL_DAYS days.
    Hard fails if > FAIL_DAYS days.
    """

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_freshness_not_critically_stale(self, universe_name):
        """Hard fail if universe is more than FAIL_DAYS days old."""
        age = _age_days(universe_name)
        if age is None:
            pytest.skip(f"{universe_name} universe.json missing or has no built_at")

        assert age <= FAIL_DAYS, (
            f"universe.json for {universe_name!r} is {age:.1f} days old "
            f"(hard-fail threshold: {FAIL_DAYS}d). "
            f"Run: python3 scripts/rebuild_universe.py --universe {universe_name}"
        )

    @pytest.mark.parametrize("universe_name", ALL_UNIVERSES)
    def test_freshness_warn_if_aging(self, universe_name):
        """Soft warn (not fail) if universe is between WARN_DAYS and FAIL_DAYS old."""
        age = _age_days(universe_name)
        if age is None:
            pytest.skip(f"{universe_name} universe.json missing or has no built_at")

        if WARN_DAYS < age <= FAIL_DAYS:
            # Soft warning — don't fail the test, but print a visible warning.
            print(
                f"\nWARNING: {universe_name!r} universe.json is {age:.1f} days old "
                f"(warn at {WARN_DAYS}d, fail at {FAIL_DAYS}d). "
                f"Consider running: python3 scripts/rebuild_universe.py --universe {universe_name}"
            )


class TestRebuildUniverseScript:
    """Unit tests for scripts/rebuild_universe.py."""

    def test_dry_run_static_universe(self, tmp_path):
        """--dry-run prints but does NOT write files."""
        from scripts.rebuild_universe import rebuild_static_universe

        with patch("scripts.rebuild_universe._universe_path") as mock_path:
            mock_path.return_value = tmp_path / "universe.json"
            result = rebuild_static_universe("commodity_etfs", dry_run=True)

        assert result is True
        assert not (tmp_path / "universe.json").exists()

    def test_static_universe_writes_json(self, tmp_path):
        """rebuild_static_universe writes a valid JSON with built_at and tickers."""
        from scripts.rebuild_universe import rebuild_static_universe

        out_path = tmp_path / "universe.json"
        with patch("scripts.rebuild_universe._universe_path", return_value=out_path):
            out_path.parent.mkdir(parents=True, exist_ok=True)
            result = rebuild_static_universe("commodity_etfs", dry_run=False)

        assert result is True
        assert out_path.exists()

        with open(out_path) as f:
            d = json.load(f)

        assert "tickers" in d
        assert len(d["tickers"]) > 0
        assert "metadata" in d
        assert "built_at" in d["metadata"]

        # built_at should be recent (within 60 seconds of now)
        built_at = datetime.datetime.fromisoformat(d["metadata"]["built_at"])
        age_seconds = (datetime.datetime.now() - built_at).total_seconds()
        assert age_seconds < 60, "built_at should be set to approximately now"

    def test_unknown_universe_returns_false(self):
        from scripts.rebuild_universe import rebuild_universe
        result = rebuild_universe("nonexistent_universe")
        assert result is False

    def test_sp500_fallback_on_too_few_tickers(self, tmp_path):
        """If build_universe returns < 50 tickers, restores pre-build state."""
        from scripts.rebuild_universe import rebuild_sp500_universe

        # Write a "good" pre-build universe.json
        pre_build = {
            "metadata": {"built_at": "2026-04-15T00:00:00", "final_count": 199},
            "tickers": [f"TICK{i}" for i in range(199)],
        }
        universe_path = tmp_path / "universe.json"
        universe_path.write_text(json.dumps(pre_build))

        # get_active_config and build_universe are imported inside the function;
        # patch them at their source modules.
        with (
            patch("scripts.rebuild_universe._universe_path", return_value=universe_path),
            patch("scripts.rebuild_universe._load_existing_universe", return_value=pre_build),
            patch("universe.builder.build_universe", return_value=["ONLY1", "ONLY2"]),
            patch("utils.config.get_active_config", return_value={"market": "sp500"}),
        ):
            result = rebuild_sp500_universe(dry_run=False)

        assert result is True
        # The JSON should have the pre-build tickers (199), not the bad 2-ticker result
        d = json.loads(universe_path.read_text())
        assert len(d["tickers"]) == 199

    def test_sp500_dry_run(self):
        from scripts.rebuild_universe import rebuild_sp500_universe
        # get_active_config is imported inside the function; patch at source.
        with patch("utils.config.get_active_config", return_value={"market": "sp500"}):
            result = rebuild_sp500_universe(dry_run=True)
        assert result is True

    def test_main_all_flag(self, tmp_path):
        """--all rebuilds all universes and returns 0 on success."""
        from scripts.rebuild_universe import main

        with patch("scripts.rebuild_universe.rebuild_universe", return_value=True) as mock_rebuild:
            rc = main(["--all"])

        assert rc == 0
        # Called once per universe
        from scripts.rebuild_universe import ALL_UNIVERSES
        assert mock_rebuild.call_count == len(ALL_UNIVERSES)

    def test_main_partial_failure_returns_1(self, tmp_path):
        """If one universe fails, exit code is 1."""
        from scripts.rebuild_universe import main

        call_count = [0]
        def fake_rebuild(name, dry_run=False):
            call_count[0] += 1
            return name != "sp500"  # sp500 fails

        with patch("scripts.rebuild_universe.rebuild_universe", side_effect=fake_rebuild):
            rc = main(["--all"])

        assert rc == 1
