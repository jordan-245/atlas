#!/usr/bin/env python3
"""
Audit tool: detect and optionally mark duplicate closed trades.

Usage
-----
# Dry-run strict mode (report only):
    python3 scripts/audit_duplicate_trades.py

# Relaxed mode (0.5% PnL tolerance):
    python3 scripts/audit_duplicate_trades.py --relaxed

# With CSV backup + mark superseded:
    python3 scripts/audit_duplicate_trades.py --relaxed \
        --mark-superseded \
        --csv-backup data/backups/duplicate_trades_audit_2026-04-27.csv

Matching modes
--------------
--strict  (default): groups on (ticker, DATE(entry_date), DATE(exit_date)) exactly.
--relaxed           : same grouping PLUS treats near-identical PnL pairs
                      (|pnl_a - pnl_b| <= 0.5% * max(|pnl_a|, |pnl_b|) + 0.01)
                      as dupes; catches rounding-off duplicates that might fall
                      in the same strict window.

Canonical row: lowest id in each dup group (oldest = the original).
Superseded  : all other rows in the group.

Idempotent: rows already status='superseded' are excluded from detection,
so re-running --mark-superseded is safe.
"""
from __future__ import annotations

import argparse
import csv
import logging
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "atlas.db"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


# ── helpers ──────────────────────────────────────────────────────────────────

