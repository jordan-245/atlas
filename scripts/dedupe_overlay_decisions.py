"""
scripts/dedupe_overlay_decisions.py
====================================
One-time (and idempotent) cleanup of duplicate rows in ``overlay_decisions``.

Background
----------
Prior to 2026-04-28, three separate premarket cron entries all invoked the
overlay engine at the same window:

    00 19 * * 1-5  premarket sp500
    00 19 * * 1-5  premarket commodity_etfs
    00 19 * * 1-5  premarket sector_etfs

Each run independently called ``record_overlay_decision()``, producing 3
near-identical rows (same action + regime_state, timestamps within 1–2 s).

The permanent fix is the idempotency guard added to ``record_overlay_decision``
in ``db/atlas_db.py``.  This script cleans up the historical duplicates.

Deduplication rule
------------------
For each cluster of rows sharing the same ``action`` + ``regime_state`` and
whose ``timestamp`` values lie within 5 minutes of each other, keep the row
with the **lowest id** and delete the rest.

MIGRATION
---------
Forward:
    python3 scripts/dedupe_overlay_decisions.py --apply

Rollback:
    No schema change was made.  The deleted rows are listed in the audit log
    written to ``logs/overlay_dedup_<timestamp>.log`` before deletion.
    To restore, re-insert the rows from the audit log or restore from a backup
    of ``data/atlas.db``.

Usage
-----
    python3 scripts/dedupe_overlay_decisions.py            # dry-run (default)
    python3 scripts/dedupe_overlay_decisions.py --apply    # commit deletes
    python3 scripts/dedupe_overlay_decisions.py --window 600  # 10-min window
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Project bootstrap
# ---------------------------------------------------------------------------
ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

DB_PATH = ATLAS_ROOT / "data" / "atlas.db"
LOGS_DIR = ATLAS_ROOT / "logs"

# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def _load_all_decisions(conn: sqlite3.Connection) -> list[dict]:
    """Return all overlay_decisions rows as dicts, ordered by id ASC."""
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, timestamp, regime_state, action, reasoning, confidence "
        "FROM overlay_decisions ORDER BY id ASC"
    ).fetchall()
    return [dict(r) for r in rows]


def _cluster_duplicates(
    rows: list[dict],
    window_seconds: int = 300,
) -> list[list[dict]]:
    """Return a list of clusters where each cluster has >1 row (duplicates).

    Clustering algorithm:
    - Walk rows in id-ascending order (so the anchor is always the lowest id).
    - A row joins an existing cluster if its (action, regime_state) match AND
      its timestamp is within ``window_seconds`` of the cluster's anchor.
    - Each row joins at most one cluster (first match wins).
    """
    clusters: list[list[dict]] = []
    anchor_datetimes: list[datetime] = []

    for row in rows:
        try:
            row_dt = datetime.fromisoformat(row["timestamp"])
            if row_dt.tzinfo is None:
                row_dt = row_dt.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            # Can't parse timestamp — treat as standalone row.
            continue

        matched = False
        for i, cluster in enumerate(clusters):
            anchor = cluster[0]
            if anchor["action"] != row["action"]:
                continue
            if anchor["regime_state"] != row["regime_state"]:
                continue
            delta = abs((row_dt - anchor_datetimes[i]).total_seconds())
            if delta <= window_seconds:
                cluster.append(row)
                matched = True
                break

        if not matched:
            clusters.append([row])
            anchor_datetimes.append(row_dt)

    # Return only clusters with genuine duplicates (>1 member)
    return [c for c in clusters if len(c) > 1]


def _build_audit_lines(clusters: list[list[dict]]) -> list[str]:
    """Return audit log lines for every row that WOULD be deleted."""
    lines: str = []
    for cluster in clusters:
        keep = cluster[0]  # lowest id
        for row in cluster[1:]:
            reasoning_excerpt = (row.get("reasoning") or "")[:120]
            lines.append(
                f"DELETE id={row['id']}  ts={row['timestamp']}  "
                f"action={row['action']}  regime={row['regime_state']}  "
                f"confidence={row.get('confidence')}  "
                f"reasoning_excerpt={reasoning_excerpt!r}  "
                f"(keeping id={keep['id']})"
            )
    return lines


def run(
    db_path: Path = DB_PATH,
    window_seconds: int = 300,
    apply: bool = False,
) -> dict:
    """
    Run deduplication and return a summary dict:

        {
          "total_rows": int,
          "duplicate_clusters": int,
          "rows_to_delete": int,
          "rows_deleted": int,      # 0 if dry-run
          "audit_log_path": str | None,
        }
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = _load_all_decisions(conn)
    clusters = _cluster_duplicates(rows, window_seconds=window_seconds)
    audit_lines = _build_audit_lines(clusters)
    ids_to_delete = [
        row["id"]
        for cluster in clusters
        for row in cluster[1:]
    ]

    summary = {
        "total_rows": len(rows),
        "duplicate_clusters": len(clusters),
        "rows_to_delete": len(ids_to_delete),
        "rows_deleted": 0,
        "audit_log_path": None,
    }

    if not ids_to_delete:
        conn.close()
        return summary

    if apply:
        # Write audit log BEFORE deleting
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        now_tag = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = LOGS_DIR / f"overlay_dedup_{now_tag}.log"
        log_path.write_text(
            f"# overlay_dedup audit log — {datetime.now().isoformat()}\n"
            f"# window_seconds={window_seconds}  db={db_path}\n"
            f"# rows_to_delete={len(ids_to_delete)}\n\n"
            + "\n".join(audit_lines)
            + "\n"
        )
        summary["audit_log_path"] = str(log_path)

        # Delete duplicates (keep lowest id per cluster)
        placeholders = ",".join("?" * len(ids_to_delete))
        conn.execute(
            f"DELETE FROM overlay_decisions WHERE id IN ({placeholders})",
            ids_to_delete,
        )
        conn.commit()
        summary["rows_deleted"] = len(ids_to_delete)

    conn.close()
    return summary


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Deduplicate overlay_decisions table.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Commit deletions (default: dry-run only).",
    )
    parser.add_argument(
        "--window",
        type=int,
        default=300,
        metavar="SECONDS",
        help="Dedup window in seconds (default: 300).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DB_PATH,
        metavar="PATH",
        help="Path to atlas.db (default: data/atlas.db).",
    )
    args = parser.parse_args(argv)

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"overlay_dedup [{mode}]  window={args.window}s  db={args.db}")

    summary = run(db_path=args.db, window_seconds=args.window, apply=args.apply)

    print(f"  total rows in table  : {summary['total_rows']}")
    print(f"  duplicate clusters   : {summary['duplicate_clusters']}")
    print(f"  rows to delete       : {summary['rows_to_delete']}")

    if args.apply:
        print(f"  rows deleted         : {summary['rows_deleted']}")
        if summary["audit_log_path"]:
            print(f"  audit log            : {summary['audit_log_path']}")
    else:
        print("  (dry-run — no changes committed)")

    if summary["duplicate_clusters"] > 0:
        # Print cluster details
        conn = sqlite3.connect(str(args.db))
        conn.row_factory = sqlite3.Row
        rows = _load_all_decisions(conn)
        clusters = _cluster_duplicates(rows, window_seconds=args.window)
        conn.close()

        print("\n  Cluster details:")
        for i, cluster in enumerate(clusters, 1):
            anchor = cluster[0]
            ids = [r["id"] for r in cluster]
            print(
                f"    [{i}] keep=id:{anchor['id']}  "
                f"delete=ids:{ids[1:]}  "
                f"action={anchor['action']}  "
                f"regime={anchor['regime_state']}  "
                f"anchor_ts={anchor['timestamp']}"
            )


if __name__ == "__main__":
    main()
