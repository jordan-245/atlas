#!/usr/bin/env python3
"""Backtest mean_reversion short signals vs long-only baseline.

Runs four walk-forward backtests:
  1. MR Long Only (solo) — current production params, no shorts
  2. MR Long+Short (solo) — with short_enabled=true
  3. Full Portfolio Baseline — all 7 strategies, no shorts
  4. Full Portfolio + Shorts — all 7 strategies, MR shorts enabled

Outputs comparison table and saves results for brain recording.
"""
import copy
import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import pandas as pd
from backtest.engine import BacktestEngine
from utils.config import get_active_config
from data.ingest import get_market_tickers
from universe.builder import get_universe_tickers
from strategies.mean_reversion import MeanReversion
from strategies.momentum_breakout import MomentumBreakout
from strategies.trend_following import TrendFollowing
from strategies.sector_rotation import SectorRotation
from strategies.short_term_mr import ShortTermMR
from strategies.opening_gap import OpeningGap
from strategies.connors_rsi2 import ConnorsRSI2

MARKET = "sp500"
OUTPUT_DIR = PROJECT / "research" / "results"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_MARKET = "sp500"


def load_data(tickers, config):
    """Load OHLCV data for tickers from cache."""
    market_id = config.get("market", DEFAULT_MARKET)
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


def get_tickers(market_id):
    """Get universe tickers."""
    try:
        return get_universe_tickers(market_id)
    except Exception:
        return get_market_tickers(market_id)[:20]


def get_strategies(config):
    """Instantiate enabled strategies from config."""
    strats = []
    sc = config["strategies"]
    if sc.get("momentum_breakout", {}).get("enabled"):
        strats.append(MomentumBreakout(config))
    if sc.get("mean_reversion", {}).get("enabled"):
        strats.append(MeanReversion(config))
    if sc.get("trend_following", {}).get("enabled"):
        strats.append(TrendFollowing(config))
    if sc.get("sector_rotation", {}).get("enabled"):
        strats.append(SectorRotation(config))
    if sc.get("short_term_mr", {}).get("enabled"):
        strats.append(ShortTermMR(config))
    if sc.get("opening_gap", {}).get("enabled"):
        strats.append(OpeningGap(config))
    if sc.get("connors_rsi2", {}).get("enabled"):
        strats.append(ConnorsRSI2(config))
    return strats


def run_backtest(label: str, config: dict, data: dict) -> dict:
    """Run a full walk-forward backtest and return metrics dict."""
    print(f"\n{'='*60}")
    print(f"  Running: {label}")
    print(f"{'='*60}")
    t0 = time.time()
    
    strategies = get_strategies(config)
    strat_names = [s.name for s in strategies]
    print(f"  Strategies: {strat_names}")
    print(f"  Tickers: {len(data)}")
    
    engine = BacktestEngine(config, market_id=MARKET)
    result = engine.run_walkforward(data, strategies)
    elapsed = time.time() - t0
    
    metrics = result.metrics if hasattr(result, "metrics") else result.get("metrics", {})
    trades = result.trades if hasattr(result, "trades") else result.get("trades", [])
    
    # Count long vs short trades
    long_trades = [t for t in trades if t.get("direction", "long") == "long"]
    short_trades = [t for t in trades if t.get("direction", "long") == "short"]
    
    # Win rates by direction
    long_wins = sum(1 for t in long_trades if t.get("pnl", 0) > 0)
    short_wins = sum(1 for t in short_trades if t.get("pnl", 0) > 0)
    
    long_pnl = sum(t.get("pnl", 0) for t in long_trades)
    short_pnl = sum(t.get("pnl", 0) for t in short_trades)
    
    # Avg short trade P&L
    avg_short_pnl = (short_pnl / len(short_trades)) if short_trades else 0
    avg_long_pnl = (long_pnl / len(long_trades)) if long_trades else 0
    
    info = {
        "label": label,
        "elapsed_s": round(elapsed, 1),
        "sharpe": metrics.get("sharpe", 0),
        "cagr_pct": metrics.get("cagr", 0) * 100,
        "max_dd_pct": metrics.get("max_drawdown", 0) * 100,
        "profit_factor": metrics.get("profit_factor", 0),
        "total_trades": metrics.get("total_trades", len(trades)),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "win_rate_pct": metrics.get("win_rate", 0) * 100,
        "long_win_rate": round(100 * long_wins / max(1, len(long_trades)), 1),
        "short_win_rate": round(100 * short_wins / max(1, len(short_trades)), 1),
        "long_pnl": round(long_pnl, 2),
        "short_pnl": round(short_pnl, 2),
        "avg_long_pnl": round(avg_long_pnl, 2),
        "avg_short_pnl": round(avg_short_pnl, 2),
        "total_return_pct": metrics.get("total_return", 0) * 100,
        "sortino": metrics.get("sortino", 0),
        "calmar": metrics.get("calmar", 0),
        "avg_trade_pnl": metrics.get("avg_trade", 0),
        "avg_hold_days": metrics.get("avg_holding_days", 0),
    }
    
    # Save full results
    safe_label = label.lower().replace(' ', '_').replace('+', '_')
    outfile = OUTPUT_DIR / f"mr_short_{safe_label}.json"
    result_data = {
        "timestamp": time.strftime("%Y%m%dT%H%M%S"),
        "label": label,
        "config_version": config.get("version", "?"),
        "metrics": metrics,
        "trade_count": len(trades),
        "long_trades": len(long_trades),
        "short_trades": len(short_trades),
        "short_trade_details": [
            {
                "ticker": t.get("ticker"),
                "entry_date": str(t.get("entry_date", "")),
                "exit_date": str(t.get("exit_date", "")),
                "entry_price": t.get("entry_price"),
                "exit_price": t.get("exit_price"),
                "pnl": t.get("pnl"),
                "pnl_pct": t.get("pnl_pct"),
                "direction": t.get("direction"),
                "exit_reason": t.get("exit_reason", ""),
                "hold_days": t.get("holding_days", t.get("hold_days", 0)),
            }
            for t in short_trades
        ],
    }
    with open(outfile, "w") as f:
        json.dump(result_data, f, indent=2, default=str)
    print(f"  Saved: {outfile.name}")
    
    return info


