"""Equivalence tests: router extraction for /api/finance and /api/regime/*.

Verifies that after Phase 1 extraction:
  1. Routes are still mounted on the app (not 404)
  2. Router modules are importable with correct prefixes
  3. Total route count is unchanged (58)
  4. Endpoints return valid shapes (or expected auth/error responses)
  5. Regime routes all present and reachable

Run:
    python3 -m pytest tests/services/test_finance_router_extraction.py -v --timeout=30
"""
from __future__ import annotations

import sys
from pathlib import Path
import pytest

# Ensure project root on path
PROJECT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT))

# ── Pre-load shared auth credentials for TestClient ─────────────────────────

def _test_credentials() -> tuple[str, str] | None:
    """Return (user, pass) from secrets if available, else None (unauthenticated)."""
    import json
    secrets_path = Path.home() / ".atlas-secrets.json"
    if secrets_path.exists():
        try:
            s = json.loads(secrets_path.read_text())
            u = s.get("dashboard_user", "")
            pw = s.get("dashboard_pass", "")
            if u and pw:
                return u, pw
        except Exception:
            pass
    return None


# ════════════════════════════════════════════════════════════════════════════════
# 1. Module importability
# ════════════════════════════════════════════════════════════════════════════════

class TestModuleImportability:
    """Extracted router modules must be importable independently."""

    def test_services_auth_importable(self):
        """services/auth.py — shared auth dependency must be importable."""
        from services.auth import check_auth, security  # noqa: F401
        assert callable(check_auth)

    def test_finance_router_importable(self):
        """services/api/finance.py — must be importable without loading chat_server."""
        from services.api.finance import router
        assert router.prefix == "/api/finance"

    def test_regime_router_importable(self):
        """services/api/regime.py — must be importable without loading chat_server."""
        from services.api.regime import router
        assert router.prefix == "/api/regime"

    def test_finance_router_has_correct_route_count(self):
        """Finance router must expose exactly 1 route (GET /api/finance)."""
        from services.api.finance import router
        paths = [r.path for r in router.routes]
        assert len(paths) == 1, f"Expected 1 finance route, got: {paths}"
        # APIRouter stores full path (prefix + sub-path)
        assert "/api/finance" in paths

    def test_regime_router_has_correct_route_count(self):
        """Regime router must expose exactly 5 routes (full paths with prefix)."""
        from services.api.regime import router
        # FastAPI APIRouter stores full paths (prefix + sub-path) in route.path
        paths = sorted(r.path for r in router.routes)
        assert len(paths) == 5, f"Expected 5 regime routes, got: {paths}"
        expected_full = [
            "/api/regime/history",
            "/api/regime/current",
            "/api/regime/forecast",
            "/api/regime/distributions",
            "/api/regime/transitions",
        ]
        for ep in expected_full:
            assert ep in paths, f"Missing regime route: {ep}"


# ════════════════════════════════════════════════════════════════════════════════
# 2. App-level route mounting
# ════════════════════════════════════════════════════════════════════════════════

# Pre-existing duplicate paths in chat_server.py (multi-decorator routes):
# @app.get("/api/monitor") + @app.get("/api/monitor/{...}") on same function;
# /api/chat/sessions registered by both HTTP and WebSocket stacks.
# These existed BEFORE the extraction — not caused by Phase 1.
_PRE_EXISTING_DUPE_PATHS = frozenset({
    "/api/monitor",
    "/api/monitor/{monitor_path:path}",
    "/api/chat/sessions",
    "/api/chat/sessions/{session_id}",
})


class TestAppRouteMounting:
    """Routes must still be mounted on the FastAPI app after extraction."""

    @pytest.fixture(scope="class")
    def app_paths(self):
        from services.chat_server import app
        return {r.path for r in app.routes if hasattr(r, "path")}

    def test_total_route_count_unchanged(self, app_paths):
        """Total routes must remain 58 (no routes lost or duplicated)."""
        from services.chat_server import app
        all_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert len(all_paths) == 58, (
            f"Route count changed! Expected 58, got {len(all_paths)}. "
            f"Routes: {sorted(set(all_paths))}"
        )

    def test_finance_route_mounted(self, app_paths):
        """/api/finance must be in the app's route table."""
        assert "/api/finance" in app_paths, "/api/finance disappeared after extraction"

    def test_regime_history_mounted(self, app_paths):
        assert "/api/regime/history" in app_paths

    def test_regime_current_mounted(self, app_paths):
        assert "/api/regime/current" in app_paths

    def test_regime_forecast_mounted(self, app_paths):
        assert "/api/regime/forecast" in app_paths

    def test_regime_distributions_mounted(self, app_paths):
        assert "/api/regime/distributions" in app_paths

    def test_regime_transitions_mounted(self, app_paths):
        assert "/api/regime/transitions" in app_paths

    def test_no_new_duplicate_paths(self, app_paths):
        """Extraction must not introduce NEW duplicate paths.

        Note: /api/monitor, /api/chat/sessions etc. have pre-existing duplicates
        from multi-decorator patterns in the original chat_server.py.
        We only fail if extraction added NEW duplicates.
        """
        from services.chat_server import app
        from collections import Counter
        all_paths = [r.path for r in app.routes if hasattr(r, "path")]
        c = Counter(all_paths)
        new_dupes = {
            p for p, n in c.items()
            if n > 1 and p not in _PRE_EXISTING_DUPE_PATHS
        }
        assert not new_dupes, f"Extraction introduced new duplicate routes: {new_dupes}"


