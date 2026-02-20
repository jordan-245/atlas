import json, os, sys, copy, time
from pathlib import Path
import pandas as pd
os.chdir('/a0/usr/projects/atlas-asx')
sys.path.insert(0, '.')

BASELINE = {'total_trades':199,'cagr':0.0834,'sharpe_ratio':0.522,'profit_factor':1.639,'max_drawdown':-0.0746,'win_rate':0.543}

def load_data():
    d = {}
    for pf in sorted(Path('data/cache').glob('*.parquet')):
        if pf.stem == 'IOZ_AX': continue
        df = pd.read_parquet(pf)
        df.columns = [c.lower() for c in df.columns]
        if 'date' in df.columns:
            df['date'] = pd.to_datetime(df['date'])
            df = df.set_index('date')
        df.index = pd.to_datetime(df.index)
        if len(df) < 100: continue
        d[pf.stem.replace('_AX', '.AX')] = df
    return d

def run_bt(cfg, data, label):
    from backtest.engine import BacktestEngine
    from strategies.mean_reversion import MeanReversion
    from strategies.trend_following import TrendFollowing
    from strategies.bb_squeeze import BBSqueeze
    from strategies.opening_gap import OpeningGap
    
    t0 = time.time()
    print('  Running [' + label + ']...', flush=True)
    strategies = [MeanReversion(cfg), TrendFollowing(cfg), OpeningGap(cfg)]
    eng = BacktestEngine(cfg)
    result = eng.run_walkforward(data, strategies)
    m = result.metrics
    s = time.time() - t0
    n = m.get('total_trades', 0)
    c = m.get('cagr', 0)
    c = c * 100 if abs(c) < 2 else c
    sh = m.get('sharpe', m.get('sharpe_ratio', 0))
    pf = m.get('profit_factor', 0)
    dd = m.get('max_drawdown', 0)
    dd = dd * 100 if abs(dd) < 2 else dd
    wr = m.get('win_rate', 0)
    wr = wr * 100 if wr < 2 else wr
    print('  [' + label + '] ' + str(int(s)) + 's: trades=' + str(n) + ' CAGR=' + str(round(c,2)) + '% Sharpe=' + str(round(sh,3)) + ' PF=' + str(round(pf,3)) + ' DD=' + str(round(dd,2)) + '% WR=' + str(round(wr,1)) + '%')
    return m

print('=== Phase 3: Volume Spike Confirmation A/B Test ===')
data = load_data()
print('Loaded ' + str(len(data)) + ' tickers')
with open('config/active_config.json') as f:
    base = json.load(f)

# Arm A: vol modifier OFF (zero boost, zero penalty)
cfg_a = copy.deepcopy(base)
cfg_a['strategies']['mean_reversion']['volume']['surge_boost'] = 0.0
cfg_a['strategies']['mean_reversion']['volume']['dry_penalty'] = 0.0

# Arm B: vol modifier ON (surge_boost=0.05, dry_penalty=0.0)
cfg_b = copy.deepcopy(base)
cfg_b['strategies']['mean_reversion']['volume']['surge_boost'] = 0.05
cfg_b['strategies']['mean_reversion']['volume']['dry_penalty'] = 0.0

print('\nArm A: Volume modifier OFF')
m_a = run_bt(cfg_a, data, 'A-Baseline (vol OFF)')

print('\nArm B: Volume modifier ON (surge_boost=0.05)')
m_b = run_bt(cfg_b, data, 'B-VolBoost (surge=0.05)')

# Extract metrics
def get(m, key, alt_key=None):
    v = m.get(key, m.get(alt_key, 0) if alt_key else 0)
    return v

cagr_a = get(m_a, 'cagr'); cagr_a = cagr_a*100 if abs(cagr_a)<2 else cagr_a
cagr_b = get(m_b, 'cagr'); cagr_b = cagr_b*100 if abs(cagr_b)<2 else cagr_b
sh_a = get(m_a, 'sharpe', 'sharpe_ratio')
sh_b = get(m_b, 'sharpe', 'sharpe_ratio')
pf_a = get(m_a, 'profit_factor')
pf_b = get(m_b, 'profit_factor')
wr_a = get(m_a, 'win_rate'); wr_a = wr_a*100 if wr_a<2 else wr_a
wr_b = get(m_b, 'win_rate'); wr_b = wr_b*100 if wr_b<2 else wr_b
tr_a = get(m_a, 'total_trades')
tr_b = get(m_b, 'total_trades')

print('\n=== RESULTS SUMMARY ===')
print(f'                    Baseline     VolBoost    Delta')
print(f'Total Trades:       {tr_a:<12} {tr_b:<12} {tr_b-tr_a:+}')
print(f'CAGR:               {cagr_a:<12.2f} {cagr_b:<12.2f} {cagr_b-cagr_a:+.2f}%')
print(f'Sharpe:             {sh_a:<12.3f} {sh_b:<12.3f} {sh_b-sh_a:+.3f}')
print(f'Profit Factor:      {pf_a:<12.3f} {pf_b:<12.3f} {pf_b-pf_a:+.3f}')
print(f'Win Rate:           {wr_a:<12.1f} {wr_b:<12.1f} {wr_b-wr_a:+.1f}%')

# Verdict
improvements = sum([
    cagr_b > cagr_a,
    sh_b > sh_a,
    pf_b > pf_a,
    wr_b > wr_a,
])
print(f'\nVolume boost improved {improvements}/4 metrics')
if improvements >= 3:
    verdict = 'ENABLE volume boost (surge_boost=0.05)'
elif improvements <= 1:
    verdict = 'DISABLE volume boost (keep at 0.0)'
else:
    verdict = 'MARGINAL - inspect trade distribution before deciding'
print(f'Verdict: {verdict}')

results = {
    'arm_a': m_a, 'arm_b': m_b,
    'summary': {
        'cagr_delta': round(cagr_b - cagr_a, 3),
        'sharpe_delta': round(sh_b - sh_a, 3),
        'pf_delta': round(pf_b - pf_a, 3),
        'wr_delta': round(wr_b - wr_a, 3),
        'improvements_of_4': improvements,
        'verdict': verdict,
    }
}
with open('backtest/results/phase3_volume_spike.json', 'w') as f:
    json.dump(results, f, indent=2, default=str)
print('\nResults saved to backtest/results/phase3_volume_spike.json')
