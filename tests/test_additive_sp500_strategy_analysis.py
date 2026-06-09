"""Focused unit tests for scripts/analyze_additive_sp500_strategy.py helpers.

These exercise the PURE helper functions (data slicing, correlation, window
consistency, gate evaluation) with synthetic data — they do NOT run the full
walk-forward backtest (too slow / data-dependent for a unit test).

Run:
    python3 -m pytest tests/test_additive_sp500_strategy_analysis.py -q
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from scripts.analyze_additive_sp500_strategy import (  # noqa: E402
    slice_data,
    equity_returns,
    series_return_correlation,
    extract_pair_correlation,
    window_consistency,
    evaluate_gates,
)


# ── slice_data ───────────────────────────────────────────────────────────
def _mk_df(start, periods):
    idx = pd.date_range(start, periods=periods, freq="D")
    return pd.DataFrame({"close": np.arange(periods, dtype=float) + 1.0}, index=idx)


def test_slice_data_none_returns_same_object():
    data = {"A": _mk_df("2020-01-01", 200)}
    assert slice_data(data, None, None) is data


def test_slice_data_filters_window_and_min_rows():
    data = {
        "A": _mk_df("2020-01-01", 400),   # long
        "B": _mk_df("2020-01-01", 120),   # short
    }
    out = slice_data(data, "2020-06-01", None, min_rows=100)
    # A still has >=100 rows after the lower bound; B drops below 100.
    assert "A" in out
    assert "B" not in out
    assert out["A"].index.min() >= pd.Timestamp("2020-06-01")


def test_slice_data_upper_bound_inclusive():
    data = {"A": _mk_df("2020-01-01", 400)}
    out = slice_data(data, None, "2020-06-30", min_rows=10)
    assert out["A"].index.max() <= pd.Timestamp("2020-06-30")


# ── equity_returns / series_return_correlation ─────────────────────────────
def test_equity_returns_basic():
    eq = pd.Series([100.0, 110.0, 99.0], index=pd.date_range("2020-01-01", periods=3))
    r = equity_returns(eq)
    assert len(r) == 2
    assert r.iloc[0] == pytest.approx(0.10)
    assert r.iloc[1] == pytest.approx(-0.10)


def test_correlation_perfect_positive():
    idx = pd.date_range("2020-01-01", periods=50)
    base = np.cumprod(1 + np.random.RandomState(0).normal(0, 0.01, 50)) * 100
    a = pd.Series(base, index=idx)
    b = pd.Series(base * 2, index=idx)  # identical returns -> corr 1
    corr = series_return_correlation(a, b, active_only=False)
    assert corr == pytest.approx(1.0, abs=1e-6)


def test_correlation_negative():
    idx = pd.date_range("2020-01-01", periods=60)
    rs = np.random.RandomState(1).normal(0, 0.01, 59)
    a = pd.Series(np.concatenate([[100.0], 100 * np.cumprod(1 + rs)]), index=idx)
    b = pd.Series(np.concatenate([[100.0], 100 * np.cumprod(1 - rs)]), index=idx)
    corr = series_return_correlation(a, b, active_only=False)
    assert corr is not None and corr < 0


def test_correlation_insufficient_overlap_returns_none():
    a = pd.Series([100.0, 101.0], index=pd.date_range("2020-01-01", periods=2))
    b = pd.Series([100.0, 101.0], index=pd.date_range("2021-01-01", periods=2))
    assert series_return_correlation(a, b) is None


def test_correlation_zero_variance_returns_none():
    idx = pd.date_range("2020-01-01", periods=30)
    flat = pd.Series([100.0] * 30, index=idx)
    moving = pd.Series(np.arange(30, dtype=float) + 100, index=idx)
    assert series_return_correlation(flat, moving) is None


# ── extract_pair_correlation ───────────────────────────────────────────────
def test_extract_pair_correlation_found():
    sc = {
        "strategies": ["momentum_breakout", "mean_reversion"],
        "matrix": [[1.0, -0.25], [-0.25, 1.0]],
    }
    assert extract_pair_correlation(sc, "momentum_breakout", "mean_reversion") == pytest.approx(-0.25)


def test_extract_pair_correlation_missing():
    sc = {"strategies": ["momentum_breakout"], "matrix": [[1.0]]}
    assert extract_pair_correlation(sc, "momentum_breakout", "mean_reversion") is None
    assert extract_pair_correlation({}, "a", "b") is None


# ── window_consistency ─────────────────────────────────────────────────────
def test_window_consistency_aligns_and_scores():
    base = [
        {"test_start": "2024-01-01", "equity_start": 1000, "equity_end": 1010},
        {"test_start": "2024-02-01", "equity_start": 1010, "equity_end": 1005},
        {"test_start": "2024-03-01", "equity_start": 1005, "equity_end": 1020},
    ]
    comb = [
        {"test_start": "2024-01-01", "equity_start": 1000, "equity_end": 1030},  # > base
        {"test_start": "2024-02-01", "equity_start": 1030, "equity_end": 1020},  # ret < base ret
        {"test_start": "2024-03-01", "equity_start": 1020, "equity_end": 1060},  # > base
    ]
    out = window_consistency(base, comb)
    assert out["n_windows"] == 3
    assert out["combined_ge_baseline_pct"] is not None


def test_window_consistency_no_overlap():
    base = [{"test_start": "2024-01-01", "equity_start": 1000, "equity_end": 1010}]
    comb = [{"test_start": "2025-01-01", "equity_start": 1000, "equity_end": 1010}]
    out = window_consistency(base, comb)
    assert out["n_windows"] == 0


# ── evaluate_gates ─────────────────────────────────────────────────────────
def _metrics(sharpe, pf, dd):
    return {"sharpe": sharpe, "profit_factor": pf, "max_drawdown_pct": dd}


def test_evaluate_gates_all_pass_promotes():
    baseline = _metrics(0.30, 1.10, 31.0)
    combined = _metrics(0.70, 1.30, 14.0)
    solo = _metrics(0.65, 1.25, 12.0)
    v = evaluate_gates(
        baseline, combined, solo,
        corr_primary=-0.2,
        oos_combined=_metrics(0.8, 1.4, 10.0),
        oos_baseline=_metrics(0.3, 1.1, 20.0),
    )
    assert v["promote"] is True
    assert v["verdict"] == "PROMOTE"
    assert v["n_pass"] == v["n_total"]


def test_evaluate_gates_dd_not_materially_worse_passes():
    # combined DD 25% > 15% absolute bar, but <= baseline 31% -> G4 passes.
    baseline = _metrics(0.30, 1.10, 31.0)
    combined = _metrics(0.40, 1.30, 25.0)
    v = evaluate_gates(
        baseline, combined, combined,
        corr_primary=0.1,
        oos_combined=_metrics(0.5, 1.2, 20.0),
        oos_baseline=_metrics(0.3, 1.1, 22.0),
    )
    assert v["gates"]["G4_max_drawdown"]["pass"] is True


def test_evaluate_gates_high_correlation_fails_g5():
    baseline = _metrics(0.30, 1.10, 31.0)
    combined = _metrics(0.40, 1.30, 14.0)
    v = evaluate_gates(
        baseline, combined, combined,
        corr_primary=0.85,
        oos_combined=_metrics(0.5, 1.2, 10.0),
        oos_baseline=_metrics(0.3, 1.1, 20.0),
    )
    assert v["gates"]["G5_correlation"]["pass"] is False
    assert v["promote"] is False


def test_evaluate_gates_low_sharpe_no_promote_but_diversifier_flag():
    # Improves Sharpe a bit + low corr, but below the 0.6 absolute bar.
    baseline = _metrics(0.12, 1.14, 31.0)
    combined = _metrics(0.20, 1.18, 30.0)
    v = evaluate_gates(
        baseline, combined, combined,
        corr_primary=-0.1,
        oos_combined=_metrics(0.25, 1.2, 28.0),
        oos_baseline=_metrics(0.10, 1.1, 30.0),
    )
    assert v["promote"] is False
    assert v["improves_and_uncorrelated"] is True


def test_evaluate_gates_missing_correlation_fails_g5():
    baseline = _metrics(0.30, 1.10, 31.0)
    combined = _metrics(0.70, 1.30, 14.0)
    v = evaluate_gates(
        baseline, combined, combined,
        corr_primary=None,
        oos_combined=_metrics(0.8, 1.4, 10.0),
        oos_baseline=_metrics(0.3, 1.1, 20.0),
    )
    assert v["gates"]["G5_correlation"]["pass"] is False
