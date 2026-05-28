"""Knowledge Layer API — sources, claims, contradictions, summaries.

Phase 4 operator surfaces.  Makes the data Phase 0-3 produces visible
without sshing into the DB.

Routes (all require HTTP Basic Auth):

  GET  /api/knowledge/contradictions/open
      — paginated, severity-ordered list of unresolved contradictions
        joined to source info.  Query params:
          severity   = 'critical' | 'major' | 'minor'
          strategy   = filter to one strategy
          limit      = default 50, max 200

  POST /api/knowledge/contradictions/{id}/resolve
      — body: {"resolution": "retested" | "claim_rejected" |
                              "measurement_corrected" | "deferred",
               "note": "..."}

  GET  /api/knowledge/strategy/{strategy}/summary
      — single-strategy roll-up from v_strategy_summary, plus the top N
        open contradictions for that strategy.

  GET  /api/knowledge/sources/{id}
      — source row + its claims (active by default).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials
from pydantic import BaseModel, Field

from services.auth import check_auth

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])
logger = logging.getLogger(__name__)


# ── Pydantic models ───────────────────────────────────────────────────────────

class ResolveContradictionRequest(BaseModel):
    resolution: str = Field(
        ...,
        description="One of: retested | claim_rejected | measurement_corrected | deferred",
    )
    note: Optional[str] = None


# ── GET /api/knowledge/contradictions/open ────────────────────────────────────

@router.get("/contradictions/open")
def get_open_contradictions_endpoint(
    severity: Optional[str] = Query(
        None, description="Filter to one severity: critical | major | minor",
    ),
    strategy: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Return open contradictions, ordered by severity then |delta|."""
    from db.knowledge import get_open_contradictions
    try:
        rows = get_open_contradictions(
            strategy=strategy, severity=severity, limit=limit,
        )
    except Exception as exc:
        logger.exception("get_open_contradictions failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"count": len(rows), "limit": limit, "rows": rows}


# ── POST /api/knowledge/contradictions/{id}/resolve ───────────────────────────

@router.post("/contradictions/{contradiction_id}/resolve")
def resolve_contradiction_endpoint(
    contradiction_id: int,
    body: ResolveContradictionRequest,
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Mark a contradiction as resolved.  Removes it from the open-list view."""
    from db.knowledge import resolve_contradiction
    try:
        resolve_contradiction(
            contradiction_id=contradiction_id,
            resolution=body.resolution,
            note=body.note,
        )
    except ValueError as exc:
        # Bad resolution value -- bubble up as 400.
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("resolve_contradiction failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"ok": True, "contradiction_id": contradiction_id,
            "resolution": body.resolution}


# ── GET /api/knowledge/strategy/{strategy}/summary ────────────────────────────

@router.get("/strategy/{strategy}/summary")
def get_strategy_summary_endpoint(
    strategy: str,
    universe: Optional[str] = Query(None),
    open_contradictions_limit: int = Query(5, ge=0, le=50),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Single-strategy roll-up: measured metrics, claim counts, lifecycle state,
    and the top N open contradictions for the strategy.
    """
    from db.atlas_db import get_db
    from db.knowledge import get_open_contradictions

    try:
        with get_db() as conn:
            query = "SELECT * FROM v_strategy_summary WHERE strategy = ?"
            params: List[Any] = [strategy]
            if universe:
                query += " AND universe = ?"
                params.append(universe)
            rows = conn.execute(query, params).fetchall()
            summary_rows = [dict(r) for r in rows]
    except Exception as exc:
        logger.exception("v_strategy_summary query failed")
        raise HTTPException(status_code=500, detail=str(exc))

    if not summary_rows:
        raise HTTPException(
            status_code=404,
            detail=f"No research_best row for strategy={strategy}"
                   + (f", universe={universe}" if universe else ""),
        )

    open_for_strat = (
        get_open_contradictions(strategy=strategy, limit=open_contradictions_limit)
        if open_contradictions_limit > 0
        else []
    )

    return {
        "strategy": strategy,
        "summary": summary_rows,
        "open_contradictions": open_for_strat,
    }


# ── GET /api/knowledge/sources/{id} ──────────────────────────────────────────

@router.get("/sources/{source_id}")
def get_source_endpoint(
    source_id: str,
    include_dismissed_claims: bool = Query(False),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Return one source plus its claims (active by default)."""
    from db.knowledge import get_source, list_claims
    try:
        src = get_source(source_id)
        if src is None:
            raise HTTPException(status_code=404, detail=f"source not found: {source_id}")
        claims = list_claims(
            source_id=source_id,
            status=None if include_dismissed_claims else "active",
            limit=200,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_source failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"source": src, "claims": claims, "claim_count": len(claims)}
