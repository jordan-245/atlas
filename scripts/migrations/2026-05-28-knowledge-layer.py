#!/usr/bin/env python3
"""Add research knowledge layer: sources, claims, contradictions, lifecycle_events, digest_history.

Phase 0 of the research-system DB consolidation.  See docs/specs/research-db-consolidation.md
and research/README.md for context.  All tables are additive -- no existing data touched,
no compat shims needed.

Idempotent -- safe to re-run:
  - CREATE TABLE IF NOT EXISTS
  - CREATE INDEX IF NOT EXISTS
  - DROP VIEW IF EXISTS + CREATE VIEW  (idempotent view refresh)

Run:
    python3 scripts/migrations/2026-05-28-knowledge-layer.py            # dry-run
    python3 scripts/migrations/2026-05-28-knowledge-layer.py --apply
    python3 scripts/migrations/2026-05-28-knowledge-layer.py --apply --db-path /tmp/test.db
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("knowledge_layer_migration")

TARGET_SCHEMA_VERSION = 30

# ── DDL ───────────────────────────────────────────────────────────────────────

_TABLES_DDL = """
CREATE TABLE IF NOT EXISTS sources (
    id            TEXT    PRIMARY KEY,
    kind          TEXT    NOT NULL,
    url           TEXT,
    title         TEXT    NOT NULL,
    authors       TEXT,
    venue         TEXT,
    published_at  TEXT,
    sha256        TEXT    UNIQUE,
    local_path    TEXT,
    ingested_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    extracted_by  TEXT,
    notes         TEXT
);

CREATE TABLE IF NOT EXISTS claims (
    id                     TEXT    PRIMARY KEY,
    source_id              TEXT    NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    strategy               TEXT    NOT NULL,
    universe               TEXT,
    regime_state           TEXT,
    period_start           TEXT,
    period_end             TEXT,
    claimed_sharpe         REAL,
    claimed_solo_sharpe    REAL,
    claimed_max_dd_pct     REAL,
    claimed_trades         INTEGER,
    claimed_cagr_pct       REAL,
    claimed_profit_factor  REAL,
    claimed_avg_hold_days  REAL,
    extraction_confidence  TEXT    DEFAULT 'medium',
    status                 TEXT    NOT NULL DEFAULT 'active',
    dismissed_reason       TEXT,
    notes                  TEXT,
    created_at             TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at             TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS contradictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id            TEXT    NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    strategy            TEXT    NOT NULL,
    universe            TEXT    NOT NULL,
    metric              TEXT    NOT NULL,
    claimed_value       REAL,
    measured_value      REAL,
    delta               REAL,
    delta_abs           REAL,
    severity            TEXT    NOT NULL,
    first_seen_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    last_checked_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    resolution          TEXT,
    resolution_note     TEXT,
    resolved_at         TEXT,
    UNIQUE(claim_id, metric)
);

CREATE TABLE IF NOT EXISTS digest_history (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at                  TEXT    NOT NULL DEFAULT (datetime('now')),
    kind                     TEXT    NOT NULL,
    new_papers               INTEGER NOT NULL DEFAULT 0,
    new_experiments          INTEGER NOT NULL DEFAULT 0,
    new_contradictions       INTEGER NOT NULL DEFAULT 0,
    lifecycle_transitions    INTEGER NOT NULL DEFAULT 0,
    summary                  TEXT,
    delivery_status          TEXT,
    payload                  TEXT
);

