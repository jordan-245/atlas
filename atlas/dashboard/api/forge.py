"""Forge monitor API — renders crucible's forge_state.json artifact (#35 inversion).

Single read endpoint GET /api/forge/state. Crucible OWNS all research semantics
(cycle parsing, FDR registry, candidates, lane classification) and emits one
versioned snapshot per forge run + triage: research-wiki/.dashboard/forge_state.json.
This module renders it and adds HOST-level facts only (systemd timer state, the
deployed-strategy registry) — things that are about this machine, not the research.

Replaced (2026-06-13): ~320 lines that parsed run_log.jsonl, the wiki registry,
candidates.md, and LOOP_DISABLED directly — including a hand-ported lane
classifier that had diverged from crucible's agent/families.py.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.security import HTTPBasicCredentials

from atlas.dashboard.auth import check_auth  # hard import: if auth is broken, fail loudly, never serve open
from atlas.kernel.paths import CONFIG_DIR

router = APIRouter(prefix="/api/forge", tags=["forge"])

ARTIFACT = Path("/root/research-wiki/.dashboard/forge_state.json")
SUPPORTED_SCHEMA = 1


def _load_artifact() -> dict | None:
    try:
        s = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if s.get("schema_version") != SUPPORTED_SCHEMA:
        return None
    return s


def _systemctl_status() -> dict:
    """Host-level: timer state lives HERE (it's about this machine, not the research)."""
    info = {"enabled": False, "next_run_str": None, "last_trigger_str": None}
    try:
        en = subprocess.run(["systemctl", "is-enabled", "crucible-forge.timer"],
                            capture_output=True, text=True, timeout=4)
        info["enabled"] = en.stdout.strip() == "enabled"
        show = subprocess.run(
            ["systemctl", "show", "crucible-forge.timer",
             "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec"],
            capture_output=True, text=True, timeout=4).stdout
        for ln in show.splitlines():
            if ln.startswith("NextElapseUSecRealtime="):
                info["next_run_str"] = ln.split("=", 1)[1].strip() or None
            elif ln.startswith("LastTriggerUSec="):
                info["last_trigger_str"] = ln.split("=", 1)[1].strip() or None
        act = subprocess.run(["systemctl", "is-active", "crucible-forge.service"],
                             capture_output=True, text=True, timeout=4)
        info["cycle_active"] = act.stdout.strip() == "active"
    except Exception:
        pass
    return info


def _pct(a: int, b: int) -> str:
    return f"{round(100 * a / b)}%" if b else "—"


@router.get("/state")
def forge_state(_auth: HTTPBasicCredentials = Depends(check_auth)) -> dict:
    art = _load_artifact()
    status = _systemctl_status()

    if art is None:
        return {"generated_at": datetime.now().isoformat(), "status": status,
                "error": f"forge_state.json missing/unreadable/unsupported at {ARTIFACT} — "
                         "crucible writes it after each forge run + triage",
                "summary": {}, "fdr": {}, "pipeline": [], "cycles": [], "candidates": [],
                "log_tail": []}

    s = art["summary"]
    status["running"] = not art.get("loop_disabled", False)
    status["last_cycle_ts"] = art["cycles"][0]["ts"] if art.get("cycles") else None
    status["artifact_generated_at"] = art.get("generated_at")

    # deployed strategies: HOST-side fact (atlas owns the live registry)
    n_deployed, deployed_names = 0, []
    try:
        _reg = json.loads((CONFIG_DIR / "live_strategies.json").read_text())
        deployed_names = [x.get("name", "?") for x in _reg]
        n_deployed = len(_reg)
    except Exception:
        pass

    n_cand, free_cand = s.get("candidates", 0), s.get("free_candidates", 0)
    wq = s.get("work_queue_detail", {})
    best_h = s.get("best_holdout_sharpe")

    pipeline = [
        {"key": "scout", "label": "Scout", "icon": "🔭", "count": s.get("sources", 0), "accent": False,
         "stats": [{"label": "research runs", "value": s.get("sources", 0)},
                   {"label": "candidates found", "value": n_cand},
                   {"label": "free-data", "value": _pct(free_cand, n_cand)}]},
        {"key": "propose", "label": "Propose", "icon": "💡", "count": n_cand, "accent": False,
         "stats": [{"label": "scout ideas", "value": n_cand},
                   {"label": "free", "value": free_cand},
                   {"label": "data-gated", "value": n_cand - free_cand}]},
        {"key": "codegen", "label": "Codegen", "icon": "⚙️", "count": s.get("ran", 0), "accent": False,
         "stats": [{"label": "work queue", "value": wq.get("queued", 0) + wq.get("claimed", 0)},
                   {"label": "coded", "value": s.get("ran", 0)},
                   {"label": "self-repair", "value": "on"}]},
        {"key": "run", "label": "Rails", "icon": "🛡️", "count": s.get("cycles", 0), "accent": False,
         "stats": [{"label": "tested", "value": s.get("cycles", 0)},
                   {"label": "failed", "value": s.get("fails", 0) + s.get("errors", 0)},
                   {"label": "near-miss", "value": s.get("near_misses", 0)},
                   {"label": "passed", "value": s.get("passes", 0)}]},
        {"key": "record", "label": "Record", "icon": "📖", "count": s.get("experiments", 0), "accent": False,
         "stats": [{"label": "experiments", "value": s.get("experiments", 0)},
                   {"label": "FDR families", "value": s.get("families", 0)},
                   {"label": "wiki pages", "value": s.get("wiki_pages", 0)}]},
        {"key": "alert", "label": "Alert", "icon": "🔔", "count": s.get("passes", 0) + n_deployed,
         "accent": s.get("passes", 0) > 0 or n_deployed > 0,
         "stats": [{"label": "passes", "value": s.get("passes", 0)},
                   {"label": "deployed to paper", "value": n_deployed},
                   {"label": "best holdout Sh", "value": (f"{best_h:.2f}" if best_h is not None else "—")}]},
    ]

    summary = {**s, "deployed": n_deployed, "deployed_names": deployed_names,
               "pass_rate": _pct(s.get("passes", 0), max(s.get("cycles", 0), 1))}

    return {
        "generated_at": datetime.now().isoformat(),
        "status": status,
        "summary": summary,
        "fdr": art.get("fdr", {}),
        "pipeline": pipeline,
        "cycles": art.get("cycles", []),
        "candidates": art.get("candidates", []),
        "log_tail": art.get("log_tail", []),
    }