def make_solo_mr_config(base_config: dict, short_enabled: bool) -> dict:
    """Create config with only mean_reversion enabled."""
    cfg = copy.deepcopy(base_config)
    for strat_name in list(cfg.get("strategies", {}).keys()):
        if strat_name == "mean_reversion":
            cfg["strategies"][strat_name]["enabled"] = True
            cfg["strategies"][strat_name]["short_enabled"] = short_enabled
        else:
            cfg["strategies"][strat_name]["enabled"] = False
    return cfg


def make_full_config(base_config: dict, short_enabled: bool) -> dict:
    """Create full portfolio config, toggling MR shorts."""
    cfg = copy.deepcopy(base_config)
    cfg["strategies"]["mean_reversion"]["short_enabled"] = short_enabled
    return cfg


def print_comparison(results: list[dict]):
    """Print side-by-side comparison table."""
    print(f"\n{'='*100}")
    print(f"  MEAN REVERSION SHORT TRADING — BACKTEST COMPARISON")
    print(f"{'='*100}")
    
    header = f"{'Metric':<25}"
    for r in results:
        header += f"  {r['label']:>16}"
    print(header)
    print("-" * 100)
    
    rows = [
        ("Sharpe", "sharpe", ".3f"),
        ("CAGR %", "cagr_pct", ".2f"),
        ("Max Drawdown %", "max_dd_pct", ".2f"),
        ("Profit Factor", "profit_factor", ".2f"),
        ("Sortino", "sortino", ".3f"),
        ("Calmar", "calmar", ".3f"),
        ("Total Trades", "total_trades", "d"),
        ("  Long Trades", "long_trades", "d"),
        ("  Short Trades", "short_trades", "d"),
        ("Win Rate %", "win_rate_pct", ".1f"),
        ("  Long Win Rate %", "long_win_rate", ".1f"),
        ("  Short Win Rate %", "short_win_rate", ".1f"),
        ("Long P&L $", "long_pnl", ".2f"),
        ("Short P&L $", "short_pnl", ".2f"),
        ("Avg Long Trade $", "avg_long_pnl", ".2f"),
        ("Avg Short Trade $", "avg_short_pnl", ".2f"),
        ("Total Return %", "total_return_pct", ".2f"),
        ("Avg Hold Days", "avg_hold_days", ".1f"),
        ("Runtime (s)", "elapsed_s", ".1f"),
    ]
    
    for label, key, fmt in rows:
        row = f"{label:<25}"
        for r in results:
            val = r.get(key, 0)
            if val is None:
                val = 0
            row += f"  {val:>16{fmt}}"
        print(row)
    
    print(f"\n{'='*100}")


