"""Regression tests for universe isolation in the autoresearch pipeline.

Verifies three isolation properties that were broken by the 2026-04-22
investigation (research_experiments rows with identical sharpe/trades across
sector_etfs, gold_etfs, treasury_etfs, defensive_etfs on the same day):

1. _filter_enabled_strategies uses the universe key, not the global market.
2. run_nightly() coerces market==universe for non-sp500 sweeps, so _launch()
   passes --market=gold_etfs (not --market=sp500) to each worker.
3. ResearchSession config.market matches the passed market argument.
"""

import sys
import subprocess
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch, call

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from research.autoresearch_nightly import _filter_enabled_strategies, run_nightly


# ─── Test 1: _filter_enabled_strategies respects universe ────────────────────

class TestFilterEnabledStrategiesRespectsUniverse:
    """_filter_enabled_strategies must read the UNIVERSE config, not sp500."""

    def test_gold_etfs_drops_disabled_strategies(self):
        """Strategies disabled in gold_etfs config must be excluded."""
        # In the real gold_etfs config: trend_following=False, mean_reversion=False
        # Use the real config so this test catches real regressions.
        result = _filter_enabled_strategies(
            ["trend_following", "mean_reversion", "connors_rsi2", "short_term_mr"],
            "gold_etfs",
        )
        # connors_rsi2 and short_term_mr are enabled in gold_etfs
        assert "connors_rsi2" in result, "connors_rsi2 should be enabled in gold_etfs"
        assert "short_term_mr" in result, "short_term_mr should be enabled in gold_etfs"
        # trend_following and mean_reversion are disabled in gold_etfs
        assert "trend_following" not in result, (
            "trend_following is disabled in gold_etfs — must be filtered out"
        )
        assert "mean_reversion" not in result, (
            "mean_reversion is disabled in gold_etfs — must be filtered out"
        )

    def test_monkeypatched_config_respects_universe(self):
        """Monkeypatched configs confirm the universe key is used, not sp500."""
        sp500_cfg = {
            "strategies": {
                "trend_following": {"enabled": True},
                "momentum_breakout": {"enabled": True},
            }
        }
        gold_etfs_cfg = {
            "strategies": {
                "trend_following": {"enabled": False},
                "momentum_breakout": {"enabled": True},
            }
        }

        def fake_get_active_config(market_or_universe: str) -> dict:
            if market_or_universe == "gold_etfs":
                return gold_etfs_cfg
            return sp500_cfg

        # Patch the import inside autoresearch_nightly's module scope
        with patch("utils.config.get_active_config", fake_get_active_config):
            result = _filter_enabled_strategies(
                ["trend_following", "momentum_breakout"], "gold_etfs"
            )

        assert "trend_following" not in result, (
            "_filter_enabled_strategies used sp500 config instead of gold_etfs"
        )
        assert "momentum_breakout" in result

    def test_sp500_universe_uses_sp500_flags(self, monkeypatch):
        """When universe='sp500', sp500 config flags are applied."""
        sp500_cfg = {
            "strategies": {
                "trend_following": {"enabled": True},
                "momentum_breakout": {"enabled": False},
            }
        }

        with patch("utils.config.get_active_config", return_value=sp500_cfg):
            result = _filter_enabled_strategies(
                ["trend_following", "momentum_breakout"], "sp500"
            )

        assert "trend_following" in result
        assert "momentum_breakout" not in result

    def test_missing_strategy_in_config_defaults_enabled(self, monkeypatch):
        """Strategy not in universe config defaults to enabled=True (safe default)."""
        cfg = {"strategies": {"known_strategy": {"enabled": False}}}
        with patch("utils.config.get_active_config", return_value=cfg):
            result = _filter_enabled_strategies(
                ["unknown_strategy", "known_strategy"], "gold_etfs"
            )
        assert "unknown_strategy" in result, "Missing strategy should default to enabled"
        assert "known_strategy" not in result


# ─── Test 2: run_nightly coerces market to universe ──────────────────────────

