"""Purge test-fixture pollution from brokers/state/live_sp500.json.

Context
-------
Repeated test runs wrote fake tickers (WIN, LOSE, TEST, GHOST, AAPL@150,
MSFT@300, etc.) into the live sp500 state file via the self-heal path in
db.atlas_db._assert_state_file_parity().  This migration removes them.

Identification logic
--------------------
Real positions have:
  - A non-empty stop_order_id (UUID from Alpaca)
  - OR they are explicitly listed in REAL_TICKERS as broker-confirmed

Pollution positions have:
  - Completely round entry_price (100.0, 150.0, 200.0, 300.0, 50.0)
  - Empty stop_order_id
  - Ticker is a known test name (WIN, LOSE, TEST, GHOST, etc.)

Usage
-----
  python3 scripts/migrations/2026-04-24-purge-sp500-state.py          # dry-run
  python3 scripts/migrations/2026-04-24-purge-sp500-state.py --apply
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent.parent
STATE_FILE = PROJECT / "brokers" / "state" / "live_sp500.json"
BACKUP_DIR = PROJECT / "brokers" / "state" / "backups"

# Broker-confirmed real sp500 positions (cross-checked against reconcile log
# and stop_order_id UUIDs in the state file — 2026-04-24 AEST).
# AMD: stop c218e806, AVGO: stop a4cf81e2, ON: stop d6ca936e
REAL_TICKERS: set[str] = {"AMD", "AVGO", "ON"}

# Explicitly known test-only tickers (belt-and-suspenders)
KNOWN_POLLUTION: set[str] = {
    "WIN", "LOSE", "TEST", "GHOST", "DUP", "WARNTEST",
    "CROSS", "REOPEN", "GRACE", "IDCHECK",
    # ETFs misattributed to sp500 (belong to commodity_etfs / sector_etfs)
    "GLD", "UNG", "XLK", "CHTR",
    # Round-priced fake entries for real-name tickers
    "AAPL", "MSFT", "GOOG", "META", "TSLA",
}


def _has_real_stop_order_id(pos: dict) -> bool:
    """Return True if position has a non-empty, plausibly-UUID stop_order_id."""
    soid = pos.get("stop_order_id", "") or ""
    # UUID format: 8-4-4-4-12 characters, total 36 with dashes
    return len(soid) == 36 and soid.count("-") == 4


def classify_position(pos: dict) -> str:
    """Return 'keep' or 'remove' for a position entry."""
    ticker = pos.get("ticker", "")
    entry_price = pos.get("entry_price", 0.0)

    # Explicit keep: broker-confirmed real positions
    if ticker in REAL_TICKERS:
        return "keep"

    # Explicit remove: known pollution tickers
    if ticker in KNOWN_POLLUTION:
        return "remove"

    # Has real stop_order_id → keep (even if not in REAL_TICKERS set)
    if _has_real_stop_order_id(pos):
        return "keep"

    # Round-number entry price AND no stop_order_id → likely test data
    if entry_price == int(entry_price) and not _has_real_stop_order_id(pos):
        return "remove"

    # Default: keep and flag for manual review
    print(f"  [REVIEW NEEDED] {ticker}: entry_price={entry_price}, "
          f"stop_order_id={pos.get('stop_order_id', '')} — keeping by default")
    return "keep"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Write changes (default: dry-run only)")
    args = parser.parse_args(argv)

    if not STATE_FILE.exists():
        print(f"ERROR: {STATE_FILE} not found")
        return 1

    with open(STATE_FILE) as f:
        state = json.load(f)

    positions: list[dict] = state.get("positions", []) or []
    print(f"Loaded {len(positions)} positions from {STATE_FILE.name}")

    kept: list[dict] = []
    removed: list[dict] = []

    for pos in positions:
        verdict = classify_position(pos)
        if verdict == "keep":
            kept.append(pos)
        else:
            removed.append(pos)

    print(f"\nKEEP  ({len(kept)}):", [p["ticker"] for p in kept])
    print(f"REMOVE ({len(removed)}):", [p["ticker"] for p in removed])

    if not removed:
        print("\nNothing to remove — state file is already clean.")
        return 0

    if not args.apply:
        print("\nDRY-RUN: no changes written. Pass --apply to execute.")
        return 0

    # Backup
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    backup_path = BACKUP_DIR / f"live_sp500.json.{ts}.bak"
    shutil.copy2(STATE_FILE, backup_path)
    print(f"\nBackup written: {backup_path}")

    # Write cleaned state
    state["positions"] = kept
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

    print(f"Written {len(kept)} positions to {STATE_FILE.name}")

    # Validate
    with open(STATE_FILE) as f:
        verify = json.load(f)
    actual = len(verify.get("positions", []))
    expected = len(kept)
    if actual != expected:
        print(f"ERROR: post-write count mismatch: expected={expected} got={actual}")
        return 1
    print(f"Validation OK: {actual} positions in file (expected {expected})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
