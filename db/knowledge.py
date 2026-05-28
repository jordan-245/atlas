"""db/knowledge -- Research knowledge layer CRUD.

Sources of external claims (papers, blogs, docs), the claims themselves,
contradictions between claims and measured research_best rows, and digest
history.

Lifecycle state transitions are recorded in the existing strategy_lifecycle_history
table (see db/lifecycle.py).  Phase 3 extended that table with gate_results and
experiment_id columns; this module does NOT duplicate it.

Phase 0+3 of the research-system DB consolidation.  All public functions are
re-exported through db.atlas_db for backward compat with the rest of Atlas.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import db.atlas_db as _adb

__all__ = [
    # sources
    "insert_source",
    "get_source",
    "list_sources",
    # claims
    "insert_claim",
    "get_claim",
    "list_claims",
    "list_shell_claims",
    "update_claim_metrics",
    "dismiss_claim",
    # contradictions
    "sync_contradictions",
    "get_open_contradictions",
    "resolve_contradiction",
    # digest history
    "log_digest",
    "get_last_digest",
    # Phase 6: SQL mirrors of queue.json / journal.json
    "upsert_queue_mirror_row",
    "list_queue_mirror_rows",
    "count_queue_mirror_rows",
    "insert_journal_mirror_row",
    "list_journal_mirror_rows",
    "count_journal_mirror_rows",
]

_log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# sources
# ═══════════════════════════════════════════════════════════════════════════════

def insert_source(
    id: str,
    kind: str,
    title: str,
    *,
    url: Optional[str] = None,
    authors: Optional[List[str]] = None,
    venue: Optional[str] = None,
    published_at: Optional[str] = None,
    sha256: Optional[str] = None,
    local_path: Optional[str] = None,
    extracted_by: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Insert a source.  Idempotent via INSERT OR IGNORE keyed on (id, sha256)."""
    authors_json = json.dumps(authors) if authors is not None else None
    with _adb.get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO sources
                (id, kind, url, title, authors, venue, published_at,
                 sha256, local_path, extracted_by, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, kind, url, title, authors_json, venue, published_at,
             sha256, local_path, extracted_by, notes),
        )