CREATE TABLE IF NOT EXISTS queue_mirror (
    id                    TEXT    PRIMARY KEY,
    title                 TEXT    NOT NULL,
    category              TEXT    NOT NULL,
    market                TEXT    NOT NULL,
    hypothesis            TEXT,
    method                TEXT    NOT NULL,
    acceptance_criteria   TEXT,
    estimated_runtime_min INTEGER NOT NULL DEFAULT 0,
    priority              TEXT    NOT NULL,
    status                TEXT    NOT NULL,
    strategy_name         TEXT,
    params_override       TEXT,
    config_snapshot       TEXT,
    claimed_by            TEXT,
    claimed_at            TEXT,
    tags                  TEXT,
    depends_on            TEXT,
    notes                 TEXT,
    payload               TEXT    NOT NULL,
    created_at            TEXT    NOT NULL,
    updated_at            TEXT    NOT NULL,
    mirrored_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS journal_mirror (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    experiment_id     TEXT    NOT NULL,
    timestamp         TEXT    NOT NULL,
    market            TEXT    NOT NULL,
    category          TEXT    NOT NULL,
    strategy          TEXT,
    hypothesis        TEXT,
    verdict           TEXT,
    key_metrics       TEXT,
    delta_vs_baseline TEXT,
    learnings         TEXT,
    promoted          INTEGER NOT NULL DEFAULT 0 CHECK (promoted IN (0, 1)),
    runtime_s         REAL,
    agent_id          TEXT,
    payload           TEXT    NOT NULL,
    mirrored_at       TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE(experiment_id, timestamp)
);
"""

_INDEXES = [
    ("idx_sources_kind",
     "CREATE INDEX IF NOT EXISTS idx_sources_kind ON sources(kind)"),
    ("idx_sources_published",
     "CREATE INDEX IF NOT EXISTS idx_sources_published ON sources(published_at DESC)"),
    ("idx_claims_strategy",
     "CREATE INDEX IF NOT EXISTS idx_claims_strategy ON claims(strategy)"),
    ("idx_claims_source",
     "CREATE INDEX IF NOT EXISTS idx_claims_source ON claims(source_id)"),
    ("idx_claims_active",
     "CREATE INDEX IF NOT EXISTS idx_claims_active "
     "ON claims(strategy, universe) WHERE status = 'active'"),
    ("idx_contradictions_unresolved",
     "CREATE INDEX IF NOT EXISTS idx_contradictions_unresolved "
     "ON contradictions(strategy, severity) WHERE resolution IS NULL"),
    ("idx_contradictions_recent",
     "CREATE INDEX IF NOT EXISTS idx_contradictions_recent "
     "ON contradictions(first_seen_at DESC)"),
    ("idx_digest_sent",
     "CREATE INDEX IF NOT EXISTS idx_digest_sent ON digest_history(sent_at DESC)"),
    ("idx_queue_mirror_status",
     "CREATE INDEX IF NOT EXISTS idx_queue_mirror_status ON queue_mirror(status)"),
    ("idx_queue_mirror_strategy",
     "CREATE INDEX IF NOT EXISTS idx_queue_mirror_strategy ON queue_mirror(strategy_name)"),
    ("idx_queue_mirror_category",
     "CREATE INDEX IF NOT EXISTS idx_queue_mirror_category ON queue_mirror(category)"),
    ("idx_queue_mirror_priority",
     "CREATE INDEX IF NOT EXISTS idx_queue_mirror_priority "
     "ON queue_mirror(priority, status)"),
    ("idx_journal_mirror_experiment",
     "CREATE INDEX IF NOT EXISTS idx_journal_mirror_experiment "
     "ON journal_mirror(experiment_id)"),
    ("idx_journal_mirror_ts",
     "CREATE INDEX IF NOT EXISTS idx_journal_mirror_ts "
     "ON journal_mirror(timestamp DESC)"),
    ("idx_journal_mirror_strategy",
     "CREATE INDEX IF NOT EXISTS idx_journal_mirror_strategy "
     "ON journal_mirror(strategy, timestamp DESC)"),
]

_VIEWS_DDL = """
DROP VIEW IF EXISTS v_candidate_contradictions;
CREATE VIEW v_candidate_contradictions AS
SELECT
    c.id                                                                AS claim_id,
    c.strategy                                                          AS strategy,
    COALESCE(c.universe, rb.universe)                                   AS universe,
    'sharpe'                                                            AS metric,
    c.claimed_sharpe                                                    AS claimed_value,
    COALESCE(rb.solo_sharpe, rb.sharpe)                                 AS measured_value,
    COALESCE(rb.solo_sharpe, rb.sharpe) - c.claimed_sharpe              AS delta,
    ABS(COALESCE(rb.solo_sharpe, rb.sharpe) - c.claimed_sharpe)         AS delta_abs,
    CASE
        WHEN ABS(COALESCE(rb.solo_sharpe, rb.sharpe) - c.claimed_sharpe) >= 1.0 THEN 'critical'
        WHEN ABS(COALESCE(rb.solo_sharpe, rb.sharpe) - c.claimed_sharpe) >= 0.5 THEN 'major'
        WHEN ABS(COALESCE(rb.solo_sharpe, rb.sharpe) - c.claimed_sharpe) >= 0.3 THEN 'minor'
        ELSE NULL
    END                                                                 AS severity
FROM claims c
JOIN research_best rb
    ON rb.strategy = c.strategy
   AND (c.universe IS NULL OR rb.universe = c.universe)
   AND (c.regime_state IS rb.regime_state)
WHERE c.status = 'active'
  AND c.claimed_sharpe IS NOT NULL
  AND COALESCE(rb.solo_sharpe, rb.sharpe) IS NOT NULL

UNION ALL

SELECT
    c.id, c.strategy,
    COALESCE(c.universe, rb.universe),
    'max_dd_pct',
    c.claimed_max_dd_pct,
    rb.max_dd_pct,
    rb.max_dd_pct - c.claimed_max_dd_pct,
    ABS(rb.max_dd_pct - c.claimed_max_dd_pct),
    CASE
        WHEN ABS(rb.max_dd_pct - c.claimed_max_dd_pct) >= 15 THEN 'critical'
        WHEN ABS(rb.max_dd_pct - c.claimed_max_dd_pct) >= 8  THEN 'major'
        WHEN ABS(rb.max_dd_pct - c.claimed_max_dd_pct) >= 5  THEN 'minor'
        ELSE NULL
    END
FROM claims c
JOIN research_best rb
    ON rb.strategy = c.strategy
   AND (c.universe IS NULL OR rb.universe = c.universe)
   AND (c.regime_state IS rb.regime_state)
WHERE c.status = 'active'
  AND c.claimed_max_dd_pct IS NOT NULL
  AND rb.max_dd_pct IS NOT NULL;

DROP VIEW IF EXISTS v_open_contradictions;
CREATE VIEW v_open_contradictions AS
SELECT
    co.id              AS contradiction_id,
    co.claim_id,
    co.strategy,
    co.universe,
    co.metric,
    co.claimed_value,
    co.measured_value,
    co.delta,
    co.delta_abs,
    co.severity,
    co.first_seen_at,
    co.last_checked_at,
    cl.source_id,
    s.title            AS source_title,
    s.url              AS source_url,
    s.published_at     AS source_published_at
FROM contradictions co
JOIN claims cl  ON cl.id = co.claim_id
JOIN sources s  ON s.id  = cl.source_id
WHERE co.resolution IS NULL
ORDER BY
    CASE co.severity WHEN 'critical' THEN 0 WHEN 'major' THEN 1 ELSE 2 END,
    co.delta_abs DESC;

DROP VIEW IF EXISTS v_strategy_summary;
CREATE VIEW v_strategy_summary AS
SELECT
    rb.strategy,
    rb.universe,
    rb.solo_sharpe,
    rb.portfolio_sharpe,
    rb.max_dd_pct,
    rb.trades,
    rb.updated_at                                                       AS last_measured_at,
    (SELECT COUNT(*) FROM claims c
        WHERE c.strategy = rb.strategy AND c.status = 'active')         AS active_claims,
    (SELECT COUNT(*) FROM contradictions co
        JOIN claims c ON c.id = co.claim_id
        WHERE c.strategy = rb.strategy AND co.resolution IS NULL)       AS open_contradictions,
    (SELECT to_state FROM strategy_lifecycle_history le
        WHERE le.strategy = rb.strategy AND le.universe = rb.universe
        ORDER BY le.transitioned_at DESC, le.id DESC LIMIT 1)           AS lifecycle_state
FROM research_best rb
WHERE rb.regime_state IS NULL;
"""

_NEW_TABLES = ["sources", "claims", "contradictions", "digest_history",
               "queue_mirror", "journal_mirror"]
_NEW_VIEWS = ["v_candidate_contradictions", "v_open_contradictions", "v_strategy_summary"]

# Phase 3: extend the existing strategy_lifecycle_history table with two
# columns.  Idempotent via "ALTER TABLE ... ADD COLUMN" guarded by PRAGMA
# table_info inspection (sqlite has no native "ADD COLUMN IF NOT EXISTS").
_LIFECYCLE_HISTORY_NEW_COLUMNS = [
    ("gate_results", "TEXT"),
    ("experiment_id", "TEXT"),
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _view_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _index_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def _ensure_column(
    conn: sqlite3.Connection, table: str, column: str, decl: str,
    *, apply: bool,
) -> str:
    """Idempotent ADD COLUMN.  Returns 'exists' | 'WOULD ADD' | 'add'."""
    if _column_exists(conn, table, column):
        return "exists"
    if not apply:
        return "WOULD ADD"
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return "add"


def _current_schema_version(conn: sqlite3.Connection) -> int | None:
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] if row else None


# ── Core migration logic ──────────────────────────────────────────────────────

def run(
    db_path: str | Path = _ATLAS_ROOT / "data" / "atlas.db",
    *,
    apply: bool = False,
) -> None:
    """Apply (or preview) the knowledge-layer migration.

    Args:
        db_path: Path to the SQLite database file.
        apply: When False (default), print what would be done but make no changes.
    """
    db_path = Path(db_path)
    if not db_path.exists():
        logger.error("DB not found at %s", db_path)
        sys.exit(1)

    logger.info("Migration: 2026-05-28-knowledge-layer")
    logger.info("DB:        %s", db_path)
    logger.info("Mode:      %s", "APPLY" if apply else "DRY-RUN")

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        current = _current_schema_version(conn)
        logger.info("Current schema_version (MAX): %s", current)
        logger.info("Target schema_version:        %d", TARGET_SCHEMA_VERSION)

        # ── Tables ────────────────────────────────────────────────────────
        for tbl in _NEW_TABLES:
            existed = _table_exists(conn, tbl)
            verb = "EXISTS" if existed else ("WOULD CREATE" if not apply else "create")
            print(f"  table  {tbl:24s}  {verb}")

        if apply:
            conn.executescript(_TABLES_DDL)

        # ── Indexes ───────────────────────────────────────────────────────
        for idx_name, _ in _INDEXES:
            existed = _index_exists(conn, idx_name)
            verb = "EXISTS" if existed else ("WOULD CREATE" if not apply else "create")
            print(f"  index  {idx_name:32s}  {verb}")

        if apply:
            for _, ddl in _INDEXES:
                conn.execute(ddl)

        # ── ALTER existing strategy_lifecycle_history (Phase 3) ───────────
        # Idempotent ADD COLUMN; safe if columns already exist.
        for col_name, col_decl in _LIFECYCLE_HISTORY_NEW_COLUMNS:
            status = _ensure_column(
                conn, "strategy_lifecycle_history", col_name, col_decl, apply=apply,
            )
            print(f"  column strategy_lifecycle_history.{col_name:<14s}  {status}")

        # ── Views (always refreshed -- DROP IF EXISTS + CREATE) ───────────
        for view in _NEW_VIEWS:
            existed = _view_exists(conn, view)
            verb = "REFRESH" if existed else ("WOULD CREATE" if not apply else "create")
            print(f"  view   {view:32s}  {verb}")

        if apply:
            conn.executescript(_VIEWS_DDL)

        # ── schema_version bump ───────────────────────────────────────────
        if apply:
            conn.execute(
                "INSERT OR IGNORE INTO schema_version (version, applied_at) "
                "VALUES (?, datetime('now'))",
                (TARGET_SCHEMA_VERSION,),
            )
            conn.commit()
        print(f"  schema_version  ->  {TARGET_SCHEMA_VERSION}  "
              f"({'bumped' if apply else 'WOULD BUMP'})")

        # ── Post-apply verification ───────────────────────────────────────
        if apply:
            missing_tables = [t for t in _NEW_TABLES if not _table_exists(conn, t)]
            missing_views = [v for v in _NEW_VIEWS if not _view_exists(conn, v)]
            missing_indexes = [n for n, _ in _INDEXES if not _index_exists(conn, n)]
            missing_cols = [
                c for c, _ in _LIFECYCLE_HISTORY_NEW_COLUMNS
                if not _column_exists(conn, "strategy_lifecycle_history", c)
            ]

            if missing_tables or missing_views or missing_indexes or missing_cols:
                logger.error("VERIFICATION FAILED")
                logger.error("  missing tables:  %s", missing_tables)
                logger.error("  missing views:   %s", missing_views)
                logger.error("  missing indexes: %s", missing_indexes)
                logger.error("  missing columns: %s", missing_cols)
                sys.exit(1)

            new_version = _current_schema_version(conn)
            print()
            print(
                f"OK: all {len(_NEW_TABLES)} tables, {len(_NEW_VIEWS)} views, "
                f"{len(_INDEXES)} indexes, "
                f"{len(_LIFECYCLE_HISTORY_NEW_COLUMNS)} lifecycle_history columns present.  "
                f"schema_version={new_version}"
            )
        else:
            print()
            print("--- Dry-run complete. Re-run with --apply to execute.")

    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Add research knowledge layer tables, views, and indexes to atlas.db",
    )
    parser.add_argument(
        "--db-path",
        default=str(_ATLAS_ROOT / "data" / "atlas.db"),
        help="Path to the SQLite database (default: data/atlas.db)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply the migration (default: dry-run)",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    run(db_path=args.db_path, apply=args.apply)