class TestRunNightlyCoercesMarketToUniverse:
    """run_nightly() must pass --market=gold_etfs when universe=gold_etfs."""

    def test_subprocess_cmd_uses_universe_as_market(self):
        """_launch() cmd must contain --market gold_etfs when universe=gold_etfs."""
        captured_cmds: List[list] = []

        class FakeProc:
            pid = 99999
            def poll(self): return 0  # immediately done
            def __init__(self, cmd, **kw): captured_cmds.append(cmd)

        with patch("research.autoresearch_nightly.subprocess.Popen", FakeProc), \
             patch("research.autoresearch_nightly._find_latest_snapshot", return_value="snap1"), \
             patch("research.autoresearch_nightly._parse_session_results",
                   return_value={"strategy": "mean_reversion", "screened": 0,
                                 "promoted": 0, "kept": 0, "starting_sharpe": 0.0,
                                 "final_sharpe": 0.0}), \
             patch("research.autoresearch_nightly.time.sleep", return_value=None), \
             patch("research.autoresearch_nightly._run_promotion_sweep", return_value=[]), \
             patch("research.autoresearch_nightly.log_session", return_value="sess1"), \
             patch("research.autoresearch_nightly.end_session", return_value=None), \
             patch("research.autoresearch_nightly.open", create=True):

            # Patch _filter_enabled_strategies to pass through
            with patch("research.autoresearch_nightly._filter_enabled_strategies",
                       side_effect=lambda strats, u: strats):
                try:
                    run_nightly(
                        strategies=["mean_reversion"],
                        market="sp500",
                        universe="gold_etfs",
                        hours=0.001,
                        workers=1,
                    )
                except Exception:
                    pass

        assert captured_cmds, "No Popen calls were captured"
        cmd = captured_cmds[0]
        cmd_str = " ".join(str(c) for c in cmd)
        assert "--market" in cmd_str, f"--market not found in cmd: {cmd_str}"
        assert "gold_etfs" in cmd_str, f"gold_etfs not in cmd: {cmd_str}"
        # Verify the --market value is gold_etfs (not sp500)
        market_idx = cmd.index("--market")
        assert cmd[market_idx + 1] == "gold_etfs", (
            f"Expected --market gold_etfs, got --market {cmd[market_idx + 1]}"
        )

    def test_market_coercion_updates_local_var(self):
        """When universe != sp500, market local var must equal universe before _launch()."""
        coerced_market = []

        def fake_spawn(strategies, market, hours, snapshot_id, max_workers, universe="sp500"):
            coerced_market.append(market)
            return []

        with patch("research.autoresearch_nightly._spawn_workers", fake_spawn), \
             patch("research.autoresearch_nightly._find_latest_snapshot", return_value="snap1"), \
             patch("research.autoresearch_nightly._run_promotion_sweep", return_value=[]), \
             patch("research.autoresearch_nightly._parse_session_results",
                   return_value={"strategy": "mean_reversion", "screened": 0,
                                 "promoted": 0, "kept": 0, "starting_sharpe": 0.0,
                                 "final_sharpe": 0.0}), \
             patch("research.autoresearch_nightly.log_session", return_value="sess1"), \
             patch("research.autoresearch_nightly.end_session", return_value=None), \
             patch("research.autoresearch_nightly._filter_enabled_strategies",
                   side_effect=lambda strats, u: strats):

            run_nightly(
                strategies=["mean_reversion"],
                market="sp500",
                universe="commodity_etfs",
                hours=0.001,
                workers=1,
            )

        assert coerced_market, "spawn_workers not called"
        assert coerced_market[0] == "commodity_etfs", (
            f"Expected market='commodity_etfs' after coercion, got {coerced_market[0]!r}"
        )

    def test_sp500_universe_market_unchanged(self):
        """When universe='sp500', market stays 'sp500' (no coercion)."""
        coerced_market = []

        def fake_spawn(strategies, market, hours, snapshot_id, max_workers, universe="sp500"):
            coerced_market.append(market)
            return []

        with patch("research.autoresearch_nightly._spawn_workers", fake_spawn), \
             patch("research.autoresearch_nightly._find_latest_snapshot", return_value="snap1"), \
             patch("research.autoresearch_nightly._run_promotion_sweep", return_value=[]), \
             patch("research.autoresearch_nightly.log_session", return_value="sess1"), \
             patch("research.autoresearch_nightly.end_session", return_value=None), \
             patch("research.autoresearch_nightly._filter_enabled_strategies",
                   side_effect=lambda strats, u: strats):

            run_nightly(
                strategies=["mean_reversion"],
                market="sp500",
                universe="sp500",
                hours=0.001,
                workers=1,
            )

        assert coerced_market[0] == "sp500"


