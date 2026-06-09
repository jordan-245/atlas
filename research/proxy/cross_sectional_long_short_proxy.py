#!/usr/bin/env python3
"""Phase A FREE PROBE — cross-sectional factor LONG-SHORT (dollar-neutral).

Pre-registration: research/brain/hypotheses/equity_long_short.md
Spec:             research/strategies/cross_sectional_long_short_SPEC.md
Board memo:       ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding

QUESTION (falsifiable null): does adding a SHORT leg to the existing cross-sectional factor
ranking (6-1 momentum + low-vol/quality) improve net-of-cost OOS risk-adjusted return over being
long-only? If the short leg adds no incremental net OOS Sharpe -> kill the short leg, keep long-only.

The Atlas engine is hard-coded LONG-ONLY (Signal.direction must be 'long'), so — exactly like the
#421 gap-fade and #422 pairs proxies — this is a standalone, look-ahead-free WALK-FORWARD returns
simulator on daily closes that NEVER touches the live Signal path. The SAME ranking is used to
build (a) a long-only top-N book and (b) a dollar-neutral long-top-N / short-bottom-N book under
identical construction + costs, so the ONLY difference is the short leg. Both are judged through
the SAME adapter.assemble_bundle + evaluate_tiers battery panel the promotion gate uses.

Costs (must survive): per-side slippage on turnover (both legs) + an annualized BORROW sweep on the
short notional {0,25,50 bps}. ETB proxy = the most-liquid decile (large caps are ~always ETB; we
forgo small/illiquid shorts, which biases AGAINST finding edge — the correct direction).

Gates/kill-criteria are pre-registered in the hypothesis doc and are NOT restated/tuned here.
"""
from __future__ import annotations

import argparse
import pickle
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT))

import scripts.validate_oos as vo  # noqa: E402
from research.cross_oos import adapter  # noqa: E402
import research.cross_oos.metrics as cm  # noqa: E402
from research.proxy.gap_fade_proxy import build_panels, regime_series  # noqa: E402

ENS_CACHE = PROJECT / "backtest" / "results" / "_ensemble_cache.pkl"

# Pre-registered defaults + small grid (committed BEFORE seeing results; mirrors the live
# cross_sectional_momentum config: 6-1 momentum + partial low-vol, trend-filtered, top_n=30).
DEFAULT = dict(mom_lookback=126, mom_skip=21, vol_lookback=126, sma_period=200,
               top_n=30, bottom_n=30, rebal=21, w_mom=1.0, w_qual=0.5,
               trend_filter=True, short_below_sma=True, slip_bps=5.0)
GRID_PARAMS = [dict(top_n=n, bottom_n=n, w_qual=q, rebal=r)
               for n in (15, 20, 30) for q in (0.0, 0.5, 1.0) for r in (10, 21)]


def _z(s: pd.Series) -> pd.Series:
    sd = s.std()
    if not np.isfinite(sd) or sd < 1e-12:
        return pd.Series(0.0, index=s.index)
    return (s - s.mean()) / sd


