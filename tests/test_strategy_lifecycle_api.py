"""Tests for services/api/strategy_lifecycle.py — standalone lifecycle router.

This module creates its own minimal FastAPI app (not the full chat_server)
to test the strategy_lifecycle router in isolation.

Covers:
  1. test_list_lifecycle_returns_all_rows
  2. test_get_history_filtered_by_strategy_universe
  3. test_manual_transition_validates_state_name
  4. test_manual_transition_records_operator
  5. test_invalid_state_returns_400
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

ATLAS_ROOT = Path(__file__).resolve().parents[1]
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

# ── Build a minimal test app around the strategy_lifecycle router ─────────────

def _build_app() -> FastAPI:
    from services.api.strategy_lifecycle import router, check_auth
    app = FastAPI()
    app.dependency_overrides[check_auth] = lambda: HTTPBasicCredentials(
        username="test", password="test"
    )
    app.include_router(router)
    return app


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0) -> str:
    return (datetime.now(timezone.utc).isoformat())


@pytest.fixture()
def db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated SQLite DB with schema + seed rows."""
    import db.atlas_db as _adb

    db_path = tmp_path / "sl_test.db"
    monkeypatch.setattr(_adb, "_db_path_override", str(db_path))
    _adb.init_db()

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    now = datetime.now(timezone.utc).isoformat()
    conn.executemany(
        "INSERT OR REPLACE INTO strategy_lifecycle "
        "(strategy, universe, state, entered_state_at, prev_state, transition_reason) "
        "VALUES (?,?,?,?,?,?)",
        [
            ("momentum_breakout", "sp500", "LIVE",     now, None,       "Migration"),
            ("mean_reversion",    "sp500", "PAPER",    now, "RESEARCH", "Gates passed"),
            ("adx_trend",         "sp500", "RESEARCH", now, None,       "Initial"),
        ],
    )
    # Seed some history rows for mean_reversion
    conn.executemany(
        "INSERT INTO strategy_lifecycle_history "
        "(strategy, universe, from_state, to_state, transitioned_at, reason, operator) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            ("mean_reversion", "sp500", None,       "RESEARCH", now, "Initial seed", "system"),
            ("mean_reversion", "sp500", "RESEARCH",  "PAPER",   now, "Gates passed", "alice"),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture()
def client(db: Path) -> TestClient:  # noqa: ARG001 (db fixture sets monkeypatch side effect)
    return TestClient(_build_app(), raise_server_exceptions=True)


# ── Test 1: list returns all rows ─────────────────────────────────────────────

def test_list_lifecycle_returns_all_rows(client: TestClient) -> None:
    resp = client.get("/api/strategy-lifecycle")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "rows" in body
    rows = body["rows"]
    assert len(rows) == 3, f"Expected 3 rows, got {len(rows)}: {rows}"

    strategies = {r["strategy"] for r in rows}
    assert "momentum_breakout" in strategies
    assert "mean_reversion" in strategies
    assert "adx_trend" in strategies


# ── Test 2: history filtered by strategy + universe ───────────────────────────

def test_get_history_filtered_by_strategy_universe(client: TestClient) -> None:
    resp = client.get("/api/strategy-lifecycle/mean_reversion/sp500/history")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert "rows" in body
    rows = body["rows"]
    # We seeded 2 history rows for mean_reversion/sp500
    assert len(rows) == 2, f"Expected 2, got {len(rows)}: {rows}"
    # History should be ordered DESC (most recent first)
    states = [r["to_state"] for r in rows]
    assert "PAPER" in states
    assert "RESEARCH" in states


def test_get_history_empty_for_unknown_combo(client: TestClient) -> None:
    resp = client.get("/api/strategy-lifecycle/unknown_strat/no_universe/history")
    assert resp.status_code == 200, resp.text
    assert resp.json()["rows"] == []


# ── Test 3: manual transition validates state name ────────────────────────────

def test_manual_transition_validates_state_name(client: TestClient) -> None:
    """Valid RESEARCH → PAPER transition should succeed (strategy already in RESEARCH)."""
    payload = {
        "strategy": "adx_trend",
        "universe": "sp500",
        "new_state": "PAPER",
        "reason": "Passed research gate — promoting to paper",
        "operator": "alice",
    }
    resp = client.post("/api/strategy-lifecycle/transition", json=payload)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["status"] == "ok"
    assert body["new_state"] == "PAPER"


# ── Test 4: manual transition records operator ────────────────────────────────

def test_manual_transition_records_operator(client: TestClient) -> None:
    """Operator name from request body is persisted in history."""
    payload = {
        "strategy": "adx_trend",
        "universe": "sp500",
        "new_state": "PAPER",
        "reason": "Promoting to paper after review by bob",
        "operator": "bob",
    }
    resp = client.post("/api/strategy-lifecycle/transition", json=payload)
    assert resp.status_code == 200, resp.text

    # Now check history — operator 'bob' should appear
    hist_resp = client.get("/api/strategy-lifecycle/adx_trend/sp500/history")
    assert hist_resp.status_code == 200, hist_resp.text
    rows = hist_resp.json()["rows"]
    operators = {r.get("operator") for r in rows}
    assert "bob" in operators, f"Expected 'bob' in operators, got {operators}"


# ── Test 5: invalid state returns 400 ────────────────────────────────────────

def test_invalid_state_returns_400(client: TestClient) -> None:
    payload = {
        "strategy": "momentum_breakout",
        "universe": "sp500",
        "new_state": "INVALID_STATE",
        "reason": "Testing bad state",
        "operator": "tester",
    }
    resp = client.post("/api/strategy-lifecycle/transition", json=payload)
    assert resp.status_code == 400, resp.text
    assert "Invalid state" in resp.json()["detail"]


# ── Test 6: disallowed system transition returns 400 ────────────────────────

def test_disallowed_system_transition_returns_400(client: TestClient) -> None:
    """LIVE → RESEARCH is not allowed for the 'system' operator (state machine check)."""
    payload = {
        "strategy": "momentum_breakout",   # LIVE in seeded db
        "universe": "sp500",
        "new_state": "RESEARCH",
        "reason": "Testing disallowed transition",
        "operator": "system",              # system operator → graph enforced
    }
    resp = client.post("/api/strategy-lifecycle/transition", json=payload)
    # Should be 400 because LIVE → RESEARCH is not in ALLOWED_TRANSITIONS
    assert resp.status_code == 400, resp.text
