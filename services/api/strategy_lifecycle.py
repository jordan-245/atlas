"""Strategy Lifecycle API — read state + manual transition endpoint.

Self-contained router for the strategy promotion lifecycle.
Delegates to monitor.strategy_lifecycle state machine and db.atlas_db.

Routes:
    GET  /api/strategy-lifecycle             — list all rows
    GET  /api/strategy-lifecycle/{s}/{u}/history — transition history
    POST /api/strategy-lifecycle/transition  — manual state transition

Note: The main chat_server already mounts the richer lifecycle.py router
at these paths.  This module is a minimal stand-alone router used by
test_strategy_lifecycle_api.py tests (which build their own FastAPI app
from this router alone) and as a clean reference implementation for the
spec requirements.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBasicCredentials
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Auth dependency — gracefully no-ops when overridden by test fixtures
try:
    from services.auth import check_auth
except ImportError:
    from fastapi.security import HTTPBasic as _HTTPBasic
    _security = _HTTPBasic()
    def check_auth(credentials: HTTPBasicCredentials = Depends(_security)):  # type: ignore[misc]
        return credentials

router = APIRouter(prefix="/api/strategy-lifecycle", tags=["lifecycle"])


# ── Pydantic models ───────────────────────────────────────────────────────────

class TransitionRequest(BaseModel):
    strategy: str
    universe: str
    new_state: str   # "RESEARCH" | "PAPER" | "LIVE" | "RETIRED"
    reason: str
    operator: str = "operator"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _days_since(iso_str: Optional[str]) -> Optional[int]:
    """Return integer days since an ISO datetime string, or None."""
    if not iso_str:
        return None
    try:
        ts = datetime.fromisoformat(iso_str.replace(" ", "T"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - ts
        return max(0, int(delta.total_seconds() // 86400))
    except (ValueError, TypeError):
        return None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("")
def list_lifecycle_states(
    _auth: HTTPBasicCredentials = Depends(check_auth),
) -> Dict[str, Any]:
    """Return all rows from strategy_lifecycle as JSON."""
    try:
        from db.atlas_db import list_lifecycle_states
        rows_raw = list_lifecycle_states()
    except Exception as exc:
        logger.error("list_lifecycle_states failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    rows: List[Dict[str, Any]] = []
    for r in rows_raw:
        row = dict(r)
        row["days_in_state"] = _days_since(row.get("entered_state_at"))
        rows.append(row)

    return {"rows": rows}


@router.get("/{strategy}/{universe}/history")
def get_history(
    strategy: str,
    universe: str,
    _auth: HTTPBasicCredentials = Depends(check_auth),
) -> Dict[str, Any]:
    """Return strategy_lifecycle_history rows for a given (strategy, universe)."""
    try:
        from db.atlas_db import get_db
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM strategy_lifecycle_history "
                "WHERE strategy=? AND universe=? "
                "ORDER BY transitioned_at DESC",
                (strategy, universe),
            ).fetchall()
    except Exception as exc:
        logger.error("get_history(%s, %s) failed: %s", strategy, universe, exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"rows": [dict(r) for r in rows]}


@router.post("/transition")
def manual_transition(
    req: TransitionRequest,
    _auth: HTTPBasicCredentials = Depends(check_auth),
) -> Dict[str, Any]:
    """Manual operator transition. Validates state name + calls transition()."""
    try:
        from monitor.strategy_lifecycle import PromotionState, transition
        new_state_enum = PromotionState[req.new_state]
    except KeyError:
        raise HTTPException(status_code=400, detail=f"Invalid state: {req.new_state!r}")

    try:
        transition(
            req.strategy,
            req.universe,
            new_state_enum,
            reason=req.reason,
            operator=req.operator,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error("manual_transition failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))

    return {"status": "ok", "new_state": req.new_state}
