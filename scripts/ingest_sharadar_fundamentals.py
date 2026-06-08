#!/usr/bin/env python3
"""Ingest Sharadar SF1 (Core US Fundamentals) -> point-in-time factor panel for the `shm` market.

Gate-1 readiness (pre-reg: research/strategies/cross_sectional_value_quality_GATE1_SPEC.md).
- Reads data/sharadar/SF1.zip (bulk export). REFUSES to proceed if it's the non-entitled SAMPLE
  (30 Dow mega-caps / MRY-only) -> tells you to subscribe SF1 first.
- Filters to the 609 cached `shm` names, dimension ARQ (as-reported quarterly).
- Keeps ONLY point-in-time columns; `datekey` is the availability date (factors lag to >= datekey).
- Prints the COVERAGE REPORT that resolves Gate-0 criterion 3 (>=60% names, >=12 quarters).
- Writes data/cache/shm_fundamentals.parquet (long format) for the strategy class to consume.

No look-ahead is introduced here: this is raw PIT storage. The strategy class is responsible for
only using a row on/after datekey (+1 trading day) at each rebalance.
"""
import json
import sys
import zipfile
from pathlib import Path

import pandas as pd

PROJECT = Path(__file__).resolve().parent.parent
SF1_ZIP = PROJECT / "data" / "sharadar" / "SF1.zip"
SHM_CACHE = PROJECT / "data" / "cache" / "shm"
OUT = PROJECT / "data" / "cache" / "shm_fundamentals.parquet"
CHUNK = 500_000

# columns we need for the FROZEN value+quality composite (+ PIT keys)
USECOLS = [
    "ticker", "dimension", "calendardate", "datekey", "reportperiod",
    "marketcap", "price", "netinc", "bvps", "fcf",
    "roe", "roa", "grossmargin", "de", "pe", "pb", "ps",
]
MIN_QUARTERS = 12
MIN_COVERAGE_PCT = 60.0


def _shm_names() -> set:
    return {p.stem for p in SHM_CACHE.glob("*.parquet")}


def main() -> int:
    if not SF1_ZIP.exists():
        print(f"ERROR: {SF1_ZIP} not found. Run: python3 scripts/sharadar_download.py SF1", flush=True)
        return 2
    names = _shm_names()
    if not names:
        print(f"ERROR: no cached shm names under {SHM_CACHE}", flush=True)
        return 2
    print(f"[fund] shm universe = {len(names)} names; reading {SF1_ZIP.name}", flush=True)

    zf = zipfile.ZipFile(SF1_ZIP)
    inner = zf.namelist()[0]

    kept = []
    seen_tickers = set()
    seen_dims = set()
    n_rows = 0
    with zf.open(inner) as f:
        for ch in pd.read_csv(f, chunksize=CHUNK, usecols=lambda c: c in USECOLS):
            n_rows += len(ch)
            seen_tickers.update(ch["ticker"].unique().tolist())
            seen_dims.update(ch["dimension"].unique().tolist())
            sub = ch[(ch["ticker"].isin(names)) & (ch["dimension"] == "ARQ")]
            if not sub.empty:
                kept.append(sub)

    # --- SAMPLE-FILE GUARD (non-entitled export) ---
    if len(seen_tickers) < 200 and seen_dims <= {"MRY"}:
        print("\n*** SAMPLE FILE DETECTED — NOT ENTITLED ***", flush=True)
        print(f"  unique tickers in file: {len(seen_tickers)} | dimensions: {sorted(seen_dims)}", flush=True)
        print("  This is the free 30-stock Dow/MRY sample, not the full SF1 dataset.", flush=True)
        print("  ACTION: subscribe to SF1 (data.nasdaq.com/databases/SF1) on the account tied to", flush=True)
        print("          NASDAQ_DATA_LINK_API_KEY, then re-run sharadar_download.py SF1.", flush=True)
        return 3

    if not kept:
        print(f"\nERROR: 0 ARQ rows matched the shm universe (file had {len(seen_tickers)} tickers, "
              f"dims={sorted(seen_dims)}). Unexpected — inspect the export.", flush=True)
        return 3

    df = pd.concat(kept, ignore_index=True)
    df["datekey"] = pd.to_datetime(df["datekey"], errors="coerce")
    df = df.dropna(subset=["datekey"]).sort_values(["ticker", "datekey"])

    # --- COVERAGE REPORT (resolves Gate-0 criterion 3) ---
    qcount = df.groupby("ticker")["datekey"].nunique()
    well_covered = qcount[qcount >= MIN_QUARTERS]
    cov_pct = 100.0 * len(well_covered) / len(names)
    med_q = int(qcount.median()) if len(qcount) else 0
    print("\n=== COVERAGE REPORT (Gate-0 criterion 3) ===", flush=True)
    print(f"  shm names with ANY SF1 ARQ data : {len(qcount)} / {len(names)}", flush=True)
    print(f"  names with >= {MIN_QUARTERS} quarters    : {len(well_covered)} / {len(names)} "
          f"= {cov_pct:.1f}%", flush=True)
    print(f"  median quarters / covered name  : {med_q}", flush=True)
    print(f"  datekey range                   : {df['datekey'].min().date()} -> {df['datekey'].max().date()}", flush=True)
    verdict = "PASS" if (cov_pct >= MIN_COVERAGE_PCT and med_q >= MIN_QUARTERS) else "FAIL"
    print(f"  >>> COVERAGE GATE: {verdict} "
          f"(need >= {MIN_COVERAGE_PCT:.0f}% names AND median >= {MIN_QUARTERS}q)", flush=True)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUT, index=False)
    print(f"\n[fund] wrote {len(df):,} rows -> {OUT}", flush=True)
    if verdict == "FAIL":
        print("[fund] coverage FAIL -> per Gate-1 spec, KILL fundamentals thesis at ingest.", flush=True)
        return 4
    print("[fund] coverage PASS -> proceed: implement cross_sectional_value_quality.py per FROZEN spec.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
