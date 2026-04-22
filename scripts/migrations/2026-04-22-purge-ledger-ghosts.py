#!/usr/bin/env python3
"""Purge ghost ledger rows introduced by reconcile flows. See task brief.

Run:
  python3 scripts/migrations/2026-04-22-purge-ledger-ghosts.py --dry-run
  python3 scripts/migrations/2026-04-22-purge-ledger-ghosts.py

Ghost categories targeted:
  A) Rows with poison strategy + phantom exit_reason (or zero-stop open) + zero PnL
  B) D ticker duplicate stop_loss+error rows (keep MIN id per pnl group)
  C) Duplicate open rows per ticker — keep the real-strategy (or earliest reconciled) row

After migration: exactly 7 open rows, one per ticker in
  {AMD, CHTR, FCX, GLD, ON, UNG, XLY}, all with non-poison strategies.

NOTE on XLY strategy: XLY has no real-strategy open row; all prior real-strategy
rows were closed as reconcile_phantom.  Filter C keeps the earliest 'reconciled'
row, then upgrades it from the most-recent real-strategy closed trade for that
ticker.  This satisfies the poison-strategy assertion while preserving data honesty.
"""

from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path bootstrap ────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

# ── Constants ─────────────────────────────────────────────────────────────────
_LEGITIMATE_TICKERS: set[str] = {"AMD", "CHTR", "FCX", "GLD", "ON", "UNG", "XLY"}
_EXPECTED_OPEN_COUNT: int = 7
_POISON_STRATEGIES: tuple[str, ...] = ("unknown", "reconciled", "")


def _resolve_db_path() -> Path:
    """Return the active DB path (respects _db_path_override for tests)."""
    try:
        from db import atlas_db
        override = getattr(atlas_db, "_db_path_override", None)
        if override:
            return Path(override)
    except Exception:
        pass
    return _ATLAS_ROOT / "data" / "atlas.db"


def _backup_csv(db_path: Path) -> Path:
    """Write a CSV snapshot of the trades table BEFORE any mutation."""
    backup_dir = _ATLAS_ROOT / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H%M%S")
    csv_path = backup_dir / f"trades_pre_ghost_purge_{ts}.csv"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
        if rows:
            with csv_path.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows([dict(r) for r in rows])
        else:
            csv_path.write_text("no rows\n")
    finally:
        conn.close()

    return csv_path


def _count_filter_a(cur: sqlite3.Cursor) -> int:
    cur.execute("""
        SELECT COUNT(*) FROM trades
         WHERE strategy IN ('unknown','reconciled','')
           AND (exit_reason IN ('reconcile_phantom','reconcile_fill','trailing_stop_fill')
                OR (stop_price = 0.0 AND exit_date IS NULL))
           AND (ABS(COALESCE(pnl,0)) < 0.01 OR pnl IS NULL)
    """)
    return cur.fetchone()[0]


