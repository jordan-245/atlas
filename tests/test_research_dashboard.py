"""Tests for the Track 3a research dashboard endpoints.

  GET /api/research/discovery-funnel
  GET /api/research/queue-health

Uses FastAPI TestClient against the full chat_server app with auth mocked.
DB isolation handled by the autouse _isolate_prod_db fixture in conftest.py.

Run:
    python3 -m pytest tests/test_research_dashboard.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

_AUTH = ("testuser", "testpass")


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr("services.auth._get_credentials", lambda: _AUTH)
    from fastapi.testclient import TestClient
    from services.chat_server import app
    return TestClient(app, raise_server_exceptions=False)


# ═══════════════════════════════════════════════════════════════════════════════
# /api/research/discovery-funnel
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiscoveryFunnel:
    def test_empty(self, client):
        r = client.get("/api/research/discovery-funnel?days=7", auth=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["days"] == 7
        assert body["funnel"] == []

    def _ensure_research_discoveries(self):
        """The research_discoveries table is created by research/migrate_research.py,
        not by db/schema.sql.  Create it inline for this test so log_discovery has
        somewhere to write.
        """
        from db.atlas_db import get_db
        with get_db() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS research_discoveries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_date TEXT NOT NULL,
                    papers_found INTEGER DEFAULT 0,
                    papers_filtered INTEGER DEFAULT 0,
                    specs_extracted INTEGER DEFAULT 0,
                    strategies_generated INTEGER DEFAULT 0,
                    paper_titles TEXT,
                    status TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)

    def test_seeded(self, client):
        self._ensure_research_discoveries()
        from research.db import log_discovery
        from datetime import datetime
        today = datetime.utcnow().date().isoformat()
        log_discovery(
            run_date=today,
            papers_found=12,
            papers_filtered=4,
            specs_extracted=2,
            strategies_generated=1,
        )
        body = client.get("/api/research/discovery-funnel?days=30",
                          auth=_AUTH).json()
        assert len(body["funnel"]) == 1
        row = body["funnel"][0]
        assert row["date"] == today
        assert row["papers_found"] == 12
        assert row["papers_filtered"] == 4
        assert row["specs_extracted"] == 2
        assert row["strategies_generated"] == 1

    def test_auth(self, client):
        r = client.get("/api/research/discovery-funnel")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# /api/research/queue-health
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueHealth:
    def test_empty(self, client):
        body = client.get("/api/research/queue-health", auth=_AUTH).json()
        # When mirror has 0 rows and JSON queue is empty/missing, both are 0.
        assert body["active"] == 0
        assert isinstance(body["by_status"], dict)
        assert isinstance(body["by_category"], dict)

    def test_with_mirror_rows(self, client, monkeypatch):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_QUEUE", "1")
        # Avoid touching the real queue.json -- redirect to a tmp path so the
        # dual-write hook doesn't write anywhere persistent.
        from pathlib import Path as _P
        import tempfile, research.models as rm
        with tempfile.TemporaryDirectory() as tmp:
            monkeypatch.setattr(rm, "QUEUE_PATH", _P(tmp) / "queue.json")
            from research.models import append_to_queue, QueueEntry, ExperimentType
            for n, (status, category, strat) in enumerate([
                ("queued",     "contradiction", "momentum_breakout"),
                ("queued",     "contradiction", "connors_rsi2"),
                ("claimed",    "param_drift",   "mean_reversion"),
                ("running",    "dormant",       "bb_squeeze"),
                ("evaluating", "filter",        "opening_gap"),
                ("passed",     "new_strategy",  "short_term_mr"),
            ]):
                entry = QueueEntry(
                    id=f"q-{n}", title=f"t{n}", category=category, market="sp500",
                    hypothesis="x", method=ExperimentType.SINGLE_STRATEGY_TEST,
                    acceptance_criteria={"min_sharpe": 0.3, "min_trades": 15},
                    estimated_runtime_min=10, priority="P3",
                    status=status, strategy_name=strat,
                )
                append_to_queue(entry, skip_validation=True)

            body = client.get("/api/research/queue-health", auth=_AUTH).json()
            assert body["source"] == "queue_mirror"
            assert body["by_status"]["queued"] == 2
            assert body["by_status"]["claimed"] == 1
            assert body["by_status"]["running"] == 1
            assert body["by_status"]["evaluating"] == 1
            assert body["by_status"]["passed"] == 1
            # Only active categories aggregated; 'new_strategy' is in 'passed' so excluded
            assert body["by_category"]["contradiction"] == 2
            assert body["by_category"]["param_drift"] == 1
            assert "new_strategy" not in body["by_category"]
            assert body["active"] == 5

    def test_auth(self, client):
        r = client.get("/api/research/queue-health")
        assert r.status_code == 401
