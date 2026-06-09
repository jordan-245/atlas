#!/usr/bin/env python3
"""One-off ingest: survivorship-correct S&P500-historical universe -> data/cache/sp500hist/.

Source list: data/universes/sp500_hist_survivorship.json (496 tradable + ~588 delisted base symbols).
Free yfinance OHLCV; delisted names that Yahoo still serves (~32%) are the partial survivorship
correction. Schema matches the existing sp500 parquet (open/high/low/close/volume/ticker, date index).
"""
import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "cache" / "sp500hist"
ART = PROJECT / "data" / "universes" / "sp500_hist_survivorship.json"
START, END = "2018-06-01", "2026-06-05"
MIN_ROWS = 260


NEED = ["open", "high", "low", "close", "volume"]


def _write(t: str, df: pd.DataFrame) -> bool:
    df = df.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if not all(c in df.columns for c in NEED):
        return False
    out = df[NEED].copy()
    out["ticker"] = t
    out.index.name = "date"
    out = out.dropna(subset=["close"])
    if len(out) < MIN_ROWS:
        return False
    OUT.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT / f"{t.replace('/', '-')}.parquet")
    return True


def _have(t: str) -> bool:
    p = OUT / f"{t.replace('/', '-')}.parquet"
    return p.exists() and p.stat().st_size > 0


def main():
    art = json.load(open(ART))
    names = sorted(set(art["tradable_now"]) | set(art["backtest_only_delisted"]))
    tradable = set(art["tradable_now"])
    OUT.mkdir(parents=True, exist_ok=True)
    missing = [t for t in names if not _have(t)]
    print(f"[ingest sp500hist] {len(names)} candidates, {len(missing)} missing -> batched yfinance", flush=True)

    CHUNK = 40
    ok = ok_delisted = 0
    for i in range(0, len(missing), CHUNK):
        chunk = missing[i:i + CHUNK]
        data = None
        for attempt in range(3):
            try:
                data = yf.download(chunk, start=START, end=END, progress=False, auto_adjust=False,
                                   group_by="ticker", threads=True)
                if data is not None and len(data) > 0:
                    break
            except Exception:
                pass
            time.sleep(5 * (attempt + 1))
        if data is None or len(data) == 0:
            print(f"  chunk {i//CHUNK}: empty (rate-limited?), skipping", flush=True)
            time.sleep(10)
            continue
        for t in chunk:
            try:
                df = data[t] if isinstance(data.columns, pd.MultiIndex) else data
                if df is None or len(df) < MIN_ROWS:
                    continue
                if _write(t, df):
                    ok += 1
                    if t not in tradable:
                        ok_delisted += 1
            except (KeyError, Exception):
                continue
        total = len(list(OUT.glob("*.parquet")))
        print(f"  [{min(i+CHUNK,len(missing))}/{len(missing)}] new_ok={ok} (delisted={ok_delisted}) total_parquet={total}", flush=True)
        time.sleep(2)

    total = len(list(OUT.glob("*.parquet")))
    n_tradable = sum(1 for t in tradable if _have(t))
    print(f"[ingest sp500hist] DONE: {total} parquet | tradable_covered={n_tradable}/{len(tradable)} "
          f"delisted_recovered={total - n_tradable}", flush=True)


if __name__ == "__main__":
    main()