def simulate(C: pd.DataFrame, reg: pd.Series, *, mom_lookback, mom_skip, vol_lookback,
             sma_period, top_n, bottom_n, rebal, w_mom, w_qual, trend_filter,
             short_below_sma, slip_bps, borrow_bps=0.0, long_only=False):
    """Walk-forward cross-sectional factor long-short. Returns (daily_return Series, trades list).

    Look-ahead-free: scores at rebalance day d use data up to d; the resulting weights earn
    returns from d+1 onward. Slippage charged on turnover at rebalance; borrow charged daily on
    short gross. long_only=True zeros the short leg (the apples-to-apples baseline).
    """
    ret = C.pct_change()
    mom = C.shift(mom_skip) / C.shift(mom_lookback) - 1.0
    vol = ret.rolling(vol_lookback).std()
    sma = C.rolling(sma_period, min_periods=sma_period).mean()

    idx = list(C.index)
    daily = pd.Series(0.0, index=C.index)
    trades: list[dict] = []
    warmup = max(mom_lookback, vol_lookback, sma_period) + 1
    prev_w = pd.Series(0.0, index=C.columns)
    borrow_daily = (borrow_bps / 1e4) / 252.0  # on short gross (=1.0 when shorts present)

    start = warmup
    while start < len(idx):
        d = idx[start]
        hold_dates = idx[start + 1:start + 1 + rebal]   # earn from d+1 (no look-ahead)
        if not hold_dates:
            break

        m, v, sm, px = mom.loc[d], vol.loc[d], sma.loc[d], C.loc[d]
        valid = C.columns[m.notna() & v.notna() & sm.notna() & (px > 0)]
        w = pd.Series(0.0, index=C.columns)
        longs: list[str] = []
        shorts: list[str] = []
        if len(valid) >= max(top_n, bottom_n) * 2:
            score = w_mom * _z(m[valid]) + w_qual * _z(-v[valid])
            above = px[valid] >= sm[valid]
            below = px[valid] < sm[valid]
            long_pool = score[above] if trend_filter else score
            longs = list(long_pool.sort_values(ascending=False).index[:top_n])
            if longs:
                w[longs] = 1.0 / len(longs)
            if not long_only:
                short_pool = score[below] if short_below_sma else score
                shorts = list(short_pool.sort_values(ascending=True).index[:bottom_n])
                if shorts:
                    w[shorts] = -1.0 / len(shorts)

        # turnover slippage at rebalance (both legs), charged on day d
        turnover = float((w - prev_w).abs().sum())
        daily.loc[d] += -(slip_bps / 1e4) * turnover
        short_gross = float((-w[w < 0]).sum())

        # accrue holding-period returns
        for hd in hold_dates:
            r = float((w * ret.loc[hd]).sum(skipna=True))
            if np.isfinite(r):
                daily.loc[hd] += r
            if short_gross > 0 and borrow_daily > 0:
                daily.loc[hd] += -borrow_daily * short_gross

        # record trade-level pnl for regime/group diagnostics
        entry_reg = reg.loc[d] if d in reg.index else "neutral"
        win = C.loc[hold_dates]
        for t in longs + shorts:
            if t in win.columns and len(win) >= 1 and px[t] > 0:
                pr = float(win[t].iloc[-1] / px[t] - 1.0)
                sign = 1.0 if t in longs else -1.0
                trades.append({"ticker": t, "strategy": "cross_sectional_long_short",
                               "direction": "long" if sign > 0 else "short",
                               "pnl": sign * pr * 1e3, "exit_date": hold_dates[-1],
                               "entry_regime": entry_reg})
        prev_w = w
        start += rebal

    return daily, trades


def _panel(b: dict) -> str:
    return (f"CPCV {b['median_cpcv_sharpe']:+.3f} | frac+ {b['frac_paths_positive']:.2f} "
            f"| PBO {b['pbo']:.3f} | DSR {b['dsr']:.3f} | regime_conc {b['regime_concentration_ratio']:.2f} "
            f"| per_regime_ok {b['per_regime_expectancy_ok']} | min_regime {b['min_regime_sharpe']:+.2f} "
            f"| top_grp {b['top_group_frac']:.2f} | loo_ok {b['loo_group_ok']} "
            f"| fwd {b.get('forward_net', float('nan')):+.0f}")


def _score(returns: pd.Series, trades: list, grid: dict, label: str):
    split = int(len(returns) * 0.80)
    fnet = float(returns.iloc[split:].sum() * 1e4)
    out = adapter.assemble_bundle(returns.dropna(), trades, grid, forward_net=fnet)
    tiers = adapter.evaluate_tiers(out["bundle"])
    sharpe = cm.annualized_sharpe(returns.to_numpy())
    print(f"\n--- {label} | net Sharpe {sharpe:+.3f} | cum {returns.sum()*100:+.1f}% | trades {len(trades)}")
    print("    " + _panel(out["bundle"]))
    print(f"    TIER: {tiers.get('tier')}")
    return {"sharpe": float(sharpe), "cum": float(returns.sum()), "trades": len(trades),
            "tier": tiers.get("tier"), "bundle": out["bundle"], "diagnostics": out["diagnostics"]}