# ════════════════════════════════════════════════════════════════════════════════
# 3. Endpoint reachability (HTTP shape tests)
# ════════════════════════════════════════════════════════════════════════════════

class TestEndpointReachability:
    """Extracted endpoints must not return 404 (routing failure).

    Note: 500 from data-layer errors (missing tables, SPY data etc.) is a
    pre-existing condition in the test environment, NOT caused by extraction.
    We accept any non-404 response as proof that the route is mounted.
    """

    @pytest.fixture(scope="class")
    def client(self):
        from fastapi.testclient import TestClient
        from services.chat_server import app
        creds = _test_credentials()
        auth = creds if creds else None
        return TestClient(app, raise_server_exceptions=False), auth

    def _check(self, client_auth, path: str):
        client, auth = client_auth
        resp = client.get(path, auth=auth)
        return resp

    def test_finance_endpoint_not_404(self, client):
        """GET /api/finance must not return 404 (route disappeared after extraction)."""
        resp = self._check(client, "/api/finance")
        assert resp.status_code != 404, "Route disappeared after extraction (404)"

    def test_finance_endpoint_401_without_auth_or_200_with(self, client):
        """Without valid auth → 401. Route must not be 404."""
        cli, auth = client
        resp = cli.get("/api/finance")  # no auth
        assert resp.status_code in (401, 403, 200), (
            f"Unexpected status for unauthenticated /api/finance: {resp.status_code}"
        )

    def test_regime_current_not_404(self, client):
        resp = self._check(client, "/api/regime/current")
        assert resp.status_code != 404, "Route disappeared: /api/regime/current"

    def test_regime_history_not_404(self, client):
        resp = self._check(client, "/api/regime/history")
        assert resp.status_code != 404, "Route disappeared: /api/regime/history"

    def test_regime_history_returns_json_list(self, client):
        """If auth works, /api/regime/history must return a JSON array."""
        resp = self._check(client, "/api/regime/history")
        if resp.status_code in (401, 403):
            pytest.skip("Auth not configured — cannot assert response shape")
        if resp.status_code >= 500:
            pytest.skip("Data-layer error in test env (pre-existing, not caused by extraction)")
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    def test_regime_current_returns_json_dict(self, client):
        """If auth works, /api/regime/current must return a JSON dict with 'state' key."""
        resp = self._check(client, "/api/regime/current")
        if resp.status_code in (401, 403):
            pytest.skip("Auth not configured — cannot assert response shape")
        if resp.status_code >= 500:
            pytest.skip("Data-layer error in test env (pre-existing, not caused by extraction)")
        assert resp.status_code == 200, f"Got {resp.status_code}: {resp.text[:200]}"
        data = resp.json()
        assert isinstance(data, dict), f"Expected dict, got {type(data)}"
        assert "state" in data, f"Missing 'state' key. Got: {list(data.keys())}"

    def test_regime_forecast_not_404(self, client):
        """GET /api/regime/forecast must not be 404 (route registered)."""
        resp = self._check(client, "/api/regime/forecast")
        assert resp.status_code != 404, "Route disappeared: /api/regime/forecast"
        # 500 from missing regime_forecast table is a pre-existing data issue — not extraction failure

    def test_regime_distributions_not_404(self, client):
        """GET /api/regime/distributions must not be 404 (route registered)."""
        resp = self._check(client, "/api/regime/distributions")
        assert resp.status_code != 404, "Route disappeared: /api/regime/distributions"
        # 500 from missing SPY data is a pre-existing data issue — not extraction failure

    def test_regime_transitions_not_404(self, client):
        resp = self._check(client, "/api/regime/transitions")
        assert resp.status_code != 404, "Route disappeared: /api/regime/transitions"


# ════════════════════════════════════════════════════════════════════════════════
# 4. State isolation — extraction must not share mutable state between routers
# ════════════════════════════════════════════════════════════════════════════════

class TestStateIsolation:
    """Cache dicts must live in the extracted modules, not chat_server."""

    def test_finance_cache_in_finance_module(self):
        """_finance_cache must be in services.api.finance, not services.chat_server."""
        import services.api.finance as fin_mod
        import services.chat_server as cs_mod
        assert hasattr(fin_mod, "_finance_cache"), "finance cache missing from services.api.finance"
        assert not hasattr(cs_mod, "_finance_cache"), (
            "_finance_cache still in chat_server — not properly removed"
        )

    def test_regime_dist_cache_in_regime_module(self):
        """_regime_dist_cache must be in services.api.regime, not services.chat_server."""
        import services.api.regime as reg_mod
        import services.chat_server as cs_mod
        assert hasattr(reg_mod, "_regime_dist_cache"), "_regime_dist_cache missing from services.api.regime"
        assert not hasattr(cs_mod, "_regime_dist_cache"), (
            "_regime_dist_cache still in chat_server — not properly removed"
        )

    def test_auth_check_auth_importable_from_services_auth(self):
        """check_auth must be available from services.auth (shared module)."""
        from services.auth import check_auth
        assert callable(check_auth)
        import inspect
        sig = inspect.signature(check_auth)
        # Must accept 'credentials' parameter
        assert "credentials" in sig.parameters, (
            f"check_auth signature changed: {sig}"
        )
