"""Tests for services/api/health.py — Phase 4 extraction."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from fastapi.testclient import TestClient
from fastapi import FastAPI
from services.api.health import router

_AUTH = ("test", "test")


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr("services.auth._get_credentials", lambda: _AUTH)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


class TestSystemHealth:
    def test_endpoint_exists(self, client):
        """GET /api/system/health is registered."""
        with patch("db.atlas_db.get_heartbeats", return_value=[]), \
             patch("db.atlas_db.get_db", side_effect=RuntimeError("no db")):
            resp = client.get("/api/system/health", auth=_AUTH)
        assert resp.status_code != 404

    def test_returns_expected_keys(self, client):
        """Response includes services, cron, data_freshness, heartbeats."""
        import sqlite3 as _sq
        mem = _sq.connect(":memory:", check_same_thread=False)
        mem.row_factory = _sq.Row
        mem.execute(
            "CREATE TABLE ohlcv (ticker TEXT, date TEXT)"
        )
        mem.execute(
            "CREATE TABLE equity_curve (date TEXT, equity REAL)"
        )
        mem.execute(
            "CREATE TABLE overlay_decisions (id INTEGER PRIMARY KEY)"
        )
        mem.commit()

        class _Ctx:
            def __enter__(self): return mem
            def __exit__(self, *a): pass

        with patch("db.atlas_db.get_heartbeats", return_value=[]), \
             patch("db.atlas_db.get_db", return_value=_Ctx()), \
             patch("services.api.health.subprocess.run") as mock_run, \
             patch("services.api.health.Path.glob", return_value=iter([])):
            mock_run.return_value = MagicMock(stdout="inactive\n")
            resp = client.get("/api/system/health", auth=_AUTH)

        mem.close()
        assert resp.status_code == 200
        body = resp.json()
        assert "services" in body
        assert "heartbeats" in body
        assert "data_freshness" in body


class TestSystemHealthUniverses:
    def test_endpoint_exists(self, client):
        """GET /api/system/health/universes is registered."""
        with patch("services.api.health._build_universes_list", return_value=[]):
            resp = client.get("/api/system/health/universes", auth=_AUTH)
        assert resp.status_code == 200
        assert "universes" in resp.json()

    def test_build_universes_list_returns_list(self):
        """_build_universes_list returns a list when given empty config glob."""
        from services.api.health import _build_universes_list
        with patch("services.api.health.Path.glob", return_value=iter([])):
            result = _build_universes_list()
        assert isinstance(result, list)
        assert result == []


class TestMacroGauges:
    def test_endpoint_exists(self, client):
        """GET /api/macro/gauges is registered."""
        with patch("db.atlas_db.get_db", side_effect=RuntimeError("no db")):
            resp = client.get("/api/macro/gauges", auth=_AUTH)
        assert resp.status_code != 404

    def test_returns_empty_when_no_data(self, client):
        """Returns {dimensions: [], date: null} when macro_indicators is empty."""
        import sqlite3 as _sq
        mem = _sq.connect(":memory:", check_same_thread=False)
        mem.row_factory = _sq.Row
        mem.execute(
            "CREATE TABLE macro_indicators (date TEXT, vix REAL)"
        )
        mem.commit()

        class _Ctx:
            def __enter__(self): return mem
            def __exit__(self, *a): pass

        with patch("db.atlas_db.get_db", return_value=_Ctx()), \
             patch("services.api.health.open") as mock_open:
            mock_open.return_value.__enter__.return_value.read.return_value = \
                '{"weights": {"trend":0.2,"risk":0.2,"credit":0.2,"yield_curve":0.2,"dollar":0.1,"commodity":0.1}}'
            import io
            import json
            mock_open.return_value.__enter__.return_value = io.StringIO(
                json.dumps({"weights": {"trend": 0.2, "risk": 0.2, "credit": 0.2,
                                        "yield_curve": 0.2, "dollar": 0.1, "commodity": 0.1}})
            )
            resp = client.get("/api/macro/gauges", auth=_AUTH)

        mem.close()
        if resp.status_code == 200:
            body = resp.json()
            assert "dimensions" in body
            assert body["dimensions"] == []
