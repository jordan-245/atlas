#!/usr/bin/env python3
"""Migration: 2026-05-14 — Normalize entry_dates in live_*.json state files.

SQLite trades.entry_date is the authoritative source for original trade entry dates.
Some live_*.json state files have entry_date set to the date the file was last
refreshed (e.g., "2026-05-14") rather than the actual trade entry date
(e.g., "2026-04-24T23:45:04.923636").

This migration:
  1. Reads each brokers/state/live_*.json
  2. For each open position, queries SQLite for the matching trade row
  3. If JSON entry_date != SQLite entry_date AND SQLite has a real value → updates JSON
  4. Writes an audit JSON to data/audit/entry_date_normalization_2026-05-14.json

Usage:
    python3 scripts/migrations/2026-05-14-normalize-entry-dates.py --dry-run
    python3 scripts/migrations/2026-05-14-normalize-entry-dates.py --apply
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

from db import atlas_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

BROKER_STATE_DIR = PROJECT / "brokers" / "state"
AUDIT_DIR = PROJECT / "data" / "audit"


def _sqlite_entry_date(conn: Any, ticker: str) -> str | None:
    """Return the most recent open trade's entry_date for ticker from SQLite.

    Prefers the longest-running open trade (earliest entry_date) for positions
    that have multiple open rows (should not happen but defensive).
    """
    row = conn.execute(
        """SELECT entry_date FROM trades
           WHERE ticker = ? AND status = 'open'
           ORDER BY entry_date ASC
           LIMIT 1""",
        (ticker,),
    ).fetchone()
    if row and row["entry_date"]:
        return str(row["entry_date"])
    return None


def _normalize_date(d: str) -> str:
    """Normalize to ISO date string (YYYY-MM-DD) for comparison."""
    if not d:
        return ""
    return d.split("T")[0][:10]


def run(dry_run: bool) -> dict[str, Any]:
    """Run the normalization and return an audit summary."""
    mode = "DRY-RUN" if dry_run else "APPLY"
    logger.info("Entry-date normalization — mode=%s", mode)

    corrections: list[dict[str, Any]] = []
    no_sqlite_match: list[dict[str, Any]] = []
    already_correct: list[dict[str, Any]] = []

    state_files = sorted(BROKER_STATE_DIR.glob("live_*.json"))
    if not state_files:
        logger.error("No live_*.json files found in %s", BROKER_STATE_DIR)
        return {"corrections": [], "no_sqlite_match": [], "already_correct": []}

    with atlas_db.get_db() as conn:
        for sf in state_files:
            market_id = sf.stem[5:] if sf.stem.startswith("live_") else sf.stem

            try:
                with open(sf) as f:
                    state = json.load(f)
            except Exception as exc:
                logger.warning("Could not load %s: %s", sf.name, exc)
                continue

            positions: list[dict] = state.get("positions", [])
            changed = False

            for i, pos in enumerate(positions):
                ticker = pos.get("ticker", "")
                if not ticker:
                    continue

                json_date = pos.get("entry_date", "")
                sqlite_date = _sqlite_entry_date(conn, ticker)

                if sqlite_date is None:
                    # No open trade in SQLite for this ticker
                    no_sqlite_match.append({
                        "file": sf.name,
                        "ticker": ticker,
                        "json_date": json_date,
                        "reason": "no open trade in SQLite",
                    })
                    logger.debug("%s/%s: no SQLite match — leaving JSON as-is", market_id, ticker)
                    continue

                json_normalized = _normalize_date(json_date)
                sqlite_normalized = _normalize_date(sqlite_date)

                if json_normalized == sqlite_normalized:
                    already_correct.append({
                        "file": sf.name,
                        "ticker": ticker,
                        "date": json_date,
                    })
                    logger.debug("%s/%s: entry_date already correct (%s)", market_id, ticker, json_date)
                    continue

                # Divergence detected — JSON has wrong date
                canonical_date = sqlite_normalized  # YYYY-MM-DD from SQLite

                logger.info(
                    "%s%s/%s: CORRECT entry_date %r → %r (was JSON %r)",
                    "[DRY-RUN] " if dry_run else "",
                    market_id, ticker, json_date, canonical_date, json_date,
                )
                corrections.append({
                    "file": sf.name,
                    "market_id": market_id,
                    "ticker": ticker,
                    "json_date_before": json_date,
                    "sqlite_date": sqlite_date,
                    "canonical_date": canonical_date,
                })

                if not dry_run:
                    positions[i]["entry_date"] = canonical_date
                    changed = True

            if changed and not dry_run:
                try:
                    with open(sf, "w") as f:
                        json.dump(state, f, indent=2)
                    logger.info("Updated %s (%d corrections)", sf.name, sum(
                        1 for c in corrections if c["file"] == sf.name
                    ))
                except Exception as exc:
                    logger.error("Failed to write %s: %s", sf.name, exc)

    audit = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "summary": {
            "corrections_applied": len(corrections) if not dry_run else 0,
            "corrections_planned": len(corrections),
            "no_sqlite_match": len(no_sqlite_match),
            "already_correct": len(already_correct),
        },
        "corrections": corrections,
        "no_sqlite_match": no_sqlite_match,
        "already_correct": already_correct,
    }

    # Write audit JSON
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_path = AUDIT_DIR / "entry_date_normalization_2026-05-14.json"
    try:
        with open(audit_path, "w") as f:
            json.dump(audit, f, indent=2, default=str)
        logger.info("Audit written to %s", audit_path)
    except Exception as exc:
        logger.warning("Could not write audit file: %s", exc)

    return audit


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize entry_dates in live_*.json from SQLite trades table."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be changed without writing to JSON files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply corrections to JSON files.",
    )
    args = parser.parse_args()

    if not args.apply and not args.dry_run:
        print("Defaulting to --dry-run. Use --apply to write changes.")
        args.dry_run = True

    dry_run = not args.apply

    print(f"\n{'=' * 60}")
    print("  Entry-Date Normalization Migration")
    if dry_run:
        print("  MODE: DRY-RUN  (no writes)")
    else:
        print("  MODE: APPLY    (writes to live_*.json)")
    print(f"{'=' * 60}\n")

    audit = run(dry_run=dry_run)
    s = audit["summary"]

    print(f"\n{'=' * 60}")
    if dry_run:
        print(f"  DRY-RUN complete:")
        print(f"    Corrections planned:  {s['corrections_planned']}")
    else:
        print(f"  APPLY complete:")
        print(f"    Corrections applied:  {s['corrections_applied']}")
    print(f"    No SQLite match:      {s['no_sqlite_match']}")
    print(f"    Already correct:      {s['already_correct']}")
    print(f"{'=' * 60}\n")

    if audit["corrections"]:
        print("Corrections:")
        for c in audit["corrections"]:
            action = "APPLIED" if not dry_run else "PLANNED"
            print(
                f"  [{action}] {c['market_id']}/{c['ticker']}: "
                f"{c['json_date_before']!r} → {c['canonical_date']!r}"
            )
    else:
        print("  ✅ All entry_dates already match SQLite — nothing to do.\n")


if __name__ == "__main__":
    main()
