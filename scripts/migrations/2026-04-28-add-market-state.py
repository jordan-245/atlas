#!/usr/bin/env python3
"""Add market_state and equity_history tables; backfill from broker state JSON files.

Wave D2 Phase 1 — broker-state schema + backfill.

Run:
    python3 scripts/migrations/2026-04-28-add-market-state.py

Idempotent — safe to re-run:
  - CREATE TABLE IF NOT EXISTS  (schema)
  - ON CONFLICT DO UPDATE       (market_state upsert)
  - INSERT OR IGNORE            (equity_history rows keyed by market_id+date)

If equity_history already exists with a different column shape the script logs
a loud WARNING and skips that table's creation to avoid destroying existing
rows. market_state is still created/backfilled normally.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

logger = logging.getLogger("add_market_state")


# ── DB path resolution ────────────────────────────────────────────────────────

def _resolve_db_path() -> Path:
    """Return active DB path — respects ATLAS_DB_PATH env var and atlas_db override."""
    import os
    env_override = os.environ.get("ATLAS_DB_PATH")
    if env_override:
        return Path(env_override)
    try:
        from db import atlas_db
        override = getattr(atlas_db, "_db_path_override", None)
        if override:
            return Path(override)
    except Exception:
        pass
    return _ATLAS_ROOT / "data" / "atlas.db"


# ── DDL ───────────────────────────────────────────────────────────────────────

_MARKET_STATE_DDL = """
CREATE TABLE IF NOT EXISTS market_state (
  market_id         TEXT    PRIMARY KEY,
  halted            INTEGER NOT NULL DEFAULT 0 CHECK (halted IN (0,1)),
  halt_reason       TEXT,
  halted_at         TEXT,
  mode              TEXT    NOT NULL DEFAULT 'paper' CHECK (mode IN ('live','paper','passive')),
  daily_high_water  REAL,
  hwm_date          TEXT,
  updated_at        TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_EQUITY_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS equity_history (
  market_id  TEXT NOT NULL,
  date       TEXT NOT NULL,
  equity     REAL NOT NULL,
  pnl        REAL,
  PRIMARY KEY (market_id, date)
);
CREATE INDEX IF NOT EXISTS idx_equity_history_market_date
    ON equity_history(market_id, date);
"""

# Expected column set for equity_history compatibility check
_EQUITY_HISTORY_EXPECTED_COLS = {"market_id", "date", "equity", "pnl"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def _get_table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def _extract_market_id(path: Path) -> str:
    """live_sp500.json -> sp500"""
    return path.stem.removeprefix("live_")


# ── Per-market backfill ───────────────────────────────────────────────────────

def _backfill_market(
    conn: sqlite3.Connection,
    json_path: Path,
    *,
    skip_equity_history: bool = False,
) -> dict:
    """Backfill one market from its JSON file. Returns stats dict."""
    market_id = _extract_market_id(json_path)
    stats: dict = {
        "market_id": market_id,
        "market_state": "skipped",
        "equity_inserted": 0,
        "equity_skipped": 0,
    }

    try:
        with open(json_path) as f:
            data = json.load(f)
    except Exception as exc:
        logger.warning("  %s: failed to read JSON -- %s", market_id, exc)
        stats["market_state"] = f"error: {exc}"
        return stats

    # ── market_state UPSERT ────────────────────────────────────────────────
    halted = bool(data.get("halted", False))
    halt_reason = data.get("halt_reason") or None
    daily_high_water = data.get("daily_high_water")
    last_saved = data.get("last_saved")
    halted_at = last_saved if halted else None
    hwm_date = datetime.now().strftime("%Y-%m-%d") if daily_high_water is not None else None

    try:
        conn.execute(
            """
            INSERT INTO market_state
                (market_id, halted, halt_reason, halted_at, mode,
                 daily_high_water, hwm_date, updated_at)
            VALUES (?, ?, ?, ?, 'live', ?, ?, datetime('now'))
            ON CONFLICT(market_id) DO UPDATE SET
                halted           = excluded.halted,
                halt_reason      = excluded.halt_reason,
                halted_at        = excluded.halted_at,
                mode             = excluded.mode,
                daily_high_water = excluded.daily_high_water,
                hwm_date         = excluded.hwm_date,
                updated_at       = datetime('now')
            """,
            (market_id, int(halted), halt_reason, halted_at,
             daily_high_water, hwm_date),
        )
        stats["market_state"] = "upserted"
    except Exception as exc:
        logger.warning("  %s: market_state UPSERT failed -- %s", market_id, exc)
        stats["market_state"] = f"error: {exc}"
        return stats

    if skip_equity_history:
        return stats

    # ── equity_history INSERT OR IGNORE ────────────────────────────────────
    equity_history = data.get("equity_history")
    if not isinstance(equity_history, list):
        logger.warning(
            "  %s: equity_history key missing or not a list -- skipping",
            market_id,
        )
        return stats

    for entry in equity_history:
        if not isinstance(entry, dict):
            stats["equity_skipped"] += 1
            continue
        entry_date = entry.get("date")
        entry_equity = entry.get("equity")
        entry_pnl = entry.get("pnl")

        if not entry_date or entry_equity is None:
            logger.warning(
                "  %s: equity_history entry missing date/equity -- skipping entry",
                market_id,
            )
            stats["equity_skipped"] += 1
            continue

        try:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO equity_history (market_id, date, equity, pnl)
                VALUES (?, ?, ?, ?)
                """,
                (
                    market_id,
                    entry_date,
                    float(entry_equity),
                    float(entry_pnl) if entry_pnl is not None else None,
                ),
            )
            if cursor.rowcount == 1:
                stats["equity_inserted"] += 1
            else:
                stats["equity_skipped"] += 1
        except Exception as exc:
            logger.warning(
                "  %s: equity_history insert failed for %s -- %s",
                market_id, entry_date, exc,
            )
            stats["equity_skipped"] += 1

    return stats


