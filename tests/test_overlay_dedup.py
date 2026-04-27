"""tests/test_overlay_dedup.py — Idempotency guard for overlay_decisions.

Tests the dedup guard added to ``db/atlas_db.record_overlay_decision`` and the
``scripts/dedupe_overlay_decisions`` backfill script.

All tests use the ``_isolate_prod_db`` fixture (autouse via conftest) so no
writes ever reach production ``data/atlas.db``.

Run with:
    cd /root/atlas && python3 -m pytest tests/test_overlay_dedup.py -xvs --timeout=30
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as _adb
from db.atlas_db import record_overlay_decision, get_overlay_decisions, init_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_seconds: float = 0.0) -> str:
    """Return an ISO UTC timestamp offset by ``offset_seconds`` from now."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    return dt.isoformat()


def _insert(
    action: str = "tighten",
    regime_state: str = "bull_risk_on",
    offset_seconds: float = 0.0,
    reasoning: str = "test reason",
    confidence: float = 0.62,
) -> int:
    """Insert a single overlay decision and return its id."""
    return record_overlay_decision(
        timestamp=_ts(offset_seconds),
        regime_state=regime_state,
        action=action,
        sizing_override=None,
        universes_deactivated=None,
        tickers_avoided=None,
        reasoning=reasoning,
        confidence=confidence,
        data_sources={"vix": 20.0},
    )


# ---------------------------------------------------------------------------
# Test 1: same action+regime within window → single row, same id returned
# ---------------------------------------------------------------------------

class TestDedupSameActionSameWindow:
    """Insert A at t=0, then identical B at t=+30s → only 1 row, both return same id."""

    def test_only_one_row_in_db(self) -> None:
        id_a = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=0)
        id_b = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=30)

        rows = get_overlay_decisions()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"

    def test_both_calls_return_same_id(self) -> None:
        id_a = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=0)
        id_b = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=30)

        assert id_a == id_b, f"Expected same id but got id_a={id_a}, id_b={id_b}"

    def test_triple_insert_still_one_row(self) -> None:
        """Simulate the real 3-market cron scenario — sp500 + commodity_etfs + sector_etfs."""
        id_a = _insert(action="tighten", regime_state="recovery_early", offset_seconds=0)
        id_b = _insert(action="tighten", regime_state="recovery_early", offset_seconds=1)
        id_c = _insert(action="tighten", regime_state="recovery_early", offset_seconds=2)

        rows = get_overlay_decisions()
        assert len(rows) == 1, f"Expected 1 row, got {len(rows)}"
        assert id_a == id_b == id_c, "All three calls must return the same id"


# ---------------------------------------------------------------------------
# Test 2: different action within window → 2 rows
# ---------------------------------------------------------------------------

class TestDedupDifferentAction:
    """Insert 'tighten' at t=0 and 'no_change' at t=+30s → 2 rows."""

    def test_different_action_inserts_new_row(self) -> None:
        id_a = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=0)
        id_b = _insert(action="no_change", regime_state="bull_risk_on", offset_seconds=30)

        rows = get_overlay_decisions()
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        assert id_a != id_b


# ---------------------------------------------------------------------------
# Test 3: same action, outside window → 2 rows
# ---------------------------------------------------------------------------

class TestDedupOutsideWindow:
    """Insert A at t=0 and identical B at t=+600s → 2 rows (outside 5-min window)."""

    def test_outside_window_inserts_new_row(self) -> None:
        id_a = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=0)
        # 600 s > 300 s window
        id_b = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=600)

        rows = get_overlay_decisions()
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        assert id_a != id_b

    def test_exactly_at_window_boundary_inserts(self) -> None:
        """t=+301s (just outside window) must produce a second row."""
        id_a = _insert(action="no_change", regime_state="transition_uncertain", offset_seconds=0)
        id_b = _insert(action="no_change", regime_state="transition_uncertain", offset_seconds=301)

        rows = get_overlay_decisions()
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"


# ---------------------------------------------------------------------------
# Test 4: same action, different regime_state → 2 rows
# ---------------------------------------------------------------------------