def get_source(id: str) -> Optional[Dict]:
    """Return source row by id, or None if not found."""
    with _adb.get_db() as db:
        row = db.execute("SELECT * FROM sources WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        r = dict(row)
        if r.get("authors"):
            try:
                r["authors"] = json.loads(r["authors"])
            except (json.JSONDecodeError, TypeError):
                pass
        return r


def list_sources(
    kind: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """Return sources ordered by ingested_at DESC."""
    with _adb.get_db() as db:
        query = "SELECT * FROM sources WHERE 1=1"
        params: List[Any] = []
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += f" ORDER BY ingested_at DESC LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("authors"):
                try:
                    r["authors"] = json.loads(r["authors"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(r)
        return result


# ═══════════════════════════════════════════════════════════════════════════════
# claims
# ═══════════════════════════════════════════════════════════════════════════════

def insert_claim(
    id: str,
    source_id: str,
    strategy: str,
    *,
    universe: Optional[str] = None,
    regime_state: Optional[str] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    claimed_sharpe: Optional[float] = None,
    claimed_solo_sharpe: Optional[float] = None,
    claimed_max_dd_pct: Optional[float] = None,
    claimed_trades: Optional[int] = None,
    claimed_cagr_pct: Optional[float] = None,
    claimed_profit_factor: Optional[float] = None,
    claimed_avg_hold_days: Optional[float] = None,
    extraction_confidence: str = "medium",
    notes: Optional[str] = None,
) -> None:
    """Insert a claim.  Idempotent via INSERT OR IGNORE on id."""
    with _adb.get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO claims
                (id, source_id, strategy, universe, regime_state,
                 period_start, period_end,
                 claimed_sharpe, claimed_solo_sharpe, claimed_max_dd_pct,
                 claimed_trades, claimed_cagr_pct, claimed_profit_factor,
                 claimed_avg_hold_days, extraction_confidence, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (id, source_id, strategy, universe, regime_state,
             period_start, period_end,
             claimed_sharpe, claimed_solo_sharpe, claimed_max_dd_pct,
             claimed_trades, claimed_cagr_pct, claimed_profit_factor,
             claimed_avg_hold_days, extraction_confidence, notes),
        )


def get_claim(id: str) -> Optional[Dict]:
    with _adb.get_db() as db:
        row = db.execute("SELECT * FROM claims WHERE id = ?", (id,)).fetchone()
        return dict(row) if row is not None else None


def list_claims(
    strategy: Optional[str] = None,
    source_id: Optional[str] = None,
    status: Optional[str] = "active",
    limit: int = 100,
) -> List[Dict]:
    """Return claims filtered by strategy/source/status.

    Default status='active' hides dismissed/superseded claims.  Pass status=None
    to return all.
    """
    with _adb.get_db() as db:
        query = "SELECT * FROM claims WHERE 1=1"
        params: List[Any] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if source_id:
            query += " AND source_id = ?"
            params.append(source_id)
        if status is not None:
            query += " AND status = ?"
            params.append(status)
        query += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def list_shell_claims(
    *,
    require_local_pdf: bool = True,
    limit: int = 100,
) -> List[Dict]:
    """Return active claims with NULL claimed_sharpe -- candidates for Phase 1.5
    LLM metric extraction.  Joins sources so callers get the PDF path.

    Args:
        require_local_pdf: When True (default), only return claims whose source
            has a non-NULL local_path -- i.e. the PDF is on disk and can be
            text-extracted.  Pass False to include reference-only sources.
        limit: Max rows.
    """
    with _adb.get_db() as db:
        query = """
            SELECT
                c.id              AS claim_id,
                c.source_id       AS source_id,
                c.strategy        AS strategy,
                c.universe        AS universe,
                c.notes           AS notes,
                s.title           AS source_title,
                s.url             AS source_url,
                s.local_path      AS local_path,
                s.kind            AS source_kind
            FROM claims c
            JOIN sources s ON s.id = c.source_id
            WHERE c.status = 'active'
              AND c.claimed_sharpe IS NULL
              AND c.claimed_max_dd_pct IS NULL
              AND c.claimed_cagr_pct IS NULL
        """
        if require_local_pdf:
            query += " AND s.local_path IS NOT NULL"
        query += f" ORDER BY c.created_at ASC LIMIT {int(limit)}"
        rows = db.execute(query).fetchall()
        return [dict(r) for r in rows]


def update_claim_metrics(
    id: str,
    *,
    claimed_sharpe: Optional[float] = None,
    claimed_solo_sharpe: Optional[float] = None,
    claimed_max_dd_pct: Optional[float] = None,
    claimed_trades: Optional[int] = None,
    claimed_cagr_pct: Optional[float] = None,
    claimed_profit_factor: Optional[float] = None,
    claimed_avg_hold_days: Optional[float] = None,
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
    extraction_confidence: Optional[str] = None,
    notes: Optional[str] = None,
) -> None:
    """Populate the claimed_* columns on an existing claim row.

    Any field passed as None leaves the existing value untouched (COALESCE).
    Bumps updated_at on every call.  Designed for Phase 1.5 LLM extraction
    pass that fills in metrics on previously-inserted shell claims.

    After the UPDATE commits, fires sync_contradictions(strategy=...) so the
    contradictions table reflects the new metric immediately.  Defensive --
    a sync error is logged but does not propagate (callers expect this
    function to never raise).
    """
    strategy: Optional[str] = None
    with _adb.get_db() as db:
        row = db.execute(
            "SELECT strategy FROM claims WHERE id = ?", (id,)
        ).fetchone()
        if row is not None:
            strategy = row["strategy"]

        db.execute(
            """
            UPDATE claims SET
                claimed_sharpe         = COALESCE(?, claimed_sharpe),
                claimed_solo_sharpe    = COALESCE(?, claimed_solo_sharpe),
                claimed_max_dd_pct     = COALESCE(?, claimed_max_dd_pct),
                claimed_trades         = COALESCE(?, claimed_trades),
                claimed_cagr_pct       = COALESCE(?, claimed_cagr_pct),
                claimed_profit_factor  = COALESCE(?, claimed_profit_factor),
                claimed_avg_hold_days  = COALESCE(?, claimed_avg_hold_days),
                period_start           = COALESCE(?, period_start),
                period_end             = COALESCE(?, period_end),
                extraction_confidence  = COALESCE(?, extraction_confidence),
                notes                  = COALESCE(?, notes),
                updated_at             = datetime('now')
            WHERE id = ?
            """,
            (claimed_sharpe, claimed_solo_sharpe, claimed_max_dd_pct,
             claimed_trades, claimed_cagr_pct, claimed_profit_factor,
             claimed_avg_hold_days, period_start, period_end,
             extraction_confidence, notes, id),
        )

    if strategy is not None:
        try:
            sync_contradictions(strategy=strategy)
        except Exception as exc:  # noqa: BLE001 -- intentionally swallow
            _log.warning(
                "sync_contradictions hook failed for claim_id=%s strategy=%s: %s",
                id, strategy, exc,
            )


def dismiss_claim(id: str, reason: str) -> None:
    """Mark a claim as dismissed.  Removes it from active-contradiction surfaces."""
    with _adb.get_db() as db:
        db.execute(
            """
            UPDATE claims
            SET status = 'dismissed',
                dismissed_reason = ?,
                updated_at = datetime('now')
            WHERE id = ?
            """,
            (reason, id),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# contradictions
# ═══════════════════════════════════════════════════════════════════════════════

def sync_contradictions(strategy: Optional[str] = None) -> Dict[str, int]:
    """Materialise new contradictions from v_candidate_contradictions.

    For each row in the view where severity IS NOT NULL, INSERT OR IGNORE
    into contradictions (uniqueness on (claim_id, metric)).  Existing rows
    have their last_checked_at refreshed.

    Args:
        strategy: If provided, only sync rows for this strategy (cheap incremental
            update after a research_best upsert).

    Returns:
        {"inserted": N, "rechecked": M} -- new and existing rows respectively.
    """
    select_sql = """
        SELECT claim_id, strategy, universe, metric, claimed_value, measured_value,
               delta, delta_abs, severity
        FROM v_candidate_contradictions
        WHERE severity IS NOT NULL
    """
    params: List[Any] = []
    if strategy is not None:
        select_sql += " AND strategy = ?"
        params.append(strategy)

    with _adb.get_db() as db:
        candidates = db.execute(select_sql, params).fetchall()

        inserted = 0
        rechecked = 0
        for row in candidates:
            cur = db.execute(
                """
                INSERT OR IGNORE INTO contradictions
                    (claim_id, strategy, universe, metric, claimed_value,
                     measured_value, delta, delta_abs, severity)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row["claim_id"], row["strategy"], row["universe"], row["metric"],
                 row["claimed_value"], row["measured_value"],
                 row["delta"], row["delta_abs"], row["severity"]),
            )
            if cur.rowcount > 0:
                inserted += 1
            else:
                # Existing row: refresh last_checked_at on the unresolved match.
                upd = db.execute(
                    """
                    UPDATE contradictions
                    SET last_checked_at = datetime('now'),
                        measured_value  = ?,
                        delta           = ?,
                        delta_abs       = ?,
                        severity        = ?
                    WHERE claim_id = ? AND metric = ? AND resolution IS NULL
                    """,
                    (row["measured_value"], row["delta"], row["delta_abs"],
                     row["severity"], row["claim_id"], row["metric"]),
                )
                if upd.rowcount > 0:
                    rechecked += 1

        return {"inserted": inserted, "rechecked": rechecked}


def get_open_contradictions(
    strategy: Optional[str] = None,
    severity: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """Return unresolved contradictions joined to source info, severity-ordered."""
    with _adb.get_db() as db:
        query = "SELECT * FROM v_open_contradictions WHERE 1=1"
        params: List[Any] = []
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        query += f" LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def resolve_contradiction(
    contradiction_id: int,
    resolution: str,
    note: Optional[str] = None,
) -> None:
    """Mark a contradiction as resolved.

    resolution: 'retested' | 'claim_rejected' | 'measurement_corrected' | 'deferred'
    """
    valid = {"retested", "claim_rejected", "measurement_corrected", "deferred"}
    if resolution not in valid:
        raise ValueError(f"resolution must be one of {sorted(valid)}, got {resolution!r}")

    with _adb.get_db() as db:
        db.execute(
            """
            UPDATE contradictions
            SET resolution      = ?,
                resolution_note = ?,
                resolved_at     = datetime('now')
            WHERE id = ?
            """,
            (resolution, note, contradiction_id),
        )


# Phase 3 note:
# Lifecycle state transitions live in the existing strategy_lifecycle_history
# table.  set_lifecycle_state() (db/lifecycle.py) writes there on every
# transition; monitor/strategy_lifecycle.transition() is the public entry point.
# The Phase 0 lifecycle_events table was redundant and has been removed.


# ═══════════════════════════════════════════════════════════════════════════════
# digest history
# ═══════════════════════════════════════════════════════════════════════════════

def log_digest(
    kind: str,
    *,
    new_papers: int = 0,
    new_experiments: int = 0,
    new_contradictions: int = 0,
    lifecycle_transitions: int = 0,
    summary: Optional[str] = None,
    delivery_status: Optional[str] = None,
    payload: Optional[Dict[str, Any]] = None,
) -> int:
    """Record a digest send.  Returns the new row id."""
    payload_json = json.dumps(payload) if payload is not None else None
    with _adb.get_db() as db:
        cur = db.execute(
            """
            INSERT INTO digest_history
                (kind, new_papers, new_experiments, new_contradictions,
                 lifecycle_transitions, summary, delivery_status, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (kind, new_papers, new_experiments, new_contradictions,
             lifecycle_transitions, summary, delivery_status, payload_json),
        )
        return cur.lastrowid


def get_last_digest(kind: Optional[str] = None) -> Optional[Dict]:
    """Return the most recent digest row, optionally filtered by kind."""
    with _adb.get_db() as db:
        query = "SELECT * FROM digest_history WHERE 1=1"
        params: List[Any] = []
        if kind:
            query += " AND kind = ?"
            params.append(kind)
        query += " ORDER BY sent_at DESC, id DESC LIMIT 1"
        row = db.execute(query, params).fetchone()
        if row is None:
            return None
        r = dict(row)
        if r.get("payload"):
            try:
                r["payload"] = json.loads(r["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return r


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6: SQL mirrors of queue.json / journal.json
#
# These functions are called from research/models.py when the dual-write env
# vars are set.  They are intentionally tolerant: a malformed payload logs a
# warning and skips the row rather than raising, because the JSON file is the
# canonical source of truth until the operator flips the read.
# ═══════════════════════════════════════════════════════════════════════════════

def _jsonify(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return None


def upsert_queue_mirror_row(entry_dict: Dict[str, Any]) -> None:
    """INSERT OR REPLACE one queue_mirror row from a QueueEntry.to_dict() payload.

    Idempotent on the entry id; subsequent calls (status updates, claims) just
    overwrite the row.  Caller MUST hold the parent JSON's lock so the mirror
    can't end up newer than the canonical record.
    """
    method = entry_dict.get("method")
    if hasattr(method, "value"):  # ExperimentType enum
        method = method.value

    with _adb.get_db() as db:
        db.execute(
            """
            INSERT OR REPLACE INTO queue_mirror
                (id, title, category, market, hypothesis, method,
                 acceptance_criteria, estimated_runtime_min, priority, status,
                 strategy_name, params_override, config_snapshot,
                 claimed_by, claimed_at, tags, depends_on, notes,
                 payload, created_at, updated_at, mirrored_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
            (
                entry_dict.get("id"),
                entry_dict.get("title", ""),
                entry_dict.get("category", ""),
                entry_dict.get("market", ""),
                entry_dict.get("hypothesis"),
                method or "",
                _jsonify(entry_dict.get("acceptance_criteria")),
                int(entry_dict.get("estimated_runtime_min") or 0),
                entry_dict.get("priority", ""),
                entry_dict.get("status", "queued"),
                entry_dict.get("strategy_name"),
                _jsonify(entry_dict.get("params_override")),
                _jsonify(entry_dict.get("config_snapshot")),
                entry_dict.get("claimed_by"),
                entry_dict.get("claimed_at"),
                _jsonify(entry_dict.get("tags")),
                _jsonify(entry_dict.get("depends_on")),
                entry_dict.get("notes", ""),
                json.dumps(entry_dict, default=str),
                entry_dict.get("created_at") or "",
                entry_dict.get("updated_at") or entry_dict.get("created_at") or "",
            ),
        )


def list_queue_mirror_rows(
    status: Optional[str] = None,
    strategy_name: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 200,
) -> List[Dict]:
    """Return queue_mirror rows ordered by priority then created_at."""
    with _adb.get_db() as db:
        query = "SELECT * FROM queue_mirror WHERE 1=1"
        params: List[Any] = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if strategy_name:
            query += " AND strategy_name = ?"
            params.append(strategy_name)
        if category:
            query += " AND category = ?"
            params.append(category)
        query += f" ORDER BY priority, created_at LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        out: List[Dict] = []
        for row in rows:
            r = dict(row)
            for k in ("acceptance_criteria", "params_override", "config_snapshot",
                       "tags", "depends_on", "payload"):
                if r.get(k):
                    try:
                        r[k] = json.loads(r[k])
                    except (json.JSONDecodeError, TypeError):
                        pass
            out.append(r)
        return out


def count_queue_mirror_rows(status: Optional[str] = None) -> int:
    with _adb.get_db() as db:
        if status:
            row = db.execute(
                "SELECT COUNT(*) AS n FROM queue_mirror WHERE status = ?", (status,),
            ).fetchone()
        else:
            row = db.execute("SELECT COUNT(*) AS n FROM queue_mirror").fetchone()
        return int(row["n"]) if row else 0


def insert_journal_mirror_row(entry_dict: Dict[str, Any]) -> None:
    """INSERT OR IGNORE one journal_mirror row from a JournalEntry.to_dict() payload.

    Idempotent via UNIQUE(experiment_id, timestamp).  Multiple journal entries
    per experiment are allowed as long as their timestamps differ.
    """
    promoted_int = 1 if entry_dict.get("promoted") else 0
    with _adb.get_db() as db:
        db.execute(
            """
            INSERT OR IGNORE INTO journal_mirror
                (experiment_id, timestamp, market, category, strategy,
                 hypothesis, verdict, key_metrics, delta_vs_baseline,
                 learnings, promoted, runtime_s, agent_id, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                entry_dict.get("experiment_id", ""),
                entry_dict.get("timestamp", ""),
                entry_dict.get("market", ""),
                entry_dict.get("category", ""),
                entry_dict.get("strategy"),
                entry_dict.get("hypothesis"),
                entry_dict.get("verdict"),
                _jsonify(entry_dict.get("key_metrics")),
                _jsonify(entry_dict.get("delta_vs_baseline")),
                _jsonify(entry_dict.get("learnings")),
                promoted_int,
                entry_dict.get("runtime_s"),
                entry_dict.get("agent_id"),
                json.dumps(entry_dict, default=str),
            ),
        )


def list_journal_mirror_rows(
    experiment_id: Optional[str] = None,
    strategy: Optional[str] = None,
    verdict: Optional[str] = None,
    limit: int = 100,
) -> List[Dict]:
    """Return journal_mirror rows ordered by timestamp DESC."""
    with _adb.get_db() as db:
        query = "SELECT * FROM journal_mirror WHERE 1=1"
        params: List[Any] = []
        if experiment_id:
            query += " AND experiment_id = ?"
            params.append(experiment_id)
        if strategy:
            query += " AND strategy = ?"
            params.append(strategy)
        if verdict:
            query += " AND verdict = ?"
            params.append(verdict)
        query += f" ORDER BY timestamp DESC, id DESC LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        out: List[Dict] = []
        for row in rows:
            r = dict(row)
            for k in ("key_metrics", "delta_vs_baseline", "learnings", "payload"):
                if r.get(k):
                    try:
                        r[k] = json.loads(r[k])
                    except (json.JSONDecodeError, TypeError):
                        pass
            out.append(r)
        return out


def count_journal_mirror_rows() -> int:
    with _adb.get_db() as db:
        row = db.execute("SELECT COUNT(*) AS n FROM journal_mirror").fetchone()
        return int(row["n"]) if row else 0
