#!/usr/bin/env python3
"""Migration: introduce portfolio_diversifier metric_type for research_best.

Adds a new valid value for research_best.metric_type:
    'portfolio_diversifier' — solo Sharpe is weak/negative but the strategy
    contributes positively to the whole-portfolio Sharpe via low correlation
    with other strategies. Kept active in config/active/*.json for
    diversification value despite failing solo quality gates.

Inserts (or updates) the connors_rsi2 / commodity_etfs row per the
validated-strategies audit 2026-05-01:
    solo_sharpe       = -0.68   (audit measurement)
    portfolio_sharpe  = +0.47   (incremental contribution to portfolio)
    metric_type       = 'portfolio_diversifier'

Bumps schema_version: 28 → 29.

Idempotent: safe to re-run (uses INSERT OR REPLACE for the row + checks
schema_version before bumping).

Usage:
    python3 scripts/migrations/2026-05-01-portfolio-diversifier-metric-type.py          # dry-run
    python3 scripts/migrations/2026-05-01-portfolio-diversifier-metric-type.py --apply  # commit
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ATLAS_ROOT / "data" / "atlas.db"

# Audit values per validated-strategies audit 2026-05-01
_DIVERSIFIER_ROWS = [
    {
        "strategy": "connors_rsi2",
        "universe": "commodity_etfs",
        "solo_sharpe": -0.68,
        "portfolio_sharpe": 0.47,
        "sharpe": 0.47,        # legacy column = portfolio sharpe (incremental contribution)
        "trades": None,        # unknown — backfill on next sweep
        "max_dd_pct": None,
        "metric_type": "portfolio_diversifier",
        # Use the existing connors_rsi2 params from sp500 as a starting point;
        # next sweep on commodity_etfs will refine.
        "params": json.dumps({
            "rsi_period": 2,
            "rsi_entry": 10,
            "_note": "Placeholder from audit 2026-05-01 — real params TBD on next commodity_etfs sweep",
        }),
    },
]


def run_migration(db_path: Path = DB_PATH, apply: bool = False) -> dict:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    stats = {"applied": apply, "rows_upserted": 0, "schema_version_bumped": False}

    # 1) Insert/update diversifier row(s)
    for row in _DIVERSIFIER_ROWS:
        if apply:
            conn.execute(
                """
                INSERT INTO research_best
                    (strategy, universe, params, sharpe, trades, max_dd_pct,
                     solo_sharpe, portfolio_sharpe, metric_type, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                ON CONFLICT(strategy, universe) DO UPDATE SET
                    solo_sharpe      = excluded.solo_sharpe,
                    portfolio_sharpe = excluded.portfolio_sharpe,
                    sharpe           = excluded.sharpe,
                    metric_type      = excluded.metric_type,
                    updated_at       = datetime('now')
                """,
                (
                    row["strategy"], row["universe"], row["params"],
                    row["sharpe"], row["trades"], row["max_dd_pct"],
                    row["solo_sharpe"], row["portfolio_sharpe"], row["metric_type"],
                ),
            )
        stats["rows_upserted"] += 1
        print(f"  {'APPLY' if apply else 'DRY '}: upsert {row['strategy']}/{row['universe']} "
              f"solo={row['solo_sharpe']} port={row['portfolio_sharpe']} "
              f"metric_type={row['metric_type']}")

    # 2) Bump schema_version 28 → 29 if currently 28
    cur = conn.execute("SELECT MAX(version) FROM schema_version")
    current_version = cur.fetchone()[0] or 0
    print(f"  current schema_version: {current_version}")
    if current_version < 29:
        if apply:
            conn.execute("INSERT INTO schema_version (version) VALUES (29)")
        stats["schema_version_bumped"] = True
        print(f"  {'APPLY' if apply else 'DRY '}: bump schema_version → 29")

    if apply:
        conn.commit()
    conn.close()
    return stats


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run)")
    p.add_argument("--db", default=str(DB_PATH))
    args = p.parse_args(argv)

    stats = run_migration(db_path=Path(args.db), apply=args.apply)
    print()
    print(f"  rows_upserted: {stats['rows_upserted']}")
    print(f"  schema_version_bumped: {stats['schema_version_bumped']}")
    if not args.apply:
        print("\n  ℹ️  This was a DRY-RUN. Pass --apply to commit.\n")
    else:
        print("\n  ✅ Migration applied successfully.\n")


if __name__ == "__main__":
    main()
