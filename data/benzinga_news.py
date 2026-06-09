#!/usr/bin/env python3
"""Benzinga historical news ingester (Alpaca /v1beta1/news, FREE Basic tier).

Phase A1 of the news-sentiment overlay (pre-reg: research/brain/hypotheses/news_sentiment_overlay.md).
Pulls symbol-filtered Benzinga articles, paginated, into monthly parquet shards. Idempotent/resumable
(skips months already written unless --force). Bounded parallel workers stay under the 200 req/min
Basic limit. Point-in-time integrity: stores created_at UTC verbatim; never backfills tags.

Usage:
  python3 data/benzinga_news.py --start 2021-07-01 --end 2023-06-30 --workers 3
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
OUT = PROJECT / "data" / "cache" / "benzinga_news"
BASE = "https://data.alpaca.markets/v1beta1/news"


def _secret(k: str):
    if k in os.environ:
        return os.environ[k]
    p = os.path.expanduser("~/.atlas-secrets.json")
    return json.load(open(p)).get(k) if os.path.exists(p) else None


KEY = _secret("ALPACA_API_KEY")
SEC = _secret("ALPACA_SECRET_KEY")


def _months(start: str, end: str):
    s = datetime.fromisoformat(start).replace(day=1)
    e = datetime.fromisoformat(end)
    out = []
    cur = s
    while cur <= e:
        if cur.month == 12:
            nxt = cur.replace(year=cur.year + 1, month=1)
        else:
            nxt = cur.replace(month=cur.month + 1)
        out.append((cur.strftime("%Y-%m-%d"), (nxt).strftime("%Y-%m-%d"), cur.strftime("%Y-%m")))
        cur = nxt
    return out


def _get(params: dict, retries: int = 5):
    url = BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": KEY, "APCA-API-SECRET-KEY": SEC})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                time.sleep(2.0 * (attempt + 1))
                continue
            raise
        except Exception:
            time.sleep(1.0 * (attempt + 1))
    raise RuntimeError(f"giving up after {retries} retries: {params.get('start')}")


def fetch_month(args):
    start, end, tag, symbols, throttle = args
    out_path = OUT / f"{tag}.parquet"
    if out_path.exists() and out_path.stat().st_size > 0:
        return tag, -1, "skip(exists)"
    rows = []
    token = None
    pages = 0
    while True:
        p = {"start": start + "T00:00:00Z", "end": end + "T00:00:00Z", "limit": 50,
             "sort": "asc", "symbols": symbols}
        if token:
            p["page_token"] = token
        d = _get(p)
        for a in d.get("news", []):
            rows.append({
                "id": a.get("id"),
                "created_at": a.get("created_at"),
                "headline": a.get("headline") or "",
                "summary": a.get("summary") or "",
                "source": a.get("source") or "",
                "symbols": ",".join(a.get("symbols") or []),
            })
        token = d.get("next_page_token")
        pages += 1
        time.sleep(throttle)
        if not token:
            break
    df = pd.DataFrame(rows).drop_duplicates(subset=["id"])
    OUT.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_path, index=False)
    return tag, len(df), f"{pages}pg"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2021-07-01")
    ap.add_argument("--end", default="2023-06-30")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--throttle", type=float, default=0.2, help="sleep per page (rate-limit safety)")
    ap.add_argument("--symbols-file", default="/tmp/sp500_syms.json")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    if a.symbols_file and os.path.exists(a.symbols_file):
        syms = json.load(open(a.symbols_file))
    else:
        sys.path.insert(0, str(PROJECT))
        import scripts.validate_oos as vo
        syms = sorted(vo.load_data(market="sp500").keys())
    symbols = ",".join(syms)
    print(f"[ingest] {len(syms)} symbols | {a.start}..{a.end} | workers={a.workers} | throttle={a.throttle}s")

    if a.force:
        for f in OUT.glob("*.parquet"):
            f.unlink()

    months = _months(a.start, a.end)
    tasks = [(s, e, t, symbols, a.throttle) for (s, e, t) in months]
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(fetch_month, t): t[2] for t in tasks}
        for fut in as_completed(futs):
            tag, n, info = fut.result()
            done += 1
            print(f"[{done}/{len(tasks)}] {tag}: {n if n>=0 else 'skipped'} articles ({info}) "
                  f"| elapsed {time.time()-t0:.0f}s", flush=True)

    # summary
    total = 0
    for f in sorted(OUT.glob("*.parquet")):
        try:
            total += len(pd.read_parquet(f, columns=["id"]))
        except Exception:
            pass
    print(f"[ingest] DONE: {total} total articles across {len(list(OUT.glob('*.parquet')))} months "
          f"in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
