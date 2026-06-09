#!/usr/bin/env python3
"""Additive SP500 Strategy Validation (Task #388)

Backtest/OOS-style analysis ONLY — makes NO live config, broker, or systemd
changes. Evaluates whether ONE complementary strategy improves the live SP500
portfolio when added to the current `momentum_breakout`-only active config.

What it does
------------
1. Loads the LIVE active SP500 config (read-only, deep-copied in memory) and
   the cached SP500 market data.
2. Runs, in parallel across cores, a set of walk-forward backtests:
     - baseline_full   : active config as-is (momentum_breakout only)
     - combined_full   : baseline + candidate strategy enabled
     - solo_candidate  : candidate strategy alone
     - baseline_h1/h2  : baseline on an in-sample / out-of-sample time split
     - combined_h1/h2  : combined on the same split (holdout consistency)
3. Estimates per-strategy return correlation two ways:
     - PRIMARY  : the engine's native `calc_strategy_correlation` matrix from
                  the combined run (daily P&L attribution → Pearson).
     - CROSS-CHK: correlation of the solo equity-curve return streams.
4. Evaluates additive gates (Sharpe, profit factor, max drawdown, correlation,
   OOS-half consistency) and prints + writes a verdict.

Candidate params come from the LIVE active config block (the maintained,
falling-knife-guarded v3.2.4 mean_reversion params), NOT the riskier
research/best JSON params — we validate what would actually be deployed.

Usage:
    python3 scripts/analyze_additive_sp500_strategy.py \
        --candidate mean_reversion --market sp500 \
        --output backtest/results/additive_sp500_mean_reversion.json

This script is intentionally self-contained and reuses the canonical strategy
factory + data loader from scripts/strategy_evaluator.py so its backtests match
the production research path exactly.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import numpy as np
import pandas as pd

# Reuse the canonical, production strategy factory + loaders so backtests match
# the research path used elsewhere (single source of truth for the registry).
from scripts.strategy_evaluator import (  # noqa: E402
    get_strategy_class,
    load_market_data,
    make_config_with_strategy,
)
from utils.config import get_active_config  # noqa: E402
from backtest.engine import BacktestEngine  # noqa: E402
from backtest.metrics import calc_strategy_correlation  # noqa: E402


# ── Module globals for fork-based parallelism ───────────────────────────────
# On Linux (fork start method) child processes inherit this via copy-on-write,
# so we load the (large) market data exactly once in the parent.
_DATA: Optional[Dict[str, pd.DataFrame]] = None


# ═════════════════════════════════════════════════════════════════════════
# Pure, importable helpers (unit-tested in tests/test_additive_sp500_strategy_analysis.py)
# ═════════════════════════════════════════════════════════════════════════
def slice_data(
    data: Dict[str, pd.DataFrame],
    start: Optional[str] = None,
    end: Optional[str] = None,
    min_rows: int = 100,
) -> Dict[str, pd.DataFrame]:
    """Return a date-windowed copy of ``data`` keeping only tickers with enough rows.

    Args:
        data:     ticker -> DataFrame indexed by DatetimeIndex.
        start:    inclusive lower bound (ISO date) or None.
        end:      inclusive upper bound (ISO date) or None.
        min_rows: drop tickers with fewer than this many rows after slicing.
    """
    if start is None and end is None:
        return data
    lo = pd.Timestamp(start) if start else None
    hi = pd.Timestamp(end) if end else None
    out: Dict[str, pd.DataFrame] = {}
    for ticker, df in data.items():
        sub = df
        if lo is not None:
            sub = sub[sub.index >= lo]
        if hi is not None:
            sub = sub[sub.index <= hi]
        if len(sub) >= min_rows:
            out[ticker] = sub
    return out


def equity_returns(eq: pd.Series) -> pd.Series:
    """Daily simple returns from an equity curve, indexed by date."""
    if eq is None or len(eq) < 2:
        return pd.Series(dtype=float)
    s = pd.Series(eq).astype(float).sort_index()
    return s.pct_change().dropna()


def series_return_correlation(
    eq_a: pd.Series,
    eq_b: pd.Series,
    active_only: bool = True,
) -> Optional[float]:
    """Pearson correlation of two equity-curve return streams.

    Aligns on common dates. If ``active_only``, restricts to days where at least
    one of the two streams has a non-zero return (mirrors the engine's
    active-day filter so flat/no-position days don't inflate correlation).

    Returns None if there is insufficient overlap or zero variance.
    """
    ra = equity_returns(eq_a)
    rb = equity_returns(eq_b)
    if ra.empty or rb.empty:
        return None
    df = pd.DataFrame({"a": ra, "b": rb}).dropna()
    if active_only:
        df = df[(df["a"].abs() > 1e-12) | (df["b"].abs() > 1e-12)]
    if len(df) < 10:
        return None
    if df["a"].std() == 0 or df["b"].std() == 0:
        return None
    return float(df["a"].corr(df["b"]))


def extract_pair_correlation(
    strategy_correlation: Dict[str, Any],
    strat_a: str,
    strat_b: str,
) -> Optional[float]:
    """Pull the pairwise correlation for (a, b) from a calc_strategy_correlation dict."""
    if not strategy_correlation:
        return None
    names = strategy_correlation.get("strategies") or []
    matrix = strategy_correlation.get("matrix") or []
    if strat_a not in names or strat_b not in names:
        return None
    ia, ib = names.index(strat_a), names.index(strat_b)
    if ia >= len(matrix) or ib >= len(matrix[ia]):
        return None
    return float(matrix[ia][ib])


def window_consistency(
    baseline_windows: List[Dict[str, Any]],
    combined_windows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Compare per-walk-forward-window returns of combined vs baseline.

    Windows are aligned by their ``test_start`` (identical across runs because
    the same data/backtest config is used). Returns a dict with the fraction of
    windows where combined's window return >= baseline's, plus the per-window
    return correlation.
    """
    def _ret(w: Dict[str, Any]) -> Optional[float]:
        es = w.get("equity_start")
        ee = w.get("equity_end")
        if not es:
            return None
        return (ee - es) / es

    def _key(w: Dict[str, Any]) -> str:
        return str(w.get("test_start"))

    b_by = {_key(w): _ret(w) for w in baseline_windows}
    c_by = {_key(w): _ret(w) for w in combined_windows}
    common = [k for k in b_by if k in c_by and b_by[k] is not None and c_by[k] is not None]
    if not common:
        return {"n_windows": 0, "combined_ge_baseline_pct": None, "window_return_corr": None}
    b_vals = np.array([b_by[k] for k in common])
    c_vals = np.array([c_by[k] for k in common])
    ge = float(np.mean(c_vals >= b_vals) * 100.0)
    corr = None
    if len(common) >= 3 and b_vals.std() > 0 and c_vals.std() > 0:
        corr = float(np.corrcoef(b_vals, c_vals)[0, 1])
    return {
        "n_windows": len(common),
        "combined_ge_baseline_pct": round(ge, 1),
        "window_return_corr": round(corr, 4) if corr is not None else None,
        "baseline_mean_window_return_pct": round(float(b_vals.mean()) * 100, 4),
        "combined_mean_window_return_pct": round(float(c_vals.mean()) * 100, 4),
    }


def evaluate_gates(
    baseline: Dict[str, Any],
    combined: Dict[str, Any],
    solo: Dict[str, Any],
    corr_primary: Optional[float],
    oos_combined: Dict[str, Any],
    oos_baseline: Dict[str, Any],
    *,
    min_oos_sharpe: float = 0.6,
    min_profit_factor: float = 1.2,
    max_drawdown_pct: float = 15.0,
    max_correlation: float = 0.7,
) -> Dict[str, Any]:
    """Apply the additive-strategy gates and return a structured verdict.

    Gates (honest, multi-lens — additive value is judged on the COMBINED
    portfolio, not the candidate alone):
      G1 Sharpe improves    : combined.sharpe > baseline.sharpe
      G2 OOS Sharpe >= min  : combined.sharpe >= min_oos_sharpe (full WF is OOS by construction)
      G3 Profit factor      : combined.profit_factor >= min_profit_factor
      G4 Max drawdown       : combined.max_dd <= max_drawdown_pct OR <= baseline.max_dd (not materially worse)
      G5 Correlation        : |corr_primary| < max_correlation
      G6 OOS-half consistency: combined beats baseline (Sharpe) on the 2024-2025 holdout half
    """
    def g(d: Dict[str, Any], k: str, default: float = 0.0) -> float:
        v = d.get(k, default)
        return float(v) if v is not None else default

    base_sharpe = g(baseline, "sharpe")
    comb_sharpe = g(combined, "sharpe")
    comb_pf = g(combined, "profit_factor")
    comb_dd = g(combined, "max_drawdown_pct")
    base_dd = g(baseline, "max_drawdown_pct")
    oos_comb_sharpe = g(oos_combined, "sharpe")
    oos_base_sharpe = g(oos_baseline, "sharpe")

    gates = {}
    gates["G1_sharpe_improves"] = {
        "pass": comb_sharpe > base_sharpe,
        "detail": f"combined {comb_sharpe:.4f} vs baseline {base_sharpe:.4f}",
    }
    gates["G2_oos_sharpe_min"] = {
        "pass": comb_sharpe >= min_oos_sharpe,
        "detail": f"combined full walk-forward Sharpe {comb_sharpe:.4f} >= {min_oos_sharpe}",
    }
    gates["G3_profit_factor"] = {
        "pass": comb_pf >= min_profit_factor,
        "detail": f"combined PF {comb_pf:.4f} >= {min_profit_factor}",
    }
    dd_ok = (comb_dd <= max_drawdown_pct) or (comb_dd <= base_dd + 1e-9)
    gates["G4_max_drawdown"] = {
        "pass": dd_ok,
        "detail": (
            f"combined maxDD {comb_dd:.2f}% (gate <= {max_drawdown_pct}% OR "
            f"<= baseline {base_dd:.2f}%)"
        ),
    }
    corr_ok = (corr_primary is not None) and (abs(corr_primary) < max_correlation)
    gates["G5_correlation"] = {
        "pass": corr_ok,
        "detail": (
            f"|corr| {abs(corr_primary):.4f} < {max_correlation}"
            if corr_primary is not None
            else "correlation unavailable"
        ),
    }
    gates["G6_oos_half_consistency"] = {
        "pass": oos_comb_sharpe > oos_base_sharpe,
        "detail": f"OOS-half combined Sharpe {oos_comb_sharpe:.4f} vs baseline {oos_base_sharpe:.4f}",
    }

    n_pass = sum(1 for v in gates.values() if v["pass"])
    # Promotion-grade requires ALL gates. Anything less => NO promotion.
    promote = all(v["pass"] for v in gates.values())
    # "Diversifier-but-weak" note: improves portfolio + low corr, but misses
    # absolute Sharpe/PF bars.
    improves_and_uncorrelated = (
        gates["G1_sharpe_improves"]["pass"] and gates["G5_correlation"]["pass"]
    )
    return {
        "gates": gates,
        "n_pass": n_pass,
        "n_total": len(gates),
        "promote": promote,
        "improves_and_uncorrelated": improves_and_uncorrelated,
        "verdict": "PROMOTE" if promote else "NO-PROMOTE",
    }


# ═════════════════════════════════════════════════════════════════════════
# Backtest execution
# ═════════════════════════════════════════════════════════════════════════
def _metrics_subset(m: Dict[str, Any]) -> Dict[str, Any]:
    cagr = m.get("cagr", 0) or 0
    cagr_pct = cagr * 100 if abs(cagr) < 2 else cagr
    return {
        "total_trades": m.get("total_trades", 0),
        "cagr_pct": round(cagr_pct, 4),
        "sharpe": round(m.get("sharpe", 0) or 0, 4),
        "sortino": round(m.get("sortino", 0) or 0, 4),
        "max_drawdown_pct": round((m.get("max_drawdown", 0) or 0) * 100, 4),
        "win_rate_pct": round((m.get("win_rate", 0) or 0) * 100, 2),
        "profit_factor": round(m.get("profit_factor", 0) or 0, 4),
        "total_pnl": round(m.get("total_pnl", 0) or 0, 2),
        "avg_trade": round(m.get("avg_trade", 0) or 0, 2),
        "final_equity": round(m.get("final_equity", 0) or 0, 2),
        "expectancy_r": round(m.get("expectancy_r", 0) or 0, 4),
        "edge_p_value": m.get("edge_p_value", 1.0),
        "edge_significant": bool(m.get("edge_significant", False)),
        "calmar": round(m.get("calmar", 0) or 0, 4),
    }


def _run_engine(cfg: Dict[str, Any], data: Dict[str, pd.DataFrame]) -> BacktestEngine:
    strategies = []
    for name, scfg in cfg.get("strategies", {}).items():
        if isinstance(scfg, dict) and scfg.get("enabled", False):
            strategies.append(get_strategy_class(name)(cfg))
    if not strategies:
        raise ValueError("No strategies enabled in config")
    engine = BacktestEngine(cfg)
    return engine.run_walkforward(data, strategies)


def _worker(task: Tuple[str, Dict[str, Any], Optional[str], Optional[str]]) -> Dict[str, Any]:
    """Run one backtest. Reads market data from the inherited module global."""
    label, cfg, start, end = task
    t0 = time.time()
    data = slice_data(_DATA, start, end)
    result = _run_engine(cfg, data)
    eq = result.equity_curve
    eq_payload = None
    if eq is not None and len(eq) > 0:
        eq_payload = {
            "dates": [pd.Timestamp(d).strftime("%Y-%m-%d") for d in eq.index],
            "values": [float(v) for v in eq.values],
        }
    return {
        "label": label,
        "metrics": _metrics_subset(result.metrics),
        "strategy_correlation": result.metrics.get("strategy_correlation", {}),
        "strategy_breakdown": _per_strategy_breakdown(result.trades),
        "walk_forward_windows": result.walk_forward_windows,
        "equity_curve": eq_payload,
        "n_tickers": len(data),
        "runtime_s": round(time.time() - t0, 1),
    }


def _per_strategy_breakdown(trades: List[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for t in trades or []:
        s = t.get("strategy", "unknown")
        pnl = t.get("pnl") or 0
        d = out.setdefault(s, {"trades": 0, "total_pnl": 0.0, "wins": 0})
        d["trades"] += 1
        d["total_pnl"] += pnl
        if pnl > 0:
            d["wins"] += 1
    for s, d in out.items():
        d["total_pnl"] = round(d["total_pnl"], 2)
        d["win_rate_pct"] = round(d["wins"] / d["trades"] * 100, 1) if d["trades"] else 0.0
        del d["wins"]
    return out


def _eq_series(payload: Optional[Dict[str, Any]]) -> pd.Series:
    if not payload:
        return pd.Series(dtype=float)
    idx = pd.to_datetime(payload["dates"])
    return pd.Series(payload["values"], index=idx)


# ═════════════════════════════════════════════════════════════════════════
# Orchestration
# ═════════════════════════════════════════════════════════════════════════
def main() -> int:
    ap = argparse.ArgumentParser(description="Additive SP500 strategy validation (backtest/OOS only)")
    ap.add_argument("--candidate", default="mean_reversion", help="Candidate strategy name")
    ap.add_argument("--market", default="sp500", help="Market id")
    ap.add_argument("--split-date", default="2024-01-01",
                    help="IS/OOS split date (OOS half = >= this date)")
    ap.add_argument("--output", default=None, help="Output JSON path")
    ap.add_argument("--max-workers", type=int, default=9)
    ap.add_argument("--alt-max-positions", type=int, default=None,
                    help="If set, also run baseline+combined at this max_open_positions "
                         "as a capacity-relief diagnostic (in-memory only).")
    args = ap.parse_args()

    global _DATA
    candidate = args.candidate
    market = args.market

    print(f"[load] active config + data for market={market} ...")
    base_cfg = get_active_config(market)
    _DATA = load_market_data(market)
    n_tickers = len(_DATA)
    all_dates = sorted({d for df in _DATA.values() for d in (df.index.min(), df.index.max())})
    data_start, data_end = str(all_dates[0].date()), str(all_dates[-1].date())
    print(f"[load] {n_tickers} tickers, data {data_start} -> {data_end}")

    # Config variants (deep-copied, in-memory only — never written to disk).
    baseline_cfg = copy.deepcopy(base_cfg)
    combined_cfg = make_config_with_strategy(base_cfg, candidate, solo=False)
    solo_cfg = make_config_with_strategy(base_cfg, candidate, solo=True)

    split = args.split_date
    oos_lo = split
    is_hi = (pd.Timestamp(split) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

    tasks: List[Tuple[str, Dict[str, Any], Optional[str], Optional[str]]] = [
        ("baseline_full", baseline_cfg, None, None),
        ("combined_full", combined_cfg, None, None),
        ("solo_candidate_full", solo_cfg, None, None),
        ("baseline_is", baseline_cfg, None, is_hi),
        ("combined_is", combined_cfg, None, is_hi),
        ("baseline_oos", baseline_cfg, oos_lo, None),
        ("combined_oos", combined_cfg, oos_lo, None),
    ]

    # Optional capacity-relief diagnostic: does raising the portfolio position
    # cap let the (uncorrelated) candidate's trades actually be harvested?
    # In-memory only — never written to the live config.
    if args.alt_max_positions:
        cap = int(args.alt_max_positions)
        base_alt = copy.deepcopy(baseline_cfg)
        comb_alt = copy.deepcopy(combined_cfg)
        base_alt.setdefault("risk", {})["max_open_positions"] = cap
        comb_alt.setdefault("risk", {})["max_open_positions"] = cap
        tasks.append((f"baseline_cap{cap}", base_alt, None, None))
        tasks.append((f"combined_cap{cap}", comb_alt, None, None))

    print(f"[run] {len(tasks)} walk-forward backtests in parallel "
          f"(<= {args.max_workers} workers) ...")
    t0 = time.time()
    results: Dict[str, Dict[str, Any]] = {}
    with ProcessPoolExecutor(max_workers=args.max_workers) as ex:
        for r in ex.map(_worker, tasks):
            results[r["label"]] = r
            m = r["metrics"]
            print(f"  [{r['label']:20s}] trades={m['total_trades']:4d} "
                  f"Sharpe={m['sharpe']:+.4f} PF={m['profit_factor']:.3f} "
                  f"maxDD={m['max_drawdown_pct']:.2f}% PnL=${m['total_pnl']:.2f} "
                  f"({r['runtime_s']:.0f}s, n={r['n_tickers']})")
    print(f"[run] all backtests done in {time.time() - t0:.0f}s wall")

    # ── Correlation estimates ───────────────────────────────────────────────
    comb = results["combined_full"]
    corr_primary = extract_pair_correlation(
        comb["strategy_correlation"], "momentum_breakout", candidate
    )
    # Cross-check: solo equity-curve return correlation
    corr_xcheck = series_return_correlation(
        _eq_series(results["baseline_full"]["equity_curve"]),     # solo MB == baseline
        _eq_series(results["solo_candidate_full"]["equity_curve"]),
    )

    # ── Walk-forward window consistency (full window) ───────────────────────
    wf = window_consistency(
        results["baseline_full"]["walk_forward_windows"],
        results["combined_full"]["walk_forward_windows"],
    )

    # ── Gate evaluation ─────────────────────────────────────────────────────
    verdict = evaluate_gates(
        baseline=results["baseline_full"]["metrics"],
        combined=results["combined_full"]["metrics"],
        solo=results["solo_candidate_full"]["metrics"],
        corr_primary=corr_primary,
        oos_combined=results["combined_oos"]["metrics"],
        oos_baseline=results["baseline_oos"]["metrics"],
    )

    # ── Optional capacity-relief diagnostic summary ─────────────────────────
    capacity_diag = None
    cap_labels = [k for k in results if k.startswith("combined_cap")]
    if cap_labels:
        cap_label = cap_labels[0]
        cap = cap_label.replace("combined_cap", "")
        base_label = f"baseline_cap{cap}"
        cm = results[cap_label]["metrics"]
        bm = results[base_label]["metrics"]
        capacity_diag = {
            "max_open_positions": int(cap),
            "baseline": bm,
            "combined": cm,
            "baseline_breakdown": results[base_label]["strategy_breakdown"],
            "combined_breakdown": results[cap_label]["strategy_breakdown"],
            "delta": {
                k: round((cm.get(k, 0) or 0) - (bm.get(k, 0) or 0), 4)
                for k in ("sharpe", "profit_factor", "max_drawdown_pct",
                          "total_pnl", "total_trades")
            },
            "note": ("In-memory diagnostic ONLY (NOT a config change). Tests whether "
                     "raising the position cap lets the uncorrelated candidate's "
                     "trades be harvested additively."),
        }

    report = {
        "task": "#388 additive SP500 strategy validation",
        "mode": "backtest_oos_only__NO_LIVE_CHANGES",
        "candidate": candidate,
        "market": market,
        "config_version": base_cfg.get("version"),
        "candidate_params_source": "live active config block (maintained v3.2.4)",
        "n_tickers": n_tickers,
        "data_start": data_start,
        "data_end": data_end,
        "split_date": split,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "results": {
            label: {
                "metrics": r["metrics"],
                "strategy_breakdown": r["strategy_breakdown"],
                "n_tickers": r["n_tickers"],
            }
            for label, r in results.items()
        },
        "deltas_full": {
            k: round(
                (results["combined_full"]["metrics"].get(k, 0) or 0)
                - (results["baseline_full"]["metrics"].get(k, 0) or 0),
                4,
            )
            for k in ("sharpe", "sortino", "profit_factor", "max_drawdown_pct",
                      "cagr_pct", "win_rate_pct", "total_pnl", "total_trades")
        },
        "correlation": {
            "primary_engine_daily_pnl": round(corr_primary, 4) if corr_primary is not None else None,
            "crosscheck_solo_equity_returns": round(corr_xcheck, 4) if corr_xcheck is not None else None,
            "concentrated_pairs": comb["strategy_correlation"].get("concentrated_pairs", []),
            "method": "engine calc_strategy_correlation (daily P&L attribution, Pearson)",
        },
        "walk_forward_consistency_full": wf,
        "capacity_diagnostic": capacity_diag,
        "verdict": verdict,
    }

    out_path = args.output or f"backtest/results/additive_{market}_{candidate}.json"
    out_p = Path(out_path)
    if not out_p.is_absolute():
        out_p = PROJECT / out_p
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with open(out_p, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _print_summary(report)
    print(f"\n[saved] {out_p}")
    return 0


def _print_summary(report: Dict[str, Any]) -> None:
    print("\n" + "=" * 72)
    print(f"ADDITIVE VALIDATION — {report['candidate']} on {report['market']} "
          f"(config {report['config_version']})")
    print("=" * 72)
    r = report["results"]
    for label in ("baseline_full", "combined_full", "solo_candidate_full",
                  "baseline_oos", "combined_oos"):
        m = r[label]["metrics"]
        print(f"  {label:20s} Sharpe={m['sharpe']:+.4f} PF={m['profit_factor']:.3f} "
              f"maxDD={m['max_drawdown_pct']:6.2f}% trades={m['total_trades']:4d} "
              f"PnL=${m['total_pnl']:.2f}")
    d = report["deltas_full"]
    print("\n  Delta (combined - baseline, full):")
    print(f"    Sharpe {d['sharpe']:+.4f} | PF {d['profit_factor']:+.4f} | "
          f"maxDD {d['max_drawdown_pct']:+.2f}% | PnL ${d['total_pnl']:+.2f}")
    c = report["correlation"]
    print(f"\n  Correlation MB↔{report['candidate']}: "
          f"primary={c['primary_engine_daily_pnl']} "
          f"crosscheck={c['crosscheck_solo_equity_returns']}")
    print("\n  Gates:")
    for name, gv in report["verdict"]["gates"].items():
        mark = "✅" if gv["pass"] else "❌"
        print(f"    {mark} {name}: {gv['detail']}")
    cd = report.get("capacity_diagnostic")
    if cd:
        b, c = cd["baseline"], cd["combined"]
        dd = cd["delta"]
        print(f"\n  Capacity diagnostic (max_open_positions={cd['max_open_positions']}, in-memory only):")
        print(f"    baseline Sharpe={b['sharpe']:+.4f} PF={b['profit_factor']:.3f} "
              f"maxDD={b['max_drawdown_pct']:.2f}% PnL=${b['total_pnl']:.2f}")
        print(f"    combined Sharpe={c['sharpe']:+.4f} PF={c['profit_factor']:.3f} "
              f"maxDD={c['max_drawdown_pct']:.2f}% PnL=${c['total_pnl']:.2f}")
        print(f"    delta Sharpe {dd['sharpe']:+.4f} | PF {dd['profit_factor']:+.4f} | "
              f"PnL ${dd['total_pnl']:+.2f} | trades {int(dd['total_trades']):+d}")
        print(f"    candidate trades in combined: "
              f"{cd['combined_breakdown'].get(report['candidate'], {}).get('trades', 0)}")
    v = report["verdict"]
    print(f"\n  VERDICT: {v['verdict']}  ({v['n_pass']}/{v['n_total']} gates pass)")
    if not v["promote"] and v["improves_and_uncorrelated"]:
        print("  NOTE: improves portfolio Sharpe AND is uncorrelated, but misses "
              "absolute Sharpe/PF/DD bars — diversifier candidate, not promotion-grade.")


if __name__ == "__main__":
    sys.exit(main())
