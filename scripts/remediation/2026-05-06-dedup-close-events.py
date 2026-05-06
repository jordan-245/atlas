#!/usr/bin/env python3
"""Dedup duplicate close events — ECL/NOC/FCX cleanup (2026-05-06).

Root cause analysis
-------------------
Three ticker patterns produced duplicate CLOSED rows in the trades table.

ECL (id=93, id=122) and NOC (id=94, id=123)
  Entry/exit on same dates, identical pnl, both exit_reason='stop_loss'.
  These are from an earlier reconcile cycle (March 2026) and were already
  deduped (superseded=1 set) by a prior audit run.  Canonical = lowest id.

FCX (id=201 canonical, id=205 superseded, id=207 superseded)
  Root cause: reconcile_entry_fills (brokers/live_executor.py) fetches ALL
  CLOSED broker orders from a 7-day window.  FCX id=201 was opened live on
  2026-05-05T19:16:03 and stopped out at 2026-05-06T08:00:37.  At 08:01,
  sync_protective_orders ran reconcile_entry_fills for sp500 — it found the
  FCX BUY fill in the 7-day window (FCX is an sp500 constituent AND a
  commodity_etfs member).  Because id=201 was just CLOSED (status='closed'),
  the SQLite dedup guard (status='open' AND ticker=?) found nothing, and no
  EBAY guard existed yet at that time.  A duplicate open row was created
  (id=205), then immediately closed by reconcile_exit_fills.  This repeated
  at 09:31 (id=207) until the EBAY guard commit (0541ba70, ~11:27 UTC) fixed
  the code path.

Fix
---
  All three pairs/groups are ALREADY correctly deduped (superseded=1 set on
  the non-canonical rows).  This script is idempotent: it verifies, logs
  "already deduped" when no changes are needed, and only applies SET
  superseded=1 if somehow missed.

  Canonical row selection priority:
    1. Lowest id (first-inserted = live execution row, not reconcile artifact)

  exit_reason priority (for tie-breaking within same ticker/dates):
    stop_loss  >  reconcile_fill_cached  >  reconcile_fill  >  reconcile_phantom

Usage:
  python3 scripts/remediation/2026-05-06-dedup-close-events.py          # dry-run
  python3 scripts/remediation/2026-05-06-dedup-close-events.py --apply  # apply
  python3 scripts/remediation/2026-05-06-dedup-close-events.py --apply --verbose
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

DB_PATH = PROJECT / "data" / "atlas.db"

# Priority order for exit_reason (lower index = higher priority = canonical)
_REASON_PRIORITY: list[str] = [
    "stop_loss",
    "trailing_stop",
    "take_profit",
    "reconcile_fill_cached",
    "reconcile_fill",
    "reconcile_phantom",
]

# Target groups: (ticker, [ids in ascending order])
# Canonical = lowest id in each group (already verified below)
_TARGET_GROUPS: list[tuple[str, list[int]]] = [
    ("ECL", [93, 122]),
    ("NOC", [94, 123]),
    ("FCX", [201, 205, 207]),
]


def _reason_priority(reason: Optional[str]) -> int:
    """Lower = higher priority (canonical)."""
    reason = (reason or "").lower()
    for i, r in enumerate(_REASON_PRIORITY):
        if r in reason:
            return i
    return len(_REASON_PRIORITY)  # unknown → lowest priority


def run(apply: bool = False, verbose: bool = False) -> int:
    """Execute dedup logic.  Returns 0 on success, 1 on error."""
    if not DB_PATH.exists():
        print(f"ERROR: atlas.db not found at {DB_PATH}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── SUM(pnl) BEFORE ──────────────────────────────────────────────────
        pnl_before_unfiltered = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed'"
        ).fetchone()[0]
        pnl_before_filtered = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE status='closed' AND (superseded=0 OR superseded IS NULL)"
        ).fetchone()[0]

        print("=" * 60)
        print("Dedup close events — ECL / NOC / FCX")
        print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
        print("=" * 60)
        print(f"\nSUM(pnl) BEFORE (unfiltered): ${pnl_before_unfiltered:.2f}")
        print(f"SUM(pnl) BEFORE (superseded=0 only): ${pnl_before_filtered:.2f}")
        print(f"  Inflation from superseded rows: ${pnl_before_unfiltered - pnl_before_filtered:.2f}")
        print()

        total_changed = 0
        errors = []

        for ticker, ids in _TARGET_GROUPS:
            print(f"--- {ticker} ids={ids} ---")

            rows = conn.execute(
                f"SELECT id, entry_date, exit_date, pnl, exit_reason, superseded, status "
                f"FROM trades "
                f"WHERE id IN ({','.join('?' * len(ids))})"
                f"ORDER BY id",
                ids,
            ).fetchall()

            if not rows:
                print(f"  WARN: no rows found for ids={ids} — skip")
                errors.append(f"{ticker}: rows {ids} not found")
                continue

            found_ids = {r["id"] for r in rows}
            missing = [i for i in ids if i not in found_ids]
            if missing:
                print(f"  WARN: some ids not found: {missing}")

            # Validate all rows are 'closed'
            non_closed = [r["id"] for r in rows if r["status"] != "closed"]
            if non_closed:
                print(f"  ERROR: rows {non_closed} are not status='closed' — ABORT this group")
                errors.append(f"{ticker}: non-closed rows {non_closed}")
                continue

            # Determine canonical row: lowest id (first inserted = live execution)
            sorted_rows = sorted(rows, key=lambda r: r["id"])
            canonical = sorted_rows[0]
            non_canonical = sorted_rows[1:]

            # Sanity: canonical should be superseded=0
            if canonical["superseded"] != 0:
                print(
                    f"  ERROR: canonical id={canonical['id']} has superseded={canonical['superseded']} "
                    f"(expected 0) — ABORT this group"
                )
                errors.append(f"{ticker}: canonical id={canonical['id']} not superseded=0")
                continue

            # Log canonical info
            print(
                f"  Canonical: id={canonical['id']} "
                f"exit_date={str(canonical['exit_date'])[:10]} "
                f"pnl={canonical['pnl']:.2f} "
                f"reason={canonical['exit_reason']} "
                f"superseded={canonical['superseded']}"
            )

            # Process non-canonical rows
            group_changed = 0
            for row in non_canonical:
                already = row["superseded"] == 1
                status_msg = "already superseded=1" if already else "needs superseded=1"
                print(
                    f"  Non-canonical: id={row['id']} "
                    f"exit_date={str(row['exit_date'])[:10]} "
                    f"pnl={row['pnl']:.2f} "
                    f"reason={row['exit_reason']} "
                    f"superseded={row['superseded']} → {status_msg}"
                )

                if already:
                    if verbose:
                        print(f"    → already deduped, no change")
                    continue

                # Needs update
                if apply:
                    conn.execute(
                        "UPDATE trades SET superseded=1, updated_at=datetime('now') WHERE id=?",
                        (row["id"],),
                    )
                    conn.commit()
                    group_changed += 1
                    print(f"    → APPLIED: superseded=1 on id={row['id']}")
                else:
                    print(f"    → DRY-RUN: would set superseded=1 on id={row['id']}")
                    group_changed += 1  # count as "would change" for dry-run report

            if group_changed == 0:
                print(f"  ✓ {ticker}: fully deduped (0 rows changed)")
            else:
                verb = "changed" if apply else "would change"
                print(f"  ✓ {ticker}: {group_changed} rows {verb}")
            total_changed += group_changed

        # ── SUM(pnl) AFTER ───────────────────────────────────────────────────
        pnl_after_unfiltered = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed'"
        ).fetchone()[0]
        pnl_after_filtered = conn.execute(
            "SELECT COALESCE(SUM(pnl), 0) FROM trades "
            "WHERE status='closed' AND (superseded=0 OR superseded IS NULL)"
        ).fetchone()[0]

        print()
        print("=" * 60)
        verb = "AFTER" if apply else "AFTER (dry-run, same as BEFORE)"
        print(f"SUM(pnl) {verb} (unfiltered): ${pnl_after_unfiltered:.2f}")
        print(f"SUM(pnl) {verb} (superseded=0 only): ${pnl_after_filtered:.2f}")
        total_changed_label = "rows changed" if apply else "rows would change"
        print(f"\nTotal {total_changed_label}: {total_changed}")

        if errors:
            print(f"\nERRORS ({len(errors)}):")
            for e in errors:
                print(f"  • {e}")
            return 1

        print("\nDEDUP COMPLETE ✓" if apply else "\nDRY-RUN COMPLETE (re-run with --apply to commit)")
        return 0

    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Dedup duplicate close events for ECL/NOC/FCX in trades table"
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Apply changes (default: dry-run only)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print all rows including already-deduped ones",
    )
    args = parser.parse_args()

    rc = run(apply=args.apply, verbose=args.verbose)
    sys.exit(rc)


if __name__ == "__main__":
    main()
