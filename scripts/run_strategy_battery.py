#!/usr/bin/env python3
"""Full cross-OOS battery for a SANDBOX strategy, with a PARAM_GRID sweep.

Sweeps configs sampled from the strategy's PARAM_GRID, runs each full walk-forward backtest,
selects the best config (by CPCV median Sharpe), runs an IS/OOS time split on it, then scores
the cross-OOS battery (CPCV / PBO / effective-N DSR / regime / leave-one-group-out / forward)
with the SCREEN/PROMOTE tiers. The sweep IS the search, so DSR deflates by the sweep's
effective independent count (participation ratio). Sandbox/research only.

Usage:
  python3 scripts/run_strategy_battery.py --strategy cross_sectional_momentum \
      --market sp500 --grid-size 12 --max-positions 30 \
      --output-path backtest/results/battery_cross_sectional_momentum.json
"""
import argparse
import copy
import datetime
import importlib.util
import json
import multiprocessing
import os
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backtest.engine import BacktestEngine            # noqa: E402
from scripts.strategy_evaluator import STRATEGY_REGISTRY, load_sandbox_strategy  # noqa: E402
from utils.config import get_active_config             # noqa: E402
from research.cross_oos import adapter                 # noqa: E402
from research.cross_oos import metrics as cm           # noqa: E402
from research.cross_oos.deployment import deployment_sanity  # noqa: E402
from research.cross_oos.holdout import holdout_start_ts, evaluate_holdout, config_hash  # noqa: E402
from research.cross_oos import registry  # noqa: E402
import scripts.validate_oos as vo                      # noqa: E402


def load_param_grid(name: str) -> dict:
    p = PROJECT / "research" / "strategies" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sandbox_grid.{name}", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return getattr(m, "PARAM_GRID", {})


def make_strategy(name: str, cfg: dict):
    cls = STRATEGY_REGISTRY.get(name) or load_sandbox_strategy(name)
    return cls(cfg)


def run_bt(cfg: dict, name: str, data: dict):
    return BacktestEngine(cfg).run_walkforward(data, [make_strategy(name, cfg)])


# ── Parallel sweep workers (CPU-bounded; forked, nice'd) ──────────────────────
# The parent loads the universe ONCE into _BATTERY_DATA; forked workers inherit it
# copy-on-write (no reload, no pickling of ~200 DataFrames). Each worker re-precomputes
# its strategy columns per run (overwrite-safe), and COW keeps the parent's copy clean for
# the post-sweep IS/OOS runs. Concurrency is deliberately bounded + nice'd so a long sweep
# never saturates the VPS and trips its CPU governor.
_BATTERY_DATA = None


def _battery_init(nice_incr: int) -> None:
    try:
        os.nice(nice_incr)
    except Exception:
        pass


