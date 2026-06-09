#!/usr/bin/env python3
"""Task #387 — Volatility-aware / fractional-Kelly position-sizing analysis.

PAPER / BACKTEST ANALYSIS ONLY. This script never writes to ``config/active/*``,
never touches the broker, and never executes live trades. It loads the live
SP500 active config **read-only**, deep-copies it, and mutates only the sizing
blocks (``dynamic_sizing`` / ``vol_scaling`` / ``dynamic_sizing.base_risk_pct``)
to compare position-sizing overlays on the **same signal set**.

Why this is a clean comparison
------------------------------
In the Atlas backtest engine, strategy signal generation (entry/stop/target/
confidence) is independent of sizing. Sizing only affects share counts (and a
few downstream value-based filters: ``min_position_value``, fee-aware, volume
participation, equal-weight ``max_position_value`` cap). So holding the strategy
params + regime/macro scaling constant and varying only the sizing overlay
isolates the sizing effect on the identical raw signal stream.

Arms (control + treatments)
---------------------------
* ``baseline_fixed``       flat ``risk.max_risk_per_trade_pct`` (dynamic_sizing OFF,
                            vol_scaling OFF) — the true fixed-sizing control.
* ``live_as_configured``   the active config exactly as deployed (vol_scaling ON +
                            dynamic_sizing ON with ATR-vol scaling + graduated DD
                            tiers). This is what is actually live today.
* ``vol_target_only``      portfolio vol-targeting only (vol_scaling ON,
                            dynamic_sizing OFF).
* ``dd_scaling_only``      drawdown de-risking only (graduated DD tiers ON,
                            ATR-vol scaling OFF, vol_scaling OFF).
* ``frac_kelly_0.25x``     flat sizing at 0.25x the empirical full-Kelly fraction.
* ``frac_kelly_0.5x``      flat sizing at 0.50x the empirical full-Kelly fraction.

The full-Kelly fraction f* is estimated from the *baseline* arm's realized
trades using R-multiples::

    b  = avg_winner_R / |avg_loser_R|        (reward:risk ratio)
    f* = W - (1 - W) / b                     (W = win rate)

Fractional-Kelly arms set ``dynamic_sizing.base_risk_pct = clamp(k * f*, floor, cap)``
and run flat (no further scaling), so the only difference vs baseline is the
per-trade risk fraction.

Board gate (from #387 brief)
----------------------------
* Improved risk-adjusted return (Sharpe) vs baseline.
* Max drawdown not worse than baseline by more than 3-5 percentage points.
* No live promotion / config change regardless of result (gated on OOS + approval).

Usage::

    python3 scripts/analyze_fractional_kelly_sizing.py                 # full analysis
    python3 scripts/analyze_fractional_kelly_sizing.py --equity 25000  # representative equity
    python3 scripts/analyze_fractional_kelly_sizing.py --quick         # 40-ticker smoke
    python3 scripts/analyze_fractional_kelly_sizing.py --workers 6
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Drawdown-period window for the board's 2024-2025 check.
DD_WINDOW_START = "2024-01-01"
DD_WINDOW_END = "2025-12-31"

# Kelly safety bounds (capped fractional-Kelly).
KELLY_RISK_FLOOR = 0.001   # 0.10% per trade minimum
KELLY_RISK_CAP = 0.02      # 2.00% per trade maximum (hard cap on aggressiveness)

# Board drawdown-degradation tolerance band (percentage points vs baseline).
DD_TOLERANCE_SOFT_PTS = 3.0
DD_TOLERANCE_HARD_PTS = 5.0


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested in tests/test_fractional_kelly_sizing_analysis.py)
# ---------------------------------------------------------------------------

def compute_kelly_fraction(
    win_rate: float, avg_winner_r: float, avg_loser_r: float
) -> float:
    """Estimate the full-Kelly bet fraction from R-multiple trade stats.

    Args:
        win_rate:     Fraction of trades that were winners (0-1).
        avg_winner_r: Mean R-multiple of winning trades (> 0).
        avg_loser_r:  Mean R-multiple of losing trades (<= 0, i.e. negative).

    Returns:
        Full-Kelly fraction f* = W - (1-W)/b where b = avg_winner_r/|avg_loser_r|.
        Returns 0.0 when inputs are degenerate or the edge is non-positive.
    """
    if not (0.0 < win_rate < 1.0):
        return 0.0
    loss_mag = abs(avg_loser_r)
    if avg_winner_r <= 0.0 or loss_mag <= 0.0:
        return 0.0
    b = avg_winner_r / loss_mag
    if b <= 0.0:
        return 0.0
    f_star = win_rate - (1.0 - win_rate) / b
    return float(f_star)


def fractional_kelly_risk_pct(
    kelly_fraction: float,
    k: float,
    floor: float = KELLY_RISK_FLOOR,
    cap: float = KELLY_RISK_CAP,
) -> float:
    """Map a (possibly negative) full-Kelly fraction to a capped risk-per-trade %.

    Args:
        kelly_fraction: Full-Kelly fraction f* (may be <= 0 for no edge).
        k:              Kelly multiplier (e.g. 0.25 or 0.5).
        floor:          Minimum allowed risk fraction.
        cap:            Maximum allowed risk fraction (hard safety cap).

    Returns:
        clamp(k * f*, floor, cap). If f* <= 0 the result is the floor.
    """
    raw = max(0.0, kelly_fraction) * float(k)
    return float(min(cap, max(floor, raw)))


def subwindow_max_drawdown(equity_curve, start: str, end: str) -> Optional[float]:
    """Max drawdown (positive fraction) computed within [start, end] of an equity curve.

    Args:
        equity_curve: pandas Series indexed by datetime.
        start:        ISO start date (inclusive).
        end:          ISO end date (inclusive).

    Returns:
        Max drawdown as a positive fraction (e.g. 0.18 = 18%), or None if the
        window has < 2 points.
    """
    try:
        import pandas as pd  # local import keeps pure-helper module import light
    except Exception:  # pragma: no cover
        return None
    if equity_curve is None or len(equity_curve) < 2:
        return None
    if not isinstance(equity_curve.index, pd.DatetimeIndex):
        return None
    window = equity_curve.loc[start:end]
    if window is None or len(window) < 2:
        return None
    running_max = window.cummax()
    dd = (window - running_max) / running_max
    return float(abs(dd.min()))


def evaluate_gate(
    arm_metrics: Dict[str, float],
    baseline_metrics: Dict[str, float],
    soft_pts: float = DD_TOLERANCE_SOFT_PTS,
    hard_pts: float = DD_TOLERANCE_HARD_PTS,
) -> Dict[str, Any]:
    """Evaluate the board sizing gate for one arm vs the baseline.

    Gate:
        * Sharpe must improve vs baseline (risk-adjusted return).
        * Max drawdown must not be worse than baseline by > the tolerance band.

    Args:
        arm_metrics:      {sharpe, max_drawdown, cagr, ...} for the candidate arm.
        baseline_metrics: same keys for the baseline arm.
        soft_pts:         soft DD degradation threshold (percentage points).
        hard_pts:         hard DD degradation threshold (percentage points).

    Returns:
        Dict with sharpe_delta, dd_degradation_pts, breaches_soft, breaches_hard,
        sharpe_improved, and overall gate_pass (sharpe improved AND DD within hard band).
    """
    sharpe_delta = float(arm_metrics.get("sharpe", 0.0)) - float(
        baseline_metrics.get("sharpe", 0.0)
    )
    # Drawdown degradation in percentage points (positive = worse than baseline).
    dd_arm = float(arm_metrics.get("max_drawdown", 0.0)) * 100.0
    dd_base = float(baseline_metrics.get("max_drawdown", 0.0)) * 100.0
    dd_degradation_pts = dd_arm - dd_base

    breaches_soft = dd_degradation_pts > soft_pts
    breaches_hard = dd_degradation_pts > hard_pts
    sharpe_improved = sharpe_delta > 0.0

    return {
        "sharpe_delta": round(sharpe_delta, 4),
        "dd_degradation_pts": round(dd_degradation_pts, 3),
        "breaches_soft_band": bool(breaches_soft),
        "breaches_hard_band": bool(breaches_hard),
        "sharpe_improved": bool(sharpe_improved),
        "gate_pass": bool(sharpe_improved and not breaches_hard),
    }


def build_arm_config(base_config: dict, arm: str, kelly_base_risk: Optional[float] = None) -> dict:
    """Return a deep-copied config mutated for the requested sizing arm.

    Never mutates ``base_config`` in place. Only the sizing blocks are touched;
    strategy params, regime/macro scaling, fees, and risk caps are left intact so
    they remain constant controls across arms.

    Args:
        base_config:     The active config (treated read-only).
        arm:             Arm name (see module docstring).
        kelly_base_risk: Required for fractional-Kelly arms — the per-trade risk
                         fraction to apply as flat sizing.

    Returns:
        A new config dict for this arm.
    """
    cfg = copy.deepcopy(base_config)
    cfg.setdefault("dynamic_sizing", {})
    cfg.setdefault("vol_scaling", {})
    base_flat = cfg.get("risk", {}).get("max_risk_per_trade_pct", 0.005)

    if arm == "baseline_fixed":
        cfg["dynamic_sizing"]["enabled"] = False
        cfg["dynamic_sizing"]["base_risk_pct"] = base_flat
        cfg["vol_scaling"]["enabled"] = False

    elif arm == "live_as_configured":
        # Leave exactly as deployed (vol_scaling + dynamic_sizing as configured).
        pass

    elif arm == "vol_target_only":
        cfg["vol_scaling"]["enabled"] = True
        cfg["dynamic_sizing"]["enabled"] = False
        cfg["dynamic_sizing"]["base_risk_pct"] = base_flat

    elif arm == "dd_scaling_only":
        # Graduated drawdown de-risking only; no ATR-vol scaling, no portfolio vol target.
        cfg["vol_scaling"]["enabled"] = False
        cfg["dynamic_sizing"]["enabled"] = True
        cfg["dynamic_sizing"]["base_risk_pct"] = base_flat
        cfg["dynamic_sizing"].setdefault("volatility_scaling", {})["enabled"] = False
        cfg["dynamic_sizing"].setdefault("confidence_scaling", {})["enabled"] = False
        cfg["dynamic_sizing"].setdefault("equity_curve_scaling", {})["enabled"] = True

    elif arm == "risk_mult_1.5x":
        # Flat sizing at 1.5x baseline risk (sub-cap sensitivity point).
        cfg["dynamic_sizing"]["enabled"] = False
        cfg["dynamic_sizing"]["base_risk_pct"] = round(base_flat * 1.5, 6)
        cfg["vol_scaling"]["enabled"] = False

    elif arm == "risk_mult_2.0x":
        # Flat sizing at 2.0x baseline risk (sub-cap sensitivity point).
        cfg["dynamic_sizing"]["enabled"] = False
        cfg["dynamic_sizing"]["base_risk_pct"] = round(base_flat * 2.0, 6)
        cfg["vol_scaling"]["enabled"] = False

    elif arm in ("frac_kelly_0.25x", "frac_kelly_0.5x"):
        if kelly_base_risk is None:
            raise ValueError(f"{arm} requires kelly_base_risk")
        cfg["dynamic_sizing"]["enabled"] = False
        cfg["dynamic_sizing"]["base_risk_pct"] = float(kelly_base_risk)
        cfg["vol_scaling"]["enabled"] = False

    else:
        raise ValueError(f"unknown arm: {arm}")

    return cfg


# ---------------------------------------------------------------------------
# Backtest execution (heavy — runs in worker processes)
# ---------------------------------------------------------------------------

@dataclass
class ArmResult:
    arm: str
    base_risk_pct: float
    metrics: Dict[str, Any] = field(default_factory=dict)
    subwindow_dd_2024_2025: Optional[float] = None
    error: Optional[str] = None


def _load_sp500_data(quick: bool = False) -> Dict[str, Any]:
    """Load cached SP500 OHLCV for universe tickers (read-only)."""
    import pandas as pd
    from universe.builder import get_universe_tickers

    tickers = get_universe_tickers("sp500")
    if quick:
        tickers = tickers[:40]
    cache = PROJECT_ROOT / "data" / "cache" / "sp500"
    data: Dict[str, Any] = {}
    for t in tickers:
        p = cache / (t.replace(".", "_") + ".parquet")
        if p.exists():
            data[t] = pd.read_parquet(p)
    return data


def _run_one_arm(arm: str, arm_config: dict, equity: Optional[float], quick: bool) -> ArmResult:
    """Run a single sizing arm end-to-end. Executed inside a worker process."""
    logging.disable(logging.CRITICAL)  # silence engine chatter in workers
    try:
        from backtest.engine import BacktestEngine
        from strategies.momentum_breakout import MomentumBreakout

        cfg = copy.deepcopy(arm_config)
        if equity is not None:
            cfg.setdefault("risk", {})["starting_equity"] = float(equity)

        data = _load_sp500_data(quick=quick)
        strat = MomentumBreakout(cfg)
        engine = BacktestEngine(cfg, market_id="sp500")
        res = engine.run_walkforward(data, [strat])

        m = dict(res.metrics or {})
        sub_dd = subwindow_max_drawdown(res.equity_curve, DD_WINDOW_START, DD_WINDOW_END)
        return ArmResult(
            arm=arm,
            base_risk_pct=float(cfg.get("dynamic_sizing", {}).get(
                "base_risk_pct", cfg.get("risk", {}).get("max_risk_per_trade_pct", 0.005))),
            metrics={
                "cagr": m.get("cagr", 0.0),
                "total_return": m.get("total_return", 0.0),
                "sharpe": m.get("sharpe", 0.0),
                "sortino": m.get("sortino", 0.0),
                "calmar": m.get("calmar", 0.0),
                "max_drawdown": m.get("max_drawdown", 0.0),
                "profit_factor": m.get("profit_factor", 0.0),
                "win_rate": m.get("win_rate", 0.0),
                "total_trades": m.get("total_trades", 0),
                "avg_trade": m.get("avg_trade", 0.0),
                "final_equity": m.get("final_equity", 0.0),
                "expectancy_r": m.get("expectancy_r", 0.0),
                "avg_winner_r": m.get("avg_winner_r", 0.0),
                "avg_loser_r": m.get("avg_loser_r", 0.0),
                "win_rate_r": m.get("win_rate_r", 0.0),
                "exposure": m.get("exposure", 0.0),
            },
            subwindow_dd_2024_2025=sub_dd,
        )
    except Exception as exc:  # pragma: no cover - defensive
        import traceback
        return ArmResult(arm=arm, base_risk_pct=0.0, error=f"{exc}\n{traceback.format_exc()}")


def _arm_worker(payload):
    arm, arm_config, equity, quick = payload
    return _run_one_arm(arm, arm_config, equity, quick)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_analysis(equity: Optional[float], quick: bool, workers: int) -> Dict[str, Any]:
    from utils.config import get_active_config

    base_config = get_active_config("sp500")  # read-only
    config_version = base_config.get("version", "unknown")

    # --- Wave 1: baseline + treatments that don't depend on Kelly ----------
    pre_kelly_arms = [
        "baseline_fixed",
        "live_as_configured",
        "vol_target_only",
        "dd_scaling_only",
        "risk_mult_1.5x",
        "risk_mult_2.0x",
    ]
    wave1_payloads = [
        (arm, build_arm_config(base_config, arm), equity, quick) for arm in pre_kelly_arms
    ]

    results: Dict[str, ArmResult] = {}
    with ProcessPoolExecutor(max_workers=min(workers, len(wave1_payloads))) as ex:
        futs = {ex.submit(_arm_worker, p): p[0] for p in wave1_payloads}
        for fut in as_completed(futs):
            r = fut.result()
            results[r.arm] = r
            status = "ERROR" if r.error else "ok"
            print(f"  [wave1] {r.arm}: {status}")

    baseline = results.get("baseline_fixed")
    if baseline is None or baseline.error:
        raise RuntimeError(f"baseline arm failed: {baseline.error if baseline else 'missing'}")

    # --- Estimate full-Kelly fraction from the baseline arm ----------------
    bm = baseline.metrics
    kelly_fraction = compute_kelly_fraction(
        win_rate=float(bm.get("win_rate_r", bm.get("win_rate", 0.0))),
        avg_winner_r=float(bm.get("avg_winner_r", 0.0)),
        avg_loser_r=float(bm.get("avg_loser_r", 0.0)),
    )
    risk_025 = fractional_kelly_risk_pct(kelly_fraction, 0.25)
    risk_050 = fractional_kelly_risk_pct(kelly_fraction, 0.50)

    print(
        f"  full-Kelly f*={kelly_fraction:.4f} -> "
        f"0.25x risk={risk_025*100:.3f}%, 0.5x risk={risk_050*100:.3f}% "
        f"(capped to [{KELLY_RISK_FLOOR*100:.2f}%,{KELLY_RISK_CAP*100:.2f}%])"
    )

    # --- Wave 2: fractional-Kelly arms -------------------------------------
    wave2_payloads = [
        ("frac_kelly_0.25x", build_arm_config(base_config, "frac_kelly_0.25x", risk_025), equity, quick),
        ("frac_kelly_0.5x", build_arm_config(base_config, "frac_kelly_0.5x", risk_050), equity, quick),
    ]
    with ProcessPoolExecutor(max_workers=min(workers, len(wave2_payloads))) as ex:
        futs = {ex.submit(_arm_worker, p): p[0] for p in wave2_payloads}
        for fut in as_completed(futs):
            r = fut.result()
            results[r.arm] = r
            status = "ERROR" if r.error else "ok"
            print(f"  [wave2] {r.arm}: {status}")

    # --- Gate evaluation vs baseline ---------------------------------------
    baseline_metrics = baseline.metrics
    arm_order = [
        "baseline_fixed",
        "live_as_configured",
        "vol_target_only",
        "dd_scaling_only",
        "risk_mult_1.5x",
        "risk_mult_2.0x",
        "frac_kelly_0.25x",
        "frac_kelly_0.5x",
    ]
    arms_out = []
    for arm in arm_order:
        r = results.get(arm)
        if r is None:
            continue
        gate = (
            evaluate_gate(r.metrics, baseline_metrics)
            if arm != "baseline_fixed" and not r.error
            else None
        )
        arms_out.append(
            {
                "arm": arm,
                "base_risk_pct": r.base_risk_pct,
                "error": r.error,
                "metrics": r.metrics,
                "subwindow_dd_2024_2025": r.subwindow_dd_2024_2025,
                "gate_vs_baseline": gate,
            }
        )

    return {
        "task": "#387 volatility-aware / fractional-Kelly sizing analysis",
        "mode": "paper_backtest_only",
        "generated_at": datetime.now().isoformat(),
        "config_version": config_version,
        "starting_equity": equity,
        "quick_mode": quick,
        "dd_window": {"start": DD_WINDOW_START, "end": DD_WINDOW_END},
        "kelly": {
            "full_kelly_fraction": kelly_fraction,
            "risk_0.25x": risk_025,
            "risk_0.5x": risk_050,
            "floor": KELLY_RISK_FLOOR,
            "cap": KELLY_RISK_CAP,
            "source": "baseline_fixed arm realized R-multiples",
        },
        "dd_tolerance_pts": {"soft": DD_TOLERANCE_SOFT_PTS, "hard": DD_TOLERANCE_HARD_PTS},
        "arms": arms_out,
    }


def _fmt_pct(x: float) -> str:
    return f"{x*100:+.2f}%"


def print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 88)
    print(f"  #387 SIZING ANALYSIS — SP500 momentum_breakout (config {report['config_version']})")
    print(f"  equity=${report['starting_equity']}, quick={report['quick_mode']}")
    print("=" * 88)
    hdr = f"{'arm':<20} {'risk%':>7} {'CAGR':>8} {'Sharpe':>7} {'Sortino':>8} {'maxDD':>7} {'PF':>6} {'trades':>7} {'DD24-25':>8}"
    print(hdr)
    print("-" * len(hdr))
    for a in report["arms"]:
        if a["error"]:
            print(f"{a['arm']:<20}  ERROR: {a['error'].splitlines()[0][:50]}")
            continue
        m = a["metrics"]
        sub = a["subwindow_dd_2024_2025"]
        sub_s = f"{sub*100:.2f}%" if sub is not None else "n/a"
        print(
            f"{a['arm']:<20} {a['base_risk_pct']*100:>6.3f}% "
            f"{m['cagr']*100:>7.2f}% {m['sharpe']:>7.3f} {m['sortino']:>8.3f} "
            f"{m['max_drawdown']*100:>6.2f}% {m['profit_factor']:>6.2f} "
            f"{int(m['total_trades']):>7d} {sub_s:>8}"
        )
    print("-" * len(hdr))
    print("Gate vs baseline (Sharpe improved AND maxDD degradation <= hard 5pts):")
    for a in report["arms"]:
        g = a.get("gate_vs_baseline")
        if not g:
            continue
        verdict = "PASS" if g["gate_pass"] else "FAIL"
        print(
            f"  {a['arm']:<20} {verdict:>4} | dSharpe={g['sharpe_delta']:+.3f} "
            f"ddDeg={g['dd_degradation_pts']:+.2f}pts "
            f"(soft>{report['dd_tolerance_pts']['soft']}={g['breaches_soft_band']}, "
            f"hard>{report['dd_tolerance_pts']['hard']}={g['breaches_hard_band']})"
        )


def main(argv=None):
    ap = argparse.ArgumentParser(description="#387 fractional-Kelly / vol-aware sizing analysis (backtest-only)")
    ap.add_argument("--equity", type=float, default=25000.0,
                    help="Starting equity for the backtest (default 25000; use 971 for live-slice sensitivity)")
    ap.add_argument("--quick", action="store_true", help="40-ticker smoke run")
    ap.add_argument("--workers", type=int, default=6, help="Parallel worker processes")
    ap.add_argument("--out", type=str, default=None, help="Output JSON path")
    args = ap.parse_args(argv)

    print(f"Running #387 sizing analysis (equity=${args.equity:,.0f}, quick={args.quick})...")
    report = run_analysis(equity=args.equity, quick=args.quick, workers=args.workers)
    print_summary(report)

    results_dir = PROJECT_ROOT / "backtest" / "results"
    results_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out) if args.out else results_dir / f"fractional_kelly_sizing_{ts}.json"
    with open(out, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nResults JSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