class TestDedupDifferentRegime:
    """Insert A at t=0 with regime X, B at t=+30s with regime Y → 2 rows."""

    def test_different_regime_inserts_new_row(self) -> None:
        id_a = _insert(action="tighten", regime_state="bull_risk_on", offset_seconds=0)
        id_b = _insert(action="tighten", regime_state="bear_risk_off", offset_seconds=30)

        rows = get_overlay_decisions()
        assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}"
        assert id_a != id_b


# ---------------------------------------------------------------------------
# Tests 5 & 6: dedupe_overlay_decisions script
# ---------------------------------------------------------------------------

class TestDedupeScript:
    """Tests for scripts/dedupe_overlay_decisions.py."""

    @pytest.fixture
    def script_db(self, tmp_path: Path) -> Path:
        """Create a fresh isolated DB with known duplicates seeded."""
        db_file = tmp_path / "script_test.db"
        init_db(str(db_file))

        # Seed the DB directly via sqlite3 (bypass the new dedup guard)
        conn = sqlite3.connect(str(db_file))
        rows = [
            # Cluster 1: ids will be 1,2,3 — same action+regime, within 5min
            ("2026-04-27T09:02:36+00:00", "bull_risk_on",     "tighten",   0.62, "reasoning A"),
            ("2026-04-27T09:02:37+00:00", "bull_risk_on",     "tighten",   0.62, "reasoning A"),
            ("2026-04-27T09:02:38+00:00", "bull_risk_on",     "tighten",   0.62, "reasoning A"),
            # Cluster 2: ids will be 4,5 — different day
            ("2026-04-24T09:01:10+00:00", "recovery_early",   "no_change", 0.72, "reasoning B"),
            ("2026-04-24T09:01:26+00:00", "recovery_early",   "no_change", 0.72, "reasoning B"),
            # Standalone (different action) — should NOT be touched
            ("2026-04-24T09:01:10+00:00", "recovery_early",   "tighten",   0.62, "reasoning C"),
            # Standalone (different regime) — should NOT be touched
            ("2026-04-22T09:01:52+00:00", "bear_risk_off",    "tighten",   0.55, "reasoning D"),
            # Cluster 3: three rows, ids 8,9,10
            ("2026-04-20T09:02:24+00:00", "recovery_early",   "tighten",   0.62, "reasoning E"),
            ("2026-04-20T09:02:25+00:00", "recovery_early",   "tighten",   0.62, "reasoning E"),
            ("2026-04-20T09:02:26+00:00", "recovery_early",   "tighten",   0.62, "reasoning E"),
        ]
        conn.executemany(
            """INSERT INTO overlay_decisions
               (timestamp, regime_state, action, confidence, reasoning)
               VALUES (?,?,?,?,?)""",
            rows,
        )
        conn.commit()
        conn.close()
        return db_file

    def test_dry_run_identifies_duplicates_without_modifying(self, script_db: Path) -> None:
        """Test 5: dry-run reports correct counts and makes NO DB changes."""
        from scripts.dedupe_overlay_decisions import run

        before_count = sqlite3.connect(str(script_db)).execute(
            "SELECT COUNT(*) FROM overlay_decisions"
        ).fetchone()[0]

        summary = run(db_path=script_db, window_seconds=300, apply=False)

        after_count = sqlite3.connect(str(script_db)).execute(
            "SELECT COUNT(*) FROM overlay_decisions"
        ).fetchone()[0]

        # Dry-run must not change anything
        assert after_count == before_count, (
            f"Dry-run modified DB: before={before_count}, after={after_count}"
        )
        # Cluster 1: 3 rows → 2 deletions
        # Cluster 2: 2 rows → 1 deletion
        # Cluster 3: 3 rows → 2 deletions
        assert summary["rows_to_delete"] == 5, (
            f"Expected 5 rows_to_delete, got {summary['rows_to_delete']}"
        )
        assert summary["duplicate_clusters"] == 3, (
            f"Expected 3 clusters, got {summary['duplicate_clusters']}"
        )
        assert summary["rows_deleted"] == 0, "Dry-run must not delete any rows"
        assert summary["audit_log_path"] is None, "Dry-run must not write audit log"

    def test_apply_deletes_duplicates_and_writes_audit_log(
        self, script_db: Path, tmp_path: Path
    ) -> None:
        """Test 6: --apply deletes duplicates and writes an audit log."""
        import os
        from scripts.dedupe_overlay_decisions import run

        # Override LOGS_DIR to tmp so audit log goes to tmp_path
        import scripts.dedupe_overlay_decisions as _script
        original_logs_dir = _script.LOGS_DIR
        _script.LOGS_DIR = tmp_path / "logs"
        try:
            summary = run(db_path=script_db, window_seconds=300, apply=True)
        finally:
            _script.LOGS_DIR = original_logs_dir

        # DB should now have 5 rows (10 - 5 deleted)
        after_count = sqlite3.connect(str(script_db)).execute(
            "SELECT COUNT(*) FROM overlay_decisions"
        ).fetchone()[0]
        assert after_count == 5, f"Expected 5 rows after dedup, got {after_count}"
        assert summary["rows_deleted"] == 5

        # Audit log must exist and contain deletion records
        assert summary["audit_log_path"] is not None
        log_path = Path(summary["audit_log_path"])
        assert log_path.exists(), f"Audit log not found: {log_path}"
        log_content = log_path.read_text()
        assert "DELETE id=" in log_content, "Audit log missing DELETE entries"
        # 5 deletions → 5 DELETE lines
        delete_lines = [l for l in log_content.splitlines() if l.startswith("DELETE id=")]
        assert len(delete_lines) == 5, (
            f"Expected 5 DELETE lines in audit log, got {len(delete_lines)}"
        )

    def test_apply_is_idempotent(self, script_db: Path, tmp_path: Path) -> None:
        """Running --apply twice produces same result (second run finds nothing to delete)."""
        import scripts.dedupe_overlay_decisions as _script
        original_logs_dir = _script.LOGS_DIR
        _script.LOGS_DIR = tmp_path / "logs"
        try:
            summary1 = _script.run(db_path=script_db, window_seconds=300, apply=True)
            summary2 = _script.run(db_path=script_db, window_seconds=300, apply=True)
        finally:
            _script.LOGS_DIR = original_logs_dir

        assert summary1["rows_deleted"] == 5
        assert summary2["rows_deleted"] == 0, "Second run should find nothing to delete"
        assert summary2["duplicate_clusters"] == 0

    def test_keeps_lowest_id_in_each_cluster(self, script_db: Path, tmp_path: Path) -> None:
        """The row with the lowest id in each cluster is retained."""
        import scripts.dedupe_overlay_decisions as _script
        original_logs_dir = _script.LOGS_DIR
        _script.LOGS_DIR = tmp_path / "logs"
        try:
            _script.run(db_path=script_db, window_seconds=300, apply=True)
        finally:
            _script.LOGS_DIR = original_logs_dir

        conn = sqlite3.connect(str(script_db))
        ids = [r[0] for r in conn.execute("SELECT id FROM overlay_decisions ORDER BY id").fetchall()]
        conn.close()
        # Cluster 1 keep=1, cluster 2 keep=4, cluster 3 keep=8, standalones=6,7
        assert 1 in ids, "Lowest id of cluster 1 (id=1) must be retained"
        assert 4 in ids, "Lowest id of cluster 2 (id=4) must be retained"
        assert 8 in ids, "Lowest id of cluster 3 (id=8) must be retained"
        assert 2 not in ids, "Duplicate id=2 must be deleted"
        assert 3 not in ids, "Duplicate id=3 must be deleted"
        assert 5 not in ids, "Duplicate id=5 must be deleted"
        assert 9 not in ids, "Duplicate id=9 must be deleted"
        assert 10 not in ids, "Duplicate id=10 must be deleted"
        # Standalones survive
        assert 6 in ids
        assert 7 in ids
