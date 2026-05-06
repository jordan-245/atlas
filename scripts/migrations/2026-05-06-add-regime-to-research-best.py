#!/usr/bin/env python3
"""Add regime_state column to research_best, change PK to include regime.

Per audit 2026-05-06 Recommendation 5.

Existing rows (PK = (strategy, universe)) become regime_state=NULL meaning
"cross-regime fallback". New per-regime rows can coexist:
    (mean_reversion, commodity_etfs, NULL) -- cross-regime fallback (legacy)
    (mean_reversion, commodity_etfs, 'bull_risk_on') -- regime-specific
    (mean_reversion, commodity_etfs, 'recovery_early') -- regime-specific

Idempotent: re-running has no effect after migration completes.

Usage:
    python3 scripts/migrations/2026-05-06-add-regime-to-research-best.py --dry-run
    python3 scripts/migrations/2026-05-06-add-regime-to-research-best.py --apply
"""
import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ATLAS_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ATLAS_ROOT / "data" / "atlas.db"
BACKUP_PATH = ATLAS_ROOT / "data" / (
    f"atlas.db.bak.regime_research_best.{datetime.now().strftime('%Y%m%dT%H%M%S')}"
)


def already_migrated(con: sqlite3.Connection) -> bool:
    cols = [r[1] for r in con.execute("PRAGMA table_info(research_best)").fetchall()]
    return "regime_state" in cols


def migrate(apply: bool, db_path: Path = DB_PATH) -> int:
    if not db_path.exists():
        print(f"DB not found: {db_path}")
        return 1
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    if already_migrated(con):
        print("[migration] research_best already has regime_state column — no-op.")
        con.close()
        return 0

    n_rows = con.execute("SELECT COUNT(*) FROM research_best").fetchone()[0]
    print(f"[migration] research_best has {n_rows} rows; will preserve all as regime_state=NULL.")

    if not apply:
        print("[migration] DRY RUN — no changes. Re-run with --apply to commit.")
        con.close()
        return 0

    # Backup
    backup_path = ATLAS_ROOT / "data" / (
        f"atlas.db.bak.regime_research_best.{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    )
    print(f"[migration] backing up DB to {backup_path}")
    con.close()
    shutil.copy2(str(db_path), str(backup_path))

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    # SQLite doesn't allow altering a PK in place; rebuild via temp table.
    try:
        con.execute("BEGIN EXCLUSIVE")
        con.execute("""
            CREATE TABLE research_best_new (
                strategy         TEXT NOT NULL,
                universe         TEXT NOT NULL,
                regime_state     TEXT,
                params           TEXT NOT NULL,
                sharpe           REAL,
                trades           INTEGER,
                max_dd_pct       REAL,
                updated_at       TEXT DEFAULT (datetime('now')),
                solo_sharpe      REAL,
                portfolio_sharpe REAL,
                metric_type      TEXT NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (strategy, universe, regime_state)
            )
        """)
        con.execute("""
            INSERT INTO research_best_new
                (strategy, universe, regime_state, params, sharpe, trades,
                 max_dd_pct, updated_at, solo_sharpe, portfolio_sharpe, metric_type)
            SELECT strategy, universe, NULL, params, sharpe, trades,
                   max_dd_pct, updated_at, solo_sharpe, portfolio_sharpe, metric_type
            FROM research_best
        """)
        con.execute("DROP TABLE research_best")
        con.execute("ALTER TABLE research_best_new RENAME TO research_best")

        # Partial unique index enforces uniqueness of the cross-regime (NULL) row
        # per (strategy, universe). SQLite treats NULL != NULL in a PK, so without
        # this index multiple NULL rows per (strategy, universe) would be possible.
        con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_research_best_cross_regime
                ON research_best (strategy, universe)
                WHERE regime_state IS NULL
        """)
        # General lookup index by regime
        con.execute("""
            CREATE INDEX IF NOT EXISTS idx_research_best_regime
                ON research_best (strategy, universe, regime_state)
        """)
        con.commit()
    except Exception:
        con.rollback()
        con.close()
        raise

    n_after = con.execute("SELECT COUNT(*) FROM research_best").fetchone()[0]
    print(f"[migration] complete — {n_after} rows preserved (all regime_state=NULL).")
    con.close()
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    if not args.apply and not args.dry_run:
        args.dry_run = True
    sys.exit(migrate(apply=args.apply))
