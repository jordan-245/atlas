#!/usr/bin/env python3
"""A/B comparison: current config vs dynamic sizing enabled.

Runs two walk-forward backtests and compares key metrics.
Output: backtest/results/dynamic_sizing_comparison.json
"""
import json, sys, time, copy
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from backtest.engine import BacktestEngine
import pandas as pd

def load_config(path):
    with open(path) as f:
        return json.load(f)

def get_tickers(market_id="sp500"):
    """Get universe tickers."""
    from universe.builder import load_universe
    info = load_universe(market_id)
    return info.get("tickers", [])

def get_strategies(config):
    """Instantiate enabled strategies from config."""
    from strategies.momentum_breakout import MomentumBreakout
    from strategies.mean_reversion import MeanReversion
    from strategies.trend_following import TrendFollowing
    from strategies.opening_gap import OpeningGap
    from strategies.sector_rotation import SectorRotation
    from strategies.short_term_mr import ShortTermMR
    from strategies.connors_rsi2 import ConnorsRSI2

    strats = []
    sc = config["strategies"]
    if sc.get("momentum_breakout", {}).get("enabled"): strats.append(MomentumBreakout(config))
    if sc.get("mean_reversion", {}).get("enabled"): strats.append(MeanReversion(config))
    if sc.get("trend_following", {}).get("enabled"): strats.append(TrendFollowing(config))
    if sc.get("opening_gap", {}).get("enabled"): strats.append(OpeningGap(config))
    if sc.get("sector_rotation", {}).get("enabled"): strats.append(SectorRotation(config))
    if sc.get("short_term_mr", {}).get("enabled"): strats.append(ShortTermMR(config))
    if sc.get("connors_rsi2", {}).get("enabled"): strats.append(ConnorsRSI2(config))
    return strats

def load_data(config, market_id="sp500"):
    """Load cached OHLCV data for universe tickers."""
    tickers = get_tickers(market_id)
    base_cache = PROJECT / config["data"]["cache_dir"]
    market_cache = base_cache / market_id
    data = {}
    for ticker in tickers:
        fname = ticker.replace(".", "_") + ".parquet"
        path = market_cache / fname
        if not path.exists():
            path = base_cache / fname
        if path.exists():
            data[ticker] = pd.read_parquet(path)
    return data

def run_backtest(config, data, label):
    print(f"\n{'='*60}")
    print(f"  Running backtest: {label}")
    print(f"{'='*60}")
    strategies = get_strategies(config)
    engine = BacktestEngine(config, market_id="sp500")
    t0 = time.time()
    result = engine.run_walkforward(data, strategies)
    elapsed = time.time() - t0
    
    metrics = result.metrics if hasattr(result, "metrics") else result.get("metrics", {})
    bench = result.benchmark_metrics if hasattr(result, "benchmark_metrics") else result.get("benchmark_metrics", {})
    trades = result.trades if hasattr(result, "trades") else result.get("trades", [])
    
    print(f"\n  {label} Results ({elapsed:.0f}s):")
    print(f"    CAGR:           {metrics.get('cagr', 0)*100:+.2f}%")
    print(f"    Max Drawdown:   {metrics.get('max_drawdown', 0)*100:.2f}%")
    print(f"    Sharpe Ratio:   {metrics.get('sharpe', 0):.3f}")
    print(f"    Sortino Ratio:  {metrics.get('sortino', 0):.3f}")
    print(f"    Calmar Ratio:   {metrics.get('calmar', 0):.3f}")
    print(f"    Win Rate:       {metrics.get('win_rate', 0)*100:.1f}%")
    print(f"    Profit Factor:  {metrics.get('profit_factor', 0):.2f}")
    print(f"    Total Trades:   {metrics.get('total_trades', len(trades))}")
    print(f"    Avg Trade:      ${metrics.get('avg_trade', 0):.2f}")
    
    return {
        "label": label,
        "elapsed_s": elapsed,
        "metrics": metrics,
        "benchmark_metrics": bench,
        "trade_count": len(trades),
    }

def main():
    print("Loading configs...")
    baseline_config = load_config(PROJECT / "config" / "active" / "sp500.json")
    candidate_config = load_config(PROJECT / "config" / "candidates" / "sp500_dynamic_sizing.json")
    
    # Verify the difference
    print(f"  Baseline dynamic_sizing.enabled: {baseline_config.get('dynamic_sizing', {}).get('enabled', False)}")
    print(f"  Candidate dynamic_sizing.enabled: {candidate_config.get('dynamic_sizing', {}).get('enabled', False)}")
    ec_cfg = candidate_config.get('dynamic_sizing', {}).get('equity_curve_scaling', {})
    print(f"  Candidate equity_curve_scaling.enabled: {ec_cfg.get('enabled', False)}")
    print(f"  Candidate graduated_tiers: {ec_cfg.get('graduated_tiers', [])}")
    
    print("\nLoading market data...")
    data = load_data(baseline_config)
    print(f"  Loaded {len(data)} tickers")
    
    # Run both backtests
    baseline = run_backtest(baseline_config, data, "BASELINE (dynamic_sizing OFF)")
    candidate = run_backtest(candidate_config, data, "CANDIDATE (dynamic_sizing ON)")
    
    # Compare
    bm = baseline["metrics"]
    cm = candidate["metrics"]
    
    dd_improvement = bm.get("max_drawdown", 0) - cm.get("max_drawdown", 0)
    sharpe_delta = cm.get("sharpe", 0) - bm.get("sharpe", 0)
    cagr_delta = cm.get("cagr", 0) - bm.get("cagr", 0)
    
    print(f"\n{'='*60}")
    print(f"  COMPARISON")
    print(f"{'='*60}")
    print(f"  Max Drawdown improvement: {dd_improvement*100:+.2f}pp")
    print(f"    Baseline: {bm.get('max_drawdown',0)*100:.2f}%  →  Candidate: {cm.get('max_drawdown',0)*100:.2f}%")
    print(f"  Sharpe change: {sharpe_delta:+.3f}")
    print(f"    Baseline: {bm.get('sharpe',0):.3f}  →  Candidate: {cm.get('sharpe',0):.3f}")
    print(f"  CAGR change: {cagr_delta*100:+.2f}pp")
    print(f"    Baseline: {bm.get('cagr',0)*100:.2f}%  →  Candidate: {cm.get('cagr',0)*100:.2f}%")
    print(f"  Sortino change: {cm.get('sortino',0) - bm.get('sortino',0):+.3f}")
    print(f"  Calmar change: {cm.get('calmar',0) - bm.get('calmar',0):+.3f}")
    
    verdict = "PASS" if dd_improvement >= 0.01 else "FAIL"
    print(f"\n  Drawdown improvement ≥ 1pp: {verdict} ({dd_improvement*100:+.2f}pp)")
    
    # Save results
    output = {
        "baseline": baseline,
        "candidate": candidate,
        "comparison": {
            "max_dd_improvement_pp": round(dd_improvement * 100, 2),
            "sharpe_delta": round(sharpe_delta, 3),
            "cagr_delta_pp": round(cagr_delta * 100, 2),
            "sortino_delta": round(cm.get("sortino", 0) - bm.get("sortino", 0), 3),
            "calmar_delta": round(cm.get("calmar", 0) - bm.get("calmar", 0), 3),
            "verdict": verdict,
        },
        "candidate_config_dynamic_sizing": candidate_config["dynamic_sizing"],
    }
    
    out_path = PROJECT / "backtest" / "results" / "dynamic_sizing_comparison.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Results saved to {out_path}")
    
    print("\n### Completed")  # sentinel for systemd log parsing

if __name__ == "__main__":
    main()
