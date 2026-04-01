"""
regime/history.py — Historical regime backfill for Atlas.

Populates the ``regime_history`` table by running the RegimeModel classifier
on every trading day from a start date to the present.  Also exposes helpers
to inspect past regime transitions.

Usage
-----
    # Run full backfill (macro data + classification)
    python3 -m regime.history --start 2015-01-01 --force

    # Skip macro download, just classify what's already in DB
    python3 -m regime.history --start 2015-01-01

    # Only download macro data, skip classification
    python3 -m regime.history --start 2015-01-01 --macro-only

Programmatic usage::

    from regime.history import backfill_regime_history, print_regime_summary

    stats = backfill_regime_history("2015-01-01")
    print_regime_summary(stats)
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Ensure project root is on the path when run as __main__
_PROJECT = Path(__file__).resolve().parents[1]
if str(_PROJECT) not in sys.path:
    sys.path.insert(0, str(_PROJECT))

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Macro data helpers
# ──────────────────────────────────────────────────────────────────────────────


def backfill_macro_data(
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
) -> int:
    """Fetch and write all macro indicators for the date range to SQLite.

    Delegates to :func:`data.macro.backfill_macro_indicators` which pulls
    data from yfinance (VIX, SPY, gold, copper, yields) and FRED (credit
    OAS, DXY, fed funds, unemployment claims) and writes the results to the
    ``macro_indicators`` table via INSERT OR REPLACE.

    Args:
        start_date: First date to include ``'YYYY-MM-DD'`` (default: 2015-01-01).
        end_date:   Last date to include (default: today).

    Returns:
        Number of rows written to ``macro_indicators``.
    """
    from data.macro import backfill_macro_indicators

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    logger.info("backfill_macro_data: fetching [%s, %s]", start_date, end_date)
    df = backfill_macro_indicators(start_date=start_date, end_date=end_date)

    n = 0 if df is None or df.empty else len(df)
    logger.info("backfill_macro_data: %d rows written", n)
    return n


# ──────────────────────────────────────────────────────────────────────────────
# Regime history backfill
# ──────────────────────────────────────────────────────────────────────────────

#: How many rows in macro_indicators implies the table is "populated" and we
#: can skip the auto-backfill unless --force is given.
_MIN_MACRO_ROWS_THRESHOLD = 2000


def backfill_regime_history(
    start_date: str = "2015-01-01",
    end_date: Optional[str] = None,
    force_macro: bool = False,
) -> Dict:
    """Run RegimeModel.classify() on every trading day in the range.

    Reads indicator rows from the ``macro_indicators`` table, classifies each
    date, and writes results to the ``regime_history`` table.

    If the ``macro_indicators`` table has fewer than
    :data:`_MIN_MACRO_ROWS_THRESHOLD` rows (or *force_macro* is True) the
    macro data backfill is triggered first.

    Processing is done in chronological order so that
    ``RegimeModel._check_recent_bear()`` — which reads from ``regime_history``
    for the prior 25 days — sees correct history as we build it up.

    Args:
        start_date:   First date to classify (default: ``"2015-01-01"``).
        end_date:     Last date to classify (default: today).
        force_macro:  If True, re-download macro data even if the table
                      already has ≥ :data:`_MIN_MACRO_ROWS_THRESHOLD` rows.

    Returns:
        dict with keys:

        * ``dates_processed``  – int: number of dates successfully classified
        * ``dates_skipped``    – int: dates with missing/None indicators
        * ``regime_transitions`` – list of ``(date, from_state, to_state)``
        * ``state_distribution`` – ``{state_value: count}``
    """
    from db.atlas_db import get_db, get_macro_indicators, record_regime
    from regime.model import RegimeModel

    if end_date is None:
        end_date = datetime.now().strftime("%Y-%m-%d")

    # ── 1. Ensure macro data is present ──────────────────────────────────────
    with get_db() as db:
        macro_total = db.execute(
            "SELECT COUNT(*) FROM macro_indicators"
        ).fetchone()[0]

    if force_macro or macro_total < _MIN_MACRO_ROWS_THRESHOLD:
        logger.info(
            "backfill_regime_history: macro_indicators has %d rows — running backfill",
            macro_total,
        )
        backfill_macro_data(start_date=start_date, end_date=end_date)
    else:
        logger.info(
            "backfill_regime_history: macro_indicators has %d rows — skipping macro backfill",
            macro_total,
        )

    # ── 2. Load all macro indicator rows in date range ────────────────────────
    rows = get_macro_indicators(start_date=start_date, end_date=end_date)
    if not rows:
        logger.warning("backfill_regime_history: no macro_indicators found in [%s, %s]", start_date, end_date)
        return {
            "dates_processed": 0,
            "dates_skipped": 0,
            "regime_transitions": [],
            "state_distribution": {},
        }

    # Sort ascending (get_macro_indicators already returns ASC, but be explicit)
    rows.sort(key=lambda r: r["date"])
    logger.info("backfill_regime_history: %d trading days to process", len(rows))

    # ── 3. Initialise RegimeModel once ────────────────────────────────────────
    model = RegimeModel()

    # ── 4. Classify each date ─────────────────────────────────────────────────
    dates_processed = 0
    dates_skipped = 0
    prev_state: Optional[str] = None
    transitions: List[Tuple[str, str, str]] = []
    state_distribution: Dict[str, int] = {}

    for i, row in enumerate(rows):
        date = row["date"]

        # Check that the row actually has useful data (not all None)
        indicator_values = {k: v for k, v in row.items() if k not in ("date", "updated_at")}
        non_null = [v for v in indicator_values.values() if v is not None]
        if not non_null:
            logger.warning("backfill_regime_history: skipping %s — all indicators are NULL", date)
            dates_skipped += 1
            continue

        try:
            result = model.classify_date(date)
        except ValueError as exc:
            logger.warning("backfill_regime_history: skipping %s — %s", date, exc)
            dates_skipped += 1
            continue
        except Exception as exc:
            logger.error("backfill_regime_history: error on %s — %s", date, exc)
            dates_skipped += 1
            continue

        # Persist to regime_history (INSERT OR REPLACE → idempotent)
        record_regime(
            date=date,
            state=result.state.value,
            trend_score=result.scores["trend"],
            risk_score=result.scores["risk"],
            active_universes=result.active_universes,
            sizing_multiplier=result.sizing_multiplier,
            reasoning=result.reasoning,
            enabled_strategies=result.enabled_strategies,
            model_version=result.model_version,
        )

        # Track state distribution
        sv = result.state.value
        state_distribution[sv] = state_distribution.get(sv, 0) + 1

        # Detect transition
        if prev_state is not None and sv != prev_state:
            transitions.append((date, prev_state, sv))
            logger.info(
                "Regime transition on %s: %s → %s",
                date, prev_state, sv,
            )

        prev_state = sv
        dates_processed += 1

        if dates_processed % 100 == 0:
            logger.info(
                "backfill_regime_history: %d/%d dates processed ...",
                dates_processed, len(rows),
            )

    logger.info(
        "backfill_regime_history: complete — processed=%d skipped=%d transitions=%d",
        dates_processed, dates_skipped, len(transitions),
    )
    return {
        "dates_processed": dates_processed,
        "dates_skipped": dates_skipped,
        "regime_transitions": transitions,
        "state_distribution": state_distribution,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Display helper
# ──────────────────────────────────────────────────────────────────────────────


def print_regime_summary(stats: Dict) -> None:
    """Pretty-print the results of a :func:`backfill_regime_history` call."""
    print("\n" + "=" * 60)
    print("  Regime History Backfill — Summary")
    print("=" * 60)
    print(f"  Dates processed : {stats['dates_processed']}")
    print(f"  Dates skipped   : {stats['dates_skipped']}")
    print(f"  Transitions     : {len(stats['regime_transitions'])}")

    if stats["state_distribution"]:
        total = sum(stats["state_distribution"].values())
        print("\n  State Distribution:")
        for state, cnt in sorted(
            stats["state_distribution"].items(), key=lambda x: -x[1]
        ):
            pct = 100.0 * cnt / total if total else 0.0
            bar = "█" * int(pct / 2)
            print(f"    {state:<25}  {cnt:>4}  ({pct:5.1f}%)  {bar}")

    if stats["regime_transitions"]:
        print(f"\n  Last 10 Transitions (most recent first):")
        for date, from_s, to_s in reversed(stats["regime_transitions"][-10:]):
            print(f"    {date}  {from_s} → {to_s}")

    print("=" * 60 + "\n")


# ──────────────────────────────────────────────────────────────────────────────
# Query helpers
# ──────────────────────────────────────────────────────────────────────────────


def get_regime_transitions(
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Tuple[str, str, str]]:
    """Read ``regime_history`` and return all state-change events.

    Args:
        start_date: Only consider entries on/after this date (inclusive).
        end_date:   Only consider entries on/before this date (inclusive).

    Returns:
        List of ``(date, from_state, to_state)`` tuples in chronological order,
        where *date* is the first day of the *new* state.
    """
    from db.atlas_db import get_db

    with get_db() as db:
        query = "SELECT date, regime_state FROM regime_history WHERE 1=1"
        params: List = []
        if start_date:
            query += " AND date >= ?"
            params.append(start_date)
        if end_date:
            query += " AND date <= ?"
            params.append(end_date)
        query += " ORDER BY date ASC"
        rows = db.execute(query, params).fetchall()

    transitions: List[Tuple[str, str, str]] = []
    prev_state: Optional[str] = None
    for row in rows:
        date = row["date"]
        state = row["regime_state"]
        if prev_state is not None and state != prev_state:
            transitions.append((date, prev_state, state))
        prev_state = state

    return transitions


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry-point
# ──────────────────────────────────────────────────────────────────────────────

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python3 -m regime.history",
        description="Backfill macro indicators and/or regime history into Atlas SQLite DB.",
    )
    parser.add_argument(
        "--start",
        default="2015-01-01",
        help="Start date (YYYY-MM-DD). Default: 2015-01-01",
    )
    parser.add_argument(
        "--end",
        default=None,
        help="End date (YYYY-MM-DD). Default: today",
    )
    parser.add_argument(
        "--macro-only",
        action="store_true",
        help="Only backfill macro indicators — skip regime classification.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download macro data even if macro_indicators table already has rows.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry-point for ``python3 -m regime.history``."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    args = _parse_args(argv)
    end_date = args.end or datetime.now().strftime("%Y-%m-%d")

    if args.macro_only:
        print(f"[regime.history] Macro-only backfill: {args.start} → {end_date}")
        n = backfill_macro_data(start_date=args.start, end_date=end_date)
        print(f"[regime.history] Done — {n} macro indicator rows written")
        return

    print(f"[regime.history] Full backfill: {args.start} → {end_date}  (force={args.force})")
    stats = backfill_regime_history(
        start_date=args.start,
        end_date=end_date,
        force_macro=args.force,
    )
    print_regime_summary(stats)


if __name__ == "__main__":
    main()
