#!/usr/bin/env python3
"""Atlas OOS Validation — PARALLEL (8-core)

Runs all 13 independent backtests in parallel:
  - Test 1: IS / OOS / Full  (3 backtests)
  - Test 2: 10 perturbation trials (10 backtests)
  - Test 3: reuses Full result (free)
"""
import json, sys, time, copy, random, datetime, argparse
import multiprocessing as mp
from pathlib import Path
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.config import get_active_config

# ── Constants ──
DATA_DIR = PROJECT_ROOT / 'data' / 'cache'
DEFAULT_CONFIG_PATH = PROJECT_ROOT / 'config' / 'active' / 'asx.json'
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / 'backtest' / 'results' / 'oos_validation.json'
SPLIT_DATE = '2024-09-01'
WARMUP_DATE = '2024-06-01'
MIN_ROWS = 60
N_PERTURBATION_TRIALS = 10
PERTURB_MIN = 0.8
PERTURB_MAX = 1.2
RANDOM_SEED = 42

OPTIMIZED_PARAMS = {
    'mean_reversion': {
        'rsi_oversold': 35, 'zscore_entry': -2.0, 'atr_stop_mult': 2.5,
        'profit_target_atr_mult': 1.5, 'max_hold_days': 7,
    },
    'bb_squeeze': {
        'bb_std': 3.0, 'kc_atr_mult': 2.0, 'momentum_period': 30,
        'atr_stop_mult': 1.0, 'trailing_stop_atr_mult': 3.0, 'max_hold_days': 20,
    },
    'trend_following': {
        'fast_ma': 20, 'slow_ma': 50, 'pullback_pct': 0.02,
        'atr_stop_mult': 3.5,
    },
}


def resolve_path(user_path, default):
    p = Path(user_path) if user_path else default
    if not p.is_absolute():
        p = PROJECT_ROOT / p
    return p


def parse_args():
    parser = argparse.ArgumentParser(description="Parallel OOS validation")
    parser.add_argument('--config-path', type=str, default=None)
    parser.add_argument('--output-path', type=str, default=None)
    parser.add_argument('--workers', type=int, default=None,
                        help='Number of parallel workers (default: cpu_count)')
    return parser.parse_args()


def load_data(market='asx'):
    data_dir = DATA_DIR / market if market else DATA_DIR
    from markets import get_market
    mkt = get_market(market)
    valid = set(mkt.get_formatted_tickers())
    valid.add(mkt.benchmark_ticker)
    suffix = mkt.yfinance_suffix
    data = {}
    for pf in sorted(data_dir.glob('*.parquet')):
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
        if ticker == mkt.benchmark_ticker:
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
            if len(df) >= MIN_ROWS:
                data[ticker] = df
        except Exception:
            pass
    return data


def make_strategies(cfg):
    from strategies.mean_reversion import MeanReversion
    from strategies.trend_following import TrendFollowing
    from strategies.bb_squeeze import BBSqueeze
    from strategies.opening_gap import OpeningGap
    strats = []
    s = cfg.get('strategies', {})
    if s.get('mean_reversion', {}).get('enabled', True):
        strats.append(MeanReversion(cfg))
    if s.get('trend_following', {}).get('enabled', True):
        strats.append(TrendFollowing(cfg))
    if s.get('bb_squeeze', {}).get('enabled', True):
        strats.append(BBSqueeze(cfg))
    if s.get('opening_gap', {}).get('enabled', True):
        strats.append(OpeningGap(cfg))
    return strats


