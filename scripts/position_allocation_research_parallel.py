#!/usr/bin/env python3
"""Atlas Position Allocation Research — Task #26 (PARALLEL)

Parallelized version: loads data once, then fans out 6 experiments
across multiple CPU cores using multiprocessing.

Experiments:
    0. BASELINE: MR+TF+OG, max_pos=10 (current active)
    1. 6-strat, max_pos=10 (reproduce contention)
    2. 6-strat, max_pos=15
    3. 6-strat, max_pos=20
    4. 6-strat, max_pos=25
    5. CONTROL: MR+TF+OG, max_pos=15 (does more room help current strategies?)
"""
import sys
import json
import copy
import time
import logging
import multiprocessing as mp
from pathlib import Path
from datetime import datetime, timezone

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

logging.basicConfig(level=logging.WARNING)

MARKET = "sp500"

# ── Experiment definitions (index, name, dormant_strategies, max_positions) ──
DORMANT_ALL = ["momentum_breakout", "short_term_mr", "sector_rotation"]
EXPERIMENTS = [
    (0, "BASELINE: MR+TF+OG, max_pos=10",      None,        10),
    (1, "6-strat, max_pos=10 (contention)",      DORMANT_ALL, 10),
    (2, "6-strat, max_pos=15",                   DORMANT_ALL, 15),
    (3, "6-strat, max_pos=20",                   DORMANT_ALL, 20),
    (4, "6-strat, max_pos=25",                   DORMANT_ALL, 25),
    (5, "CONTROL: MR+TF+OG, max_pos=15",        None,        15),
]


def load_market_data(market_id):
    """Load cached data."""
    import pandas as pd
    from markets import get_market
    market = get_market(market_id)
    valid = set(market.get_formatted_tickers())
    valid.add(market.benchmark_ticker)
    suffix = market.yfinance_suffix
    cache = PROJECT / 'data' / 'cache' / market_id
    data = {}
    for pf in sorted(cache.glob('*.parquet')):
        stem = pf.stem
        if suffix:
            su = suffix.replace('.', '_')
            if not stem.endswith(su):
                continue
            ticker = stem.replace(su, suffix)
        else:
            if '_AX' in stem:
                continue
            ticker = stem
        if ticker == market.benchmark_ticker:
            continue
        if ticker not in valid:
            continue
        try:
            df = pd.read_parquet(pf)
            df.columns = [c.lower() for c in df.columns]
            if 'date' in df.columns:
                df['date'] = pd.to_datetime(df['date'])
                df = df.set_index('date')
            df.index = pd.to_datetime(df.index)
            if len(df) >= 100:
                data[ticker] = df
        except Exception:
            pass
    return data


def make_config(base, add_strategies, max_pos):
    """Build config with specified strategies and max positions."""
    cfg = copy.deepcopy(base)
    cfg["risk"]["max_open_positions"] = max_pos

    dormant_params = {
        "momentum_breakout": {
            "enabled": True, "breakout_period": 10, "atr_stop_mult": 2.0,
            "trend_ma_period": 150, "volume_surge_mult": 1.5,
            "max_hold_days": 15, "sma200_filter": True,
        },
        "short_term_mr": {
            "enabled": True, "rsi_period": 2, "rsi_oversold": 5,
            "ibs_max": 0.15, "atr_stop_mult": 2.0,
            "max_hold_days": 7, "sma200_filter": True,
        },
        "sector_rotation": {
            "enabled": True, "top_sectors": 2, "atr_stop_mult": 2.5,
            "max_hold_days": 30, "sma200_filter": True,
        },
    }

    if add_strategies:
        for s in add_strategies:
            if s in dormant_params:
                candidate_map = {
                    "momentum_breakout": "sp500_wave1_moment_opt.json",
                    "short_term_mr": "sp500_wave1_short__opt.json",
                    "sector_rotation": "sp500_wave1_sector_opt.json",
                }
                fname = candidate_map.get(s)
                params = dormant_params[s]
                if fname:
                    path = PROJECT / "config" / "candidates" / fname
                    if path.exists():
                        with open(path) as f:
                            cand = json.load(f)
                        file_params = cand.get("strategies", {}).get(s, {})
                        if file_params:
                            params = file_params
                            params["enabled"] = True

                if s not in cfg["strategies"]:
                    cfg["strategies"][s] = params
                else:
                    cfg["strategies"][s].update(params)
                cfg["strategies"][s]["enabled"] = True

    return cfg


