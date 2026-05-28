"""Tests for the Phase 4 knowledge API.

Uses FastAPI TestClient against the full chat_server app with auth mocked.
DB isolation is handled by the autouse _isolate_prod_db fixture in conftest.py.

Run:
    python3 -m pytest tests/test_knowledge_api.py -v
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
    """FastAPI TestClient backed by the full chat_server app with mocked auth."""
    monkeypatch.setattr("services.auth._get_credentials", lambda: _AUTH)
    from fastapi.testclient import TestClient
    from services.chat_server import app
    return TestClient(app, raise_server_exceptions=False)


def _seed_contradiction(strategy: str = "donchian_breakout",
                        universe: str = "sp500",
                        claimed_sharpe: float = 1.6,
                        measured_sharpe: float = 0.4):
    # |1.6 - 0.4| = 1.2 -> well into the critical (>=1.0) bucket without
    # tripping the 1.0 floating-point boundary case (1.4 - 0.4 = 0.999...).
    """Seed source + claim + research_best so a contradiction surfaces."""
    from db import knowledge as kn
    from db.research import upsert_research_best

    src_id = f"src-test-{strategy}"
    claim_id = f"clm-{strategy}-0"
    kn.insert_source(id=src_id, kind="paper",
                     title=f"Paper: {strategy}",
                     url=f"https://arxiv.org/abs/2401.{abs(hash(strategy))%99999:05d}")
    kn.insert_claim(id=claim_id, source_id=src_id, strategy=strategy,
                    universe=universe)
    kn.update_claim_metrics(id=claim_id, claimed_sharpe=claimed_sharpe,
                            extraction_confidence="high")
    upsert_research_best(strategy=strategy, universe=universe, params={},
                        solo_sharpe=measured_sharpe)
    return claim_id, src_id


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/knowledge/contradictions/open
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetOpenContradictions:
    def test_empty_returns_empty_list(self, client):
        r = client.get("/api/knowledge/contradictions/open", auth=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["count"] == 0
        assert body["rows"] == []

    def test_seeded_returns_contradiction(self, client):
        _seed_contradiction()
        r = client.get("/api/knowledge/contradictions/open", auth=_AUTH)
        assert r.status_code == 200
        body = r.json()
        # 2 metric rows for sharpe + max_dd? Only sharpe -- we didn't seed dd.
        sharpes = [c for c in body["rows"] if c["metric"] == "sharpe"]
        assert len(sharpes) == 1
        assert sharpes[0]["strategy"] == "donchian_breakout"
        assert sharpes[0]["severity"] == "critical"

    def test_severity_filter(self, client):
        _seed_contradiction(strategy="strat_minor", claimed_sharpe=1.0,
                            measured_sharpe=0.7)   # |0.3| -> minor
        _seed_contradiction(strategy="strat_crit", claimed_sharpe=1.8,
                            measured_sharpe=0.5)   # |1.3| -> critical

        crit = client.get("/api/knowledge/contradictions/open?severity=critical",
                          auth=_AUTH).json()
        assert all(r["severity"] == "critical" for r in crit["rows"])
        assert any(r["strategy"] == "strat_crit" for r in crit["rows"])
        assert not any(r["strategy"] == "strat_minor" for r in crit["rows"])

    def test_strategy_filter(self, client):
        _seed_contradiction(strategy="strat_a")
        _seed_contradiction(strategy="strat_b")
        r = client.get("/api/knowledge/contradictions/open?strategy=strat_a",
                       auth=_AUTH).json()
        assert all(row["strategy"] == "strat_a" for row in r["rows"])

    def test_auth_required(self, client):
        r = client.get("/api/knowledge/contradictions/open")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/knowledge/contradictions/{id}/resolve
# ═══════════════════════════════════════════════════════════════════════════════

class TestResolveContradiction:
    def test_happy_path_hides_from_open(self, client):
        _seed_contradiction()
        opens = client.get("/api/knowledge/contradictions/open", auth=_AUTH).json()
        cid = opens["rows"][0]["contradiction_id"]

        r = client.post(
            f"/api/knowledge/contradictions/{cid}/resolve",
            json={"resolution": "retested", "note": "Backtested, matches."},
            auth=_AUTH,
        )
        assert r.status_code == 200
        assert r.json()["ok"] is True

        after = client.get("/api/knowledge/contradictions/open", auth=_AUTH).json()
        assert all(row["contradiction_id"] != cid for row in after["rows"])

    def test_invalid_resolution_returns_400(self, client):
        _seed_contradiction()
        opens = client.get("/api/knowledge/contradictions/open", auth=_AUTH).json()
        cid = opens["rows"][0]["contradiction_id"]
        r = client.post(
            f"/api/knowledge/contradictions/{cid}/resolve",
            json={"resolution": "bogus"},
            auth=_AUTH,
        )
        assert r.status_code == 400

    def test_auth_required(self, client):
        r = client.post("/api/knowledge/contradictions/1/resolve",
                        json={"resolution": "retested"})
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/knowledge/strategy/{strategy}/summary
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategySummary:
    def test_returns_summary_and_contradictions(self, client):
        _seed_contradiction(strategy="strat_x", claimed_sharpe=1.4,
                            measured_sharpe=0.4)
        r = client.get("/api/knowledge/strategy/strat_x/summary", auth=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["strategy"] == "strat_x"
        assert len(body["summary"]) == 1
        assert body["summary"][0]["solo_sharpe"] == 0.4
        assert len(body["open_contradictions"]) >= 1

    def test_unknown_strategy_returns_404(self, client):
        r = client.get("/api/knowledge/strategy/nonexistent/summary", auth=_AUTH)
        assert r.status_code == 404

    def test_universe_filter(self, client):
        _seed_contradiction(strategy="strat_u", universe="sp500")
        # Different universe with no measured row -> 404
        r = client.get("/api/knowledge/strategy/strat_u/summary?universe=sector_etfs",
                       auth=_AUTH)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/knowledge/sources/{id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetSource:
    def test_returns_source_with_claims(self, client):
        _, src_id = _seed_contradiction(strategy="strat_src")
        r = client.get(f"/api/knowledge/sources/{src_id}", auth=_AUTH)
        assert r.status_code == 200
        body = r.json()
        assert body["source"]["id"] == src_id
        assert body["claim_count"] >= 1
        assert all(c["status"] == "active" for c in body["claims"])

    def test_unknown_source_returns_404(self, client):
        r = client.get("/api/knowledge/sources/src-does-not-exist", auth=_AUTH)
        assert r.status_code == 404

    def test_include_dismissed_flag(self, client):
        from db import knowledge as kn
        claim_id, src_id = _seed_contradiction(strategy="strat_dismiss")
        kn.dismiss_claim(claim_id, reason="test")

        # Default: hides dismissed.
        body = client.get(f"/api/knowledge/sources/{src_id}", auth=_AUTH).json()
        assert body["claim_count"] == 0

        # With flag: shows them.
        body2 = client.get(
            f"/api/knowledge/sources/{src_id}?include_dismissed_claims=true",
            auth=_AUTH,
        ).json()
        assert body2["claim_count"] == 1
        assert body2["claims"][0]["status"] == "dismissed"
