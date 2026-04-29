"""Regime API routes — market regime classification, forecasting, distributions.

Phase 1 extraction from services/chat_server.py per
docs/phase-c-god-file-decomposition.md.

Routes:
  GET /api/regime/history        — regime history (days=90)
  GET /api/regime/current        — most recent regime state
  GET /api/regime/forecast       — forward Monte Carlo forecast
  GET /api/regime/distributions  — distribution stats per regime state
  GET /api/regime/transitions    — transition probability matrix
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasicCredentials

from services.auth import check_auth  # shared auth — no circular import

router = APIRouter(prefix="/api/regime", tags=["regime"])
logger = logging.getLogger(__name__)

# PROJECT_ROOT: mirror of chat_server.PROJECT_ROOT (pure constant — safe to define locally)
_PROJECT_ROOT = Path("/root/atlas")

# Daily cache for regime distributions (reset when date changes)
_regime_dist_cache: dict = {"as_of": None, "data": None}


# ── GET /api/regime/history ───────────────────────────────────────────────────

@router.get("/history")
def regime_history(
    days: int = 90,
    _auth: HTTPBasicCredentials = Depends(check_auth),
):
    """GET /api/regime/history?days=90 — regime classification history."""
    try:
        from db.atlas_db import get_regime_history
        rows = get_regime_history(days=days)
        # get_regime_history already returns most-recent-first
        # Normalise: rename regime_state → state for consistent API field naming
        normalised = [
            {**r, "state": r["regime_state"]} if "regime_state" in r and "state" not in r else r
            for r in rows
        ]
        return JSONResponse(normalised)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/regime/current ───────────────────────────────────────────────────

@router.get("/current")
def regime_current(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """GET /api/regime/current — most recent regime state."""
    try:
        from db.atlas_db import get_current_regime
        regime = get_current_regime()
        if regime:
            # Normalise: rename regime_state → state so the API field is consistent
            if "regime_state" in regime and "state" not in regime:
                regime["state"] = regime.pop("regime_state")
            return JSONResponse(regime)
        return JSONResponse({"state": "unknown"})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/regime/forecast ──────────────────────────────────────────────────

@router.get("/forecast")
def regime_forecast(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """GET /api/regime/forecast — regime forward Monte Carlo forecast."""
    try:
        import json as _json
        from db.atlas_db import get_db
        with get_db() as db:
            rows = db.execute(
                "SELECT * FROM regime_forecast WHERE as_of = (SELECT MAX(as_of) FROM regime_forecast) ORDER BY horizon_days"
            ).fetchall()
        if rows:
            horizons = {}
            current = None
            n_paths = None
            as_of = None
            for r in rows:
                rd = dict(r)
                current = rd["current_regime"]
                n_paths = rd["n_paths"]
                as_of = rd["as_of"]
                state_probs = {}
                try:
                    state_probs = _json.loads(rd.get("state_probabilities") or "{}")
                except Exception as e:
                    logger.debug("state_probs JSON parse failed: %s", e)
                horizons[f"{rd['horizon_days']}d"] = {
                    "days": rd["horizon_days"],
                    "expected_return": rd["expected_return"],
                    "median_return": rd["median_return"],
                    "std": rd["std"],
                    "var_5": rd["var_5"],
                    "var_1": rd["var_1"],
                    "cvar_5": rd["cvar_5"],
                    "cvar_1": rd["cvar_1"],
                    "p95": rd["p95"],
                    "p75": rd["p75"],
                    "p25": rd["p25"],
                    "prob_positive": rd["prob_positive"],
                    "state_probabilities": state_probs,
                }
            return {
                "current_regime": current,
                "n_paths": n_paths,
                "as_of": as_of,
                "horizons": horizons,
                "source": "cached",
            }

        # Fallback: live compute
        from regime.forward_mc import simulate_return_paths_from_regime, persist_forecast, get_current_regime
        cur = get_current_regime()
        result = simulate_return_paths_from_regime(cur, n_paths=5000, n_days=90, seed=42)
        try:
            persist_forecast(result)
        except Exception as e:
            logger.warning("persist_forecast failed: %s", e)
        result["source"] = "live"
        return result
    except Exception as e:
        logger.exception("regime_forecast failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/regime/distributions ─────────────────────────────────────────────

@router.get("/distributions")
def regime_distributions(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """GET /api/regime/distributions — return distribution stats for all 6 regime states. Cached daily."""
    try:
        from datetime import date
        today = date.today().isoformat()
        if _regime_dist_cache["as_of"] == today and _regime_dist_cache["data"] is not None:
            return JSONResponse(_regime_dist_cache["data"])

        from regime.distributions import RegimeDistributions
        rd = RegimeDistributions()
        rd.fit(lookback_years=10)
        all_stats = rd.all_regime_stats()

        # Reshape to spec — rename n_samples → n
        distributions = {}
        for state, stats in all_stats.items():
            distributions[state] = {
                "n": stats.get("n_samples", 0),
                "mean": round(stats.get("mean", 0.0), 6),
                "vol": round(stats.get("vol", 0.0), 6),
                "skew": round(stats.get("skew", 0.0), 4),
                "kurt": round(stats.get("kurt", 0.0), 4),
                "var_5": round(stats.get("var_5", 0.0), 6),
                "var_1": round(stats.get("var_1", 0.0), 6),
                "cvar_5": round(stats.get("cvar_5", 0.0), 6),
                "cvar_1": round(stats.get("cvar_1", 0.0), 6),
                "fallback": bool(stats.get("fallback", False)),
            }

        result = {"as_of": today, "distributions": distributions}
        _regime_dist_cache["as_of"] = today
        _regime_dist_cache["data"] = result
        return JSONResponse(result)
    except Exception as e:
        logger.exception("regime_distributions failed")
        raise HTTPException(status_code=500, detail=str(e))


# ── GET /api/regime/transitions ───────────────────────────────────────────────

@router.get("/transitions")
def regime_transitions(_auth: HTTPBasicCredentials = Depends(check_auth)):
    """GET /api/regime/transitions — regime transition probability matrix."""
    # === REGIME CACHE (P2.7) ===
    try:
        from db.atlas_db import get_cached_regime_transitions
        _cached_rt = get_cached_regime_transitions(max_age_hours=24)
        if _cached_rt:
            return JSONResponse({
                "matrix": _cached_rt.get("matrix", {}),
                "durations": {},
                "states": list(_cached_rt.get("matrix", {}).keys()),
                "current_state": None,
                "total_days": _cached_rt.get("n_observations", 0),
                "as_of": _cached_rt.get("as_of"),
                "stale": False,
                "source": "cache",
            })
    except Exception as _rce:
        logger.warning("regime_transitions: cache lookup failed: %s", _rce)

    # Cache absent — kick off background refresh (non-blocking)
    try:
        import subprocess as _sp2
        _sp2.Popen(
            [sys.executable, "scripts/precompute_risk.py", "--target=regime"],
            cwd=str(_PROJECT_ROOT),
            stdout=open("logs/risk_precompute.log", "a"),
            stderr=_sp2.STDOUT,
        )
    except Exception as _rpe:
        logger.warning("regime_transitions: bg refresh failed to start: %s", _rpe)
    # === END REGIME CACHE ===
    try:
        from db.atlas_db import get_db

        STATES = [
            "bull_risk_on", "bull_risk_off", "transition_uncertain",
            "bear_risk_off", "bear_capitulation", "recovery_early"
        ]

        with get_db() as db:
            rows = db.execute(
                "SELECT date, regime_state FROM regime_history ORDER BY date ASC"
            ).fetchall()

        if not rows:
            return JSONResponse({"matrix": {}, "durations": {}, "total_days": 0, "states": STATES})

        history = [dict(r) for r in rows]

        # Count transitions between consecutive days
        transition_counts: dict = {s: {t: 0 for t in STATES} for s in STATES}
        from_counts: dict = {s: 0 for s in STATES}

        for i in range(len(history) - 1):
            from_state = history[i]["regime_state"]
            to_state = history[i + 1]["regime_state"]
            if from_state in transition_counts and to_state in transition_counts[from_state]:
                transition_counts[from_state][to_state] += 1
                from_counts[from_state] += 1

        # Convert to probabilities
        matrix: dict = {}
        for from_s in STATES:
            matrix[from_s] = {}
            total = from_counts[from_s]
            for to_s in STATES:
                if total > 0:
                    matrix[from_s][to_s] = round(transition_counts[from_s][to_s] / total * 100, 1)
                else:
                    matrix[from_s][to_s] = 0.0

        # Calculate average duration in each state (consecutive day runs)
        durations: dict = {s: [] for s in STATES}
        if history:
            current_state = history[0]["regime_state"]
            run_length = 1
            for i in range(1, len(history)):
                if history[i]["regime_state"] == current_state:
                    run_length += 1
                else:
                    if current_state in durations:
                        durations[current_state].append(run_length)
                    current_state = history[i]["regime_state"]
                    run_length = 1
            # Don't forget the last run
            if current_state in durations:
                durations[current_state].append(run_length)

        avg_durations = {}
        for s in STATES:
            runs = durations[s]
            if runs:
                avg_durations[s] = {
                    "avg_days": round(sum(runs) / len(runs), 1),
                    "max_days": max(runs),
                    "occurrences": len(runs),
                    "total_days": sum(runs),
                }
            else:
                avg_durations[s] = {"avg_days": 0, "max_days": 0, "occurrences": 0, "total_days": 0}

        # Current state
        current = history[-1]["regime_state"] if history else None

        return JSONResponse({
            "matrix": matrix,
            "durations": avg_durations,
            "states": STATES,
            "current_state": current,
            "total_days": len(history),
        })
    except Exception as e:
        logger.exception("regime_transitions failed")
        raise HTTPException(status_code=500, detail=str(e))
