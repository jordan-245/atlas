#!/usr/bin/env python3
"""Build sector_map_sp500hist.json for the ingested sp500hist universe.

Reuses existing sector_map_sp500.json where possible; fetches yfinance .info sector for the rest
(best-effort, bounded concurrency). Without sectors the engine's max_sector_concentration collapses
the book to the 'Unknown' bucket (the csm bug). Unknowns are tolerated (deployment-sanity will flag).
"""
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import yfinance as yf

PROJECT = Path(__file__).resolve().parent.parent
CACHE = PROJECT / "data" / "cache" / "sp500hist"
EXISTING = PROJECT / "data" / "processed" / "sector_map_sp500.json"
OUT = PROJECT / "data" / "processed" / "sector_map_sp500hist.json"


def _sector(t):
    try:
        info = yf.Ticker(t).info
        s = info.get("sector")
        return t, (s if s else "Unknown")
    except Exception:
        return t, "Unknown"


def main():
    names = sorted(p.stem for p in CACHE.glob("*.parquet"))
    existing = json.load(open(EXISTING)) if EXISTING.exists() else {}
    smap = {}
    todo = []
    for t in names:
        if existing.get(t) and existing[t] != "Unknown":
            smap[t] = existing[t]
        else:
            todo.append(t)
    print(f"[sector map] {len(names)} names | {len(smap)} from existing | {len(todo)} to fetch", flush=True)
    done = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_sector, t): t for t in todo}
        for fut in as_completed(futs):
            t, s = fut.result()
            smap[t] = s
            done += 1
            if done % 50 == 0:
                print(f"  [{done}/{len(todo)}] fetched", flush=True)
    cov = sum(1 for v in smap.values() if v and v != "Unknown")
    json.dump(smap, open(OUT, "w"), indent=1)
    from collections import Counter
    print(f"[sector map] DONE: {len(smap)} names, {cov} with real sector ({cov/len(smap)*100:.0f}%)", flush=True)
    print("  dist:", dict(Counter(smap.values()).most_common(13)), flush=True)
    print(f"  saved {OUT}", flush=True)


if __name__ == "__main__":
    main()
