"""Contradiction-driven ideation channel (Phase 5).

Reads open major/critical contradictions, generates a QueueEntry per claim
that targets the (strategy, universe) under test with the paper's claimed
metrics as the acceptance criteria.  Operator-friendly hypothesis text.

Decay rule:
    Skip claims that already have a research_experiments row in the last
    DECAY_DAYS days targeting the same strategy + source.  Source linkage
    is stored in QueueEntry.tags as a 'source:<source_id>' marker so the
    decay query can join experiments → queue entries → tags.

The channel is read-only against the knowledge layer (sources/claims/
contradictions) and append-only against the queue.  Failures during
generation log + skip the offending claim; one bad contradiction does
not block the rest.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# How recent an experiment counts as "we already tested this contradiction".
DECAY_DAYS = 30

# Which contradiction severities feed the queue.  Minors are noise budget,
# critical/major are real divergences worth a retest.
ELIGIBLE_SEVERITIES = ("critical", "major")

# Default priority for contradiction-driven entries.  P3 = medium; sits below
# degradation (P1) and dormant (P2) but above new_strategy (P4).
DEFAULT_PRIORITY = "P3"

# Default runtime estimate (minutes) for a single-strategy backtest retest.
DEFAULT_RUNTIME_MIN = 20

# Category tag used on QueueEntry rows so downstream filters can target this
# channel specifically.  Already advertised in QueueEntry's docstring as a
# free string; no enum change needed.
CATEGORY = "contradiction"


@dataclass
class CandidateEntry:
    """A would-be QueueEntry, plus the contradiction context that justified it."""
    queue_entry: "object"           # research.models.QueueEntry
    contradiction_id: int
    claim_id: str
    source_id: str
    severity: str
    delta_abs: float


# ── Decay query ──────────────────────────────────────────────────────────────

def _recently_tested_sources(strategy: str, universe: str,
                              cutoff_iso: str) -> set[str]:
    """Return source_ids that already have a recent experiment.

    Joins research_experiments → queue tags via experiment params or notes.
    Since research_experiments doesn't carry source_id directly, we approximate
    by matching on (strategy, universe, created_at >= cutoff) and reading the
    queue.json entries we previously emitted to see which sources were targeted.

    The query is intentionally permissive: any recent test of the same
    (strategy, universe) suffices to defer ALL contradictions for that pair,
    not just the ones touching this exact source.  Rationale: the backtester
    measurement is independent of the paper's claim -- one run answers many
    contradictions.
    """
    from db.atlas_db import get_db
    sources_recently_targeted: set[str] = set()

    try:
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM research_experiments
                WHERE strategy = ?
                  AND universe = ?
                  AND created_at >= ?
                """,
                (strategy, universe, cutoff_iso),
            ).fetchone()
            n_recent = int(row["n"]) if row else 0
    except Exception as exc:  # noqa: BLE001 -- defensive; treat as "no decay"
        logger.warning("decay query failed for (%s, %s): %s",
                       strategy, universe, exc)
        return set()

    if n_recent == 0:
        return set()

    # Anything goes: signal "decay applies to all sources for this pair".
    # The caller treats a non-empty set as "skip everything", and we use
    # a sentinel to avoid pretending we know specific source ids.
    sources_recently_targeted.add("__ANY__")
    return sources_recently_targeted


# ── QueueEntry construction ──────────────────────────────────────────────────

def _format_hypothesis(strategy: str, universe: str, metric: str,
                       claimed: float | None, measured: float | None,
                       source_title: str, source_url: str | None) -> str:
    def _f(v):
        if v is None:
            return "?"
        try:
            return f"{float(v):.2f}"
        except (TypeError, ValueError):
            return str(v)

    src_ref = f"<{source_url}>" if source_url else ""
    return (
        f"Paper '{source_title}' {src_ref} claims {metric}={_f(claimed)} for "
        f"{strategy} on {universe}; our research_best measured {metric}={_f(measured)}. "
        f"Retest with the paper's spec to identify whether the divergence is "
        f"data, parameter, or implementation."
    )


def _acceptance_criteria(metric: str, claimed: float | None) -> Dict[str, Any]:
    """Translate the paper's claim into a runner-friendly acceptance bar.

    Conservative: the paper's number is the *target*, and we accept anything
    within ±20% of it as 'matched'.  Outside that band the experiment counts
    as 'failed' from the contradiction-resolution perspective.
    """
    if claimed is None:
        return {}
    tol = abs(claimed) * 0.2 if claimed != 0 else 0.2
    if metric == "sharpe":
        return {"min_sharpe": max(0.0, claimed - tol), "min_trades": 15}
    if metric == "max_dd_pct":
        return {"max_dd_pct": claimed + tol, "min_trades": 15}
    return {"target_metric": metric, "target_value": claimed,
            "tolerance_abs": tol, "min_trades": 15}


