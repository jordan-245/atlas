#!/usr/bin/env python3
"""Phase A2 — news-sentiment as a tilt/overlay on the long-only factor book (backtestable).

Pre-reg: research/brain/hypotheses/news_sentiment_overlay.md | Spec: news_sentiment_overlay_SPEC.md
Board: ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding (fast-follow #2).

QUESTION (falsifiable null): does blending a deterministic Loughran-McDonald sentiment signal
(Benzinga history, FREE) into the cross-sectional factor rank improve net-of-cost OOS Sharpe over
the pure factor book? If incremental <= 0 (or it only works with look-ahead) -> KILL.

Same returns-based-proxy + cross-OOS battery pattern that killed the long-short sleeve. Long-only
(the long-short kill stands). Pure-factor (w_sent=0) is the apples-to-apples baseline; sentiment
tilts (w_sent>0) must BEAT it by >= +0.10 Sharpe net of costs. Mandatory no-look-ahead: signal for
day d uses news strictly BEFORE d (lag=1 trading day); a lag=0 look-ahead variant is run as a
falsification check — if edge only exists at lag=0, KILL.
"""
from __future__ import annotations

import argparse
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
from research.sentiment.lm_score import daily_symbol_sentiment  # noqa: E402

DEFAULT = dict(mom_lookback=126, mom_skip=21, vol_lookback=126, sma_period=200,
               top_n=30, rebal=21, w_mom=1.0, w_qual=0.5, sent_window=5, slip_bps=5.0)
W_SENT_GRID = [0.0, 0.25, 0.5, 1.0]


def _z(s: pd.Series) -> pd.Series:
    sd = s.std()
    return pd.Series(0.0, index=s.index) if (not np.isfinite(sd) or sd < 1e-12) else (s - s.mean()) / sd


def build_sentiment_signal(C: pd.DataFrame, universe: set, *, sent_window: int, lag: int):
    """Per trading-day x symbol windowed mean polarity, point-in-time with `lag` trading days.

    lag=1 (safe): news on/through trading day t-1 informs the signal used at day t.
    lag=0 (look-ahead falsification): same-day news allowed.
    """
    tidy = daily_symbol_sentiment(universe=universe)
    if tidy.empty:
        return None
    tdays = pd.DatetimeIndex(C.index)
    cal = pd.DatetimeIndex(tidy["cal_date"]).tz_localize(None)
    # assign each cal_date to a trading day: lag=0 -> first tday >= cal; lag=1 -> first tday > cal
    side = "left" if lag == 0 else "right"
    pos = tdays.searchsorted(cal, side=side)
    keep = pos < len(tdays)
    tidy = tidy.loc[keep].copy()
    tidy["tday"] = tdays[pos[keep]]
    S = tidy.pivot_table(index="tday", columns="symbol", values="sent_sum", aggfunc="sum")
    N = tidy.pivot_table(index="tday", columns="symbol", values="news_count", aggfunc="sum")
    S = S.reindex(index=C.index, columns=C.columns).fillna(0.0)
    N = N.reindex(index=C.index, columns=C.columns).fillna(0.0)
    Ssum = S.rolling(sent_window, min_periods=1).sum()
    Ncnt = N.rolling(sent_window, min_periods=1).sum()
    signal = Ssum / (Ncnt + 1.0)            # windowed mean polarity (attention-weighted)
    return signal, float((N.sum().sum()))    # signal panel + total news obs