def main(market="sp500", top_liquid=150):
    print(f"=== Phase A: cross_sectional_long_short ({market}) ===")
    data = vo.load_data(market=market)
    O, C = build_panels(data, top_liquid=top_liquid)
    reg = regime_series(C)
    print(f"universe: {C.shape[1]} most-liquid (ETB proxy) | {C.shape[0]} days "
          f"{C.index.min().date()}..{C.index.max().date()}")

    # grid of LONG-SHORT configs (the 'search' the DSR deflates)
    ls_grid = {}
    for gp in GRID_PARAMS:
        cfg = {**DEFAULT, **gp}
        s, _ = simulate(C, reg, **cfg)
        ls_grid[f"n{gp['top_n']}_q{gp['w_qual']}_r{gp['rebal']}"] = s

    # apples-to-apples: same construction, short leg ON vs OFF, default config
    lo_ret, lo_tr = simulate(C, reg, **DEFAULT, long_only=True)
    lo_grid = {}
    for gp in GRID_PARAMS:
        cfg = {**DEFAULT, **gp}
        s, _ = simulate(C, reg, **cfg, long_only=True)
        lo_grid[f"n{gp['top_n']}_q{gp['w_qual']}_r{gp['rebal']}"] = s

    res_lo = _score(lo_ret, lo_tr, lo_grid, "LONG-ONLY leg (proxy baseline)")

    print("\n=== borrow sweep (long-short, default config) ===")
    sweep = {}
    for bbps in (0.0, 25.0, 50.0):
        ls_ret, ls_tr = simulate(C, reg, **DEFAULT, borrow_bps=bbps)
        res = _score(ls_ret, ls_tr, ls_grid, f"LONG-SHORT borrow={bbps:.0f}bps")
        sweep[bbps] = res

    # engine cached long-only csm reference (different mechanics; context only)
    csm_ref = None
    if ENS_CACHE.exists():
        comp = pickle.loads(ENS_CACHE.read_bytes())
        if "cross_sectional_momentum" in comp:
            rc = comp["cross_sectional_momentum"]["returns"]
            csm_ref = float(cm.annualized_sharpe(rc.to_numpy()))

    # ---- verdict vs pre-registered gates ----
    base = res_lo["sharpe"]
    ls0 = sweep[0.0]["sharpe"]; ls50 = sweep[50.0]["sharpe"]
    beats = ls50 >= base                      # must beat long-only AFTER worst-case borrow
    incremental = ls0 - base
    print("\n" + "=" * 72)
    print("PRE-REGISTERED VERDICT (gates in research/brain/hypotheses/equity_long_short.md)")
    print(f"  long-only proxy baseline net Sharpe : {base:+.3f}")
    if csm_ref is not None:
        print(f"  (ref) engine cached csm net Sharpe  : {csm_ref:+.3f}  [different mechanics, context]")
    print(f"  long-short net Sharpe  @0bps borrow  : {ls0:+.3f}   (incremental vs long-only {incremental:+.3f})")
    print(f"  long-short net Sharpe  @50bps borrow : {ls50:+.3f}")
    print(f"  best tier across long-short sweep    : {max((sweep[b]['tier'] for b in sweep), key=lambda t: ['FAIL','SCREEN','PROMOTE'].index(t))}")
    # KILL conditions (any one)
    kill = []
    if ls50 < 0.30:
        kill.append("net-of-cost (worst borrow) Sharpe < 0.30")
    if incremental <= 0.0:
        kill.append("short leg adds NO incremental net OOS Sharpe over long-only")
    if not beats:
        kill.append("does not beat long-only after worst-case borrow")
    if sweep[0.0]["trades"] < 50:
        kill.append("<50 trades")
    if ls0 > 0 and ls50 <= 0:
        kill.append("edge only survives at $0 borrow")
    verdict = "KILL" if kill else "ADVANCE-TO-PAPER (clears Phase A bar — confirm regime/DSR panel)"
    print(f"  KILL conditions hit: {kill if kill else 'none'}")
    print(f"  ==> {verdict}")
    print("=" * 72)

    # append TSV row
    tsv = PROJECT / "research" / "results" / "cross_sectional_long_short.tsv"
    newfile = not tsv.exists()
    b0 = sweep[0.0]["bundle"]
    with open(tsv, "a") as f:
        if newfile:
            f.write("timestamp\tsharpe\ttrades\tmax_dd_pct\tpf\tcagr_pct\tparams_changed\tstatus\tdescription\n")
        ts = pd.Timestamp.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        status = "dead_end" if kill else "keep"
        desc = (f"PhaseA long-short vs long-only baseline {base:+.2f}; LS@0bps {ls0:+.2f} "
                f"(incr {incremental:+.2f}) @50bps {ls50:+.2f}; "
                f"DSR {b0['dsr']:.2f} PBO {b0['pbo']:.2f} per_regime_ok {b0['per_regime_expectancy_ok']}; "
                f"verdict {verdict}; KILL={kill}")
        f.write(f"{ts}\t{ls0:.4f}\t{sweep[0.0]['trades']}\t\t\t\tlong_short_default\t{status}\t{desc}\n")
    print(f"\nappended -> {tsv}")
    return {"baseline": base, "csm_ref": csm_ref, "ls0": ls0, "ls50": ls50,
            "incremental": incremental, "kill": kill, "verdict": verdict}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="sp500")
    ap.add_argument("--top-liquid", type=int, default=150)
    a = ap.parse_args()
    main(a.market, a.top_liquid)
