#!/usr/bin/env python3
"""Build the survivorship-correct mid/small-cap market from the local Sharadar SEP bulk file.

Two-pass over SEP.zip (no full load): (1) median $-volume per universe ticker -> pick top-N liquid
(incl. delisted-but-was-liquid = survivorship-correct), (2) write per-ticker parquet -> data/cache/
<market>/. Schema matches existing (open/high/low/close/volume/ticker, date index). SEP OHLC are
split+stock-div adjusted (fine for momentum/technical).
"""
import json
import zipfile
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
SEP_ZIP = PROJECT / "data" / "sharadar" / "SEP.zip"
UNI = PROJECT / "data" / "universes" / "sharadar_midsmall.json"
MARKET = "shm"   # sharadar mid/small
OUT = PROJECT / "data" / "cache" / MARKET
TOP_N = 1000
CHUNK = 2_000_000
COLS = ["ticker", "date", "open", "high", "low", "close", "volume"]


def main():
    uni = set(json.load(open(UNI))["tickers"])
    zf = zipfile.ZipFile(SEP_ZIP)
    name = zf.namelist()[0]
    print(f"[shm] SEP={name} | universe={len(uni)} | picking top {TOP_N} by $-volume", flush=True)

    # Pass 1: median dollar-volume proxy (sum/count) per universe ticker
    agg = {}
    with zf.open(name) as f:
        for ch in pd.read_csv(f, chunksize=CHUNK, usecols=["ticker", "close", "volume"]):
            ch = ch[ch["ticker"].isin(uni)]
            if ch.empty:
                continue
            ch = ch.assign(dv=ch["close"] * ch["volume"])
            g = ch.groupby("ticker")["dv"].agg(["sum", "count"])
            for t, r in g.iterrows():
                a = agg.get(t, [0.0, 0])
                a[0] += float(r["sum"]); a[1] += int(r["count"]); agg[t] = a
    adv = {t: (v[0] / v[1] if v[1] else 0.0) for t, v in agg.items()}
    top = sorted(adv, key=adv.get, reverse=True)[:TOP_N]
    topset = set(top)
    print(f"[shm] pass1 done: {len(adv)} tickers w/ data; top {len(top)} selected "
          f"(min ADV ${adv[top[-1]]:,.0f})", flush=True)

    # Pass 2: collect rows for top tickers, write parquet
    OUT.mkdir(parents=True, exist_ok=True)
    buf = {t: [] for t in topset}
    with zf.open(name) as f:
        for ch in pd.read_csv(f, chunksize=CHUNK, usecols=["ticker", "date", "open", "high", "low", "close", "volume"]):
            ch = ch[ch["ticker"].isin(topset)]
            if ch.empty:
                continue
            for t, sub in ch.groupby("ticker"):
                buf[t].append(sub)
    written = 0
    for t, parts in buf.items():
        if not parts:
            continue
        df = pd.concat(parts)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").drop_duplicates("date").set_index("date")
        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"])
        df = df[df.index >= "2016-01-01"]   # ~10yr is plenty; keeps files lean
        if len(df) < 260:
            continue
        df["ticker"] = t
        df.to_parquet(OUT / f"{t.replace('/', '-')}.parquet")
        written += 1
    print(f"[shm] DONE: wrote {written} parquet -> {OUT}", flush=True)
    # subset sector map to written tickers
    smap_full = json.load(open(PROJECT / "data" / "processed" / "sector_map_sharadar_midsmall.json"))
    have = {p.stem for p in OUT.glob("*.parquet")}
    smap = {t: smap_full.get(t, "Unknown") for t in have}
    json.dump(smap, open(PROJECT / "data" / "processed" / f"sector_map_{MARKET}.json", "w"), indent=1)
    cov = sum(1 for v in smap.values() if v and v != "Unknown")
    print(f"[shm] sector_map_{MARKET}.json: {cov}/{len(smap)} real sectors", flush=True)


if __name__ == "__main__":
    main()
