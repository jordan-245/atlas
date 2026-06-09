"""Unit tests for scripts/analyze_fractional_kelly_sizing.py pure helpers.

These tests cover the deterministic, side-effect-free logic only:
  * empirical full-Kelly fraction estimation
  * capped fractional-Kelly risk mapping
  * sub-window (2024-2025) max-drawdown extraction
  * board gate evaluation (Sharpe + drawdown-degradation band)
  * arm config construction (deep-copy + sizing-block mutation, never the active config)

The heavy walk-forward backtest path is intentionally NOT exercised here (slow,
data-dependent) — it is validated via the CLI run recorded in the report.
"""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Load the script module by path (it lives in scripts/, not an importable package).
_SPEC = importlib.util.spec_from_file_location(
    "analyze_fractional_kelly_sizing",
    PROJECT_ROOT / "scripts" / "analyze_fractional_kelly_sizing.py",
)
fk = importlib.util.module_from_spec(_SPEC)
# Register before exec so @dataclass annotation resolution can find the module.
sys.modules[_SPEC.name] = fk
_SPEC.loader.exec_module(fk)


# --------------------------------------------------------------------------
# compute_kelly_fraction
# --------------------------------------------------------------------------

def test_kelly_fraction_positive_edge():
    # W=0.5, b = 2.0/1.0 = 2.0 -> f* = 0.5 - 0.5/2.0 = 0.25
    f = fk.compute_kelly_fraction(win_rate=0.5, avg_winner_r=2.0, avg_loser_r=-1.0)
    assert f == pytest.approx(0.25, abs=1e-9)


def test_kelly_fraction_no_edge_returns_nonpositive():
    # W=0.4, b = 1.0 -> f* = 0.4 - 0.6/1.0 = -0.2 (no edge -> negative)
    f = fk.compute_kelly_fraction(win_rate=0.4, avg_winner_r=1.0, avg_loser_r=-1.0)
    assert f < 0.0


@pytest.mark.parametrize(
    "w,aw,al",
    [
        (0.0, 2.0, -1.0),   # degenerate win rate
        (1.0, 2.0, -1.0),   # degenerate win rate
        (0.5, 0.0, -1.0),   # no winners magnitude
        (0.5, 2.0, 0.0),    # no losers magnitude
    ],
)
def test_kelly_fraction_degenerate_returns_zero(w, aw, al):
    assert fk.compute_kelly_fraction(w, aw, al) == 0.0


# --------------------------------------------------------------------------
# fractional_kelly_risk_pct
# --------------------------------------------------------------------------

def test_fractional_kelly_scales_and_caps():
    # f*=0.25, k=0.5 -> 0.125 raw, capped at 0.02
    assert fk.fractional_kelly_risk_pct(0.25, 0.5, floor=0.001, cap=0.02) == pytest.approx(0.02)


def test_fractional_kelly_within_band():
    # f*=0.04, k=0.25 -> 0.01 raw, within [0.001, 0.02]
    assert fk.fractional_kelly_risk_pct(0.04, 0.25, floor=0.001, cap=0.02) == pytest.approx(0.01)


def test_fractional_kelly_negative_edge_floored():
    assert fk.fractional_kelly_risk_pct(-0.5, 0.5, floor=0.001, cap=0.02) == pytest.approx(0.001)


# --------------------------------------------------------------------------
# subwindow_max_drawdown
# --------------------------------------------------------------------------

def test_subwindow_max_drawdown_isolates_window():
    idx = pd.date_range("2023-01-01", "2025-06-30", freq="D")
    # Build an equity curve: flat in 2023, then a 20% drawdown inside 2024-2025.
    vals = []
    for d in idx:
        if d.year == 2024 and d.month == 6:
            vals.append(80.0)   # trough during the window (from 100 peak)
        elif d.year == 2023 and d.month == 6:
            vals.append(50.0)   # deeper dip OUTSIDE the window — must be ignored
        else:
            vals.append(100.0)
    ec = pd.Series(vals, index=idx)
    dd = fk.subwindow_max_drawdown(ec, "2024-01-01", "2025-12-31")
    assert dd == pytest.approx(0.20, abs=1e-6)


def test_subwindow_max_drawdown_handles_empty_window():
    idx = pd.date_range("2020-01-01", "2020-12-31", freq="D")
    ec = pd.Series([100.0] * len(idx), index=idx)
    assert fk.subwindow_max_drawdown(ec, "2024-01-01", "2025-12-31") is None


def test_subwindow_max_drawdown_needs_datetime_index():
    ec = pd.Series([100.0, 90.0, 95.0])  # RangeIndex, not datetime
    assert fk.subwindow_max_drawdown(ec, "2024-01-01", "2025-12-31") is None


# --------------------------------------------------------------------------
# evaluate_gate
# --------------------------------------------------------------------------

def test_gate_pass_when_sharpe_up_and_dd_within_band():
    base = {"sharpe": 0.80, "max_drawdown": 0.20}
    arm = {"sharpe": 0.90, "max_drawdown": 0.22}  # +0.10 sharpe, +2pts dd
    g = fk.evaluate_gate(arm, base)
    assert g["sharpe_improved"] is True
    assert g["breaches_soft_band"] is False
    assert g["breaches_hard_band"] is False
    assert g["gate_pass"] is True


