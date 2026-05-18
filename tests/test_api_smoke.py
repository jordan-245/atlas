"""Smoke tests — verify research_matrix routes are mounted in chat_server.app.

Task #344: confirms that `services/api/research_matrix.py` router is mounted
via `chat_server.py` and all routes return < 500 when called through the
full FastAPI application.

These tests use:
- The full `chat_server.app` (not an isolated mini-app)
- Auth bypassed via dependency_overrides (same pattern as test_rca_phase4e_pnl_slicers.py)
- Isolated DB from conftest._isolate_prod_db (autouse=True)

Covered routes (enumerated dynamically from router.routes):
  GET /api/research-matrix/coverage
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Generator

import pytest
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient

ATLAS_ROOT = Path(__file__).resolve().parents[1]
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_research_matrix_routes() -> list[tuple[str, str]]:
    """Return list of (method, path) pairs from research_matrix.router."""
    from services.api.research_matrix import router

    result: list[tuple[str, str]] = []
    for route in router.routes:
        # APIRoute objects have .methods (set) and .path
        methods = getattr(route, "methods", None) or set()
        path = getattr(route, "path", None)
        if path is not None:
            for method in methods:
                result.append((method.upper(), path))
    return result


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def smoke_client() -> Generator[TestClient, None, None]:
    """Full chat_server.app TestClient with auth bypassed.

    Uses the same pattern as test_rca_phase4e_pnl_slicers.py.
    DB isolation is handled by conftest._isolate_prod_db (autouse).
    """
    from services.chat_server import app, check_auth  # noqa: PLC0415

    app.dependency_overrides[check_auth] = lambda: HTTPBasicCredentials(
        username="smoke", password="smoke"
    )
    try:
        yield TestClient(app, raise_server_exceptions=True)
    finally:
        app.dependency_overrides.clear()


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestResearchMatrixRoutesMounted:
    """Assert every route from research_matrix.router is mounted and reachable."""

    def test_router_has_at_least_one_route(self) -> None:
        """research_matrix router exposes at least one route."""
        routes = _get_research_matrix_routes()
        assert len(routes) >= 1, (
            "research_matrix.router has no routes — check services/api/research_matrix.py"
        )

    def test_coverage_endpoint_mounted(self, smoke_client: TestClient) -> None:
        """GET /api/research-matrix/coverage is mounted and returns < 500."""
        resp = smoke_client.get("/api/research-matrix/coverage")
        assert resp.status_code < 500, (
            f"Expected < 500 from /api/research-matrix/coverage, "
            f"got {resp.status_code}. Body: {resp.text[:400]}"
        )

    def test_coverage_returns_json_with_expected_keys(
        self, smoke_client: TestClient
    ) -> None:
        """GET /api/research-matrix/coverage JSON structure matches spec."""
        resp = smoke_client.get("/api/research-matrix/coverage")
        assert resp.status_code == 200, (
            f"Expected 200, got {resp.status_code}. Body: {resp.text[:400]}"
        )
        body = resp.json()
        assert "strategies" in body, f"Missing 'strategies' key; got: {list(body)}"
        assert "universes" in body, f"Missing 'universes' key; got: {list(body)}"
        assert "matrix" in body, f"Missing 'matrix' key; got: {list(body)}"
        assert "generated_at" in body, f"Missing 'generated_at' key; got: {list(body)}"
        assert isinstance(body["strategies"], list)
        assert isinstance(body["universes"], list)
        assert isinstance(body["matrix"], list)

    def test_all_router_routes_return_below_500(
        self, smoke_client: TestClient
    ) -> None:
        """Enumerate every route in research_matrix.router and smoke-test each one.

        Only GET routes are tested (POST/PUT/DELETE need request bodies).
        Non-200 responses are acceptable here — the contract is "not a server error".
        """
        routes = _get_research_matrix_routes()
        failures: list[str] = []

        for method, path in routes:
            if method != "GET":
                continue  # skip non-GET (need body / side effects)
            resp = smoke_client.request(method, path)
            if resp.status_code >= 500:
                failures.append(
                    f"  {method} {path} → {resp.status_code}: {resp.text[:200]}"
                )

        assert not failures, (
            "The following research_matrix routes returned 5xx:\n"
            + "\n".join(failures)
        )

    def test_research_matrix_router_is_included_in_app(self) -> None:
        """research_matrix router is registered in chat_server.app.routes.

        Checks by looking for the /api/research-matrix prefix in the app's
        route paths — proves include_router() was called.
        """
        from services.chat_server import app

        all_paths = [
            getattr(route, "path", "") for route in app.routes
        ]
        research_matrix_paths = [p for p in all_paths if "research-matrix" in p]
        assert research_matrix_paths, (
            "No /api/research-matrix/* route found in chat_server.app.routes. "
            "Has app.include_router(_research_matrix_router) been called in chat_server.py?"
        )