def simulate(C, reg, sent_signal, *, mom_lookback, mom_skip, vol_lookback, sma_period,
             top_n, rebal, w_mom, w_qual, sent_window, slip_bps, w_sent=0.0):
    ret = C.pct_change()
    mom = C.shift(mom_skip) / C.shift(mom_lookback) - 1.0
    vol = ret.rolling(vol_lookback).std()
    sma = C.rolling(sma_period, min_periods=sma_period).mean()
    idx = list(C.index)
    daily = pd.Series(0.0, index=C.index)
    trades: list[dict] = []
    warmup = max(mom_lookback, vol_lookback, sma_period) + 1
    prev_w = pd.Series(0.0, index=C.columns)
    start = warmup
    while start < len(idx):
        d = idx[start]
        hold = idx[start + 1:start + 1 + rebal]
        if not hold:
            break
        m, v, sm, px = mom.loc[d], vol.loc[d], sma.loc[d], C.loc[d]
        valid = C.columns[m.notna() & v.notna() & sm.notna() & (px > 0)]
        w = pd.Series(0.0, index=C.columns)
        longs: list[str] = []
        if len(valid) >= top_n * 2:
            score = w_mom * _z(m[valid]) + w_qual * _z(-v[valid])
            if w_sent and sent_signal is not None:
                sig = sent_signal.loc[d, valid] if d in sent_signal.index else pd.Series(0.0, index=valid)
                score = score + w_sent * _z(sig.fillna(0.0))
            above = px[valid] >= sm[valid]
            longs = list(score[above].sort_values(ascending=False).index[:top_n])
            if longs:
                w[longs] = 1.0 / len(longs)
        daily.loc[d] += -(slip_bps / 1e4) * float((w - prev_w).abs().sum())
        for hd in hold:
            r = float((w * ret.loc[hd]).sum(skipna=True))
            if np.isfinite(r):
                daily.loc[hd] += r
        entry_reg = reg.loc[d] if d in reg.index else "neutral"
        win = C.loc[hold]
        for t in longs:
            if t in win.columns and px[t] > 0:
                trades.append({"ticker": t, "strategy": "news_sentiment", "direction": "long",
                               "pnl": float(win[t].iloc[-1] / px[t] - 1.0) * 1e3,
                               "exit_date": hold[-1], "entry_regime": entry_reg})
        prev_w = w
        start += rebal
    return daily, trades


def _panel(b):
    return (f"CPCV {b['median_cpcv_sharpe']:+.3f} | PBO {b['pbo']:.3f} | DSR {b['dsr']:.3f} "
            f"| per_regime_ok {b['per_regime_expectancy_ok']} | min_regime {b['min_regime_sharpe']:+.2f} "
            f"| fwd {b.get('forward_net', float('nan')):+.0f}")


def _score(returns, trades, grid, label):
    split = int(len(returns) * 0.80)
    out = adapter.assemble_bundle(returns.dropna(), trades, grid, forward_net=float(returns.iloc[split:].sum() * 1e4))
    tiers = adapter.evaluate_tiers(out["bundle"])
    sh = cm.annualized_sharpe(returns.to_numpy())
    print(f"\n--- {label} | net Sharpe {sh:+.3f} | cum {returns.sum()*100:+.1f}% | trades {len(trades)}")
    print("    " + _panel(out["bundle"]) + f" | TIER {tiers.get('tier')}")
    return {"sharpe": float(sh), "tier": tiers.get("tier"), "bundle": out["bundle"], "trades": len(trades)}


def event_study(C, reg, sent_signal, horizons=(1, 3, 5, 10)):
    ret = C.pct_change()
    sig = sent_signal.copy()
    # strong buckets via cross-sectional sign of standardized signal each day
    z = sig.sub(sig.mean(axis=1), axis=0).div(sig.std(axis=1).replace(0, np.nan), axis=0)
    print("\n=== event study: mean fwd return by sentiment bucket (net of 5bps) ===")
    for h in horizons:
        fwd = C.shift(-h) / C - 1.0 - 5e-4
        pos_mask = z > 0.75
        neg_mask = z < -0.75
        mp = fwd.where(pos_mask).stack().mean()
        mn = fwd.where(neg_mask).stack().mean()
        spread = (mp - mn) * 100 if (mp == mp and mn == mn) else float("nan")
        print(f"  h={h:2d}d: pos {mp*100:+.2f}% | neg {mn*100:+.2f}% | spread {spread:+.2f}pp")


