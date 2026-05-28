#!/usr/bin/env python3
"""Phase 3 backfill: data/promotion_log.json -> strategy_lifecycle_history.

Reads the historical JSON audit log and inserts any rows that aren't already
present in strategy_lifecycle_history.  Preserves the original ts on the
backfilled rows so the audit timeline stays accurate.

Idempotent.  Existence check (in order):
  1. Match by auto_promotion_id when present.
  2. Fall back to natural key: (strategy, universe, transitioned_at, to_state).

Run:
    python3 scripts/backfill_lifecycle_history.py                 # dry-run
    python3 scripts/backfill_lifecycle_history.py --apply
    python3 scripts/backfill_lifecycle_history.py --apply --log-path data/promotion_log.json

Prints a JSON summary.  Appends per-run line to logs/backfill_lifecycle_history.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

DEFAULT_LOG_PATH = _ATLAS_ROOT / "data" / "promotion_log.json"
LOG_PATH = _ATLAS_ROOT / "logs" / "backfill_lifecycle_history.log"


def _setup_logging(verbose: bool) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )


def _row_exists(conn, *, strategy: str, universe: str, transitioned_at: str,
                to_state: str, auto_promotion_id: Optional[str]) -> bool:
    """Existence check: prefer auto_promotion_id, fall back to natural key."""
    if auto_promotion_id:
        row = conn.execute(
            "SELECT 1 FROM strategy_lifecycle_history "
            "WHERE auto_promotion_id = ?",
            (auto_promotion_id,),
        ).fetchone()
        if row is not None:
            return True
    row = conn.execute(
        "SELECT 1 FROM strategy_lifecycle_history "
        "WHERE strategy = ? AND universe = ? AND transitioned_at = ? AND to_state = ?",
        (strategy, universe, transitioned_at, to_state),
    ).fetchone()
    return row is not None


def _to_history_row(entry: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Validate a promotion_log entry and map it to history-row kwargs.

    Returns (row_kwargs, error_or_None).  row_kwargs is None on validation
    failure; error_or_None is a short tag describing why.
    """
    if not isinstance(entry, dict):
        return None, "not_a_dict"
    strategy = entry.get("strategy")
    universe = entry.get("universe")
    to_state = entry.get("to_state")
    ts = entry.get("ts")
    if not (strategy and universe and to_state and ts):
        return None, "missing_required_field"

    # gate_results is stored as JSON text; promotion_log entries usually carry
    # metric numbers (paper_sharpe, research_sharpe, gap, ...) but no explicit
    # per-gate pass/fail.  Stash the whole entry as the gate_results payload
    # so the structured data survives -- operators / wiki materializer can
    # re-derive A/B/C/D/F from it, future readers see the full audit trail.
    gate_results_payload = {
        k: v for k, v in entry.items()
        if k in ("paper_sharpe", "research_sharpe", "gap", "paper_trades",
                 "days_in_paper", "note", "consecutive_breach_days")
    }
    gate_results_json = (
        json.dumps(gate_results_payload) if gate_results_payload else None
    )

    return {
        "strategy": strategy,
        "universe": universe,
        "from_state": entry.get("from_state"),
        "to_state": to_state,
        "transitioned_at": ts,
        "reason": entry.get("reason") or "backfill: from data/promotion_log.json",
        "auto_promotion_id": entry.get("auto_promotion_id"),
        # The promotion_log doesn't store the "system"/"manual"/"rollback" tag
        # historically -- infer from the path the entry came through.
        "operator": "rollback" if (entry.get("from_state") == "PAPER"
                                    and entry.get("to_state") == "RESEARCH")
                                  else "system",
        "gate_results": gate_results_json,
        "experiment_id": None,
    }, None


def _insert_history_row(conn, row: Dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO strategy_lifecycle_history
            (strategy, universe, from_state, to_state, transitioned_at,
             reason, auto_promotion_id, operator,
             gate_results, experiment_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (row["strategy"], row["universe"], row["from_state"], row["to_state"],
         row["transitioned_at"], row["reason"], row["auto_promotion_id"],
         row["operator"], row["gate_results"], row["experiment_id"]),
    )


def run_backfill(log_path: Path, *, apply: bool) -> Dict[str, Any]:
    """Read log_path, plan/apply inserts.  Returns a summary dict."""
    log = logging.getLogger("backfill_lifecycle_history")

    if not log_path.exists():
        log.info("promotion log not found: %s -- nothing to backfill", log_path)
        return {
            "log_path": str(log_path),
            "log_present": False,
            "entries": 0,
            "would_insert": 0,
            "inserted": 0,
            "skipped_existing": 0,
            "skipped_invalid": 0,
        }

    try:
        raw = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        log.error("Failed to read %s: %s", log_path, exc)
        return {
            "log_path": str(log_path),
            "log_present": True,
            "read_error": str(exc),
            "entries": 0, "would_insert": 0, "inserted": 0,
            "skipped_existing": 0, "skipped_invalid": 0,
        }

    if not isinstance(raw, list):
        log.error("Top-level of %s is not a list (got %s) -- aborting", log_path, type(raw))
        return {
            "log_path": str(log_path),
            "log_present": True,
            "shape_error": "not_a_list",
            "entries": 0, "would_insert": 0, "inserted": 0,
            "skipped_existing": 0, "skipped_invalid": 0,
        }

    from db.atlas_db import get_db

    would_insert = 0
    inserted = 0
    skipped_existing = 0
    skipped_invalid = 0
    errors: List[str] = []

    with get_db() as conn:
        for n, entry in enumerate(raw):
            row, err = _to_history_row(entry)
            if row is None:
                log.warning("entry[%d]: invalid (%s) -- skipping", n, err)
                skipped_invalid += 1
                continue

            if _row_exists(conn,
                           strategy=row["strategy"], universe=row["universe"],
                           transitioned_at=row["transitioned_at"],
                           to_state=row["to_state"],
                           auto_promotion_id=row["auto_promotion_id"]):
                skipped_existing += 1
                continue

            if apply:
                try:
                    _insert_history_row(conn, row)
                    inserted += 1
                except Exception as exc:  # noqa: BLE001 -- log and continue
                    log.error("entry[%d]: insert failed: %s", n, exc)
                    errors.append(f"entry[{n}]: {exc}")
            else:
                would_insert += 1

    return {
        "log_path": str(log_path),
        "log_present": True,
        "entries": len(raw),
        "would_insert": would_insert,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "skipped_invalid": skipped_invalid,
        "errors": errors,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Insert missing rows (default: dry-run)")
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH),
                        help=f"Path to promotion_log.json (default: {DEFAULT_LOG_PATH})")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("backfill_lifecycle_history")
    log.info("Lifecycle history backfill")
    log.info("  log_path: %s", args.log_path)
    log.info("  mode:     %s", "APPLY" if args.apply else "DRY-RUN")

    summary = run_backfill(Path(args.log_path), apply=args.apply)
    summary["mode"] = "apply" if args.apply else "dry-run"
    print(json.dumps(summary, indent=2))
    return 0 if not summary.get("errors") else 1


if __name__ == "__main__":
    sys.exit(main())
