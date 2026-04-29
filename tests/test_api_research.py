"""Tests for services/api/research.py — Phase 6 extraction."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from fastapi.testclient import TestClient
from fastapi import FastAPI
from services.api.research import router

_AUTH = ("test", "test")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("services.auth._get_credentials", lambda: _AUTH)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _mem_ctx():
    """In-memory SQLite with minimal research schema."""
    import sqlite3 as _sq
    mem = _sq.connect(":memory:", check_same_thread=False)
    mem.row_factory = _sq.Row
    mem.execute(
        "CREATE TABLE research_experiments "
        "(strategy TEXT, universe TEXT, sharpe REAL, status TEXT, "
        "created_at TEXT, experiment_type TEXT, regime_state TEXT, "
        "params_changed TEXT, cagr_pct REAL)"
    )
    mem.execute(
        "CREATE TABLE research_best "
        "(strategy TEXT, universe TEXT, sharpe REAL, solo_sharpe REAL, "
        "portfolio_sharpe REAL, metric_type TEXT, trades INTEGER, "
        "max_dd_pct REAL, updated_at TEXT, params TEXT)"
    )
    mem.execute(
        "CREATE TABLE research_discoveries "
        "(id INTEGER PRIMARY KEY, paper_titles TEXT, created_at TEXT)"
    )
    mem.execute(
        "CREATE TABLE research_brain "
        "(entry_type TEXT, title TEXT, content TEXT, source_file TEXT, "
        "updated_at TEXT, strategy TEXT, sharpe_delta REAL)"
    )
    mem.commit()

    class _Ctx:
        def __enter__(self): return mem
        def __exit__(self, *a): pass
        def _mem(self): return mem

    return _Ctx(), mem


class TestResearchSummary:
    def test_endpoint_exists(self, client):
        """GET /api/research/summary returns 200 (not 404)."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/summary", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200

    def test_returns_correct_shape(self, client):
        """Returns total_experiments, kept_count, keep_rate, etc."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/summary", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        body = resp.json()
        assert "total_experiments" in body
        assert "kept_count" in body
        assert "keep_rate" in body
        assert "by_strategy" in body


class TestResearchExperiments:
    def test_endpoint_exists(self, client):
        """GET /api/research/experiments returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/experiments", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200

    def test_pagination_shape(self, client):
        """Returns experiments list and total."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/experiments?limit=10&offset=0", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        body = resp.json()
        assert "experiments" in body
        assert "total" in body


class TestResearchLeaderboard:
    def test_endpoint_exists(self, client):
        """GET /api/research/leaderboard returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/leaderboard", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        assert "leaderboard" in resp.json()


class TestResearchTimeline:
    def test_endpoint_exists(self, client):
        """GET /api/research/timeline returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/timeline", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200


class TestResearchBrain:
    def test_endpoint_exists(self, client):
        """GET /api/research/brain returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/brain", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200

    def test_returns_params_and_patterns(self, client):
        """Response includes params and patterns keys."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/brain", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        body = resp.json()
        assert "params" in body
        assert "patterns" in body


class TestResearchDiscoveries:
    def test_endpoint_exists(self, client):
        """GET /api/research/discoveries returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/discoveries", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        assert "discoveries" in resp.json()


class TestResearchCoverage:
    def test_endpoint_exists(self, client):
        """GET /api/research/coverage returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/coverage", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200

    def test_returns_matrix_shape(self, client):
        """Returns strategies, universes, matrix."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/coverage", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        body = resp.json()
        assert "strategies" in body
        assert "universes" in body
        assert "matrix" in body


class TestResearchStrategies:
    def test_endpoint_exists(self, client):
        """GET /api/research/strategies returns 200."""
        ctx, mem = _mem_ctx()
        with patch("db.atlas_db.get_db", return_value=ctx):
            resp = client.get("/api/research/strategies", auth=_AUTH)
        mem.close()
        assert resp.status_code == 200
        assert "strategies" in resp.json()
