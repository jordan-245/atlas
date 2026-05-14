"""Tests for services/api/research_matrix.py — research matrix coverage endpoint.

Covers:
  1. test_coverage_returns_strategies_and_universes
  2. test_matrix_cells_have_lifecycle_state
  3. test_stale_cells_marked_with_days_stale
  4. test_in_active_config_field_correct
  5. test_empty_db_returns_empty_matrix
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

ATLAS_ROOT = Path(__file__).resolve().parents[1]
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ── App factory ───────────────────────────────────────────────────────────────

def _build_app() -> FastAPI:
    from services.api.research_matrix import router, check_auth
    app = FastAPI()
    app.dependency_overrides[check_auth] = lambda: HTTPBasicCredentials(
        username="test", password="test"
    )
    app.include_router(router)
    return app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _iso(days_ago: float) -> str:
    ts = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return ts.isoformat()


def _seed_db(db_path: Path) -> None:
    """Seed research_best and strategy_lifecycle tables."""
    import db.atlas_db as _adb
    _adb.init_db()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # research_best: 2 strategies × 2 universes (one fresh, one stale)
    rb_rows = [
        # strategy_a/sp500 — fresh (2 days), good Sharpe
        ("strategy_a", "sp500", 1.5, 50, 3.0, _iso(2)),
        # strategy_a/asx — stale (15 days), low Sharpe
        ("strategy_a", "asx",  0.2, 20, 8.0, _iso(15)),
        # strategy_b/sp500 — fresh (1 day), good Sharpe
        ("strategy_b", "sp500", 0.8, 30, 4.0, _iso(1)),
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO research_best "
        "(strategy, universe, sharpe, trades, max_dd_pct, updated_at, "
        " metric_type, params) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [(*r, "solo", "{}") for r in rb_rows],
    )

    # strategy_lifecycle: enrich strategy_a/sp500 with LIVE state
    conn.execute(
        "INSERT OR REPLACE INTO strategy_lifecycle "
        "(strategy, universe, state, entered_state_at) "
        "VALUES (?,?,?,?)",
        ("strategy_a", "sp500", "LIVE", _iso(30)),
    )
    conn.execute(
        "INSERT OR REPLACE INTO strategy_lifecycle "
        "(strategy, universe, state, entered_state_at) "
        "VALUES (?,?,?,?)",
        ("strategy_b", "sp500", "PAPER", _iso(5)),
    )

    conn.commit()
    conn.close()


@pytest.fixture()
def seeded_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    import db.atlas_db as _adb
    db_path = tmp_path / "rm_test.db"
    monkeypatch.setattr(_adb, "_db_path_override", str(db_path))
    _seed_db(db_path)
    return db_path


@pytest.fixture()
def client(seeded_db: Path) -> TestClient:  # noqa: ARG001
    # Patch _load_active_strategies to return a controlled set
    with patch(
        "services.api.research_matrix._load_active_strategies",
        return_value={("strategy_a", "sp500"), ("strategy_b", "sp500")},
    ):
        app = _build_app()
    return TestClient(app, raise_server_exceptions=True)


# ── Test 1: coverage returns strategies and universes ────────────────────────

def test_coverage_returns_strategies_and_universes(client: TestClient) -> None:
    resp = client.get("/api/research-matrix/coverage")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "strategies" in body
    assert "universes" in body
    assert "matrix" in body
    assert "generated_at" in body

    strategies = body["strategies"]
    universes = body["universes"]
    assert "strategy_a" in strategies
    assert "strategy_b" in strategies
    assert "sp500" in universes
    assert "asx" in universes


# ── Test 2: matrix cells have lifecycle_state ────────────────────────────────

def test_matrix_cells_have_lifecycle_state(client: TestClient) -> None:
    resp = client.get("/api/research-matrix/coverage")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    universes = body["universes"]
    sp500_idx = universes.index("sp500")

    # Find strategy_a row
    strategy_a_row = next(
        (r for r in body["matrix"] if r["strategy"] == "strategy_a"), None
    )
    assert strategy_a_row is not None, "strategy_a not in matrix"

    cell = strategy_a_row["cells"][sp500_idx]
    assert cell is not None, "strategy_a/sp500 cell is None"
    assert cell["lifecycle_state"] == "LIVE", f"Expected LIVE, got {cell['lifecycle_state']}"

    # Find strategy_b row
    strategy_b_row = next(
        (r for r in body["matrix"] if r["strategy"] == "strategy_b"), None
    )
    assert strategy_b_row is not None, "strategy_b not in matrix"
    cell_b = strategy_b_row["cells"][sp500_idx]
    assert cell_b is not None
    assert cell_b["lifecycle_state"] == "PAPER", (
        f"Expected PAPER, got {cell_b['lifecycle_state']}"
    )


# ── Test 3: stale cells are marked with days_stale ───────────────────────────

def test_stale_cells_marked_with_days_stale(client: TestClient) -> None:
    resp = client.get("/api/research-matrix/coverage")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    universes = body["universes"]
    asx_idx = universes.index("asx")

    strategy_a_row = next(r for r in body["matrix"] if r["strategy"] == "strategy_a")
    cell_stale = strategy_a_row["cells"][asx_idx]
    assert cell_stale is not None, "strategy_a/asx cell should exist"
    assert cell_stale["days_stale"] is not None, "days_stale should be set"
    assert cell_stale["days_stale"] >= 14, (
        f"Expected >= 14 days stale, got {cell_stale['days_stale']}"
    )
    assert cell_stale["health"] == "red", (
        f"Expected 'red' health for stale+low-sharpe cell, got {cell_stale['health']}"
    )


# ── Test 4: in_active_config field is correct ────────────────────────────────

def test_in_active_config_field_correct(seeded_db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cells in the active config set have in_active_config=True."""
    import db.atlas_db as _adb
    monkeypatch.setattr(_adb, "_db_path_override", str(seeded_db))

    active = {("strategy_a", "sp500"), ("strategy_b", "sp500")}

    with patch(
        "services.api.research_matrix._load_active_strategies",
        return_value=active,
    ):
        app = _build_app()
        c = TestClient(app, raise_server_exceptions=True)
        resp = c.get("/api/research-matrix/coverage")

    assert resp.status_code == 200, resp.text

    body = resp.json()
    universes = body["universes"]
    sp500_idx = universes.index("sp500")
    asx_idx = universes.index("asx")

    strategy_a_row = next(r for r in body["matrix"] if r["strategy"] == "strategy_a")
    # sp500 → active
    assert strategy_a_row["cells"][sp500_idx]["in_active_config"] is True
    # asx → NOT active
    assert strategy_a_row["cells"][asx_idx]["in_active_config"] is False


# ── Test 5: empty DB returns valid but empty matrix ──────────────────────────

def test_empty_db_returns_empty_matrix(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import db.atlas_db as _adb

    db_path = tmp_path / "empty.db"
    monkeypatch.setattr(_adb, "_db_path_override", str(db_path))
    _adb.init_db()

    with patch(
        "services.api.research_matrix._load_active_strategies",
        return_value=set(),
    ):
        app = _build_app()
    c = TestClient(app, raise_server_exceptions=True)

    resp = c.get("/api/research-matrix/coverage")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["strategies"] == []
    assert body["universes"] == []
    assert body["matrix"] == []