def main():
    print("Loading SP500 active config v3.0...")
    base_config = get_active_config(MARKET)
    
    print("Loading ticker data...")
    tickers = get_tickers(MARKET)
    data = load_data(tickers, base_config)
    print(f"  Loaded {len(data)} tickers")
    
    results = []
    
    # ── Test 1: Solo MR long-only (baseline) ──
    cfg1 = make_solo_mr_config(base_config, short_enabled=False)
    r1 = run_backtest("MR Long", cfg1, data)
    results.append(r1)
    
    # ── Test 2: Solo MR long+short ──
    cfg2 = make_solo_mr_config(base_config, short_enabled=True)
    r2 = run_backtest("MR Long+Short", cfg2, data)
    results.append(r2)
    
    # ── Test 3: Full portfolio baseline ──
    cfg3 = make_full_config(base_config, short_enabled=False)
    r3 = run_backtest("Full Base", cfg3, data)
    results.append(r3)
    
    # ── Test 4: Full portfolio with MR shorts ──
    cfg4 = make_full_config(base_config, short_enabled=True)
    r4 = run_backtest("Full+Shorts", cfg4, data)
    results.append(r4)
    
    print_comparison(results)
    
    # ── Verdict ──
    print("\n" + "="*60)
    print("  VERDICT")
    print("="*60)
    
    base_sharpe = r3["sharpe"]
    short_sharpe = r4["sharpe"]
    base_dd = r3["max_dd_pct"]
    short_dd = r4["max_dd_pct"]
    base_cagr = r3["cagr_pct"]
    short_cagr = r4["cagr_pct"]
    short_count = r4["short_trades"]
    short_wr = r4["short_win_rate"]
    short_pnl = r4["short_pnl"]
    avg_short = r4["avg_short_pnl"]
    
    # Solo MR comparison
    mr_base_sharpe = r1["sharpe"]
    mr_short_sharpe = r2["sharpe"]
    mr_short_count = r2["short_trades"]
    
    print(f"\n  Solo MR Impact:")
    print(f"    Short trades generated: {mr_short_count}")
    print(f"    Sharpe: {mr_base_sharpe:.3f} → {mr_short_sharpe:.3f} (Δ{mr_short_sharpe - mr_base_sharpe:+.3f})")
    
    print(f"\n  Full Portfolio Impact:")
    print(f"    Short trades generated: {short_count}")
    print(f"    Short win rate: {short_wr:.1f}%")
    print(f"    Short P&L: ${short_pnl:.2f} (avg ${avg_short:.2f}/trade)")
    print(f"    Sharpe: {base_sharpe:.3f} → {short_sharpe:.3f} (Δ{short_sharpe - base_sharpe:+.3f})")
    print(f"    CAGR: {base_cagr:.2f}% → {short_cagr:.2f}% (Δ{short_cagr - base_cagr:+.2f}%)")
    print(f"    MaxDD: {base_dd:.2f}% → {short_dd:.2f}% (Δ{short_dd - base_dd:+.2f}%)")
    
    if short_count < 15:
        print(f"\n  ⚠️  INSUFFICIENT DATA: Only {short_count} short trades (need ≥15)")
    
    # Evaluate
    sharpe_improved = short_sharpe > base_sharpe
    dd_acceptable = short_dd <= base_dd * 1.15  # max 15% DD increase
    cagr_acceptable = short_cagr >= base_cagr * 0.90  # max 10% CAGR loss
    shorts_profitable = short_pnl > 0
    
    if sharpe_improved and dd_acceptable and shorts_profitable:
        verdict = "POSITIVE"
        emoji = "✅"
        msg = "Shorts improve risk-adjusted returns. Consider enabling after OOS validation."
    elif cagr_acceptable and dd_acceptable and shorts_profitable:
        verdict = "NEUTRAL_POSITIVE"
        emoji = "🟡"
        msg = "Shorts are modestly positive. Run OOS validation before deciding."
    elif not shorts_profitable:
        verdict = "NEGATIVE"
        emoji = "❌"
        msg = "Shorts lose money. Do NOT enable."
    elif not dd_acceptable:
        verdict = "NEGATIVE_DD"
        emoji = "❌"
        msg = "Shorts increase drawdown unacceptably. Do NOT enable."
    else:
        verdict = "NEGATIVE"
        emoji = "❌"
        msg = "Shorts degrade portfolio metrics. Do NOT enable."
    
    print(f"\n  {emoji} {verdict}: {msg}")
    
    # Save summary
    summary = {
        "test_date": time.strftime("%Y-%m-%d"),
        "config_version": "v3.0",
        "market": MARKET,
        "results": results,
        "verdict": {
            "conclusion": verdict,
            "message": msg,
            "short_trades": short_count,
            "short_win_rate": short_wr,
            "short_pnl": short_pnl,
            "avg_short_pnl": avg_short,
            "sharpe_delta": round(short_sharpe - base_sharpe, 4),
            "cagr_delta": round(short_cagr - base_cagr, 2),
            "maxdd_delta": round(short_dd - base_dd, 2),
        }
    }
    summary_file = OUTPUT_DIR / "mr_short_comparison_summary.json"
    with open(summary_file, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    print(f"\n  Summary saved: {summary_file}")


if __name__ == "__main__":
    main()
