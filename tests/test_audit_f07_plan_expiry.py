"""F-07 acceptance tests: no stale pending_approval plans >72h remain.

Tests both the expire_stale_plans script and the production DB state.
"""
from __future__ import annotations

import json
import sqlite3
import tempfile
import os
from datetime import datetime, timezone

import pytest


def _make_plans_db(rows: list[dict]) -> str:
    """Create a temp SQLite DB with a plans table seeded with given rows."""
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE plans (
            id INTEGER PRIMARY KEY,
            date TEXT,
            market_id TEXT,
            status TEXT,
            plan_data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO plans (id, date, market_id, status, plan_data, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (
                r["id"],
                r.get("date", "2026-01-01"),
                r.get("market_id", "sp500"),
                r.get("status", "pending_approval"),
                r.get("plan_data", "{}"),
                r.get("created_at", "2026-01-01 00:00:00"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


class TestExpireStalePlans:
    """Unit tests for scripts/expire_stale_plans.expire_stale_plans()."""

    def test_expires_old_pending_approval(self):
        from scripts.expire_stale_plans import expire_stale_plans
        db_path = _make_plans_db([
            {"id": 1, "status": "pending_approval", "created_at": "2026-01-01 00:00:00"},
            {"id": 2, "status": "pending_approval", "created_at": "2026-02-15 00:00:00"},
        ])
        expired = expire_stale_plans(db_path)
        assert set(expired) == {1, 2}, f"Expected both expired, got {expired}"
        # Verify DB state
        conn = sqlite3.connect(db_path)
        rows = conn.execute("SELECT id, status FROM plans").fetchall()
        conn.close()
        for row in rows:
            assert row[1] == "expired", f"Plan {row[0]} still has status {row[1]}"

    def test_leaves_fresh_pending_approval(self):
        from scripts.expire_stale_plans import expire_stale_plans
        import datetime
        fresh_ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        db_path = _make_plans_db([
            {"id": 1, "status": "pending_approval", "created_at": "2026-01-01 00:00:00"},
            {"id": 2, "status": "pending_approval", "created_at": fresh_ts},
        ])
        expired = expire_stale_plans(db_path)
        assert 1 in expired, "Old plan should be expired"
        assert 2 not in expired, "Fresh plan should NOT be expired"
        # Verify: plan 2 still pending
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT status FROM plans WHERE id=2").fetchone()
        conn.close()
        assert row[0] == "pending_approval", "Fresh plan was incorrectly expired"

    def test_idempotent(self):
        """Running expire_stale_plans twice should only expire once."""
        from scripts.expire_stale_plans import expire_stale_plans
        db_path = _make_plans_db([
            {"id": 1, "status": "pending_approval", "created_at": "2026-01-01 00:00:00"},
        ])
        expired_first = expire_stale_plans(db_path)
        expired_second = expire_stale_plans(db_path)
        assert expired_first == [1], "First run should expire plan 1"
        assert expired_second == [], "Second run should be no-op"

    def test_leaves_other_statuses_untouched(self):
        """Only pending_approval is expired — approved/rejected/expired are safe."""
        from scripts.expire_stale_plans import expire_stale_plans
        db_path = _make_plans_db([
            {"id": 1, "status": "approved",  "created_at": "2026-01-01 00:00:00"},
            {"id": 2, "status": "rejected",  "created_at": "2026-01-01 00:00:00"},
            {"id": 3, "status": "expired",   "created_at": "2026-01-01 00:00:00"},
            {"id": 4, "status": "pending_approval", "created_at": "2026-01-01 00:00:00"},
        ])
        expired = expire_stale_plans(db_path)
        assert expired == [4]
        conn = sqlite3.connect(db_path)
        statuses = {r[0]: r[1] for r in conn.execute("SELECT id, status FROM plans")}
        conn.close()
        assert statuses[1] == "approved"
        assert statuses[2] == "rejected"
        assert statuses[3] == "expired"

    def test_plan_data_annotated_with_reason(self):
        """Expired plan_data should contain expired_at and expired_reason."""
        from scripts.expire_stale_plans import expire_stale_plans
        db_path = _make_plans_db([
            {"id": 1, "status": "pending_approval", "plan_data": '{"signals": []}',
             "created_at": "2026-01-01 00:00:00"},
        ])
        expire_stale_plans(db_path)
        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT plan_data FROM plans WHERE id=1").fetchone()
        conn.close()
        pd = json.loads(row[0])
        assert "expired_at" in pd
        assert "expired_reason" in pd
        assert "signals" in pd  # original data preserved


# ─── Prod DB acceptance test ───────────────────────────────────────────────────

def test_no_stale_pending_approval_plans():
    """F-07: zero plans in pending_approval older than 72h in production DB."""
    from pathlib import Path
    db_path = Path(__file__).resolve().parent.parent / "data" / "atlas.db"
    if not db_path.exists():
        pytest.skip("Production DB not found")
    conn = sqlite3.connect(str(db_path))
    n = conn.execute(
        "SELECT COUNT(*) FROM plans WHERE status='pending_approval'"
        " AND julianday('now') - julianday(created_at) > 3"
    ).fetchone()[0]
    conn.close()
    assert n == 0, f"Found {n} stale pending_approval plans >3 days old"