def run_single_experiment(args):
    """Worker function: runs one experiment. Receives (idx, name, add_strats, max_pos, base_cfg, data)."""
    idx, name, add_strats, max_pos, base_cfg, data = args

    # Re-import inside worker (fresh process)
    from backtest.engine import BacktestEngine
    from strategies.mean_reversion import MeanReversion
    from strategies.trend_following import TrendFollowing
    from strategies.opening_gap import OpeningGap
    from strategies.momentum_breakout import MomentumBreakout
    from strategies.short_term_mr import ShortTermMR
    from strategies.sector_rotation import SectorRotation

    STRATEGY_CLASSES = {
        'mean_reversion': MeanReversion,
        'trend_following': TrendFollowing,
        'opening_gap': OpeningGap,
        'momentum_breakout': MomentumBreakout,
        'short_term_mr': ShortTermMR,
        'sector_rotation': SectorRotation,
    }

    cfg = make_config(base_cfg, add_strats, max_pos)

    strats = []
    for sname, scfg in cfg.get('strategies', {}).items():
        if scfg.get('enabled', False) and sname in STRATEGY_CLASSES:
            strats.append(STRATEGY_CLASSES[sname](cfg))

    if not strats:
        return idx, name, add_strats, max_pos, {'error': 'No strategies', 'total_trades': 0}, 0.0

    t0 = time.time()
    engine = BacktestEngine(cfg)
    result = engine.run_walkforward(data, strats)
    dt = time.time() - t0

    m = result.metrics
    cagr = m.get('cagr', 0)
    cagr_pct = cagr * 100 if abs(cagr) < 2 else cagr

    # Per-strategy breakdown
    strat_trades = {}
    for t in result.trades:
        s = t.get('strategy', 'unknown')
        strat_trades.setdefault(s, []).append(t)
    breakdown = {}
    for s, trades in strat_trades.items():
        pnls = [t.get('pnl', 0) for t in trades]
        wins = sum(1 for p in pnls if p > 0)
        breakdown[s] = {
            'trades': len(trades),
            'total_pnl': round(sum(pnls), 2),
            'win_rate_pct': round(wins / len(trades) * 100, 1) if trades else 0,
        }

    metrics = {
        'total_trades': m.get('total_trades', 0),
        'cagr_pct': round(cagr_pct, 4),
        'sharpe': round(m.get('sharpe', 0), 4),
        'sortino': round(m.get('sortino', 0), 4),
        'max_drawdown_pct': round(m.get('max_drawdown', 0) * 100, 4),
        'win_rate_pct': round(m.get('win_rate', 0) * 100, 2),
        'profit_factor': round(m.get('profit_factor', 0), 4),
        'total_pnl': round(m.get('total_pnl', 0), 2),
        'avg_trade': round(m.get('avg_trade', 0), 2),
        'final_equity': round(m.get('final_equity', 0), 2),
        'expectancy_r': round(m.get('expectancy_r', 0), 4),
        'edge_p_value': m.get('edge_p_value', 1.0),
        'edge_significant': m.get('edge_significant', False),
        'strategy_breakdown': breakdown,
    }

    print(f"  ✓ [{idx}] {name} done in {dt:.0f}s — Sharpe={metrics['sharpe']:+.3f}", flush=True)
    return idx, name, add_strats or [], max_pos, metrics, round(dt, 1)


