"""Up Bank webhook receiver — makes the finance tab live.

Public, HMAC-verified endpoint mounted on the existing dashboard FastAPI app
(reachable via the cloudflared tunnel at https://atlas.getflowtide.com/api/up/webhook).
Up Bank pushes TRANSACTION_CREATED / TRANSACTION_SETTLED / TRANSACTION_DELETED events;
each triggers a coalesced incremental ``up_sync`` (refreshes account balances + new
transactions in /root/up-bank/up_bank.db) and invalidates the /api/finance cache, so the
dashboard reflects changes within seconds.

Security: this route is intentionally NOT behind the dashboard's HTTP Basic auth (Up Bank
cannot send Basic credentials). It is authenticated by verifying the HMAC-SHA256 signature
Up sends in the X-Up-Authenticity-Signature header against ~/.atlas-secrets.json
``up_webhook_secret`` (the secretKey returned when the webhook was registered).

Replaces the retired standalone up_webhook_server.py (which collided with port :8000 and
ran outside the tunnel). The /webhook/health endpoint + the periodic safety-net sync exist
specifically so a silently-dead webhook is noticeable this time.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials

from services.auth import check_auth

router = APIRouter(prefix="/api/up", tags=["up-webhook"])
logger = logging.getLogger(__name__)

_SECRETS_PATH = Path(os.environ.get("ATLAS_SECRETS_PATH", str(Path.home() / ".atlas-secrets.json")))
_UP_SYNC = "/root/up-bank/up_sync.py"
_UP_DIR = "/root/up-bank"

# ── Observability state (read by /webhook/health) ──────────────────────────────
_state: dict = {
    "last_event": None,
    "last_event_type": None,
    "last_event_ts": None,
    "last_sync_ts": None,
    "last_sync_ok": None,
    "events_total": 0,
}

# ── Coalescing background sync worker ───────────────────────────────────────────
# Webhook events can arrive in bursts; we never block the request thread and we
# coalesce bursts into a single incremental sync with a short cooldown.
_sync_event = threading.Event()
_MIN_INTERVAL = 12  # seconds between syncs (debounce/coalesce)


def _webhook_secret() -> str:
    """Read the current webhook secret fresh each call (so re-registration takes
    effect without a server restart)."""
    try:
        return json.loads(_SECRETS_PATH.read_text()).get("up_webhook_secret", "") or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("could not read webhook secret: %s", exc)
        return ""


def _verify(body: bytes, signature: str, secret: str) -> bool:
    if not signature or not secret:
        return False
    computed = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(computed, signature)


def _run_incremental_sync() -> bool:
    """Run up_sync.py in incremental mode (refreshes balances + new transactions)."""
    try:
        proc = subprocess.run(
            ["/usr/bin/python3", _UP_SYNC],
            cwd=_UP_DIR, capture_output=True, text=True, timeout=120,
        )
        ok = proc.returncode == 0
        if not ok:
            logger.warning("incremental up_sync rc=%s stderr=%s",
                           proc.returncode, (proc.stderr or "")[-400:])
        return ok
    except Exception as exc:  # noqa: BLE001
        logger.warning("incremental up_sync failed: %s", exc)
        return False


def _sync_worker() -> None:
    last_run = 0.0
    while True:
        _sync_event.wait()
        _sync_event.clear()
        # Coalesce a burst: wait out the cooldown, absorbing further events.
        gap = time.time() - last_run
        if gap < _MIN_INTERVAL:
            time.sleep(_MIN_INTERVAL - gap)
            _sync_event.clear()
        ok = _run_incremental_sync()
        last_run = time.time()
        _state["last_sync_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _state["last_sync_ok"] = ok
        # Invalidate the finance API cache so the next dashboard poll re-reads the DB.
        try:
            from services.api.finance import invalidate_cache  # noqa: PLC0415
            invalidate_cache()
        except Exception as exc:  # noqa: BLE001
            logger.debug("finance cache invalidate skipped: %s", exc)


_worker = threading.Thread(target=_sync_worker, name="up-webhook-sync", daemon=True)
_worker.start()


def _request_sync() -> None:
    _sync_event.set()


@router.post("/webhook")
async def up_webhook(request: Request):
    """Receive an Up Bank webhook event (HMAC-verified) and trigger a live sync."""
    body = await request.body()
    sig = request.headers.get("X-Up-Authenticity-Signature", "")
    secret = _webhook_secret()
    if not _verify(body, sig, secret):
        raise HTTPException(status_code=401, detail="invalid webhook signature")

    try:
        data = json.loads(body)
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON body")

    kind = (((data.get("data") or {}).get("attributes") or {}).get("eventType")) or "UNKNOWN"
    _state["last_event"] = data.get("data", {}).get("id")
    _state["last_event_type"] = kind
    _state["last_event_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _state["events_total"] += 1
    logger.info("Up webhook event: %s", kind)

    if kind == "PING":
        return JSONResponse({"ok": True, "ping": True})
    if kind.startswith("TRANSACTION_"):
        _request_sync()
    return JSONResponse({"ok": True, "eventType": kind})


@router.get("/webhook/health")
def webhook_health(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """Status for monitoring: last event + last sync (so a dead webhook is visible)."""
    return {
        "secret_configured": bool(_webhook_secret()),
        **_state,
    }
