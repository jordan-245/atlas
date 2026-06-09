"""db/research — Research experiments and best-params CRUD.

All public functions are re-exported through db.atlas_db for backward compat.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import db.atlas_db as _adb

__all__ = [
    "record_experiment",
    "get_experiments",
    "update_experiment_status",
    "upsert_research_best",
    "get_research_best",
]

_log = logging.getLogger(__name__)


def record_experiment(
    id: str,
    strategy: str,
    universe: str = "sp500",
    experiment_type: Optional[str] = None,
    params_changed: Optional[Dict] = None,
    description: Optional[str] = None,
    sharpe: Optional[float] = None,
    trades: Optional[int] = None,
    max_dd_pct: Optional[float] = None,
    profit_factor: Optional[float] = None,
    cagr_pct: Optional[float] = None,
    status: str = "running",
    recommendation: Optional[str] = None,
    baseline_sharpe: Optional[float] = None,
    runtime_s: Optional[float] = None,
    agent_id: Optional[str] = None,
    completed_at: Optional[str] = None,
) -> None:
    """Insert a new research experiment."""
    with _adb.get_db() as db:
        db.execute(
            """
            INSERT INTO research_experiments
                (id, strategy, universe, experiment_type, params_changed, description,
                 sharpe, trades, max_dd_pct, profit_factor, cagr_pct, status,
                 recommendation, baseline_sharpe, runtime_s, agent_id, completed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                id, strategy, universe, experiment_type,
                json.dumps(params_changed) if params_changed is not None else None,
                description, sharpe, trades, max_dd_pct, profit_factor, cagr_pct,
                status, recommendation, baseline_sharpe, runtime_s, agent_id,
                completed_at,
            ),
        )