def main():
    n_workers = min(len(EXPERIMENTS), mp.cpu_count())
    print(f"Position Allocation Research — {len(EXPERIMENTS)} experiments × {n_workers} workers")
    print(f"Loading {MARKET} data...")

    t_start = time.time()
    from utils.config import get_active_config
    base = get_active_config(MARKET)
    data = load_market_data(MARKET)
    print(f"Loaded {len(data)} tickers in {time.time()-t_start:.0f}s")
    print(f"Launching parallel experiments...\n")

    # Prepare worker args: (idx, name, add_strats, max_pos, base_cfg, data)
    worker_args = [
        (idx, name, add_strats, max_pos, base, data)
        for idx, name, add_strats, max_pos in EXPERIMENTS
    ]

    # Run in parallel using fork-based pool (shares data efficiently via COW)
    mp.set_start_method('fork', force=True)
    with mp.Pool(processes=n_workers) as pool:
        raw_results = pool.map(run_single_experiment, worker_args)

    # Sort by original experiment index
    raw_results.sort(key=lambda x: x[0])

    total_time = time.time() - t_start
    print(f"\nAll experiments done in {total_time:.0f}s wall-clock\n")

    # Build results list
    results = []
    for idx, name, add_strats, max_pos, metrics, runtime in raw_results:
        results.append({
            "name": name,
            "max_positions": max_pos,
            "strategies_added": add_strats,
            "metrics": metrics,
            "runtime_s": runtime,
        })

    # ── Per-experiment detail ──
    for r in results:
        m = r["metrics"]
        print(f"[{results.index(r)}] {r['name']}")
        print(f"  Sharpe={m['sharpe']:+.3f}  CAGR={m['cagr_pct']:+.1f}%  DD={m['max_drawdown_pct']:.1f}%  "
              f"PF={m['profit_factor']:.2f}  WR={m['win_rate_pct']:.1f}%  "
              f"Trades={m['total_trades']}  PnL=${m['total_pnl']:+.0f}  [{r['runtime_s']:.0f}s]")
        bd = m.get('strategy_breakdown', {})
        for s in sorted(bd):
            d = bd[s]
            print(f"    {s:<25} {d['trades']:>4}t  PnL=${d['total_pnl']:>+8.2f}  WR={d['win_rate_pct']:.1f}%")
        print()

    # ── Summary table ──
    print("=" * 130)
    print("RESULTS COMPARISON")
    print("=" * 130)
    hdr = f"{'Experiment':<40} {'MaxP':>4} {'Sharpe':>7} {'CAGR%':>7} {'DD%':>6} {'PF':>5} {'WR%':>5} {'Trades':>6} {'PnL':>8} {'Edge?':>5}"
    print(hdr)
    print("-" * 130)
    for r in results:
        m = r["metrics"]
        edge = "✓" if m.get('edge_significant') else "✗"
        print(f"{r['name']:<40} {r['max_positions']:>4} {m['sharpe']:>+7.3f} {m['cagr_pct']:>+7.1f} "
              f"{m['max_drawdown_pct']:>6.1f} {m['profit_factor']:>5.2f} {m['win_rate_pct']:>5.1f} "
              f"{m['total_trades']:>6} ${m['total_pnl']:>+7.0f} {edge:>5}")
    print("=" * 130)

    # ── Delta vs baseline ──
    bl = results[0]["metrics"]
    print("\nDELTA vs BASELINE:")
    print(f"{'Experiment':<40} {'ΔSharpe':>8} {'ΔCAGR':>7} {'ΔDD':>6} {'ΔTrades':>8} {'ΔPnL':>8}")
    print("-" * 80)
    for r in results[1:]:
        m = r["metrics"]
        print(f"{r['name']:<40} {m['sharpe']-bl['sharpe']:>+8.3f} {m['cagr_pct']-bl['cagr_pct']:>+7.1f} "
              f"{m['max_drawdown_pct']-bl['max_drawdown_pct']:>+6.1f} "
              f"{m['total_trades']-bl['total_trades']:>+8} ${m['total_pnl']-bl['total_pnl']:>+7.0f}")

    # ── Speedup stats ──
    sequential_time = sum(r['runtime_s'] for r in results)
    print(f"\nPERFORMANCE: {sequential_time:.0f}s sequential → {total_time:.0f}s parallel "
          f"({sequential_time/total_time:.1f}× speedup, {n_workers} workers)")

    # ── Save results ──
    output = {
        "experiment": "position_allocation_research",
        "task_id": 26,
        "market": MARKET,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "baseline_config_version": base.get("version"),
        "n_tickers": len(data),
        "total_runtime_s": round(total_time, 1),
        "sequential_runtime_s": round(sequential_time, 1),
        "n_workers": n_workers,
        "results": results,
    }
    out_path = PROJECT / "research" / "experiments" / "position_allocation_research.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\nSaved to: {out_path}")


if __name__ == "__main__":
    main()
