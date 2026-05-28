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


# ── Track 3a: Variant-D dashboard support endpoints ──────────────────────────

@router.get("/sources")
def list_sources_endpoint(
    kind: Optional[str] = Query(None, description="Filter to one kind: paper | blog | doc | internal"),
    q: Optional[str] = Query(None, description="Case-insensitive substring search across title/url"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Paginated source list with per-row claim counts and open-contradiction counts."""
    from db.atlas_db import get_db
    try:
        with get_db() as conn:
            base = """
                SELECT
                    s.id, s.kind, s.url, s.title, s.venue, s.published_at,
                    s.ingested_at, s.extracted_by,
                    (SELECT COUNT(*) FROM claims c
                       WHERE c.source_id = s.id AND c.status = 'active') AS claim_count,
                    (SELECT COUNT(*) FROM contradictions co
                       JOIN claims c ON c.id = co.claim_id
                       WHERE c.source_id = s.id AND co.resolution IS NULL) AS open_contradictions
                FROM sources s
                WHERE 1=1
            """
            params: List[Any] = []
            if kind:
                base += " AND s.kind = ?"
                params.append(kind)
            if q:
                base += " AND (LOWER(s.title) LIKE ? OR LOWER(s.url) LIKE ?)"
                ql = f"%{q.lower()}%"
                params.extend([ql, ql])
            base += " ORDER BY s.ingested_at DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])
            rows = [dict(r) for r in conn.execute(base, params).fetchall()]

            count_sql = "SELECT COUNT(*) AS n FROM sources s WHERE 1=1"
            count_params: List[Any] = []
            if kind:
                count_sql += " AND s.kind = ?"
                count_params.append(kind)
            if q:
                count_sql += " AND (LOWER(s.title) LIKE ? OR LOWER(s.url) LIKE ?)"
                ql = f"%{q.lower()}%"
                count_params.extend([ql, ql])
            total = int(conn.execute(count_sql, count_params).fetchone()["n"])
    except Exception as exc:
        logger.exception("list_sources failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"total": total, "limit": limit, "offset": offset, "rows": rows}


@router.get("/contradictions-timeline")
def contradictions_timeline_endpoint(
    days: int = Query(30, ge=1, le=365),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Daily counts of contradictions by severity.

    For each calendar day in the window, counts contradictions whose
    first_seen_at falls on that day, bucketed by severity.  Used by the
    stacked-area chart of contradictions-over-time.
    """
    from db.atlas_db import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT substr(first_seen_at, 1, 10) AS date,
                       severity,
                       COUNT(*) AS n
                FROM contradictions
                WHERE first_seen_at >= date('now', ? || ' days')
                GROUP BY date, severity
                ORDER BY date
                """,
                (f"-{int(days)}",),
            ).fetchall()
            by_date: Dict[str, Dict[str, int]] = {}
            for r in rows:
                d = r["date"]
                by_date.setdefault(d, {"critical": 0, "major": 0, "minor": 0})
                sev = r["severity"]
                if sev in by_date[d]:
                    by_date[d][sev] = int(r["n"])

            # Resolved-over-time (for the velocity chart)
            resolved_rows = conn.execute(
                """
                SELECT substr(resolved_at, 1, 10) AS date, COUNT(*) AS n
                FROM contradictions
                WHERE resolved_at IS NOT NULL
                  AND resolved_at >= date('now', ? || ' days')
                GROUP BY date
                ORDER BY date
                """,
                (f"-{int(days)}",),
            ).fetchall()
            resolved_by_date = {r["date"]: int(r["n"]) for r in resolved_rows}

        timeline = [
            {"date": d, "critical": v["critical"], "major": v["major"], "minor": v["minor"],
             "resolved": resolved_by_date.get(d, 0)}
            for d, v in sorted(by_date.items())
        ]
    except Exception as exc:
        logger.exception("contradictions_timeline failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"days": days, "timeline": timeline}


@router.get("/digest-history")
def digest_history_endpoint(
    limit: int = Query(30, ge=1, le=180),
    kind: Optional[str] = Query(None),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Recent digest_history rows for the sparkline."""
    from db.atlas_db import get_db
    try:
        with get_db() as conn:
            query = (
                "SELECT id, kind, sent_at, new_papers, new_experiments, "
                "new_contradictions, lifecycle_transitions, delivery_status "
                "FROM digest_history WHERE 1=1"
            )
            params: List[Any] = []
            if kind:
                query += " AND kind = ?"
                params.append(kind)
            query += " ORDER BY sent_at DESC, id DESC LIMIT ?"
            params.append(limit)
            rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    except Exception as exc:
        logger.exception("digest_history failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"rows": list(reversed(rows))}  # chronological for charting


@router.get("/extraction-confidence")
def extraction_confidence_endpoint(
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """Histogram of `extraction_confidence` over claims with measured metrics."""
    from db.atlas_db import get_db
    try:
        with get_db() as conn:
            rows = conn.execute(
                """
                SELECT COALESCE(extraction_confidence, 'unknown') AS confidence,
                       COUNT(*) AS n
                FROM claims
                WHERE status = 'active' AND claimed_sharpe IS NOT NULL
                GROUP BY extraction_confidence
                """,
            ).fetchall()
            histogram: Dict[str, int] = {"high": 0, "medium": 0, "low": 0, "unknown": 0}
            for r in rows:
                c = r["confidence"]
                histogram[c if c in histogram else "unknown"] = int(r["n"])
            total = sum(histogram.values())
    except Exception as exc:
        logger.exception("extraction_confidence failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"total": total, "histogram": histogram}


@router.get("/strategy-summaries")
def strategy_summaries_endpoint(
    limit: int = Query(200, ge=1, le=1000),
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """All rows from v_strategy_summary.  One per (strategy, universe) cross-regime."""
    from db.atlas_db import get_db
    try:
        with get_db() as conn:
            rows = [dict(r) for r in conn.execute(
                f"SELECT * FROM v_strategy_summary "
                f"ORDER BY open_contradictions DESC, strategy LIMIT {int(limit)}",
            ).fetchall()]
    except Exception as exc:
        logger.exception("strategy_summaries failed")
        raise HTTPException(status_code=500, detail=str(exc))

    return {"rows": rows, "count": len(rows)}