def get_experiments(
    strategy: Optional[str] = None,
    status: Optional[str] = None,
    universe: Optional[str] = None,
    limit: int = 50,
) -> List[Dict]:
    """Return research experiments, most recent first."""
    with _adb.get_db() as db:
        query = "SELECT * FROM research_experiments WHERE 1=1"
        params: List[Any] = []
        if strategy:
            query += " AND strategy=?"
            params.append(strategy)
        if status:
            query += " AND status=?"
            params.append(status)
        if universe:
            query += " AND universe=?"
            params.append(universe)
        query += f" ORDER BY created_at DESC LIMIT {int(limit)}"
        rows = db.execute(query, params).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("params_changed"):
                try:
                    r["params_changed"] = json.loads(r["params_changed"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(r)
        return result


def update_experiment_status(
    experiment_id: str,
    status: str,
    recommendation: Optional[str] = None,
    sharpe: Optional[float] = None,
    trades: Optional[int] = None,
    max_dd_pct: Optional[float] = None,
    profit_factor: Optional[float] = None,
    cagr_pct: Optional[float] = None,
    runtime_s: Optional[float] = None,
    completed_at: Optional[str] = None,
) -> None:
    """Update the status and results of a research experiment."""
    with _adb.get_db() as db:
        db.execute(
            """
            UPDATE research_experiments
            SET status          = ?,
                recommendation  = COALESCE(?, recommendation),
                sharpe          = COALESCE(?, sharpe),
                trades          = COALESCE(?, trades),
                max_dd_pct      = COALESCE(?, max_dd_pct),
                profit_factor   = COALESCE(?, profit_factor),
                cagr_pct        = COALESCE(?, cagr_pct),
                runtime_s       = COALESCE(?, runtime_s),
                completed_at    = COALESCE(?, completed_at)
            WHERE id = ?
            """,
            (
                status, recommendation, sharpe, trades, max_dd_pct,
                profit_factor, cagr_pct, runtime_s,
                completed_at or datetime.now().isoformat(),
                experiment_id,
            ),
        )


def upsert_research_best(
    strategy: str,
    universe: str,
    params: Dict,
    sharpe: Optional[float] = None,
    trades: Optional[int] = None,
    max_dd_pct: Optional[float] = None,
    solo_sharpe: Optional[float] = None,
    portfolio_sharpe: Optional[float] = None,
    metric_type: Optional[str] = None,
    regime_state: Optional[str] = None,
    oos_sharpe: Optional[float] = None,
    oos_trades: Optional[int] = None,
    oos_cagr: Optional[float] = None,
    oos_max_dd: Optional[float] = None,
) -> None:
    """Insert or replace the best known parameters for (strategy, universe[, regime_state])."""
    # Compute metric_type if not supplied
    if metric_type is None:
        if solo_sharpe is not None and portfolio_sharpe is not None:
            metric_type = "both"
        elif solo_sharpe is not None:
            metric_type = "solo"
        elif portfolio_sharpe is not None:
            metric_type = "portfolio"

    if sharpe is not None and solo_sharpe is None and portfolio_sharpe is None:
        _log.debug(
            "research_best.sharpe is deprecated -- use solo_sharpe / portfolio_sharpe "
            "(strategy=%s universe=%s). Writing legacy-only row.",
            strategy, universe,
        )

    params_json = json.dumps(params)

    # Columns are written defensively against the *live* schema so this writer
    # works against the current research_best layout (composite PK incl.
    # regime_state + solo/portfolio/oos columns) as well as older schemas used
    # by temp-DB test fixtures and pre-regime_state backups.  We never assume a
    # column exists -- a missing column simply drops out of the statement.
    #   * overwrite_cols  -- always replaced with the supplied value (incl.
    #                        oos_* which callers explicitly clear by passing
    #                        None when OOS is not recomputed)
    #   * preserve_cols   -- COALESCE(new, existing): a re-sweep that does not
    #                        recompute solo/portfolio Sharpe must not clobber a
    #                        previously measured value with NULL
    _overwrite_cols = (
        "params", "sharpe", "trades", "max_dd_pct",
        "oos_sharpe", "oos_trades", "oos_cagr", "oos_max_dd",
    )
    _preserve_cols = ("solo_sharpe", "portfolio_sharpe")
    _values = {
        "params": params_json,
        "sharpe": sharpe,
        "trades": trades,
        "max_dd_pct": max_dd_pct,
        "solo_sharpe": solo_sharpe,
        "portfolio_sharpe": portfolio_sharpe,
        "oos_sharpe": oos_sharpe,
        "oos_trades": oos_trades,
        "oos_cagr": oos_cagr,
        "oos_max_dd": oos_max_dd,
    }

    with _adb.get_db() as db:
        existing_cols = {
            row[1] for row in db.execute("PRAGMA table_info(research_best)").fetchall()
        }
        has_regime = "regime_state" in existing_cols
        has_metric_type = "metric_type" in existing_cols
        has_updated_at = "updated_at" in existing_cols

        # Row-match predicate.  IS NULL handles the cross-regime row correctly
        # (SQLite treats NULL != NULL, so ON CONFLICT/REPLACE can't target it).
        if has_regime and regime_state is None:
            match_clause = "strategy=? AND universe=? AND regime_state IS NULL"
            match_params: List[Any] = [strategy, universe]
        elif has_regime:
            match_clause = "strategy=? AND universe=? AND regime_state=?"
            match_params = [strategy, universe, regime_state]
        else:
            match_clause = "strategy=? AND universe=?"
            match_params = [strategy, universe]

        # ---- UPDATE first so preserve_cols can COALESCE onto existing values ----
        set_parts: List[str] = []
        set_params: List[Any] = []
        for col in _overwrite_cols:
            if col in existing_cols:
                set_parts.append(f"{col}=?")
                set_params.append(_values[col])
        for col in _preserve_cols:
            if col in existing_cols:
                set_parts.append(f"{col}=COALESCE(?, {col})")
                set_params.append(_values[col])
        if has_metric_type:
            set_parts.append("metric_type=COALESCE(?, metric_type, 'unknown')")
            set_params.append(metric_type)
        if has_updated_at:
            set_parts.append("updated_at=datetime('now')")

        updated = 0
        if set_parts:
            cur = db.execute(
                f"UPDATE research_best SET {', '.join(set_parts)} WHERE {match_clause}",
                set_params + match_params,
            )
            updated = cur.rowcount or 0

        # ---- INSERT a fresh row when nothing matched ----
        if updated == 0:
            insert_cols: List[str] = ["strategy", "universe"]
            insert_ph: List[str] = ["?", "?"]
            insert_vals: List[Any] = [strategy, universe]
            if has_regime:
                insert_cols.append("regime_state")
                insert_ph.append("?")
                insert_vals.append(regime_state)
            for col in _overwrite_cols + _preserve_cols:
                if col in existing_cols:
                    insert_cols.append(col)
                    insert_ph.append("?")
                    insert_vals.append(_values[col])
            if has_metric_type:
                insert_cols.append("metric_type")
                insert_ph.append("COALESCE(?, 'unknown')")
                insert_vals.append(metric_type)
            if has_updated_at:
                insert_cols.append("updated_at")
                insert_ph.append("datetime('now')")
            db.execute(
                f"INSERT INTO research_best ({', '.join(insert_cols)}) "
                f"VALUES ({', '.join(insert_ph)})",
                insert_vals,
            )

    # Knowledge-layer hook: refresh contradictions for this strategy after the
    # measured row changed.  Scoped to one strategy = cheap.  Defensive: any
    # error here MUST NOT break the upsert path -- 13+ callers rely on this.
    try:
        from db.knowledge import sync_contradictions  # noqa: PLC0415 -- avoid import cycle at module load
        sync_contradictions(strategy=strategy)
    except Exception as exc:  # noqa: BLE001 -- intentionally swallow
        _log.warning(
            "sync_contradictions hook failed for strategy=%s universe=%s: %s",
            strategy, universe, exc,
        )


def get_research_best(
    strategy: Optional[str] = None,
    universe: Optional[str] = None,
    regime_state: Optional[str] = None,
    fallback_to_cross_regime: bool = True,
) -> List[Dict]:
    """Return research_best rows, optionally filtered."""

    def _fetch(db: Any, extra_clause: str, extra_params: List[Any]) -> List[Dict]:
        query = "SELECT * FROM research_best WHERE 1=1"
        qparams: List[Any] = []
        if strategy:
            query += " AND strategy=?"
            qparams.append(strategy)
        if universe:
            query += " AND universe=?"
            qparams.append(universe)
        query += extra_clause
        qparams.extend(extra_params)
        query += " ORDER BY strategy, universe"
        rows = db.execute(query, qparams).fetchall()
        result = []
        for row in rows:
            r = dict(row)
            if r.get("params"):
                try:
                    r["params"] = json.loads(r["params"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(r)
        return result

    with _adb.get_db() as db:
        if regime_state is None:
            return _fetch(db, " AND regime_state IS NULL", [])
        else:
            rows = _fetch(db, " AND regime_state=?", [regime_state])
            if not rows and fallback_to_cross_regime:
                rows = _fetch(db, " AND regime_state IS NULL", [])
            return rows