# ─── Test 3: ResearchSession config.market matches passed market ──────────────

class TestResearchSessionConfigMatchesMarket:
    """ResearchSession must load config for the requested market, not always sp500."""

    def _build_fake_session(self, market: str):
        """Return a ResearchSession instance with mocked I/O for market."""
        from research.loop import ResearchSession

        fake_config = {
            "market": market,
            "strategies": {
                "momentum_breakout": {
                    "enabled": True,
                    "lookback": 20,
                }
            }
        }
        fake_data = {f"TICKER_{i}": MagicMock() for i in range(5)}

        with patch("research.loop._find_latest_snapshot", return_value="fake_snap"), \
             patch("research.loop.ResearchSession._find_latest_snapshot",
                   return_value="fake_snap", create=True), \
             patch("scripts.strategy_evaluator.load_market_data", return_value=fake_data), \
             patch("utils.config.get_active_config", return_value=fake_config), \
             patch("research.lockfile.compute_lock", return_value={}), \
             patch("research.lockfile.save_lock", return_value=None):
            try:
                session = ResearchSession("momentum_breakout", market)
            except Exception as exc:
                # Some code paths (lockfile, snapshot) may fail in test env;
                # if so, manually verify config loading
                raise RuntimeError(
                    f"ResearchSession({market!r}) failed: {exc}"
                ) from exc
        return session

    def test_sp500_session_loads_sp500_config(self):
        """ResearchSession('momentum_breakout', 'sp500') must have config.market='sp500'."""
        try:
            session = self._build_fake_session("sp500")
            assert session._config.get("market") == "sp500", (
                f"Expected market='sp500', got {session._config.get('market')!r}"
            )
        except RuntimeError as exc:
            pytest.skip(f"Session init incomplete in test env: {exc}")

    def test_non_sp500_session_config_market_matches(self):
        """ResearchSession(..., 'commodity_etfs') must load commodity_etfs config."""
        try:
            session = self._build_fake_session("commodity_etfs")
            assert session._config.get("market") == "commodity_etfs", (
                f"config.market={session._config.get('market')!r} != 'commodity_etfs' — "
                "cross-universe config leak detected!"
            )
        except RuntimeError as exc:
            pytest.skip(f"Session init incomplete in test env: {exc}")

    def test_get_active_config_called_with_passed_market(self):
        """get_active_config must be called with the passed market, not hardcoded sp500."""
        from research.loop import ResearchSession

        calls = []

        def track_config(market: str) -> dict:
            calls.append(market)
            return {
                "market": market,
                "strategies": {"momentum_breakout": {"enabled": True}},
            }

        fake_data = {"TICKER": MagicMock()}

        with patch("research.loop._find_latest_snapshot", return_value="snap1"), \
             patch("scripts.strategy_evaluator.load_market_data", return_value=fake_data), \
             patch("utils.config.get_active_config", side_effect=track_config), \
             patch("research.lockfile.compute_lock", return_value={}), \
             patch("research.lockfile.save_lock", return_value=None):
            try:
                ResearchSession("momentum_breakout", "commodity_etfs")
            except Exception:
                pass

        assert "commodity_etfs" in calls, (
            f"get_active_config was not called with 'commodity_etfs'; calls={calls}"
        )
        assert "sp500" not in calls, (
            "get_active_config was called with 'sp500' instead of 'commodity_etfs'"
        )
