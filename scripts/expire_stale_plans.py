"""F-07: Expire plans stuck in pending_approval >72h.

Sets status='expired' on plans with status='pending_approval' AND created_at < 3 days ago.
Records the reason in plan_data JSON. Idempotent — won't re-expire already-expired plans.

Run on cron or manually after restart.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB = Path(__file__).parent.parent / "data" / "atlas.db"


def expire_stale_plans(db_path: str | Path | None = None) -> list[int]:
    """Expire pending_approval plans older than 72 h.

    Returns list of plan IDs that were expired.
    """
    db_path = str(db_path or DB)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Find candidates
        rows = conn.execute("""
            SELECT id, date, market_id, created_at, plan_data
            FROM plans
            WHERE status='pending_approval'
              AND julianday('now') - julianday(created_at) > 3
        """).fetchall()
        logger.info("Found %d plans to expire", len(rows))
        if not rows:
            return []

        now_iso = datetime.now(timezone.utc).isoformat()
        expired_ids: list[int] = []
        for r in rows:
            # Annotate plan_data with expiry reason
            try:
                pd = json.loads(r["plan_data"]) if r["plan_data"] else {}
            except Exception:
                pd = {"raw": r["plan_data"]}
            pd["expired_at"] = now_iso
            pd["expired_reason"] = (
                "F-07 audit: stale pending_approval >72h, auto-expired 2026-05-11"
            )
            conn.execute(
                "UPDATE plans SET status='expired', plan_data=? WHERE id=?",
                (json.dumps(pd), r["id"]),
            )
            expired_ids.append(r["id"])
        conn.commit()
        logger.info("Expired plan IDs: %s", expired_ids)

        # Verify
        n_remaining = conn.execute(
            "SELECT COUNT(*) FROM plans"
            " WHERE status='pending_approval'"
            "   AND julianday('now') - julianday(created_at) > 3"
        ).fetchone()[0]
        logger.info("Remaining stale (>3d) pending_approval plans: %d", n_remaining)
        if n_remaining != 0:
            raise RuntimeError(
                f"F-07 acceptance failed: still have {n_remaining} stale plans"
            )
        return expired_ids
    finally:
        conn.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    expired = expire_stale_plans()
    print(f"Expired {len(expired)} plan(s). IDs: {expired}")
    # Final verification against prod DB
    conn = sqlite3.connect(str(DB))
    n = conn.execute(
        "SELECT COUNT(*) FROM plans"
        " WHERE status='pending_approval'"
        "   AND julianday('now') - julianday(created_at) > 3"
    ).fetchone()[0]
    conn.close()
    print(f"Remaining stale (>3d) pending_approval plans: {n}")
    assert n == 0, f"F-07 acceptance failed: still {n} stale plans"
    print("F-07 acceptance PASSED: 0 stale pending_approval plans.")


if __name__ == "__main__":
    main()