def test_gate_fail_when_dd_degrades_past_hard_band():
    base = {"sharpe": 0.80, "max_drawdown": 0.20}
    arm = {"sharpe": 0.95, "max_drawdown": 0.27}  # +0.15 sharpe but +7pts dd
    g = fk.evaluate_gate(arm, base)
    assert g["sharpe_improved"] is True
    assert g["breaches_hard_band"] is True
    assert g["gate_pass"] is False


def test_gate_fail_when_sharpe_not_improved():
    base = {"sharpe": 0.80, "max_drawdown": 0.20}
    arm = {"sharpe": 0.78, "max_drawdown": 0.19}  # better dd but worse sharpe
    g = fk.evaluate_gate(arm, base)
    assert g["sharpe_improved"] is False
    assert g["gate_pass"] is False


def test_gate_soft_band_flag_between_thresholds():
    base = {"sharpe": 0.80, "max_drawdown": 0.20}
    arm = {"sharpe": 0.85, "max_drawdown": 0.24}  # +4pts dd: soft breached, hard not
    g = fk.evaluate_gate(arm, base)
    assert g["breaches_soft_band"] is True
    assert g["breaches_hard_band"] is False
    assert g["gate_pass"] is True  # still passes (hard band governs)


# --------------------------------------------------------------------------
# build_arm_config — never mutates input; only touches sizing blocks
# --------------------------------------------------------------------------

def _sample_config():
    return {
        "version": "v3.2.4-test",
        "risk": {"max_risk_per_trade_pct": 0.005, "max_open_positions": 10},
        "strategies": {"momentum_breakout": {"enabled": True, "atr_stop_mult": 0.61}},
        "dynamic_sizing": {
            "enabled": True,
            "base_risk_pct": 0.005,
            "volatility_scaling": {"enabled": True},
            "equity_curve_scaling": {"enabled": True},
        },
        "vol_scaling": {"enabled": True, "target_vol": 0.12},
    }


def test_build_arm_does_not_mutate_input():
    cfg = _sample_config()
    snapshot = fk.copy.deepcopy(cfg)
    fk.build_arm_config(cfg, "baseline_fixed")
    assert cfg == snapshot  # input untouched


def test_baseline_arm_disables_all_overlays():
    cfg = fk.build_arm_config(_sample_config(), "baseline_fixed")
    assert cfg["dynamic_sizing"]["enabled"] is False
    assert cfg["vol_scaling"]["enabled"] is False
    assert cfg["dynamic_sizing"]["base_risk_pct"] == 0.005


def test_live_arm_is_unchanged():
    src = _sample_config()
    cfg = fk.build_arm_config(src, "live_as_configured")
    assert cfg["dynamic_sizing"]["enabled"] is True
    assert cfg["vol_scaling"]["enabled"] is True


def test_vol_target_only_arm():
    cfg = fk.build_arm_config(_sample_config(), "vol_target_only")
    assert cfg["vol_scaling"]["enabled"] is True
    assert cfg["dynamic_sizing"]["enabled"] is False


def test_dd_scaling_only_arm():
    cfg = fk.build_arm_config(_sample_config(), "dd_scaling_only")
    assert cfg["vol_scaling"]["enabled"] is False
    assert cfg["dynamic_sizing"]["enabled"] is True
    assert cfg["dynamic_sizing"]["volatility_scaling"]["enabled"] is False
    assert cfg["dynamic_sizing"]["equity_curve_scaling"]["enabled"] is True


def test_risk_mult_arms_scale_base_flat():
    cfg15 = fk.build_arm_config(_sample_config(), "risk_mult_1.5x")
    cfg20 = fk.build_arm_config(_sample_config(), "risk_mult_2.0x")
    assert cfg15["dynamic_sizing"]["enabled"] is False
    assert cfg15["vol_scaling"]["enabled"] is False
    assert cfg15["dynamic_sizing"]["base_risk_pct"] == pytest.approx(0.0075)  # 1.5 * 0.005
    assert cfg20["dynamic_sizing"]["base_risk_pct"] == pytest.approx(0.010)   # 2.0 * 0.005


def test_frac_kelly_arm_sets_flat_base_risk():
    cfg = fk.build_arm_config(_sample_config(), "frac_kelly_0.5x", kelly_base_risk=0.012)
    assert cfg["dynamic_sizing"]["enabled"] is False
    assert cfg["vol_scaling"]["enabled"] is False
    assert cfg["dynamic_sizing"]["base_risk_pct"] == pytest.approx(0.012)


def test_frac_kelly_requires_base_risk():
    with pytest.raises(ValueError):
        fk.build_arm_config(_sample_config(), "frac_kelly_0.25x")


def test_unknown_arm_raises():
    with pytest.raises(ValueError):
        fk.build_arm_config(_sample_config(), "nonexistent_arm")
