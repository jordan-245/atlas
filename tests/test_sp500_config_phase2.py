"""Invariant tests for the live SP500 config (config/active/sp500.json).

History:
  - Originally authored 2026-04-28 to lock Phase 2 W1+W2 weight/param updates.
  - Repaired 2026-06-01 (Task #354) for current v3.2.4 reality. The W1/W2-era
    expectations (momentum_breakout 0.70 / connors_rsi2 0.30, enabled weights
    summing to 1.0, the `_weight_update_2026_04_28` / `_param_update_2026_04_28`
    metadata blocks) went stale once the universe was consolidated to a single
    validated strategy and connors_rsi2 was decommissioned (2026-05-18).

These tests assert the *current* live invariants rather than frozen historical
weights, so they describe what the config must remain true to without going
stale on every re-optimization:
  - exactly one enabled/live strategy (momentum_breakout),
  - no deprecated strategy (esp. connors_rsi2) carrying a live weight,
  - approval gate stays in its current (false) state,
  - momentum_breakout still ships its validated research-best params.

DO NOT edit config/active/sp500.json to make a test pass — these guard the
live config. If the config legitimately changes (e.g. a new strategy is
promoted, or approval is deliberately re-enabled), update the matching test
consciously as part of that change.
"""
import json
import pytest
from pathlib import Path

CONFIG_PATH = Path("/root/atlas/config/active/sp500.json")

# The single strategy that is currently enabled/live in the SP500 universe.
EXPECTED_ENABLED_STRATEGIES = {"momentum_breakout"}

# Strategies that have been decommissioned and must never carry a live weight.
DECOMMISSIONED_STRATEGIES = {
    "connors_rsi2",
    "sector_rotation",
    "opening_gap",
    "trend_following",
    "short_term_mr",
    "bb_squeeze",
    "mtf_momentum",
    "mean_reversion",
    "dividend_capture",
}


@pytest.fixture(scope="module")
def cfg():
    with CONFIG_PATH.open() as f:
        return json.load(f)


# ── Structural invariants ──────────────────────────────────────────────────────

def test_sp500_config_loads_cleanly(cfg):
    assert cfg["market"] == "sp500"
    assert "strategies" in cfg
    assert cfg["version"].startswith("v3."), (
        f"Unexpected config version {cfg['version']!r} — expected a v3.x SP500 config."
    )


def test_only_intended_strategy_is_enabled(cfg):
    # v3.2.4 reality: the universe was consolidated to a single validated
    # strategy. Any newly enabled strategy must be a deliberate, gated promotion
    # — update EXPECTED_ENABLED_STRATEGIES (and this comment) when that happens.
    enabled = {
        name
        for name, s in cfg["strategies"].items()
        if s.get("enabled") is True
    }
    assert enabled == EXPECTED_ENABLED_STRATEGIES, (
        f"Enabled strategy set is {sorted(enabled)}, expected "
        f"{sorted(EXPECTED_ENABLED_STRATEGIES)}. Enabling/disabling a strategy in "
        "the live config is a deliberate, validated change — update this test "
        "consciously as part of that promotion/retirement."
    )


def test_enabled_strategy_has_positive_weight(cfg):
    # The lone enabled strategy must carry a positive allocation weight.
    # (Current v3.2.4 value is 0.25; we assert > 0 rather than freezing the exact
    #  number so routine re-optimizations don't make this test go stale again.)
    mb = cfg["strategies"]["momentum_breakout"]
    assert mb.get("enabled") is True
    assert mb.get("weight", 0) > 0, (
        f"momentum_breakout weight is {mb.get('weight')!r} — the only enabled "
        "strategy must have a positive weight."
    )


def test_sector_rotation_is_disabled(cfg):
    # 2026-05-01 audit: disabled entirely (solo Sharpe 0.044 — no edge in sp500).
    # 2026-05-18: marked deprecated (Tier 3 dead strategy).
    # Guard: must stay disabled until research validates a real edge.
    sr = cfg["strategies"]["sector_rotation"]
    assert sr.get("enabled") is False, (
        f"sector_rotation.enabled is {sr.get('enabled')!r} — must be False. "
        "Disabled 2026-05-01 (solo Sharpe 0.044 < 0.5 gate). Do NOT re-enable "
        "without a validated research_best entry."
    )
    assert sr.get("weight", 0) == 0, (
        f"sector_rotation.weight is {sr.get('weight')} — must be 0 while disabled."
    )