def extract_metrics(result):
    m = result.metrics
    cagr = m.get('cagr', 0)
    cagr_pct = cagr * 100 if abs(cagr) < 2 else cagr
    return {
        'total_trades': m.get('total_trades', 0),
        'cagr_pct': round(cagr_pct, 4),
        'sharpe': round(m.get('sharpe', 0), 4),
        'profit_factor': round(m.get('profit_factor', 0), 4),
        'max_drawdown_pct': round(m.get('max_drawdown', 0) * 100, 4),
        'win_rate_pct': round(m.get('win_rate', 0) * 100, 2),
        'total_pnl': round(m.get('total_pnl', 0), 2),
        'sortino': round(m.get('sortino', 0), 4),
        'avg_trade': round(m.get('avg_trade', 0), 2),
        'final_equity': round(m.get('final_equity', 0), 2),
    }


def perturb_params(cfg, seed):
    rng = random.Random(seed)
    cfg_new = copy.deepcopy(cfg)
    perturbed_log = {}
    for strat_name, params in OPTIMIZED_PARAMS.items():
        strat_cfg = cfg_new.get('strategies', {}).get(strat_name, {})
        perturbed_log[strat_name] = {}
        for param_name, orig_val in params.items():
            factor = rng.uniform(PERTURB_MIN, PERTURB_MAX)
            new_val = orig_val * factor
            if isinstance(orig_val, int):
                new_val = max(1, round(new_val))
            else:
                new_val = round(new_val, 4)
            strat_cfg[param_name] = new_val
            perturbed_log[strat_name][param_name] = {
                'original': orig_val, 'factor': round(factor, 4), 'perturbed': new_val,
            }
    return cfg_new, perturbed_log


def analyze_walk_forward_windows(result):
    windows = result.walk_forward_windows
    if not windows:
        return {'error': 'No walk-forward windows found'}
    window_returns = []
    for w in windows:
        eq_start = w.get('equity_start', 0)
        eq_end = w.get('equity_end', 0)
        ret = (eq_end - eq_start) / eq_start if eq_start > 0 else 0.0
        window_returns.append(ret)
    window_pnls = [w.get('pnl', 0) for w in windows]
    window_trades = [w.get('trades', 0) for w in windows]
    n_positive = sum(1 for r in window_returns if r > 0)
    n_negative = sum(1 for r in window_returns if r <= 0)
    return {
        'n_windows': len(windows),
        'n_positive_windows': n_positive,
        'n_negative_windows': n_negative,
        'win_rate_windows_pct': round(n_positive / len(windows) * 100, 1) if windows else 0,
        'mean_window_return_pct': round(np.mean(window_returns) * 100, 4),
        'std_window_return_pct': round(np.std(window_returns) * 100, 4),
        'min_window_return_pct': round(min(window_returns) * 100, 4),
        'max_window_return_pct': round(max(window_returns) * 100, 4),
        'median_window_return_pct': round(np.median(window_returns) * 100, 4),
        'mean_window_pnl': round(np.mean(window_pnls), 2),
        'std_window_pnl': round(np.std(window_pnls), 2),
        'mean_trades_per_window': round(np.mean(window_trades), 1),
        'total_trades_across_windows': sum(window_trades),
        'per_window_detail': [
            {
                'window': w.get('window', i),
                'test_start': str(w.get('test_start', ''))[:10],
                'test_end': str(w.get('test_end', ''))[:10],
                'trades': w.get('trades', 0),
                'pnl': round(w.get('pnl', 0), 2),
                'return_pct': round(window_returns[i] * 100, 4),
                'equity_start': round(w.get('equity_start', 0), 2),
                'equity_end': round(w.get('equity_end', 0), 2),
            }
            for i, w in enumerate(windows)
        ],
    }


