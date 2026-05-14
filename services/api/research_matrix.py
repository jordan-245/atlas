"""Research Matrix API — strategy × universe coverage view.

Routes:
    GET /api/research-matrix/coverage  — full coverage matrix combining
        research_best, strategy_lifecycle, and active config.

Spec: Task 20 — Phase 5 Dashboard Research-Matrix Coverage View.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials

logger = logging.getLogger(__name__)

try:
    from services.auth import check_auth
except ImportError:
    from fastapi.security import HTTPBasic as _HTTPBasic
    _security = _HTTPBasic()
    def check_auth(credentials: HTTPBasicCredentials = Depends(_security)):  # type: ignore[misc]
        return credentials

router = APIRouter(prefix="/api/research-matrix", tags=["research"])

# ── Active config loader ──────────────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).resolve().parents[2] / "config" / "active"


def _load_active_strategies() -> set[tuple[str, str]]:
    """Return set of (strategy, universe) pairs currently active in config."""
    active: set[tuple[str, str]] = set()
    if not _CONFIG_DIR.exists():
        return active
    for cfg_file in _CONFIG_DIR.glob("*.json"):
        if cfg_file.stem.endswith(".bak") or cfg_file.stem.startswith("."):
            continue
        try:
            data = json.loads(cfg_file.read_text())
            universe = data.get("universe") or cfg_file.stem
            for s_cfg in data.get("strategies", []):
                name = s_cfg.get("name") or s_cfg.get("strategy")
                if name:
                    active.add((name, universe))
        except Exception:  # noqa: BLE001
            continue
    return active


# ── Days-stale helper ─────────────────────────────────────────────────────────

def _days_stale(updated_at: Optional[str]) -> Optional[float]:
    if not updated_at:
        return None
    try:
        ts = datetime.fromisoformat(updated_at.replace(" ", "T"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return round((datetime.now(timezone.utc) - ts).total_seconds() / 86400, 1)
    except (ValueError, TypeError):
        return None


# ── Cell health classifier ────────────────────────────────────────────────────

def _cell_health(
    sharpe: Optional[float],
    days: Optional[float],
    lifecycle_state: Optional[str],
) -> str:
    """Return 'green' | 'yellow' | 'red' | 'grey'."""
    if lifecycle_state == "RETIRED":
        return "grey"
    if days is None:
        return "red"
    if days < 7 and (sharpe or 0) >= 0.3:
        return "green"
    if days < 14 or (sharpe or 0) > 0.2:
        return "yellow"
    return "red"


# ── Endpoint ──────────────────────────────────────────────────────────────────

@router.get("/coverage")
def get_coverage_matrix(
    _auth: HTTPBasicCredentials = Depends(check_auth),
) -> JSONResponse:
    """Return the full strategy × universe coverage matrix.

    Combines:
    - research_best (best params + sharpe)
    - strategy_lifecycle (current promotion state)
    - config/active (currently active strategies)
    """
    try:
        from db.atlas_db import get_db

        with get_db() as db:
            rb_rows: List[Dict[str, Any]] = [
                dict(r)
                for r in db.execute(
                    "SELECT strategy, universe, "
                    "COALESCE(solo_sharpe, sharpe) AS sharpe, "
                    "max_dd_pct, trades, updated_at "
                    "FROM research_best"
                ).fetchall()
            ]
            lc_rows: List[Dict[str, Any]] = [
                dict(r)
                for r in db.execute(
                    "SELECT strategy, universe, state, entered_state_at "
                    "FROM strategy_lifecycle"
                ).fetchall()
            ]

    except Exception as exc:  # noqa: BLE001
        logger.exception("get_coverage_matrix DB query failed")
        raise HTTPException(status_code=500, detail=str(exc))

    active_pairs = _load_active_strategies()

    # Build lookup maps
    lc_map: Dict[tuple[str, str], Dict[str, Any]] = {
        (r["strategy"], r["universe"]): r for r in lc_rows
    }

    # Gather all strategies + universes from research_best (coverage source)
    strategies: List[str] = sorted({r["strategy"] for r in rb_rows})
    universes: List[str] = sorted({r["universe"] for r in rb_rows})

    # Also include any lifecycle-only combos (not yet in research_best)
    for lc in lc_rows:
        if lc["strategy"] not in strategies:
            strategies.append(lc["strategy"])
        if lc["universe"] not in universes:
            universes.append(lc["universe"])
    strategies.sort()
    universes.sort()

    # Build rb lookup: (strategy, universe) → row
    rb_map: Dict[tuple[str, str], Dict[str, Any]] = {
        (r["strategy"], r["universe"]): r for r in rb_rows
    }

    # Build matrix rows (list of dicts per strategy)
    now = datetime.now(timezone.utc)
    matrix: List[Dict[str, Any]] = []

    for strategy in strategies:
        row_cells: List[Optional[Dict[str, Any]]] = []
        for universe in universes:
            key = (strategy, universe)
            rb = rb_map.get(key)
            lc = lc_map.get(key)

            if rb is None and lc is None:
                row_cells.append(None)
                continue

            sharpe = rb["sharpe"] if rb else None
            trades = rb["trades"] if rb else None
            max_dd = rb["max_dd_pct"] if rb else None
            updated_at = rb["updated_at"] if rb else None
            days = _days_stale(updated_at)
            lifecycle_state = lc["state"] if lc else None
            entered_state_at = lc["entered_state_at"] if lc else None
            in_active = key in active_pairs

            row_cells.append({
                "sharpe": sharpe,
                "trades": trades,
                "max_dd_pct": max_dd,
                "last_updated": updated_at,
                "days_stale": days,
                "lifecycle_state": lifecycle_state,
                "entered_state_at": entered_state_at,
                "in_active_config": in_active,
                "health": _cell_health(sharpe, days, lifecycle_state),
            })

        matrix.append({
            "strategy": strategy,
            "cells": row_cells,
        })

    return JSONResponse(
        content={
            "strategies": strategies,
            "universes": universes,
            "matrix": matrix,
            "generated_at": now.isoformat(),
        }
    )