def _build_queue_entry(contradiction: Dict[str, Any]) -> "object":
    """Translate a v_open_contradictions row into a QueueEntry."""
    from research.models import (
        QueueEntry, ExperimentType, generate_experiment_id,
    )

    strategy = contradiction["strategy"]
    universe = contradiction["universe"]
    metric = contradiction["metric"]
    claimed = contradiction.get("claimed_value")
    measured = contradiction.get("measured_value")
    severity = contradiction["severity"]
    source_id = contradiction.get("source_id")
    source_title = contradiction.get("source_title") or strategy
    source_url = contradiction.get("source_url")
    claim_id = contradiction["claim_id"]
    contradiction_id = contradiction["contradiction_id"]

    title = f"contradiction[{severity}]: {strategy}/{universe} {metric}"

    return QueueEntry(
        id=generate_experiment_id(),
        title=title[:160],
        category=CATEGORY,
        market=universe,
        hypothesis=_format_hypothesis(
            strategy=strategy, universe=universe, metric=metric,
            claimed=claimed, measured=measured,
            source_title=source_title, source_url=source_url,
        ),
        method=ExperimentType.SINGLE_STRATEGY_TEST,
        acceptance_criteria=_acceptance_criteria(metric, claimed),
        estimated_runtime_min=DEFAULT_RUNTIME_MIN,
        priority=DEFAULT_PRIORITY,
        strategy_name=strategy,
        params_override=None,
        tags=[
            f"source:{source_id}",
            f"claim:{claim_id}",
            f"contradiction:{contradiction_id}",
            f"severity:{severity}",
            f"metric:{metric}",
            "channel:contradiction",
        ],
        notes=(
            f"Auto-generated by contradiction channel. "
            f"Source: {source_title}. "
            f"Claimed {metric}={claimed}, measured={measured}."
        )[:1000],
    )


# ── Channel entry point ──────────────────────────────────────────────────────

def generate_candidates(
    *,
    limit: int = 25,
    severities: Tuple[str, ...] = ELIGIBLE_SEVERITIES,
    decay_days: int = DECAY_DAYS,
) -> List[CandidateEntry]:
    """Read open contradictions and produce candidate QueueEntries (without queuing).

    Dry-run safe: no DB writes, no queue mutation.
    """
    from db.knowledge import get_open_contradictions

    candidates: List[CandidateEntry] = []
    cutoff_iso = (
        datetime.now(timezone.utc) - timedelta(days=decay_days)
    ).isoformat()

    # Pull a generous slice; we'll truncate to limit after decay filtering.
    rows = get_open_contradictions(limit=max(limit * 3, 50))
    # Filter by severity in-Python (the view already orders by severity DESC).
    rows = [r for r in rows if r.get("severity") in severities]

    # Group by (strategy, universe) so decay applies per-pair, not per-row.
    pair_to_rows: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in rows:
        key = (r["strategy"], r["universe"])
        pair_to_rows.setdefault(key, []).append(r)

    for (strategy, universe), pair_rows in pair_to_rows.items():
        decay = _recently_tested_sources(strategy=strategy, universe=universe,
                                          cutoff_iso=cutoff_iso)
        if "__ANY__" in decay:
            logger.debug("decay applies for %s/%s -- skipping %d contradiction(s)",
                          strategy, universe, len(pair_rows))
            continue

        for r in pair_rows:
            try:
                entry = _build_queue_entry(r)
            except Exception as exc:  # noqa: BLE001 -- one bad row shouldn't kill the batch
                logger.warning("build_queue_entry failed for contradiction_id=%s: %s",
                               r.get("contradiction_id"), exc)
                continue

            candidates.append(CandidateEntry(
                queue_entry=entry,
                contradiction_id=r["contradiction_id"],
                claim_id=r["claim_id"],
                source_id=r.get("source_id") or "",
                severity=r["severity"],
                delta_abs=float(r.get("delta_abs") or 0.0),
            ))

            if len(candidates) >= limit:
                return candidates

    return candidates


def queue_candidates(candidates: List[CandidateEntry]) -> Dict[str, Any]:
    """Append each candidate to the queue.  Returns {"queued": N, "errors": [...]}.

    Failure of any single append is logged and skipped; the rest still queue.
    """
    from research.models import append_to_queue

    queued = 0
    errors: List[str] = []

    for cand in candidates:
        try:
            # skip_validation=False: keep the runner-shape check active so a
            # malformed entry never lands in the queue.
            append_to_queue(cand.queue_entry, skip_validation=False)
            queued += 1
        except Exception as exc:  # noqa: BLE001 -- batch isolation
            errors.append(
                f"contradiction_id={cand.contradiction_id} "
                f"strategy={cand.queue_entry.strategy_name}: {exc}"
            )
            logger.warning("queue append failed: %s", errors[-1])

    return {"queued": queued, "errors": errors}


def run_channel(
    *,
    apply: bool,
    limit: int = 25,
    severities: Tuple[str, ...] = ELIGIBLE_SEVERITIES,
    decay_days: int = DECAY_DAYS,
) -> Dict[str, Any]:
    """End-to-end: generate candidates, optionally queue them."""
    candidates = generate_candidates(
        limit=limit, severities=severities, decay_days=decay_days,
    )

    summary: Dict[str, Any] = {
        "candidates": len(candidates),
        "by_severity": {},
        "sample": [],
    }
    for cand in candidates:
        summary["by_severity"][cand.severity] = (
            summary["by_severity"].get(cand.severity, 0) + 1
        )
    summary["sample"] = [
        {
            "contradiction_id": c.contradiction_id,
            "claim_id": c.claim_id,
            "strategy": c.queue_entry.strategy_name,
            "market": c.queue_entry.market,
            "severity": c.severity,
            "delta_abs": c.delta_abs,
            "title": c.queue_entry.title,
        }
        for c in candidates[:5]
    ]

    if not apply:
        summary["mode"] = "dry-run"
        return summary

    queue_result = queue_candidates(candidates)
    summary["mode"] = "apply"
    summary["queued"] = queue_result["queued"]
    summary["queue_errors"] = queue_result["errors"]
    return summary