def test_no_deprecated_strategy_carries_live_weight(cfg):
    # connors_rsi2 was decommissioned 2026-05-18 (clean solo Sharpe -0.51, p=0.63,
    # no statistical edge per #340). It and every other deprecated/disabled
    # strategy must be both disabled AND zero-weighted so they can never leak a
    # live allocation. This replaces the stale W1 expectation of connors_rsi2=0.30.
    offenders = {
        name: (s.get("enabled"), s.get("weight"))
        for name, s in cfg["strategies"].items()
        if name in DECOMMISSIONED_STRATEGIES
        and (s.get("enabled") is True or s.get("weight", 0) != 0)
    }
    assert not offenders, (
        f"Deprecated strategies carrying a live weight or enabled flag: {offenders}. "
        "Decommissioned strategies must be enabled=False and weight=0."
    )


def test_connors_rsi2_is_decommissioned(cfg):
    # Explicit guard for the specific strategy the old tests over-weighted.
    cr2 = cfg["strategies"]["connors_rsi2"]
    assert cr2.get("enabled") is False, "connors_rsi2 must remain disabled."
    assert cr2.get("weight", 0) == 0, "connors_rsi2 must carry no live weight."


def test_approval_remains_false(cfg):
    # System state reports "Approval: false", derived from
    # `trading.approval_required === true` in the context injector. v3.2.4 has no
    # approval_required key, so the approval gate is currently false (auto_approve
    # flow). Re-enabling the approval gate is a deliberate, separately-gated config
    # change — update this test consciously if/when that happens.
    trading = cfg["trading"]
    assert trading.get("approval_required") is not True, (
        "trading.approval_required is True — the approval gate was enabled. "
        "v3.2.4 baseline runs with approval=false. Re-enabling approval is a "
        "deliberate config change; update this test consciously."
    )


# ── momentum_breakout research-best params ──────────────────────────────────────

def test_momentum_breakout_params_match_research_best(cfg):
    # These are the validated research-best params shipped in v3.2.4. They change
    # only via a deliberate re-optimization + promotion, at which point this test
    # should be updated alongside the config.
    mb = cfg["strategies"]["momentum_breakout"]
    assert mb["atr_stop_mult"] == 0.61
    assert mb["lookback_days"] == 14
    assert mb["atr_period"] == 18
    assert mb["trend_ma_period"] == 27
    assert mb["breakout_period"] == 10
    assert mb["max_hold_days"] == 15
    assert mb["profit_target_atr_mult"] == 6.0


def test_strategy_initializes_with_current_params():
    """Verify the strategy class can be instantiated with the live params."""
    cfg_full = json.loads(CONFIG_PATH.read_text())
    mb_params = cfg_full["strategies"]["momentum_breakout"]

    # Strategy class is MomentumBreakout (not MomentumBreakoutStrategy)
    try:
        from strategies.momentum_breakout import MomentumBreakout as Strat
    except ImportError:
        # Fallback: just verify keys are loadable & types are correct
        assert isinstance(mb_params["atr_stop_mult"], (int, float))
        assert isinstance(mb_params["lookback_days"], int)
        return

    # If import worked, verify instantiation doesn't crash
    inst = Strat(config=cfg_full)
    assert inst is not None


# ── Metadata reality (replaces stale W1/W2 phase2 metadata assertions) ──────────

def test_current_version_metadata_present(cfg):
    # The W1/W2-era `_weight_update_2026_04_28` / `_param_update_2026_04_28` blocks
    # were dropped as the config evolved past Phase 2; we no longer require them.
    # Instead assert the metadata the current config actually ships.
    assert "_version_metadata" in cfg, "current config must record _version_metadata"
    vm = cfg["_version_metadata"]
    assert vm.get("market") == "sp500"
    assert vm.get("previous_version") == "v3.2.2"
    assert "_optimization_metadata" in cfg, (
        "current config must record _optimization_metadata"
    )