def _battery_run_config(payload):
    """Run one config's full-period backtest in a worker. Returns picklable results."""
    label, strategy, params, base_cfg, max_positions = payload
    cfg = copy.deepcopy(base_cfg)
    cfg.setdefault("strategies", {}).setdefault(strategy, {})
    cfg["strategies"][strategy].update(params)
    cfg["strategies"][strategy]["enabled"] = True
    cfg.setdefault("risk", {})["max_open_positions"] = max_positions
    res = BacktestEngine(cfg).run_walkforward(_BATTERY_DATA, [make_strategy(strategy, cfg)])
    r = adapter.daily_returns(res.equity_curve)
    paths = adapter.cpcv_path_sharpes(r) if len(r) > 10 else []
    cpcv = float(np.median(paths)) if paths else float("nan")
    return (label, r, res.trades, vo.extract_metrics(res), cpcv)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", required=True)
    ap.add_argument("--market", default="sp500")
    ap.add_argument("--grid-size", type=int, default=12)
    ap.add_argument("--max-positions", type=int, default=30)
    ap.add_argument("--pin", default="",
                    help='JSON of params to FIX on every config (removed from the sweep).')
    ap.add_argument("--pin-kv", default="",
                    help='Quote-free alternative: comma-separated key=value, '
                         'e.g. top_n=30,max_hold_days=90 (ints/floats auto-detected).')
    ap.add_argument("--select", choices=["default", "best_cpcv", "best_oos"], default="default",
                    help="How to pick the validated primary config. 'default' = the strategy's "
                         "PRE-REGISTERED defaults (no selection bias; grid only informs PBO/DSR). "
                         "'best_cpcv' = best full-period CPCV (selection-biased). 'best_oos' = best "
                         "held-out-window Sharpe.")
    ap.add_argument("--workers", type=int,
                    default=int(os.environ.get("ATLAS_BATTERY_WORKERS", 0)) or None,
                    help="Parallel backtest workers. Default: conservative min(3, cpu-1) to avoid "
                         "tripping the VPS CPU governor. Override via --workers or "
                         "ATLAS_BATTERY_WORKERS env.")
    ap.add_argument("--nice", type=int, default=10,
                    help="nice increment for workers (lower priority; default 10).")
    ap.add_argument("--output-path", required=True)
    ap.add_argument("--no-holdout-quarantine", action="store_true",
                    help="Search on FULL data (default: quarantine data >= config/holdout.json start). "
                         "Use only for explicit manual full-history runs, never the automated loop.")
    ap.add_argument("--holdout-eval", action="store_true",
                    help="If the search tier is PROMOTE, evaluate the frozen primary on the write-once "
                         "holdout ONCE (single-use ledger) and downgrade to FAIL if it does not clear.")
    a = ap.parse_args()
    return 0 if run_battery(a) is not None else 1