# ── Main migration ────────────────────────────────────────────────────────────

def run(
    db_path: "Path | None" = None,
    state_dir: "Path | None" = None,
) -> int:
    """
    Run the migration.  Returns 0 on success, 1 on fatal error.

    Args:
        db_path:   Override the SQLite database path (default: auto-resolved).
        state_dir: Override the broker state directory
                   (default: ATLAS_ROOT/brokers/state/).
                   Used by tests to point at a tmp directory with mock JSON.
    """
    if db_path is None:
        db_path = _resolve_db_path()
    if state_dir is None:
        state_dir = _ATLAS_ROOT / "brokers" / "state"

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        # ── Create market_state (always) ───────────────────────────────────
        conn.executescript(_MARKET_STATE_DDL)
        logger.info("market_state: CREATE TABLE IF NOT EXISTS done")

        # ── Create/verify equity_history ────────────────────────────────────
        skip_equity = False
        eq_exists = _table_exists(conn, "equity_history")
        if eq_exists:
            existing_cols = _get_table_cols(conn, "equity_history")
            if not _EQUITY_HISTORY_EXPECTED_COLS.issubset(existing_cols):
                logger.warning(
                    "equity_history already exists with UNEXPECTED columns: "
                    "got=%s, expected superset of %s -- skipping CREATE to "
                    "protect existing rows. Review manually before proceeding.",
                    existing_cols,
                    _EQUITY_HISTORY_EXPECTED_COLS,
                )
                skip_equity = True
            else:
                logger.info("equity_history: already exists with correct schema")
        else:
            conn.executescript(_EQUITY_HISTORY_DDL)
            logger.info("equity_history: created")

        conn.commit()

        # ── Discover JSON files ────────────────────────────────────────────
        json_files = sorted(state_dir.glob("live_*.json"))
        if not json_files:
            logger.warning("No live_*.json files found in %s", state_dir)
            print(
                f"\n=== Backfill Summary ===\n"
                f"  (no live_*.json files found in {state_dir})"
            )
            return 0

        logger.info("Found %d JSON file(s) to backfill:", len(json_files))
        for jf in json_files:
            logger.info("  %s", jf.name)

        # ── Backfill each market ───────────────────────────────────────────
        all_stats = []
        for jf in json_files:
            stats = _backfill_market(conn, jf, skip_equity_history=skip_equity)
            all_stats.append(stats)

        conn.commit()

        # ── Summary ────────────────────────────────────────────────────────
        print("\n=== Backfill Summary ===")
        for s in all_stats:
            mid = s["market_id"]
            ms = s["market_state"]
            ei = s["equity_inserted"]
            esk = s["equity_skipped"]
            print(
                f"  {mid}: market_state {ms}, "
                f"{ei} equity_history rows backfilled ({esk} skipped as duplicates)"
            )

        return 0

    except Exception as exc:
        logger.error("Fatal migration error: %s", exc, exc_info=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return 1
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    ts_str = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = _ATLAS_ROOT / "logs" / f"add_market_state_{ts_str}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(str(log_path))
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(sh)

    db_path = _resolve_db_path()
    logger.info("DB path:    %s", db_path)
    logger.info("State dir:  %s", _ATLAS_ROOT / "brokers" / "state")

    rc = run(db_path=db_path)
    logger.info("Exit code: %d", rc)
    sys.exit(rc)


if __name__ == "__main__":
    main()
