"""Tests for /api/promotions/* endpoints — Item C5."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.security import HTTPBasicCredentials
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(app):
    """Return a TestClient with auth dependency bypassed."""
    from services.chat_server import check_auth
    app.dependency_overrides[check_auth] = lambda: HTTPBasicCredentials(
        username="testuser", password="testpass"
    )
    return TestClient(app, raise_server_exceptions=True)


def _make_pending_entry(
    pending_id: str = "abc123def456",
    strategy: str = "mean_reversion",
    market: str = "sp500",
    status: str = "pending",
    delta_sharpe: float = 0.12,
    final_sharpe: float = 1.45,
) -> dict:
    return {
        "pending_id": pending_id,
        "strategy": strategy,
        "market": market,
        "delta_sharpe": delta_sharpe,
        "final_sharpe": final_sharpe,
        "timestamp": "2026-04-28T00:00:00+00:00",
        "metadata": {"candidate_metrics": {}, "baseline_metrics": {}},
        "candidate_config": {},
        "status": status,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPromotionsPendingList:
    """GET /api/promotions/pending."""

    def test_pending_returns_only_pending_entries(self, tmp_path, monkeypatch):
        """Only status='pending' entries are included in the response."""
        pending_file = tmp_path / "pending_promotions.json"
        entries = [
            _make_pending_entry("id001", status="pending"),
            _make_pending_entry("id002", status="approved"),
            _make_pending_entry("id003", status="rejected"),
        ]
        pending_file.write_text(json.dumps(entries))

        # Patch PENDING_PROMOTIONS_PATH and expire function
        import research.promoter as promoter
        monkeypatch.setattr(promoter, "PENDING_PROMOTIONS_PATH", pending_file)

        from services.chat_server import app
        client = _make_client(app)
        try:
            resp = client.get("/api/promotions/pending")
            assert resp.status_code == 200, resp.text
            data = resp.json()
            assert data["count"] == 1
            assert len(data["pending"]) == 1
            assert data["pending"][0]["pending_id"] == "id001"
            assert data["pending"][0]["status"] == "pending"
        finally:
            app.dependency_overrides.clear()

    def test_pending_empty_when_all_resolved(self, tmp_path, monkeypatch):
        """No pending entries → count=0 and empty list."""
        pending_file = tmp_path / "pending_promotions.json"
        entries = [
            _make_pending_entry("id001", status="approved"),
            _make_pending_entry("id002", status="expired"),
        ]
        pending_file.write_text(json.dumps(entries))

        import research.promoter as promoter
        monkeypatch.setattr(promoter, "PENDING_PROMOTIONS_PATH", pending_file)

        from services.chat_server import app
        client = _make_client(app)
        try:
            resp = client.get("/api/promotions/pending")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
            assert data["pending"] == []
        finally:
            app.dependency_overrides.clear()

    def test_pending_missing_file_returns_empty(self, tmp_path, monkeypatch):
        """No file at all → count=0, no crash."""
        pending_file = tmp_path / "nonexistent_promotions.json"

        import research.promoter as promoter
        monkeypatch.setattr(promoter, "PENDING_PROMOTIONS_PATH", pending_file)

        from services.chat_server import app
        client = _make_client(app)
        try:
            resp = client.get("/api/promotions/pending")
            assert resp.status_code == 200
            data = resp.json()
            assert data["count"] == 0
        finally:
            app.dependency_overrides.clear()


class TestPromotionsApprove:
    """POST /api/promotions/{pending_id}/approve."""

    def test_approve_happy_path(self, monkeypatch):
        """Mock complete_pending_promotion returns promoted=True → 200 approved=true."""
        mock_result = {
            "promoted": True,
            "version": "v9.99",
            "strategy": "mean_reversion",
            "market": "sp500",
        }
        with patch("research.promoter.complete_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc123/approve")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["approved"] is True
                assert data["version"] == "v9.99"
                assert data["strategy"] == "mean_reversion"
                assert data["market"] == "sp500"
                assert "approver" in data
            finally:
                app.dependency_overrides.clear()

    def test_approve_when_already_approved_returns_409(self, monkeypatch):
        """complete_pending_promotion returns 'Already approved' → 409 Conflict."""
        mock_result = {"promoted": False, "reason": "Already approved"}
        with patch("research.promoter.complete_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc123/approve")
                assert resp.status_code == 409, resp.text
                assert "already" in resp.json()["detail"].lower()
            finally:
                app.dependency_overrides.clear()

    def test_approve_respects_gate_failure(self, monkeypatch):
        """Gate failure (not found/already) → 400 with the reason in detail."""
        mock_result = {
            "promoted": False,
            "reason": "Promotion write failed: validation X",
        }
        with patch("research.promoter.complete_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc123/approve")
                assert resp.status_code == 400, resp.text
                assert "validation X" in resp.json()["detail"]
            finally:
                app.dependency_overrides.clear()

    def test_approve_not_found_returns_404(self, monkeypatch):
        """complete_pending_promotion returns 'not found' → 404."""
        mock_result = {"promoted": False, "reason": "Pending promotion abc999 not found"}
        with patch("research.promoter.complete_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc999/approve")
                assert resp.status_code == 404, resp.text
            finally:
                app.dependency_overrides.clear()

    def test_audit_log_emitted_on_approve(self, monkeypatch, caplog):
        """POST approve emits an [audit] log line with the username."""
        mock_result = {
            "promoted": True,
            "version": "v1.0.0",
            "strategy": "x",
            "market": "sp500",
        }
        with patch("research.promoter.complete_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                with caplog.at_level(logging.INFO, logger="chat_server"):
                    resp = client.post("/api/promotions/myid123/approve")
                assert resp.status_code == 200
                audit_lines = [r.message for r in caplog.records
                               if "[audit] promotion approve" in r.message]
                assert audit_lines, (
                    f"No [audit] log found. Records: {[r.message for r in caplog.records]}"
                )
                assert "testuser" in audit_lines[0], (
                    f"Username not in audit log: {audit_lines[0]}"
                )
            finally:
                app.dependency_overrides.clear()

    def test_unauthenticated_rejected(self):
        """POST /api/promotions/abc/approve without auth → 401."""
        from services.chat_server import app
        client = TestClient(app, raise_server_exceptions=False)
        # No dependency override — real auth check fires
        resp = client.post("/api/promotions/abc/approve")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"


class TestPromotionsReject:
    """POST /api/promotions/{pending_id}/reject."""

    def test_reject_happy_path(self, monkeypatch):
        """Mock reject_pending_promotion returns rejected=True → 200 rejected=true."""
        mock_result = {"rejected": True, "strategy": "mean_reversion"}
        with patch("research.promoter.reject_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc123/reject?reason=test")
                assert resp.status_code == 200, resp.text
                data = resp.json()
                assert data["rejected"] is True
                assert data["strategy"] == "mean_reversion"
                assert "approver" in data
            finally:
                app.dependency_overrides.clear()

    def test_reject_not_found_returns_404(self, monkeypatch):
        """reject_pending_promotion returns 'Not found' → 404."""
        mock_result = {"rejected": False, "reason": "Not found"}
        with patch("research.promoter.reject_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/nonexistent/reject")
                assert resp.status_code == 404, resp.text
            finally:
                app.dependency_overrides.clear()

    def test_reject_reason_from_query_param(self, monkeypatch):
        """?reason=myReason is passed to reject_pending_promotion."""
        captured: dict = {}

        def mock_reject(pending_id: str, reason: str = "User rejected") -> dict:
            captured["reason"] = reason
            return {"rejected": True, "strategy": "x"}

        with patch("research.promoter.reject_pending_promotion", side_effect=mock_reject):
            from services.chat_server import app
            client = _make_client(app)
            try:
                resp = client.post("/api/promotions/abc123/reject?reason=too+risky")
                assert resp.status_code == 200
                assert captured.get("reason") == "too risky"
            finally:
                app.dependency_overrides.clear()

    def test_reject_unauthenticated_returns_401(self):
        """POST /api/promotions/abc/reject without auth → 401."""
        from services.chat_server import app
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/api/promotions/abc/reject")
        assert resp.status_code == 401, f"Expected 401, got {resp.status_code}"

    def test_audit_log_emitted_on_reject(self, monkeypatch, caplog):
        """POST reject emits an [audit] log line."""
        mock_result = {"rejected": True, "strategy": "x"}
        with patch("research.promoter.reject_pending_promotion", return_value=mock_result):
            from services.chat_server import app
            client = _make_client(app)
            try:
                with caplog.at_level(logging.INFO, logger="chat_server"):
                    client.post("/api/promotions/myid456/reject?reason=not+good")
                audit_lines = [r.message for r in caplog.records
                               if "[audit] promotion reject" in r.message]
                assert audit_lines, (
                    f"No [audit] log found. Records: {[r.message for r in caplog.records]}"
                )
            finally:
                app.dependency_overrides.clear()
