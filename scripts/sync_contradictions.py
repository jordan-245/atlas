#!/usr/bin/env python3
"""Manual contradiction-sync trigger.

The sync runs automatically on every research_best upsert and every claim
metric update (Phase 2 hooks).  This script is for operator-driven full
resyncs -- e.g. after a bulk import, after editing the severity thresholds
in the v_candidate_contradictions view, or for one-off diagnosis.

Run:
    python3 scripts/sync_contradictions.py                        # dry-run summary
    python3 scripts/sync_contradictions.py --apply                # full resync
    python3 scripts/sync_contradictions.py --apply --strategy donchian_breakout

Prints a JSON summary; appends per-run line to logs/sync_contradictions.log.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

LOG_PATH = _ATLAS_ROOT / "logs" / "sync_contradictions.log"


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Run the sync (default: dry-run)")
    parser.add_argument("--strategy", default=None,
                        help="Restrict sync to one strategy (default: all)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("sync_contradictions")

    from db.atlas_db import get_db
    from db.knowledge import sync_contradictions

    log.info("Manual contradiction sync")
    log.info("  mode:     %s", "APPLY" if args.apply else "DRY-RUN")
    log.info("  strategy: %s", args.strategy or "<all>")

    # Always report the candidate population the sync would consider.
    with get_db() as db:
        cand_sql = """
            SELECT COUNT(*) AS n
            FROM v_candidate_contradictions
            WHERE severity IS NOT NULL
        """
        params: list = []
        if args.strategy:
            cand_sql += " AND strategy = ?"
            params.append(args.strategy)
        candidate_rows = db.execute(cand_sql, params).fetchone()["n"]

        open_sql = "SELECT COUNT(*) AS n FROM contradictions WHERE resolution IS NULL"
        open_params: list = []
        if args.strategy:
            open_sql += " AND strategy = ?"
            open_params.append(args.strategy)
        open_before = db.execute(open_sql, open_params).fetchone()["n"]

    log.info("  candidate_rows:  %d", candidate_rows)
    log.info("  open_before:     %d", open_before)

    if not args.apply:
        summary = {
            "mode": "dry-run",
            "strategy": args.strategy,
            "candidate_rows": candidate_rows,
            "open_before": open_before,
        }
        print(json.dumps(summary, indent=2))
        log.info("Dry-run complete -- re-run with --apply to materialise contradictions.")
        return 0

    result = sync_contradictions(strategy=args.strategy)
    log.info("  sync_contradictions: %s", result)

    with get_db() as db:
        open_after = db.execute(open_sql, open_params).fetchone()["n"]

    summary = {
        "mode": "apply",
        "strategy": args.strategy,
        "candidate_rows": candidate_rows,
        "open_before": open_before,
        "open_after": open_after,
        "inserted": result["inserted"],
        "rechecked": result["rechecked"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
