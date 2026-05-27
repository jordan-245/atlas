#!/usr/bin/env python3
"""Dry-run audit/repair for equity_curve rows with corrupt positions_value.

F-04 / #364 background
----------------------
Between 2026-05-12 and 2026-05-27 the writers (``brokers.live_portfolio.
record_equity`` and ``scripts/eod_settlement.py``) computed
``positions_value = round(eq - portfolio.cash, 2)`` where ``eq`` was the
per-market Atlas slice but ``portfolio.cash`` was the FULL broker cash.
That produced impossible negative ``positions_value`` rows in
``equity_curve`` (e.g. sp500 2026-05-26: pv=-$2,893.32) and broke the F-04
regression test.

The writer fix on 2026-05-27 now records ``positions_value`` and ``cash`` as
the Atlas slice values directly, so the invariant ``eq == cash +
positions_value`` holds going forward.  This script REPORTS the historical
corrupt rows but does NOT modify them by default \u2014 destructive backfill of
live trading data is explicitly out of scope (see tasks/lessons.md and the
#364 task description).

Usage
-----
    # Default \u2014 dry-run report only, exits 0.
    python3 scripts/repair_equity_curve_positions_value.py

    # Verbose listing of every corrupt row.
    python3 scripts/repair_equity_curve_positions_value.py --verbose

The script never writes to atlas.db.  Treat the report as historical context;
all NEW rows are written correctly by the post-fix code path.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "atlas.db"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--verbose",
        action="store_true",
        help="List every corrupt row, not just the summary.",
    )
    p.add_argument(
        "--floor",
        type=float,
        default=-1000.0,
        help="positions_value floor below which a row is considered corrupt.",
    )
    p.add_argument(
        "--db",
        default=str(DB_PATH),
        help=f"Path to atlas.db (default {DB_PATH}).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found", file=sys.stderr)
        return 2

    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT date, market_id, equity, cash, positions_value, broker_equity "
            "FROM equity_curve "
            "WHERE positions_value IS NOT NULL AND positions_value < ? "
            "ORDER BY date ASC, market_id ASC",
            (args.floor,),
        ).fetchall()

    print("=" * 70)
    print("equity_curve positions_value repair audit (DRY RUN \u2014 read-only)")
    print("=" * 70)
    print(f"floor: {args.floor}")
    print(f"corrupt rows: {len(rows)}")
    if not rows:
        print("\nNo rows below floor. No repair needed.")
        return 0

    by_market: dict[str, list[sqlite3.Row]] = {}
    for r in rows:
        by_market.setdefault(r["market_id"], []).append(r)

    print("\nCorrupt rows by market:")
    for m, rs in sorted(by_market.items()):
        first_date = rs[0]["date"]
        last_date = rs[-1]["date"]
        worst = min(rs, key=lambda r: r["positions_value"])
        print(
            f"  {m:18s}  count={len(rs):3d}  first={first_date}  "
            f"last={last_date}  worst=${worst['positions_value']:.2f} on {worst['date']}"
        )

    if args.verbose:
        print("\nAll corrupt rows:")
        for r in rows:
            print(
                f"  {r['date']}  {r['market_id']:14s}  "
                f"eq=${(r['equity'] or 0):.2f}  "
                f"cash=${(r['cash'] or 0):.2f}  "
                f"pv=${(r['positions_value'] or 0):.2f}  "
                f"broker_eq=${(r['broker_equity'] or 0):.2f}"
            )

    print(
        "\nThese rows pre-date the 2026-05-27 writer fix and are NOT modified.\n"
        "Reporting only \u2014 no destructive migration is performed."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