def main(market="sp500", top_liquid=150):
    print(f"=== Phase A2: news-sentiment tilt on csm ({market}) ===")
    data = vo.load_data(market=market)
    O, C = build_panels(data, top_liquid=top_liquid)
    reg = regime_series(C)
    universe = set(C.columns)
    sig_safe, n_obs = build_sentiment_signal(C, universe, sent_window=DEFAULT["sent_window"], lag=1)
    # start the eval window where news coverage is DENSE (breadth-based), not at the first sparse
    # stray article (old Benzinga pieces updated-in-window surface with their original created_at).
    breadth = (sig_safe != 0).sum(axis=1)
    dense = breadth.rolling(10, min_periods=10).mean() >= 0.10 * C.shape[1]
    first = dense.idxmax() if dense.any() else C.index[0]
    Cc = C.loc[first:]; regc = reg.loc[first:]; sigc = sig_safe.loc[first:]
    print(f"universe {C.shape[1]} | news obs {n_obs:.0f} | dense-news eval window "
          f"{Cc.index.min().date()}..{Cc.index.max().date()} ({len(Cc)}d)")

    # grid per w_sent (for PBO/DSR) = vary top_n/rebal
    def gridfor(ws, signal, Cx, regx):
        g = {}
        for tn in (15, 20, 30):
            for rb in (10, 21):
                cfg = {**DEFAULT, "top_n": tn, "rebal": rb}
                s, _ = simulate(Cx, regx, signal, **cfg, w_sent=ws)
                g[f"n{tn}_r{rb}"] = s
        return g

    results = {}
    for ws in W_SENT_GRID:
        r, tr = simulate(Cc, regc, sigc, **DEFAULT, w_sent=ws)
        results[ws] = _score(r, tr, gridfor(ws, sigc, Cc, regc), f"w_sent={ws} (lag=1 safe)")

    base = results[0.0]["sharpe"]
    best_ws = max([w for w in W_SENT_GRID if w > 0], key=lambda w: results[w]["sharpe"])
    best = results[best_ws]["sharpe"]
    incr = best - base

    # look-ahead falsification: best w_sent at lag=0
    sig_la, _ = build_sentiment_signal(C, universe, sent_window=DEFAULT["sent_window"], lag=0)
    sig_la = sig_la.loc[first:]
    r_la, tr_la = simulate(Cc, regc, sig_la, **DEFAULT, w_sent=best_ws)
    la = _score(r_la, tr_la, gridfor(best_ws, sig_la, Cc, regc), f"w_sent={best_ws} (lag=0 LOOK-AHEAD check)")
    lookahead_only = (la["sharpe"] - base) > 0.10 and incr <= 0.10

    event_study(Cc, regc, sigc)

    # ---- pre-registered verdict ----
    kill = []
    if incr <= 0.0:
        kill.append("sentiment tilt adds NO incremental net Sharpe vs pure factor")
    elif incr < 0.10:
        kill.append(f"incremental {incr:+.3f} < required +0.10 Sharpe")
    if best < base:
        kill.append("best tilt does not beat base book")
    if lookahead_only:
        kill.append("edge only appears with look-ahead (lag=0), collapses at lag=1")
    verdict = "KILL" if kill else "ADVANCE-TO-PAPER (clears A2 bar — confirm DSR/regime panel)"

    print("\n" + "=" * 74)
    print("PRE-REGISTERED VERDICT (gates: research/brain/hypotheses/news_sentiment_overlay.md)")
    print(f"  pure-factor baseline (w_sent=0) net Sharpe : {base:+.3f}")
    for ws in W_SENT_GRID:
        if ws > 0:
            print(f"  + sentiment w={ws:<4} net Sharpe            : {results[ws]['sharpe']:+.3f}  (incr {results[ws]['sharpe']-base:+.3f}, tier {results[ws]['tier']})")
    print(f"  best tilt w={best_ws} incremental             : {incr:+.3f}  (bar +0.10)")
    print(f"  look-ahead(lag0) Sharpe @w={best_ws}           : {la['sharpe']:+.3f}  (look-ahead-only: {lookahead_only})")
    print(f"  KILL conditions: {kill if kill else 'none'}")
    print(f"  ==> {verdict}")
    print("=" * 74)

    tsv = PROJECT / "research" / "results" / "news_sentiment_overlay.tsv"
    newf = not tsv.exists()
    b = results[best_ws]["bundle"]
    with open(tsv, "a") as f:
        if newf:
            f.write("timestamp\tsharpe\ttrades\tmax_dd_pct\tpf\tcagr_pct\tparams_changed\tstatus\tdescription\n")
        ts = pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%dT%H:%M:%S")
        status = "dead_end" if kill else "keep"
        desc = (f"A2 sentiment tilt; base {base:+.2f}; best w={best_ws} {best:+.2f} (incr {incr:+.2f}); "
                f"lag0 {la['sharpe']:+.2f} la_only {lookahead_only}; DSR {b['dsr']:.2f}; verdict {verdict}; KILL={kill}")
        f.write(f"{ts}\t{best:.4f}\t{results[best_ws]['trades']}\t\t\t\tsent_w{best_ws}\t{status}\t{desc}\n")
    print(f"\nappended -> {tsv}")
    return {"base": base, "best_ws": best_ws, "best": best, "incr": incr, "kill": kill, "verdict": verdict}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="sp500")
    ap.add_argument("--top-liquid", type=int, default=150)
    a = ap.parse_args()
    main(a.market, a.top_liquid)