def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _fetch_closed_trades(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return all closed (non-superseded) trades as dicts.

    Uses only columns guaranteed to exist across all schema versions to allow
    the audit script to run against stripped-down test tables.
    """
    rows = conn.execute(
        """
        SELECT id, ticker, strategy, universe, entry_date, exit_date,
               entry_price, exit_price, shares, pnl, status, created_at
        FROM trades
        WHERE status = 'closed'
        ORDER BY id ASC
        """
    ).fetchall()
    return [dict(r) for r in rows]


def _find_dup_groups_strict(
    trades: list[dict[str, Any]]
) -> list[list[dict[str, Any]]]:
    """Group by (ticker, DATE(entry_date), DATE(exit_date)). n>=2 is a dup group."""
    groups: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        entry_day = (t["entry_date"] or "")[:10]
        exit_day  = (t["exit_date"]  or "")[:10]
        key = (t["ticker"], entry_day, exit_day)
        groups[key].append(t)
    return [v for v in groups.values() if len(v) >= 2]


def _pnl_similar(a: float | None, b: float | None) -> bool:
    """True if PnL values are within 0.5% + $0.01 of each other."""
    if a is None or b is None:
        return True  # NULL pnl treated as match (zero-pnl rows)
    a_f, b_f = float(a), float(b)
    return abs(a_f - b_f) <= 0.005 * max(abs(a_f), abs(b_f)) + 0.01


def _find_dup_groups_relaxed(
    trades: list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    """Same as strict but tolerates 0.5% PnL difference.

    In practice returns the same groups as strict for the current dataset
    (all existing dupes have exactly matching PnLs in the same date window).
    The relaxed mode is future-proofing for rounding-off artifacts.
    """
    strict_groups = _find_dup_groups_strict(trades)
    seen_ids: set[int] = set()
    result: list[list[dict[str, Any]]] = []

    for group in strict_groups:
        group_ids = {r["id"] for r in group}
        if group_ids & seen_ids:
            continue
        result.append(group)
        seen_ids |= group_ids

    # Extended relaxed: scan remaining trades for same (ticker, entry_day, exit_day)
    # with near-identical PnL — catches floating-point rounding variants.
    by_window: dict[tuple, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        if t["id"] in seen_ids:
            continue
        entry_day = (t["entry_date"] or "")[:10]
        exit_day  = (t["exit_date"]  or "")[:10]
        by_window[(t["ticker"], entry_day, exit_day)].append(t)

    for candidates in by_window.values():
        if len(candidates) < 2:
            continue
        merged: list[list[dict[str, Any]]] = []
        for cand in candidates:
            placed = False
            for grp in merged:
                if _pnl_similar(cand["pnl"], grp[0]["pnl"]):
                    grp.append(cand)
                    placed = True
                    break
            if not placed:
                merged.append([cand])
        for grp in merged:
            if len(grp) >= 2:
                ids = {r["id"] for r in grp}
                if not (ids & seen_ids):
                    result.append(grp)
                    seen_ids |= ids

    return result


def _split_canonical_superseded(
    group: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Canonical = lowest id; superseded = the rest (older row is canonical)."""
    sorted_group = sorted(group, key=lambda r: r["id"])
    return sorted_group[0], sorted_group[1:]


def _format_group(
    canonical: dict[str, Any],
    superseded: list[dict[str, Any]],
) -> str:
    all_rows  = [canonical] + superseded
    ids       = [r["id"] for r in all_rows]
    pnls      = [round(float(r["pnl"] or 0.0), 2) for r in all_rows]
    strategies = [r["strategy"] for r in all_rows]
    shares    = [r["shares"] for r in all_rows]
    entry_day = (canonical["entry_date"] or "")[:10]
    exit_day  = (canonical["exit_date"]  or "")[:10]
    lines = [
        f"  ticker={canonical['ticker']}  n={len(all_rows)}  ids={ids}",
        f"  entry_day={entry_day}  exit_day={exit_day}",
        f"  pnls={pnls}  shares={shares}",
        f"  strategies={strategies}",
        f"  canonical_id={canonical['id']}  superseded_ids={[r['id'] for r in superseded]}",
    ]
    return "\n".join(lines)


# ── CSV backup ────────────────────────────────────────────────────────────────

def _write_csv_backup(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        logger.info("No rows to back up — CSV not written.")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("CSV backup written: %s (%d rows)", path, len(rows))


# ── mark superseded ───────────────────────────────────────────────────────────

def _mark_superseded_rows(
    conn: sqlite3.Connection,
    superseded_ids: list[int],
) -> int:
    """UPDATE status='superseded' for given ids. Returns count changed."""
    if not superseded_ids:
        return 0
    ph = ",".join("?" * len(superseded_ids))
    already_rows = conn.execute(
        f"SELECT id FROM trades WHERE id IN ({ph}) AND status = 'superseded'",
        superseded_ids,
    ).fetchall()
    already_ids = {r["id"] for r in already_rows}
    to_update = [i for i in superseded_ids if i not in already_ids]
    if not to_update:
        logger.info(
            "All %d superseded rows already marked — nothing to update.",
            len(superseded_ids),
        )
        return 0
    conn.executemany(
        "UPDATE trades SET status='superseded', updated_at=datetime('now') WHERE id=?",
        [(i,) for i in to_update],
    )
    conn.commit()
    logger.info("Marked %d rows as status='superseded'.", len(to_update))
    return len(to_update)


# ── public API ────────────────────────────────────────────────────────────────

def run_audit(
    db_path: Path = DB_PATH,
    relaxed: bool = False,
    mark_superseded: bool = False,
    csv_backup: Path | None = None,
) -> dict[str, Any]:
    """Run the duplicate audit.

    Returns:
        {
          "dup_group_count": int,
          "superseded_row_count": int,
          "pnl_inflation": float,
          "groups": [ {"canonical": {...}, "superseded": [{...}, ...]}, ... ],
          "all_superseded_rows": [ {...}, ... ],
        }
    """
    conn = _connect(db_path)
    try:
        trades = _fetch_closed_trades(conn)
        logger.info("Fetched %d closed (non-superseded) trades.", len(trades))

        if relaxed:
            dup_groups = _find_dup_groups_relaxed(trades)
            mode_label = "RELAXED"
        else:
            dup_groups = _find_dup_groups_strict(trades)
            mode_label = "STRICT"

        logger.info("[%s] Found %d duplicate groups.", mode_label, len(dup_groups))

        groups_out: list[dict[str, Any]] = []
        all_superseded: list[dict[str, Any]] = []
        pnl_inflation = 0.0

        for group in dup_groups:
            canonical, superseded = _split_canonical_superseded(group)
            print(_format_group(canonical, superseded))
            for s in superseded:
                pnl_inflation += float(s["pnl"] or 0.0)
                all_superseded.append(s)
            groups_out.append({"canonical": canonical, "superseded": superseded})

        print()
        print(f"── Summary [{mode_label}] ─────────────────────────────────")
        print(f"  Dup groups:         {len(dup_groups)}")
        print(f"  Rows to supersede:  {len(all_superseded)}")
        print(f"  PnL inflation:      ${pnl_inflation:+.2f}")

        pre_total = conn.execute(
            "SELECT SUM(pnl) FROM trades WHERE status='closed'"
        ).fetchone()[0] or 0.0

        if csv_backup and all_superseded:
            _write_csv_backup(csv_backup, all_superseded)

        if mark_superseded:
            superseded_ids = [r["id"] for r in all_superseded]
            updated = _mark_superseded_rows(conn, superseded_ids)
            print(f"  Rows marked:        {updated}")
            new_total = conn.execute(
                "SELECT SUM(pnl) FROM trades WHERE status='closed'"
            ).fetchone()[0] or 0.0
            print(f"  Pre-dedup SUM(pnl):  ${pre_total:.2f}")
            print(f"  Post-dedup SUM(pnl): ${new_total:.2f}")
            print(f"  Inflation removed:   ${pnl_inflation:.2f}")
        else:
            post_estimate = pre_total - pnl_inflation
            print(f"  Current SUM(pnl):   ${pre_total:.2f}")
            print(f"  Post-dedup est.:    ${post_estimate:.2f}")
            print("  (dry-run — no changes made)")

        return {
            "dup_group_count": len(dup_groups),
            "superseded_row_count": len(all_superseded),
            "pnl_inflation": pnl_inflation,
            "groups": groups_out,
            "all_superseded_rows": all_superseded,
        }
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Audit duplicate closed trades in atlas.db"
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--relaxed",
        action="store_true",
        help="Relaxed match: 0.5%% PnL tolerance within same (ticker, entry_day, exit_day)",
    )
    mode_group.add_argument(
        "--strict",
        action="store_true",
        help="Strict mode (default): exact (ticker, entry_day, exit_day) match only",
    )
    parser.add_argument(
        "--mark-superseded",
        action="store_true",
        dest="mark_superseded",
        help="Actually update DB (status='superseded'). Default = report only.",
    )
    parser.add_argument(
        "--csv-backup",
        type=Path,
        dest="csv_backup",
        help="Path to write CSV backup of rows being modified (before update).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        help=f"Path to atlas.db (default: {DB_PATH})",
    )
    args = parser.parse_args()

    run_audit(
        db_path=args.db,
        relaxed=args.relaxed,
        mark_superseded=args.mark_superseded,
        csv_backup=args.csv_backup,
    )


if __name__ == "__main__":
    main()
