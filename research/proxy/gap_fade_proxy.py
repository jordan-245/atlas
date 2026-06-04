#!/usr/bin/env python3
"""#421 Stage-0(a) FREE PROBE — daily-OHLC intraday proxy (gap-fade).

Tests the board's intraday-microstructure hypothesis using ONLY data we already own
(daily OHLC), at $0, with NO production-engine changes. A gap-fade is the cleanest
look-ahead-free intraday proxy expressible from daily bars:

    setup observed AT the open[t]: gap = open[t]/close[t-1] - 1
    if gap <= -gap_thresh (down-gap) AND close[t-1] > SMA200 (uptrend):
        BUY at open[t], SELL at close[t]   (intraday hold)
        net return = close[t]/open[t] - 1 - round_trip_cost

No same-bar look-ahead: the entry decision uses open[t] + history through t-1; the
exit is the same bar's close (an intraday round trip). Evaluated through the IDENTICAL
cross-OOS gate panel (assemble_bundle + evaluate_tiers) the battery uses.

Costs are PESSIMISTIC per board mandate: round-trip bps subtracted from every trade,
and the probe runs on the MOST-LIQUID names (best-case spreads) -> fail-here is a
decisive signal that the daily-derived intraday edge does not exist net-of-cost.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT))

import scripts.validate_oos as vo  # noqa: E402
from research.cross_oos import adapter  # noqa: E402

COST_BPS_RT_DEFAULT = 15.0  # pessimistic round-trip (spread+fee) for liquid large-caps


def build_panels(data: dict, top_liquid: int | None = None):
    opens, closes, vols = {}, {}, {}
    for t, df in data.items():
        if df is None or len(df) < 260 or not {"open", "close"}.issubset(df.columns):
            continue
        opens[t], closes[t] = df["open"], df["close"]
        vols[t] = (df["close"] * df.get("volume", pd.Series(index=df.index, dtype=float)))
    O = pd.DataFrame(opens).sort_index()
    C = pd.DataFrame(closes).sort_index()
    O, C = O.align(C, join="inner")
    if top_liquid:
        adv = pd.DataFrame(vols).reindex(C.index).mean(axis=0).sort_values(ascending=False)
        keep = [t for t in adv.index[:top_liquid] if t in C.columns]
        O, C = O[keep], C[keep]
    return O, C


def regime_series(C: pd.DataFrame) -> pd.Series:
    """3-state breadth regime (matches the engine's breadth signal): fraction of the
    universe above its own 200-day MA -> bull >=0.60, bear <0.40, else neutral."""
    sma200 = C.rolling(200, min_periods=200).mean()
    breadth = (C >= sma200).mean(axis=1)
    reg = pd.Series("neutral", index=C.index)
    reg[breadth >= 0.60] = "bull"
    reg[breadth < 0.40] = "bear"
    return reg


def simulate(O, C, reg, *, gap_thresh, top_n, trend_filter=True, min_price=5.0,
             cost_bps_rt=COST_BPS_RT_DEFAULT):
    prev_close = C.shift(1)
    gap = O / prev_close - 1.0
    sma200_prev = prev_close.rolling(200, min_periods=200).mean()
    elig = (gap <= -gap_thresh) & (prev_close >= min_price)
    if trend_filter:
        elig = elig & (prev_close > sma200_prev)
    intraday_ret = C / O - 1.0
    cost = cost_bps_rt / 1e4

    gap_masked = gap.where(elig)
    daily, trades = [], []
    for date in C.index:
        row = gap_masked.loc[date].dropna()
        if row.empty:
            daily.append(0.0)
            continue
        picks = row.nsmallest(int(top_n)).index
        r = intraday_ret.loc[date, picks].dropna()
        if r.empty:
            daily.append(0.0)
            continue
        net = r - cost
        daily.append(float(net.mean()))
        rg = reg.loc[date]
        for tk in net.index:
            trades.append({"ticker": tk, "pnl": float(net[tk]) * 1e4,
                           "exit_date": date, "entry_regime": rg})
    return pd.Series(daily, index=C.index, dtype=float), trades


# Pre-registered default + small grid (committed BEFORE seeing results).
DEFAULT = {"gap_thresh": 0.010, "top_n": 10, "trend_filter": True}
GRID = [{"gap_thresh": g, "top_n": n, "trend_filter": True}
        for g in (0.005, 0.010, 0.015, 0.020) for n in (5, 10, 20)]


def run(market="sp500", top_liquid=120, cost_bps_rt=COST_BPS_RT_DEFAULT):
    print(f"=== #421 gap-fade intraday proxy ({market}) ===")
    data = vo.load_data(market=market)
    O, C = build_panels(data, top_liquid=top_liquid)
    reg = regime_series(C)
    print(f"universe: {C.shape[1]} most-liquid tickers | {C.shape[0]} days "
          f"| pessimistic cost {cost_bps_rt:.0f}bps round-trip")
    print(f"regime mix: {reg.value_counts().to_dict()}")

    # gross (default, no cost) for reference
    pr_gross, _ = simulate(O, C, reg, **DEFAULT, cost_bps_rt=0.0)
    pr, trades = simulate(O, C, reg, **DEFAULT, cost_bps_rt=cost_bps_rt)
    n_trade_days = int((pr != 0).sum())
    import research.cross_oos.metrics as cm
    print(f"DEFAULT {DEFAULT}: trade-days={n_trade_days} trades={len(trades)} "
          f"gross Sharpe={cm.annualized_sharpe(pr_gross.to_numpy()):.2f} "
          f"NET Sharpe={cm.annualized_sharpe(pr.to_numpy()):.2f} "
          f"net cum={pr.sum()*100:.1f}%")

    grid_returns = {}
    for cfg in GRID:
        s, _ = simulate(O, C, reg, **cfg, cost_bps_rt=cost_bps_rt)
        grid_returns[f"g{cfg['gap_thresh']}_n{cfg['top_n']}"] = s

    # forward holdout = last 20% of the sample (net cum return)
    split = int(len(pr) * 0.80)
    forward_net = float(pr.iloc[split:].sum() * 1e4)

    out = adapter.assemble_bundle(pr, trades, grid_returns, forward_net=forward_net)
    tiers = adapter.evaluate_tiers(out["bundle"])
    b, d = out["bundle"], out["diagnostics"]
    print("\n--- GATE PANEL (net-of-cost, same gates as battery) ---")
    print(f"CPCV median {b['median_cpcv_sharpe']:.3f} | frac+ {b['frac_paths_positive']:.2f} "
          f"| PBO {b['pbo']:.3f} | DSR {b['dsr']:.3f}")
    print(f"top_ticker {b['top_group_frac']:.2f} | loo_ok {b['loo_group_ok']} "
          f"| min_regime {b['min_regime_sharpe']:.2f} | regime_conc {b['regime_concentration_ratio']:.2f} "
          f"| per_regime_ok {b['per_regime_expectancy_ok']} | forward_net {b.get('forward_net', float('nan')):.1f}")
    print(f"regime_net: { {k: round(v,1) for k,v in d['regime']['regime_net'].items()} }")
    print(f"\nGATES: {tiers.get('gates')}")
    print(f"TIER: {tiers.get('tier')}  (SCREEN dsr>={adapter.SCREEN_DSR}, PROMOTE dsr>={adapter.PROMOTE_DSR})")
    return tiers


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="sp500")
    ap.add_argument("--top-liquid", type=int, default=120)
    ap.add_argument("--cost-bps", type=float, default=COST_BPS_RT_DEFAULT)
    a = ap.parse_args()
    run(a.market, a.top_liquid, a.cost_bps)
