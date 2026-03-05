"""Run multi-offset walk-forward stability test and save results."""
import copy
import json
import sys
sys.path.insert(0, '/root/atlas')

from scripts.strategy_evaluator import get_active_config, load_market_data, get_strategy_class
from backtest.engine import BacktestEngine

print("Loading data...")
cfg = get_active_config('sp500')
data = load_market_data('sp500')
print(f"Tickers: {len(data)}")

# Build strategies
strategies = []
for name, scfg in cfg.get('strategies', {}).items():
    if scfg.get('enabled', False):
        strategies.append(get_strategy_class(name)(copy.deepcopy(cfg)))
print(f"Strategies: {[s.name for s in strategies]}")

# Run multi-offset
print("\nRunning 5-offset stability test (offsets: 0, 5, 10, 15, 20)...")
engine = BacktestEngine(copy.deepcopy(cfg))
result = engine.run_walkforward_multioffset(data, strategies, n_offsets=5)

# Print results
print(f"\n{'='*60}")
print(f"MULTI-OFFSET STABILITY REPORT")
print(f"{'='*60}")
print(f"Median Sharpe: {result['median_sharpe']:.4f}")
print(f"Mean Sharpe:   {result['mean_sharpe']:.4f} ± {result['std_sharpe']:.4f}")
print(f"Median Trades: {result['median_trades']:.0f}")
print(f"Mean Trades:   {result['mean_trades']:.0f} ± {result['std_trades']:.0f}")
print(f"CV Sharpe:     {result['cv_sharpe']:.4f}")
print(f"CV Trades:     {result['cv_trades']:.4f}")
print(f"STABLE:        {'✅ YES' if result['stable'] else '❌ NO'} (threshold: cv_trades < 0.30)")

print(f"\nPer-offset breakdown:")
for r in result['per_offset']:
    err = r.get('error', '')
    if err:
        print(f"  offset={r['offset']:2d}: ERROR {err}")
    else:
        print(f"  offset={r['offset']:2d}: trades={r['trades']:3d}, sharpe={r['sharpe']:.4f}, "
              f"cagr={r['cagr']*100:.2f}%, max_dd={r['max_drawdown']*100:.1f}%")

# Save to file
with open('/root/atlas/research/experiments/multioffset_stability.json', 'w') as f:
    json.dump(result, f, indent=2, default=str)
print(f"\nResults saved to research/experiments/multioffset_stability.json")
