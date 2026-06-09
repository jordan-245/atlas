#!/usr/bin/env python3
"""8-week clean-data edge SEARCH orchestrator (board trial 2026-06-06).

Runs the rail-equipped cross-OOS battery (run_strategy_battery.run_battery) over a list of sandbox
strategies on a market, sequentially + nice'd. Each battery: Rail 1 holdout quarantine + holdout-eval
on PROMOTE (single-use), Rail 3 deployment-sanity auto-FAIL, Rail 2 FDR-aware promote bar + registry.
A strategy that ends FINAL tier PROMOTE (cleared the holdout too) is the WIN -> flagged for human.

Idempotent: skips strategies already battery-tested on this market (artifact exists) unless --force.
Headless-friendly; logs one line per strategy to research/results/search_<market>.log.
"""
import json
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
OUTDIR = PROJECT / "backtest" / "results" / "search"
SUMMARY = PROJECT / "research" / "results"

# Price-based strategies suited to a pure-OHLCV mid/small-cap search (exclude data-dependent ones:
# put_call_vix_proxy/pead_earnings_drift/dividend_capture/sector_rotation/monthly_rotation).
STRATEGIES = [
    "cross_sectional_momentum", "short_horizon_mr", "adx_trend_pullback", "donchian_breakout",
    "gap_and_go", "keltner_reversion", "lower_band_reversion", "macd_divergence",
    "relative_strength_pullback", "rsi_divergence", "stochastic_oversold", "triple_rsi",
    "volume_climax", "vwap_reversion", "williams_percent_r", "consecutive_down_days",
    "heikin_ashi_reversal", "inside_bar_nr7", "overnight_return", "demark_sequential", "mtf_momentum",
]


def run_one(strat, market, grid_size, max_positions, timeout_s):
    out = OUTDIR / f"battery_{strat}_{market}.json"
    if out.exists() and out.stat().st_size > 0:
        return strat, "skip", None
    OUTDIR.mkdir(parents=True, exist_ok=True)
    cmd = ["/usr/bin/python3", str(PROJECT / "scripts" / "run_strategy_battery.py"),
           "--strategy", strat, "--market", market, "--grid-size", str(grid_size),
           "--max-positions", str(max_positions), "--select", "default",
           "--holdout-eval", "--output-path", str(out)]
    t0 = time.time()
    try:
        subprocess.run(cmd, timeout=timeout_s, capture_output=True, text=True,
                       env={"ATLAS_BATTERY_WORKERS": "6", "PATH": "/usr/bin:/bin"})
    except subprocess.TimeoutExpired:
        return strat, "timeout", round(time.time() - t0)
    except Exception as e:
        return strat, f"err:{str(e)[:40]}", round(time.time() - t0)
    if not out.exists():
        return strat, "nofile", round(time.time() - t0)
    try:
        d = json.loads(out.read_text())
        co = d.get("cross_oos", {})
        b = co.get("bundle", {})
        tier = d.get("verdict")
        rec = {"tier": tier, "tier_raw": co.get("tier_raw"),
               "cpcv": round(b.get("median_cpcv_sharpe", float("nan")), 3),
               "dsr": round(b.get("dsr", float("nan")), 3),
               "deploy": (d.get("deployment") or {}).get("passed"),
               "holdout": (d.get("holdout") or {}).get("passed") if d.get("holdout") else None,
               "n_fam": (d.get("multiple_testing") or {}).get("n_families"),
               "secs": round(time.time() - t0)}
        return strat, tier, rec
    except Exception as e:
        return strat, f"parse_err:{str(e)[:30]}", round(time.time() - t0)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="shm")
    ap.add_argument("--grid-size", type=int, default=12)
    ap.add_argument("--max-positions", type=int, default=35)
    ap.add_argument("--timeout-s", type=int, default=5400)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    log = SUMMARY / f"search_{a.market}.log"
    SUMMARY.mkdir(parents=True, exist_ok=True)
    if a.force:
        for p in OUTDIR.glob(f"battery_*_{a.market}.json"):
            p.unlink()
    print(f"[search] market={a.market} | {len(STRATEGIES)} strategies | holdout-eval ON | rails ON", flush=True)
    promotes = []
    for i, strat in enumerate(STRATEGIES, 1):
        strat_, tier, rec = run_one(strat, a.market, a.grid_size, a.max_positions, a.timeout_s)
        line = f"[{i}/{len(STRATEGIES)}] {strat_:28s} -> {tier} {rec if isinstance(rec, dict) else (rec or '')}"
        print(line, flush=True)
        with open(log, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {line}\n")
        if tier == "PROMOTE":
            promotes.append(strat_)
            with open(SUMMARY / f"search_{a.market}_PROMOTES.txt", "a") as f:
                f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {strat_} {rec}\n")
    print(f"[search] DONE. PROMOTES (holdout-cleared): {promotes or 'NONE this pass'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