# ── Worker function: runs one backtest ──
def _run_one(args):
    """Worker: (job_tag, cfg, data_dict) → (job_tag, metrics_dict, elapsed, raw_result_or_None)"""
    job_tag, cfg, data_dict, keep_result = args

    from backtest.engine import BacktestEngine

    t0 = time.time()
    strategies = make_strategies(cfg)
    engine = BacktestEngine(cfg)
    result = engine.run_walkforward(data_dict, strategies)
    elapsed = time.time() - t0

    metrics = extract_metrics(result)
    wf_windows = None
    if keep_result:
        wf_windows = analyze_walk_forward_windows(result)

    m = result.metrics
    cagr = m.get('cagr', 0)
    cagr_pct = cagr * 100 if abs(cagr) < 2 else cagr
    print(f"  ✓ [{job_tag}] Trades={m.get('total_trades',0)} "
          f"CAGR={cagr_pct:.2f}% Sharpe={m.get('sharpe',0):.3f} "
          f"PF={m.get('profit_factor',0):.3f} DD={m.get('max_drawdown',0)*100:.1f}% "
          f"[{elapsed:.0f}s]", flush=True)

    return job_tag, metrics, round(elapsed, 1), wf_windows


def main():
    args = parse_args()
    config_path = resolve_path(args.config_path, DEFAULT_CONFIG_PATH)
    output_path = resolve_path(args.output_path, DEFAULT_OUTPUT_PATH)
    n_workers = args.workers or mp.cpu_count()
    overall_start = time.time()

    # ── Load config & data ──
    cfg = json.loads(config_path.read_text())
    market = cfg.get('market', 'asx')

    print("=" * 70)
    print(f"ATLAS {market.upper()} OOS VALIDATION — PARALLEL ({n_workers} workers)")
    print("=" * 70)
    print(f"Config: {cfg.get('version', '?')} — {config_path}")

    print(f"\nLoading {market} data...")
    t0 = time.time()
    data_all = load_data(market=market)
    data_all = {k: v for k, v in data_all.items() if len(v) >= MIN_ROWS}
    print(f"Loaded {len(data_all)} tickers in {time.time()-t0:.0f}s")

    # ── Prepare data splits ──
    split_ts = pd.Timestamp(SPLIT_DATE)
    warmup_ts = pd.Timestamp(WARMUP_DATE)
    data_is = {k: v[v.index < split_ts] for k, v in data_all.items()
               if len(v[v.index < split_ts]) >= MIN_ROWS}
    data_oos = {k: v[v.index >= warmup_ts] for k, v in data_all.items()
                if len(v[v.index >= warmup_ts]) >= MIN_ROWS}
    print(f"IS tickers: {len(data_is)} | OOS tickers: {len(data_oos)}")

    # ── Build all 13 jobs ──
    jobs = []
    # Test 1: IS, OOS, Full
    jobs.append(('IS',   cfg, data_is,  False))
    jobs.append(('OOS',  cfg, data_oos, False))
    jobs.append(('FULL', cfg, data_all, True))   # keep result for Test 3 window analysis

    # Test 2: 10 perturbation trials
    perturb_logs = {}
    for i in range(N_PERTURBATION_TRIALS):
        seed = RANDOM_SEED + i
        cfg_p, p_log = perturb_params(cfg, seed)
        tag = f'PERTURB-{i+1}'
        jobs.append((tag, cfg_p, data_all, False))
        perturb_logs[tag] = {'seed': seed, 'trial': i + 1, 'log': p_log}

    n_jobs = len(jobs)
    print(f"\nDispatching {n_jobs} backtests across {n_workers} workers...\n")

    # ── Run in parallel ──
    mp.set_start_method('fork', force=True)
    with mp.Pool(processes=n_workers) as pool:
        raw = pool.map(_run_one, jobs)

    # ── Collect results by tag ──
    by_tag = {tag: (metrics, elapsed, wf) for tag, metrics, elapsed, wf in raw}
    parallel_time = time.time() - overall_start
    sequential_time = sum(elapsed for _, _, elapsed, _ in raw)

    print(f"\nAll {n_jobs} backtests done in {parallel_time:.0f}s wall "
          f"(vs {sequential_time:.0f}s sequential → {sequential_time/parallel_time:.1f}× speedup)\n")

    # ═══════════════════════════════════════════════
    # Assemble results identical to original script
    # ═══════════════════════════════════════════════
    results = {
        'validation_type': 'oos_validation_parallel',
        'timestamp': datetime.datetime.now().isoformat(),
        'config_version': cfg.get('version', 'unknown'),
        'config_path': str(config_path),
        'output_path': str(output_path),
        'split_date': SPLIT_DATE,
        'warmup_date': WARMUP_DATE,
        'n_perturbation_trials': N_PERTURBATION_TRIALS,
        'perturbation_range': [PERTURB_MIN, PERTURB_MAX],
        'n_workers': n_workers,
    }

    # ── Test 1 ──
    m_is  = by_tag['IS'][0]
    m_oos = by_tag['OOS'][0]
    m_full = by_tag['FULL'][0]
    t_test1 = by_tag['IS'][1] + by_tag['OOS'][1] + by_tag['FULL'][1]

    degradation = {}
    for key in ('cagr_pct', 'sharpe', 'profit_factor', 'win_rate_pct'):
        is_val = m_is.get(key, 0)
        oos_val = m_oos.get(key, 0)
        if is_val and abs(is_val) > 1e-9:
            degradation[key] = round(((oos_val - is_val) / abs(is_val)) * 100, 2)
        else:
            degradation[key] = None

    results['test1_time_period_split'] = {
        'in_sample': m_is,
        'out_of_sample': m_oos,
        'degradation_pct': degradation,
        'full_metrics': m_full,
        'runtime_s': round(t_test1, 1),
    }

    print("-" * 70)
    print("TEST 1: Time-Period Split")
    print("-" * 70)
    for label, m in [('IS', m_is), ('OOS', m_oos), ('FULL', m_full)]:
        print(f"  {label:>4}: Sharpe={m['sharpe']:+.3f}  CAGR={m['cagr_pct']:+.1f}%  "
              f"DD={m['max_drawdown_pct']:.1f}%  PF={m['profit_factor']:.2f}  Trades={m['total_trades']}")
    print(f"  Degradation: {degradation}")

    # ── Test 2 ──
    perturb_trials = []
    for i in range(N_PERTURBATION_TRIALS):
        tag = f'PERTURB-{i+1}'
        m_p, elapsed_p, _ = by_tag[tag]
        m_p['trial'] = perturb_logs[tag]['trial']
        m_p['seed'] = perturb_logs[tag]['seed']
        m_p['runtime_s'] = elapsed_p
        m_p['perturbation_log'] = perturb_logs[tag]['log']
        perturb_trials.append(m_p)

    def summarize_numeric(field):
        vals = [t[field] for t in perturb_trials if isinstance(t.get(field), (int, float))]
        if not vals:
            return {'mean': None, 'std': None, 'min': None, 'max': None}
        return {
            'mean': round(float(np.mean(vals)), 4),
            'std': round(float(np.std(vals)), 4),
            'min': round(float(np.min(vals)), 4),
            'max': round(float(np.max(vals)), 4),
        }

    perturb_summary = {
        'cagr_pct': summarize_numeric('cagr_pct'),
        'sharpe': summarize_numeric('sharpe'),
        'profit_factor': summarize_numeric('profit_factor'),
        'max_drawdown_pct': summarize_numeric('max_drawdown_pct'),
        'total_trades': summarize_numeric('total_trades'),
    }
    collapse_count = sum(1 for t in perturb_trials if (t.get('cagr_pct') or 0) < 0)
    robust = (
        (perturb_summary['cagr_pct']['mean'] or 0) > 0
        and collapse_count < max(3, int(N_PERTURBATION_TRIALS * 0.3))
    )

    results['test2_perturbation'] = {
        'summary': perturb_summary,
        'trials': perturb_trials,
        'collapse_count': collapse_count,
        'robust': robust,
    }

    print(f"\n" + "-" * 70)
    print("TEST 2: Parameter Perturbation")
    print("-" * 70)
    print(f"  Sharpe: mean={perturb_summary['sharpe']['mean']:.3f}  "
          f"std={perturb_summary['sharpe']['std']:.3f}  "
          f"range=[{perturb_summary['sharpe']['min']:.3f}, {perturb_summary['sharpe']['max']:.3f}]")
    print(f"  CAGR%:  mean={perturb_summary['cagr_pct']['mean']:.1f}  "
          f"std={perturb_summary['cagr_pct']['std']:.1f}  "
          f"range=[{perturb_summary['cagr_pct']['min']:.1f}, {perturb_summary['cagr_pct']['max']:.1f}]")
    print(f"  Collapses (CAGR<0): {collapse_count}/{N_PERTURBATION_TRIALS}  → {'ROBUST' if robust else 'FRAGILE'}")

    # ── Test 3 ──
    wf_analysis = by_tag['FULL'][2]  # window analysis from FULL run
    results['test3_walkforward_consistency'] = {
        'full_metrics': m_full,
        'window_analysis': wf_analysis,
        'runtime_s': round(by_tag['FULL'][1], 1),
    }

    print(f"\n" + "-" * 70)
    print("TEST 3: Walk-Forward Consistency")
    print("-" * 70)
    if wf_analysis and 'error' not in wf_analysis:
        print(f"  Windows: {wf_analysis['n_positive_windows']}/{wf_analysis['n_windows']} profitable "
              f"({wf_analysis['win_rate_windows_pct']:.0f}%)")
        print(f"  Mean return: {wf_analysis['mean_window_return_pct']:.2f}%  "
              f"Std: {wf_analysis['std_window_return_pct']:.2f}%")
    else:
        print(f"  {wf_analysis}")

    # ── Verdicts ──
    oos_sharpe = m_oos.get('sharpe', 0) or 0
    oos_pf = m_oos.get('profit_factor', 0) or 0
    cagr_deg = degradation.get('cagr_pct')
    test1_fail = (cagr_deg is not None and cagr_deg < -50) or oos_sharpe < 0 or oos_pf < 1.0
    test1_verdict = 'FAIL - significant OOS degradation' if test1_fail else 'PASS'
    test2_verdict = 'PASS' if robust else 'FAIL - perturbation instability'

    win_rate_windows = wf_analysis.get('win_rate_windows_pct') if isinstance(wf_analysis, dict) else None
    test3_pass = isinstance(win_rate_windows, (int, float)) and win_rate_windows >= 50
    test3_verdict = 'PASS - majority profitable' if test3_pass else 'FAIL - inconsistent windows'

    verdicts = [test1_verdict.startswith('PASS'), test2_verdict.startswith('PASS'), test3_verdict.startswith('PASS')]
    overall = 'PASS' if all(verdicts) else ('MIXED' if any(verdicts) else 'FAIL')

    results['summary'] = {
        'test1_verdict': test1_verdict,
        'test2_verdict': test2_verdict,
        'test3_verdict': test3_verdict,
        'overall_verdict': overall,
        'total_runtime_s': round(parallel_time, 1),
        'total_runtime_min': round(parallel_time / 60, 1),
        'sequential_runtime_s': round(sequential_time, 1),
        'speedup': round(sequential_time / parallel_time, 1),
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)

    print("\n" + "=" * 70)
    print("VALIDATION SUMMARY")
    print("=" * 70)
    print(f"  Test 1 (IS/OOS split):     {test1_verdict}")
    print(f"  Test 2 (Perturbation):     {test2_verdict}")
    print(f"  Test 3 (WF consistency):   {test3_verdict}")
    print(f"  ──────────────────────────")
    print(f"  OVERALL:                   {overall}")
    print(f"  Runtime: {parallel_time:.0f}s ({parallel_time/60:.1f}min) — "
          f"{sequential_time/parallel_time:.1f}× speedup over sequential")
    print(f"  Saved: {output_path}")
    return 0 if overall == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
