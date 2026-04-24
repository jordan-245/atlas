#!/usr/bin/env python3
"""Signal-write divergence watchdog.

Compares the number of proposed signals recorded in the daily plan JSON file(s)
against what was actually written to the SQLite ``signals`` table.  A
divergence larger than the tolerance threshold triggers an EXIT 1 and sends a
Telegram CRITICAL alert.

Background
----------
Commit d0b939d0 (P1-9) fixed a sp500 signal silent-failure where plan
generation completed successfully but signals were never persisted to SQLite,
going undetected for 10 days (2026-04-14 → 2026-04-24).  This script is the
recurrence guard: it is called from scripts/healthz_hourly.sh and will alert
within one hour if the same pattern re-occurs.

Exit codes
----------
0  — all universes with plan JSON data have matching SQLite row counts
1  — one or more universes show a divergence above tolerance

Usage
-----
    python3 scripts/check_signal_writes.py
    python3 scripts/check_signal_writes.py --date 2026-04-14
    python3 scripts/check_signal_writes.py --plans-dir /path/to/plans
    python3 scripts/check_signal_writes.py --db-path /path/to/atlas.db
    python3 scripts/check_signal_writes.py --tolerance 5
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Optional

# ── Path bootstrap ────────────────────────────────────────────────────────────
_ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

logger = logging.getLogger(__name__)

# Rows may differ by up to this many due to e.g. intraday re-runs or test rows.
DEFAULT_TOLERANCE = 2

# Default locations
DEFAULT_PLANS_DIR = _ATLAS_ROOT / "plans"
DEFAULT_DB_PATH = _ATLAS_ROOT / "data" / "atlas.db"


# ── Core check ────────────────────────────────────────────────────────────────

def check_signal_writes(
    date: Optional[datetime.date] = None,
    plans_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    tolerance: int = DEFAULT_TOLERANCE,
) -> list[tuple[str, str, int, int]]:
    """Return a list of diverging (universe, date_str, json_count, sqlite_count).

    An empty list means everything is consistent.

    Parameters
    ----------
    date:
        Calendar date to check.  Defaults to today.
    plans_dir:
        Directory containing ``plan_{universe}_{date}.json`` files.
    db_path:
        Path to the Atlas SQLite database.
    tolerance:
        Maximum allowed absolute difference before a divergence is reported.
    """
    date = date or datetime.date.today()
    plans_dir = plans_dir or DEFAULT_PLANS_DIR
    db_path = db_path or DEFAULT_DB_PATH
    date_str = date.isoformat()  # YYYY-MM-DD

    # ── Step 1: Discover plan JSONs for this date ─────────────────────────
    # Pattern: plan_{universe}_{date}.json
    plan_files = list(plans_dir.glob(f"plan_*_{date_str}.json"))
    if not plan_files:
        logger.info("check_signal_writes: no plan files found for %s — skipping", date_str)
        return []

    # ── Step 2: Count proposed entries from each plan JSON ────────────────
    json_counts: dict[str, int] = {}
    for pf in plan_files:
        try:
            data = json.loads(pf.read_text())
        except Exception as exc:
            logger.warning("check_signal_writes: failed to parse %s — %s", pf.name, exc)
            continue
        universe = data.get("market_id") or data.get("universe")
        if not universe:
            # Derive universe from file name: plan_{universe}_{date}.json
            stem = pf.stem  # e.g. "plan_sp500_2026-04-10"
            parts = stem.split("_", 1)  # ["plan", "sp500_2026-04-10"]
            if len(parts) == 2:
                universe = "_".join(parts[1].split("_")[:-3])  # strip date suffix
        if not universe:
            logger.warning("check_signal_writes: could not determine universe from %s", pf.name)
            continue
        proposed = data.get("proposed_entries") or []
        json_counts[universe] = len(proposed)
        logger.info(
            "check_signal_writes: plan JSON %s → universe=%s proposed=%d",
            pf.name, universe, len(proposed),
        )

    if not json_counts:
        return []

    # ── Step 3: Query SQLite for the same date ────────────────────────────
    sqlite_counts: dict[str, int] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            """
            SELECT universe, COUNT(*) AS cnt
            FROM   signals
            WHERE  DATE(timestamp) = ?
              AND  action = 'proposed'
            GROUP BY universe
            """,
            (date_str,),
        )
        for row in cur.fetchall():
            sqlite_counts[row["universe"]] = row["cnt"]
        conn.close()
    except Exception as exc:
        logger.error("check_signal_writes: SQLite query failed — %s", exc)
        # Treat as divergence so the operator knows something is wrong
        return [
            (u, date_str, json_counts[u], -1)
            for u in json_counts
        ]

    # ── Step 4: Compare ───────────────────────────────────────────────────
    divergences: list[tuple[str, str, int, int]] = []
    for universe, json_n in json_counts.items():
        sqlite_n = sqlite_counts.get(universe, 0)
        diff = abs(json_n - sqlite_n)
        logger.info(
            "check_signal_writes: %s %s json=%d sqlite=%d diff=%d (tolerance=%d)",
            universe, date_str, json_n, sqlite_n, diff, tolerance,
        )
        if diff > tolerance:
            divergences.append((universe, date_str, json_n, sqlite_n))

    return divergences


# ── Alert helper ──────────────────────────────────────────────────────────────

def _send_alert(lines: list[str]) -> None:
    """Send a Telegram CRITICAL alert.  Non-fatal if delivery fails."""
    try:
        from utils.telegram import send_message, tg_escape

        body = "\n".join(
            f"  \u2022 <code>{tg_escape(line)}</code>" for line in lines
        )
        msg = (
            "\U0001f6a8 <b>Signal-write divergence detected</b>\n\n"
            f"{body}\n\n"
            "<i>Signals may not have been persisted to SQLite.  "
            "Check the plan-generator logs and db/atlas.db.</i>"
        )
        send_message(msg)
        logger.info("check_signal_writes: Telegram alert sent")
    except Exception as exc:
        logger.warning("check_signal_writes: Telegram alert failed — %s", exc)


# ── CLI entry-point ───────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check that plan-JSON signal counts match SQLite signal rows.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date to check in YYYY-MM-DD format (default: today)",
    )
    parser.add_argument(
        "--plans-dir",
        default=str(DEFAULT_PLANS_DIR),
        help=f"Directory containing plan JSON files (default: {DEFAULT_PLANS_DIR})",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB_PATH),
        help=f"Path to SQLite database (default: {DEFAULT_DB_PATH})",
    )
    parser.add_argument(
        "--tolerance",
        type=int,
        default=DEFAULT_TOLERANCE,
        help=f"Max allowed row-count difference before alerting (default: {DEFAULT_TOLERANCE})",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    check_date: Optional[datetime.date] = None
    if args.date:
        try:
            check_date = datetime.date.fromisoformat(args.date)
        except ValueError as exc:
            logger.error("Invalid --date value: %s", exc)
            return 1

    try:
        divergences = check_signal_writes(
            date=check_date,
            plans_dir=Path(args.plans_dir),
            db_path=Path(args.db_path),
            tolerance=args.tolerance,
        )
    except Exception as exc:
        logger.error("check_signal_writes: unexpected error — %s", exc)
        return 1

    if not divergences:
        logger.info("check_signal_writes: OK — all signal counts match")
        return 0

    # Log CRITICAL for each divergence and collect alert lines
    alert_lines: list[str] = []
    for universe, date_str, json_n, sqlite_n in divergences:
        msg = (
            f"CRITICAL: signal write divergence {universe} {date_str} "
            f"json={json_n} sqlite={sqlite_n}"
        )
        logger.critical(msg)
        print(msg, file=sys.stderr)
        alert_lines.append(f"{universe} {date_str}: json={json_n} sqlite={sqlite_n}")

    _send_alert(alert_lines)
    return 1


if __name__ == "__main__":
    sys.exit(main())
