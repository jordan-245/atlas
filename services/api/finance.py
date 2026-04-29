"""Finance API routes (Up Bank data).

Phase 1 extraction from services/chat_server.py per
docs/phase-c-god-file-decomposition.md.

Route: GET /api/finance
"""
from __future__ import annotations

import json
import logging
import sys
import sqlite3 as _sqlite3
import time as _time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials

from services.auth import check_auth  # shared auth — no circular import

router = APIRouter(prefix="/api/finance", tags=["finance"])
logger = logging.getLogger(__name__)

# SERVE_DIR: mirror of chat_server.SERVE_DIR (pure path constant — safe to define locally)
_SERVE_DIR = Path("/root/atlas") / "dashboard" / "data"

# ── Route-local cache ─────────────────────────────────────────────────────────
_finance_cache: dict = {"data": None, "ts": 0.0}
_FINANCE_CACHE_TTL = 60  # seconds


@router.get("")
def finance_data(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """GET /api/finance — personal finance data from Up Bank SQLite + Atlas DB.

    Queries /root/up-bank/up_bank.db directly (same pattern as trading tab
    querying Atlas SQLite). Caches result for 60 seconds. Falls back to
    static finance-data.json if the DB query fails.
    """
    now = _time.time()
    if _finance_cache["data"] and (now - _finance_cache["ts"]) < _FINANCE_CACHE_TTL:
        return JSONResponse(content=_finance_cache["data"])

    try:
        if "/root/up-bank" not in sys.path:
            sys.path.insert(0, "/root/up-bank")
        from up_sync import build_finance_payload  # noqa: PLC0415

        # Open Up Bank DB read-only
        up_conn = _sqlite3.connect("file:///root/up-bank/up_bank.db?mode=ro", uri=True)
        up_conn.row_factory = _sqlite3.Row

        # Get Atlas equity from Atlas SQLite (equity_curve table)
        atlas_eq = 0.0
        atlas_pnl = 0.0
        portfolio_history: list = []
        try:
            from db.atlas_db import get_db  # noqa: PLC0415
            with get_db() as atlas_conn:
                rows = atlas_conn.execute(
                    "SELECT * FROM equity_curve WHERE market_id='sp500' "
                    "ORDER BY date DESC LIMIT 60"
                ).fetchall()
                if rows:
                    atlas_eq = float(rows[0]["equity"] or 0)
                    atlas_pnl = float(rows[0]["day_pnl"] or 0)
                    portfolio_history = [dict(r) for r in reversed(rows)]
        except Exception as e:
            logger.warning("Atlas equity lookup failed: %s", e)

        # Moomoo data (manual JSON, if available)
        moomoo_data: dict = {}
        moomoo_path = Path("/root/atlas/dashboard/cache/moomoo_manual.json")
        if moomoo_path.exists():
            try:
                with open(moomoo_path) as f:
                    moomoo_data = json.load(f)
            except Exception as e:
                logger.warning("Moomoo manual cache parse failed: %s", e)

        payload = build_finance_payload(
            up_conn, atlas_eq, atlas_pnl, portfolio_history, moomoo_data
        )
        up_conn.close()

        _finance_cache["data"] = payload
        _finance_cache["ts"] = now
        return JSONResponse(content=payload)

    except Exception as e:
        logger.exception("Finance API SQLite query failed, falling back to JSON")
        # Fallback to static JSON file
        finance_path = _SERVE_DIR / "finance-data.json"
        if finance_path.exists():
            try:
                with open(finance_path) as f:
                    return JSONResponse(content=json.load(f))
            except Exception as e2:
                logger.warning("Finance cache fallback parse failed: %s", e2)
        raise HTTPException(status_code=500, detail=str(e))
