#!/usr/bin/env python3
"""Migration: seed strategy_lifecycle table for all known (strategy, universe) combos.

Rules (idempotent — skips rows that already have a state):
  1. If (strategy, universe) appears in config/active/<universe>.json with
     enabled=true → state=LIVE (pre-existing live strategy at lifecycle rollout).
  2. Else if it appears in research_best with ANY sharpe > 0 → state=RESEARCH.

Uses INSERT OR IGNORE semantics: re-running does NOT clobber existing rows.

Usage:
    python3 scripts/migrations/2026-05-06-seed-strategy-lifecycle.py           # dry-run
    python3 scripts/migrations/2026-05-06-seed-strategy-lifecycle.py --apply   # commit
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ───────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
ATLAS_ROOT = SCRIPT_DIR.parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from db.atlas_db import get_db, init_db, _db_path_override, DB_PATH  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

SEED_DATE = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
ACTIVE_CONFIG_DIR = ATLAS_ROOT / "config" / "active"


def _load_live_strategies(active_config_dir: Path) -> set[tuple[str, str]]:
    """Scan all config/active/*.json; return set of (strategy, universe) with enabled=True."""
    live: set[tuple[str, str]] = set()
    for json_path in sorted(active_config_dir.glob("*.json")):
        universe = json_path.stem
        if universe in ("regime",):
            continue  # regime.json has no strategies block
        try:
            config = json.loads(json_path.read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Skipping %s (parse error: %s)", json_path.name, exc)
            continue
        strategies = config.get("strategies", {})
        for strat_name, strat_cfg in strategies.items():
            if strat_cfg.get("enabled", False):
                live.add((strat_name, universe))
    return live


def _load_research_best_combos(db_path: str) -> set[tuple[str, str]]:
    """Return (strategy, universe) combos in research_best with sharpe > 0 (any regime)."""
    combos: set[tuple[str, str]] = set()
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT DISTINCT strategy, universe FROM research_best WHERE sharpe > 0"
        ).fetchall()
        conn.close()
        for r in rows:
            combos.add((r["strategy"], r["universe"]))
    except sqlite3.Error as exc:
        logger.warning("research_best query failed: %s", exc)
    return combos


def run_migration(
    apply: bool,
    active_config_dir: Path = ACTIVE_CONFIG_DIR,
    db_path: str | None = None,
) -> dict[str, int]:
    """Execute (or dry-run) the seed migration.

    Returns a summary dict: {'live': N, 'research': M, 'skipped': K}.
    """
    effective_db = db_path or _db_path_override or str(DB_PATH)

    live_combos = _load_live_strategies(active_config_dir)
    research_combos = _load_research_best_combos(effective_db)

    # Build plan: live first, then research (exclude already-marked-live)
    plan: list[tuple[str, str, str, str]] = []  # (strategy, universe, state, reason)

    for strat, univ in sorted(live_combos):
        plan.append((
            strat, univ, "LIVE",
            "Migration: pre-existing live strategy at lifecycle rollout 2026-05-06",
        ))

    for strat, univ in sorted(research_combos - live_combos):
        plan.append((
            strat, univ, "RESEARCH",
            "Migration: research-discovered strategy at lifecycle rollout 2026-05-06",
        ))

    if not apply:
        print(f"\n{'[DRY-RUN]':=^60}")
        print(f"{'Strategy':<35} {'Universe':<20} {'State':<10}")
        print("-" * 65)
        live_n = 0
        research_n = 0
        for strat, univ, state, _ in plan:
            print(f"  {strat:<33} {univ:<20} {state}")
            if state == "LIVE":
                live_n += 1
            else:
                research_n += 1
        print(f"\nLIVE: {live_n}, RESEARCH: {research_n}, total: {live_n + research_n}")
        print("Re-run with --apply to commit.\n")
        return {"live": live_n, "research": research_n, "skipped": 0}

    # ── Apply ──────────────────────────────────────────────────────────────
    live_n = research_n = skipped_n = 0

    # Ensure schema exists (idempotent)
    init_db()

    with get_db() as db:
        for strat, univ, state, reason in plan:
            # Check if already tracked (INSERT OR IGNORE semantics)
            existing = db.execute(
                "SELECT state FROM strategy_lifecycle WHERE strategy = ? AND universe = ?",
                (strat, univ),
            ).fetchone()

            if existing is not None:
                logger.debug("  SKIP  (%s, %s) already has state=%s", strat, univ, existing["state"])
                skipped_n += 1
                continue

            # Insert lifecycle row
            db.execute(
                """INSERT OR IGNORE INTO strategy_lifecycle
                       (strategy, universe, state, entered_state_at,
                        prev_state, transition_reason, auto_promotion_id)
                   VALUES (?, ?, ?, ?, NULL, ?, NULL)
                """,
                (strat, univ, state, SEED_DATE, reason),
            )

            # Insert history row
            db.execute(
                """INSERT INTO strategy_lifecycle_history
                       (strategy, universe, from_state, to_state,
                        transitioned_at, reason, operator)
                   VALUES (?, ?, NULL, ?, ?, ?, 'system')
                """,
                (strat, univ, state, SEED_DATE, reason),
            )

            if state == "LIVE":
                live_n += 1
            else:
                research_n += 1
            logger.info("  SEED  (%s, %s) → %s", strat, univ, state)

    total = live_n + research_n
    print(f"\nMigration complete: LIVE={live_n}, RESEARCH={research_n}, SKIPPED={skipped_n}, total seeded={total}")
    return {"live": live_n, "research": research_n, "skipped": skipped_n}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true",
        help="Commit the migration. Default is dry-run.",
    )
    args = parser.parse_args(argv)
    run_migration(apply=args.apply)


if __name__ == "__main__":
    main()