def run_battery(a):
    """Rail-equipped cross-OOS battery as a CALLABLE (single source of truth for the CLI AND the
    automated research loop). `a` is a namespace with battery args: strategy, market, grid_size,
    max_positions, pin, pin_kv, select, workers, nice, output_path, no_holdout_quarantine,
    holdout_eval. Returns the result dict (None if no configs completed). Rails 1/2/3 applied here so
    every caller (manual CLI or research_runner) gets quarantine + deployment-sanity + FDR bar."""
    _cpu = os.cpu_count() or 4
    workers = a.workers or min(3, max(1, _cpu - 1))   # conservative default
    pinned = json.loads(a.pin) if a.pin.strip() else {}
    if a.pin_kv.strip():
        for tok in a.pin_kv.split(","):
            k, _, v = tok.partition("=")
            k = k.strip(); v = v.strip()
            if not k:
                continue
            try:
                pinned[k] = int(v)
            except ValueError:
                try:
                    pinned[k] = float(v)
                except ValueError:
                    pinned[k] = v
    t0 = time.time()

    print(f"=== cross-OOS battery: {a.strategy} ({a.market}) ===", flush=True)
    data = vo.load_data(market=a.market)
    data = {k: v for k, v in data.items() if len(v) >= 260}
    # Rail 1: quarantine the write-once holdout from the search (default on).
    hs = holdout_start_ts()
    if hs is not None and not a.no_holdout_quarantine:
        data = {k: v[v.index < hs] for k, v in data.items() if len(v[v.index < hs]) >= 260}
        print(f"holdout quarantine ON: search on data < {hs.date()} ({len(data)} tickers)", flush=True)
    elif hs is not None:
        print(f"holdout quarantine OFF (--no-holdout-quarantine): searching FULL data incl. holdout", flush=True)
    else:
        print(f"universe: {len(data)} tickers (no holdout configured)", flush=True)

    base = get_active_config(a.market)
    base.setdefault("strategies", {})[a.strategy] = {"enabled": True, **pinned}
    base.setdefault("risk", {})["max_open_positions"] = a.max_positions
    grid = load_param_grid(a.strategy)
    keys = [k for k in grid.keys() if k not in pinned]   # pinned params don't sweep
    if pinned:
        print(f"pinned params (fixed on every config): {pinned}", flush=True)

    # Config sweep: default (strategy defaults) + distinct random PARAM_GRID samples.
    rng = random.Random(42)
    configs = {"default": {}}
    seen = {tuple()}
    attempts = 0
    while len(configs) < a.grid_size and keys and attempts < a.grid_size * 50:
        attempts += 1
        params = {k: rng.choice(grid[k]) for k in keys}
        sig = tuple(sorted(params.items()))
        if sig in seen:
            continue
        seen.add(sig)
        configs[f"cfg{len(configs)}"] = params

    # Parallel sweep — bounded workers, nice'd, forked (workers inherit `data` via COW).
    global _BATTERY_DATA
    _BATTERY_DATA = data
    payloads = [(label, a.strategy, params, base, a.max_positions)
                for label, params in configs.items()]
    print(f"running {len(payloads)} configs across {workers} worker(s) "
          f"(nice +{a.nice}, CPU-bounded; cpu_count={_cpu})", flush=True)
    results = {}
    ctx = multiprocessing.get_context("fork")
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx,
                             initializer=_battery_init, initargs=(a.nice,)) as ex:
        futs = {ex.submit(_battery_run_config, p): p[0] for p in payloads}
        for fut in as_completed(futs):
            label = futs[fut]
            try:
                lbl, r, trades, m, cpcv = fut.result()
            except Exception as e:
                print(f"[{label:7s}] FAILED: {e}", flush=True)
                continue
            results[lbl] = {"params": dict(configs[lbl]), "returns": r, "trades": trades,
                            "metrics": m, "cpcv": cpcv}
            print(f"[{lbl:7s}] cpcv={cpcv:+.3f} sharpe={m['sharpe']:+.3f} "
                  f"trades={m['total_trades']} pnl=${m['total_pnl']:.0f} pf={m['profit_factor']:.2f} "
                  f"params={results[lbl]['params']}", flush=True)
    if not results:
        print("ERROR: no configs completed", flush=True)
        return None

    # Select the validated primary. Default = the PRE-REGISTERED config (no selection bias):
    # picking the best-of-grid is itself overfitting, which the forward gate then rejects.
    # The grid still informs PBO + effective-N DSR (the multiple-testing context).
    def _oos_window_sharpe(returns, frac=0.30):
        r = pd.Series(returns, dtype=float).dropna()
        if len(r) < 50:
            return float("-inf")
        seg = r.iloc[int(len(r) * (1 - frac)):]
        from research.cross_oos import metrics as _cm
        return _cm.annualized_sharpe(seg.to_numpy(), 252) if len(seg) > 10 else float("-inf")

    if a.select == "default" and "default" in results:
        prim_label = "default"
    elif a.select == "best_oos":
        prim_label = max(results, key=lambda k: _oos_window_sharpe(results[k]["returns"]))
    else:  # best_cpcv (selection-biased; kept for comparison)
        prim_label = max(results, key=lambda k: results[k]["cpcv"]
                         if results[k]["cpcv"] == results[k]["cpcv"] else -9.0)
    prim = results[prim_label]
    print(f"\nPRIMARY ({a.select}) = {prim_label}: {prim['params']} (cpcv={prim['cpcv']:+.3f})", flush=True)

    # IS/OOS time split on the primary config -> forward holdout + degradation.
    cfgP = copy.deepcopy(base)
    cfgP["strategies"][a.strategy].update(prim["params"])
    cfgP["strategies"][a.strategy]["enabled"] = True
    SPLIT, WARM = vo.compute_split_dates(data)
    split_ts, warm_ts = pd.Timestamp(SPLIT), pd.Timestamp(WARM)
    d_is = {k: v[v.index < split_ts] for k, v in data.items() if len(v[v.index < split_ts]) >= 60}
    d_oos = {k: v[v.index >= warm_ts] for k, v in data.items() if len(v[v.index >= warm_ts]) >= 60}
    m_is = vo.extract_metrics(run_bt(cfgP, a.strategy, d_is))
    m_oos = vo.extract_metrics(run_bt(cfgP, a.strategy, d_oos))
    deg = None
    if m_is.get("cagr_pct") and abs(m_is["cagr_pct"]) > 1e-9:
        deg = round((m_oos.get("cagr_pct", 0) - m_is["cagr_pct"]) / abs(m_is["cagr_pct"]) * 100, 2)
    print(f"time split: IS sharpe={m_is['sharpe']:.3f} cagr={m_is['cagr_pct']:.2f}% | "
          f"OOS sharpe={m_oos['sharpe']:.3f} cagr={m_oos['cagr_pct']:.2f}% pnl=${m_oos['total_pnl']:.0f} | "
          f"deg={deg}%", flush=True)

    # The sweep IS the search burden for this freshly-built strategy.
    grid_returns = {k: v["returns"] for k, v in results.items()}
    sweep_sr = [cm.sharpe(v["returns"].to_numpy(), 1) for v in results.values() if len(v["returns"]) > 2]
    sweep_sr = [s for s in sweep_sr if s == s]
    burden = {"n_trials": len(results),
              "sr_variance_pp": float(np.var(sweep_sr)) if len(sweep_sr) >= 2 else 0.0,
              "sr_variance_ann": float(np.var(sweep_sr)) * 252 if len(sweep_sr) >= 2 else 0.0,
              "n_experiments": len(results), "strategies_found": [a.strategy],
              "source": "param_grid_sweep"}

    bt = adapter.assemble_bundle(prim["returns"], prim["trades"], grid_returns=grid_returns,
                                 forward_net=m_oos.get("total_pnl", 0.0),
                                 oos_cagr_degradation_pct=deg, search_burden=burden)
    # Rail 2: FDR-aware PROMOTE bar escalates with the cumulative count of distinct hypothesis
    # families ever tested (cross-family multiple testing; within-family search already in DSR).
    fam = registry.family_of(a.strategy)
    n_families = registry.distinct_families(extra=fam)
    promote_bar = adapter.promote_dsr(n_families)
    tiers = adapter.evaluate_tiers(bt["bundle"], promote_dsr=promote_bar)
    if n_families > 1 or promote_bar > adapter.PROMOTE_DSR:
        print(f"FDR-aware PROMOTE bar: {promote_bar:.3f} (n_families={n_families}, base {adapter.PROMOTE_DSR})", flush=True)

    # Rail 3 (research integrity): a tier is meaningless unless the strategy DEPLOYS as designed.
    # Auto-FAIL artifacts (e.g. a sector-bug-capped 2-position book) regardless of DSR.
    deploy = deployment_sanity(
        prim["trades"], primary_config=prim["params"],
        strategy_meta={"max_positions": a.max_positions,
                       "max_sector_concentration": base.get("risk", {}).get("max_sector_concentration", 2)})
    raw_tier = tiers["tier"]
    final_tier = "FAIL" if not deploy["passed"] else raw_tier
    if not deploy["passed"]:
        print("\n" + "!" * 64, flush=True)
        print(f"DEPLOYMENT-SANITY FAIL: tier {raw_tier} -> FAIL. peak_concurrent="
              f"{deploy.get('peak_concurrent')} expected={deploy.get('expected_positions')} "
              f"trades={deploy.get('n_trades')} single_name_share={deploy.get('single_name_share')}", flush=True)
        for r in deploy["forced_fail_reasons"]:
            print(f"  - {r}", flush=True)
        print("!" * 64, flush=True)

    # Rail 1: a PROMOTE candidate must clear the write-once holdout ONCE before it counts.
    holdout_result = None
    if a.holdout_eval and final_tier == "PROMOTE":
        print("\nholdout-eval: PROMOTE candidate -> evaluating write-once holdout (single-use)...", flush=True)
        holdout_result = evaluate_holdout(
            a.strategy, prim["params"], market=a.market, max_positions=a.max_positions,
            search_sharpe=float(prim["cpcv"]) if prim["cpcv"] == prim["cpcv"] else None)
        if not holdout_result.get("ok"):
            print(f"  holdout NOT evaluated: {holdout_result.get('reason')}", flush=True)
            final_tier = "SCREEN"  # cannot confirm PROMOTE without a fresh holdout -> hold at SCREEN
        elif holdout_result.get("passed"):
            print(f"  holdout PASS: sharpe={holdout_result.get('holdout_sharpe')} "
                  f"deg={holdout_result.get('degradation_vs_search_pct')}% "
                  f"trades={holdout_result.get('holdout_trades')}", flush=True)
        else:
            print(f"  holdout FAIL -> PROMOTE downgraded to FAIL (candidate burned). "
                  f"reasons: {holdout_result.get('gate_reasons')}", flush=True)
            final_tier = "FAIL"

    # Rail 2: log this run to the hypothesis registry (one record per battery run).
    registry.append_run({
        "ts": datetime.datetime.now().isoformat(), "strategy": a.strategy, "family": fam,
        "market": a.market, "config_hash": config_hash(a.strategy, prim["params"], a.market),
        "grid_size": len(results), "raw_tier": raw_tier, "final_tier": final_tier,
        "dsr": bt["bundle"].get("dsr"), "promote_dsr_used": promote_bar, "n_families": n_families,
        "holdout_touched": bool(holdout_result and holdout_result.get("ok")),
    })

    out = {
        "strategy": a.strategy, "market": a.market,
        "generated_at": datetime.datetime.now().isoformat(),
        "grid_size": len(results), "max_positions": a.max_positions,
        "primary_label": prim_label, "primary_config": prim["params"],
        "sweep": {k: {"params": v["params"], "cpcv_median": v["cpcv"],
                      "sharpe": v["metrics"]["sharpe"], "pnl": v["metrics"]["total_pnl"],
                      "trades": v["metrics"]["total_trades"]} for k, v in results.items()},
        "time_split": {"in_sample": m_is, "out_of_sample": m_oos, "degradation_cagr_pct": deg},
        "deployment": deploy,
        "holdout": holdout_result,
        "multiple_testing": {"n_families": n_families, "promote_dsr_used": promote_bar,
                             "promote_dsr_base": adapter.PROMOTE_DSR},
        "cross_oos": {
            "bundle": bt["bundle"], "diagnostics": bt["diagnostics"],
            "tier": final_tier, "tier_raw": raw_tier,
            "screen_dsr": tiers["screen_dsr"], "promote_dsr": tiers["promote_dsr"],
            "gate_checks": {g.name: {"value": g.value, "status": g.status, "threshold": g.threshold,
                                     "comparator": g.comparator} for g in tiers["promote"]["gates"]},
            "gate_checks_screen": {g.name: g.status for g in tiers["screen"]["gates"]},
        },
        "verdict": final_tier, "runtime_s": round(time.time() - t0, 1),
    }
    outp = Path(a.output_path)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(out, indent=2, default=str))

    b = bt["bundle"]; d = bt["diagnostics"]
    print("\n" + "=" * 64)
    print(f"CROSS-OOS BATTERY: {a.strategy}  ->  TIER: {final_tier}"
          + (f"  (raw {raw_tier}, deployment-sanity FAIL)" if final_tier != raw_tier else ""))
    print("=" * 64)
    print("gates:", {g.name: g.status for g in tiers["promote"]["gates"]})
    print(f"CPCV median {b['median_cpcv_sharpe']:.3f} | frac+ {b['frac_paths_positive']:.2f} | "
          f"PBO {b['pbo']:.3f}")
    print(f"DSR(effective-N {d.get('dsr_n_trials_effective')} of raw {d.get('dsr_n_trials_raw')}, "
          f"grid PR {d.get('grid_participation_ratio')}): {b['dsr']:.3f}  [grid-proxy {d.get('dsr_grid'):.3f}]")
    print(f"top_ticker {b['top_group_frac']:.2f} | loo_ok {b['loo_group_ok']} | "
          f"min_regime {b['min_regime_sharpe']:.2f} | "
          f"regime_conc_ratio {b.get('regime_concentration_ratio', float('nan')):.2f} | "
          f"per_regime_ok {b.get('per_regime_expectancy_ok')} | forward_net {b['forward_net']}")
    print(f"runtime {out['runtime_s']:.0f}s  saved {outp}")
    return out


if __name__ == "__main__":
    sys.exit(main())