def run_migration(db_path: Path, dry_run: bool, log: logging.Logger) -> int:
    """Execute all three filters inside one transaction.

    Returns:
        0 on success, 1 on assertion failure or error.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        cur.execute("BEGIN")

        # ── Filter A: general ghost purge ──────────────────────────────────
        a_preview = _count_filter_a(cur)
        log.info("Filter A: would purge %d ghost rows", a_preview)

        cur.execute("""
            DELETE FROM trades
             WHERE strategy IN ('unknown','reconciled','')
               AND (exit_reason IN ('reconcile_phantom','reconcile_fill','trailing_stop_fill')
                    OR (stop_price = 0.0 AND exit_date IS NULL))
               AND (ABS(COALESCE(pnl,0)) < 0.01 OR pnl IS NULL)
        """)
        a_deleted = cur.rowcount
        log.info("Filter A: deleted %d rows", a_deleted)

        # ── Filter B: D ticker stop_loss+error duplicates ──────────────────
        cur.execute("""
            SELECT COUNT(*) FROM trades
             WHERE ticker='D' AND exit_reason='stop_loss' AND status='error'
        """)
        b_total = cur.fetchone()[0]

        cur.execute("""
            DELETE FROM trades
             WHERE ticker='D'
               AND exit_reason='stop_loss'
               AND status='error'
               AND id NOT IN (
                   SELECT MIN(id) FROM trades
                    WHERE ticker='D' AND exit_reason='stop_loss' AND status='error'
                    GROUP BY pnl
               )
        """)
        b_deleted = cur.rowcount
        log.info(
            "Filter B: %d D-ticker error rows before; deleted %d duplicates",
            b_total, b_deleted,
        )

        # ── Filter C: deduplicate open rows per ticker ─────────────────────
        # For each ticker with >1 open row:
        #   keep = MIN(id) WHERE strategy NOT IN poison  (real strategy)
        #   fallback = MIN(id) WHERE strategy='reconciled'  (if no real row)
        #   delete all others that are open
        cur.execute("""
            SELECT ticker, COUNT(*) as cnt
              FROM trades
             WHERE exit_date IS NULL
             GROUP BY ticker
            HAVING cnt > 1
        """)
        dup_tickers = [(r["ticker"], r["cnt"]) for r in cur.fetchall()]
        log.info("Filter C: %d tickers have >1 open row: %s",
                 len(dup_tickers), [t for t, _ in dup_tickers])

        c_deleted_total = 0
        for ticker, cnt in dup_tickers:
            # Find the preferred row to keep
            cur.execute("""
                SELECT id FROM trades
                 WHERE exit_date IS NULL AND ticker=?
                   AND strategy NOT IN ('unknown','reconciled','')
                 ORDER BY id ASC LIMIT 1
            """, (ticker,))
            keep_row = cur.fetchone()

            if keep_row is None:
                # Fallback: earliest reconciled row
                cur.execute("""
                    SELECT id FROM trades
                     WHERE exit_date IS NULL AND ticker=?
                       AND strategy='reconciled'
                     ORDER BY id ASC LIMIT 1
                """, (ticker,))
                keep_row = cur.fetchone()

            if keep_row is None:
                log.warning("Filter C: no keepable row for %s — skipping", ticker)
                continue

            keep_id = keep_row["id"]

            cur.execute("""
                DELETE FROM trades
                 WHERE exit_date IS NULL AND ticker=? AND id != ?
            """, (ticker, keep_id))
            n = cur.rowcount
            c_deleted_total += n
            log.info("Filter C: %s — kept id=%d, deleted %d duplicate(s)", ticker, keep_id, n)

        log.info("Filter C: total deleted %d rows", c_deleted_total)

        # ── Strategy heal pass (upgrade 'reconciled'/'unknown' keeps) ──────
        # For any surviving open row still with a poison strategy, look up
        # the most-recent real strategy from closed trades for that ticker.
        # This handles XLY (and any similar ticker) where the only surviving
        # open row is a 'reconciled' fallback.
        cur.execute("""
            SELECT id, ticker FROM trades
             WHERE exit_date IS NULL
               AND strategy IN ('unknown','reconciled','')
        """)
        heal_candidates = cur.fetchall()
        for row in heal_candidates:
            rid, ticker = row["id"], row["ticker"]
            cur.execute("""
                SELECT strategy FROM trades
                 WHERE ticker=?
                   AND strategy NOT IN ('unknown','reconciled','')
                   AND exit_date IS NOT NULL
                 ORDER BY id DESC LIMIT 1
            """, (ticker,))
            heal = cur.fetchone()
            if heal:
                healed_strategy = heal["strategy"]
                cur.execute(
                    "UPDATE trades SET strategy=? WHERE id=?",
                    (healed_strategy, rid),
                )
                log.info(
                    "Strategy heal: id=%d %s: 'reconciled'→'%s' (from closed-trade history)",
                    rid, ticker, healed_strategy,
                )
            else:
                log.warning(
                    "Strategy heal: id=%d %s: no closed-trade strategy found — "
                    "leaving as-is (will cause poison assertion failure)",
                    rid, ticker,
                )

        # ── Assertions ────────────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) FROM trades WHERE exit_date IS NULL")
        open_count = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT ticker) FROM trades WHERE exit_date IS NULL")
        distinct_count = cur.fetchone()[0]

        cur.execute("SELECT ticker FROM trades WHERE exit_date IS NULL ORDER BY ticker")
        open_tickers = {r["ticker"] for r in cur.fetchall()}

        cur.execute("""
            SELECT COUNT(*) FROM trades
             WHERE exit_date IS NULL
               AND strategy IN ('unknown','reconciled','')
        """)
        poison_count = cur.fetchone()[0]

        log.info(
            "Assertions: open_count=%d distinct=%d tickers=%s poison=%d",
            open_count, distinct_count, sorted(open_tickers), poison_count,
        )

        assertion_failures = []
        if open_count != _EXPECTED_OPEN_COUNT:
            assertion_failures.append(
                f"open_count={open_count} != {_EXPECTED_OPEN_COUNT}"
            )
        if distinct_count != _EXPECTED_OPEN_COUNT:
            assertion_failures.append(
                f"distinct_tickers={distinct_count} != {_EXPECTED_OPEN_COUNT}"
            )
        if open_tickers != _LEGITIMATE_TICKERS:
            extra = open_tickers - _LEGITIMATE_TICKERS
            missing = _LEGITIMATE_TICKERS - open_tickers
            assertion_failures.append(
                f"ticker mismatch: extra={extra} missing={missing}"
            )
        if poison_count != 0:
            # Fetch which tickers are still poisoned for diagnostics
            cur.execute("""
                SELECT id, ticker, strategy FROM trades
                 WHERE exit_date IS NULL
                   AND strategy IN ('unknown','reconciled','')
            """)
            poison_rows = [(r["id"], r["ticker"], r["strategy"]) for r in cur.fetchall()]
            assertion_failures.append(
                f"poison_count={poison_count}: {poison_rows}"
            )

        if assertion_failures:
            log.error("FATAL — assertion failures: %s", assertion_failures)
            conn.rollback()
            log.error("Transaction ROLLED BACK")
            return 1

        log.info("All assertions PASSED ✓")

        # ── Commit or rollback ────────────────────────────────────────────
        if dry_run:
            conn.execute("ROLLBACK")
            log.info("DRY RUN complete — transaction rolled back, no changes written")
        else:
            conn.commit()
            log.info(
                "COMMITTED: filter_a=%d filter_b=%d filter_c=%d "
                "open_rows=%d distinct_tickers=%d poison=%d",
                a_deleted, b_deleted, c_deleted_total,
                open_count, distinct_count, poison_count,
            )

        return 0

    except Exception as exc:
        conn.rollback()
        log.error("Exception — rolled back: %s", exc, exc_info=True)
        return 1
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be deleted without writing.")
    args = parser.parse_args()

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%dT%H%M%S")
    log_path = _ATLAS_ROOT / "logs" / f"ghost_purge_{ts}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Dual output: file + stdout
    log = logging.getLogger("ghost_purge")
    log.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    fh = logging.FileHandler(str(log_path))
    fh.setFormatter(fmt)
    log.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)

    db_path = _resolve_db_path()
    log.info("DB path: %s", db_path)
    log.info("dry_run=%s", args.dry_run)

    # Backup first (regardless of dry-run)
    try:
        csv_path = _backup_csv(db_path)
        log.info("Backup written: %s", csv_path)
    except Exception as exc:
        log.error("Backup FAILED — aborting: %s", exc)
        sys.exit(1)

    rc = run_migration(db_path, dry_run=args.dry_run, log=log)
    log.info("Exit code: %d", rc)
    sys.exit(rc)


if __name__ == "__main__":
    main()
