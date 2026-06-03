#!/usr/bin/env python3
"""
Same-bar stop-out monitor.

Counts trades where DATE(entry_date) == DATE(exit_date) — i.e., entry and stop
fill on the same calendar day. Indicates opening-bar volatility blowing
through tight ATR stops before any real price action.

Anti-pattern documented in mental-model 2026-05-08 Lesson #6. Deferred fix
(entry_delay_minutes=15) blocked on #316 5-min intraday bar backfill.

Run via cron daily; alerts via Telegram if rate exceeds threshold.

Output:
  - Stdout summary: per-strategy same-bar count, rate, total $ impact (trailing N days)
  - Telegram alert if rate > THRESHOLD_PCT (default 20%) AND count >= THRESHOLD_MIN_EVENTS (default 5)
  - Exit 0 on success, 1 on alert fired, 2 on error

Alert de-duplication (anti-spam):
  Because the same-bar fix is deferred (blocked on #316), the underlying
  condition can persist for weeks. To avoid daily duplicate Telegram alerts for
  the SAME set of events, an alert only (re)fires when the same-bar count is
  strictly GREATER than the count at the last alert (a genuine escalation).
  An unchanged condition is logged but not re-sent. The 24h cooldown remains a
  secondary backstop. Reset by deleting data/monitor_same_bar_state.json.

Why both threshold + min events:
  - Below 5 events the rate is statistically meaningless (current state: n=2)
  - Above 5 events with >20% rate justifies revisiting the entry_delay decision

Usage:
  python3 scripts/monitor_same_bar_stops.py [--days 30] [--threshold 0.20] [--min-events 5] [--quiet]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

import os
os.chdir(_ATLAS_ROOT)

from db.atlas_db import get_db

# Module-level import for test patchability
try:
    from utils.telegram import notify as _tg_notify
except ImportError:
    _tg_notify = None  # type: ignore[assignment]

# ── Constants ──────────────────────────────────────────────────────────────────
DEFAULT_DAYS: int = 30
DEFAULT_THRESHOLD: float = 0.20
DEFAULT_MIN_EVENTS: int = 5
COOLDOWN_HOURS: int = 24

STATE_FILE: Path = _ATLAS_ROOT / "data" / "monitor_same_bar_state.json"

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


# ── State helpers ──────────────────────────────────────────────────────────────

def _load_state(state_file: Path = STATE_FILE) -> dict[str, Any]:
    """Load cooldown state from JSON file; return empty dict on error."""
    if not state_file.exists():
        return {}
    try:
        return json.loads(state_file.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load state file %s: %s", state_file, exc)
        return {}


def _save_state(state: dict[str, Any], state_file: Path = STATE_FILE) -> None:
    """Persist cooldown state; non-fatal on error."""
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2))
    except OSError as exc:
        log.warning("Failed to save state file %s: %s", state_file, exc)


def _is_within_cooldown(state: dict[str, Any], cooldown_hours: int = COOLDOWN_HOURS) -> bool:
    """Return True if last alert was sent within the cooldown window."""
    last_alert_str = state.get("last_alert_at")
    if not last_alert_str:
        return False
    try:
        last_alert = datetime.fromisoformat(last_alert_str)
        if last_alert.tzinfo is None:
            last_alert = last_alert.replace(tzinfo=timezone.utc)
        elapsed = datetime.now(timezone.utc) - last_alert
        return elapsed < timedelta(hours=cooldown_hours)
    except (ValueError, TypeError) as exc:
        log.warning("Could not parse last_alert_at %r: %s", last_alert_str, exc)
        return False


# ── DB queries ─────────────────────────────────────────────────────────────────

def query_same_bar_stops(days: int) -> dict[str, Any]:
    """
    Return aggregated same-bar stop data for the trailing *days* window.

    Returns a dict with:
        window_start: ISO date string
        window_end: ISO date string
        total_round_trips: int (all closed trades in window)
        same_bar_total: int (trades where entry_date == exit_date)
        rate: float (same_bar_total / total_round_trips, or 0.0 if no trades)
        per_strategy: list[dict] — one entry per strategy that had same-bar stops
            Each entry: strategy, count, total_pnl, avg_pnl, rate_for_strategy,
                        total_strategy_round_trips
        same_bar_trades: list[dict] — individual same-bar trades (for detail)
    """
    now = datetime.now(timezone.utc)
    window_end = now.date()
    window_start = (now - timedelta(days=days)).date()
    window_start_str = str(window_start)
    window_end_str = str(window_end)

    with get_db() as conn:
        # Total closed round-trips in window
        row = conn.execute(
            """
            SELECT COUNT(*) AS n
            FROM trades
            WHERE status = 'closed'
              AND superseded = 0
              AND DATE(entry_date) >= ?
              AND DATE(entry_date) <= ?
            """,
            (window_start_str, window_end_str),
        ).fetchone()
        total_round_trips: int = row["n"] if row else 0

        # Same-bar stops: entry_date == exit_date
        same_bar_rows = conn.execute(
            """
            SELECT
                id,
                ticker,
                strategy,
                entry_date,
                exit_date,
                exit_reason,
                pnl,
                pnl_pct,
                entry_price,
                exit_price,
                shares
            FROM trades
            WHERE status = 'closed'
              AND superseded = 0
              AND DATE(entry_date) = DATE(exit_date)
              AND DATE(entry_date) >= ?
              AND DATE(entry_date) <= ?
            ORDER BY entry_date DESC
            """,
            (window_start_str, window_end_str),
        ).fetchall()

        same_bar_trades = [dict(r) for r in same_bar_rows]
        same_bar_total = len(same_bar_trades)

        # Per-strategy breakout: count, total pnl, avg pnl
        strat_agg = conn.execute(
            """
            SELECT
                strategy,
                COUNT(*) AS same_bar_count,
                SUM(COALESCE(pnl, 0.0)) AS total_pnl,
                AVG(COALESCE(pnl, 0.0)) AS avg_pnl
            FROM trades
            WHERE status = 'closed'
              AND superseded = 0
              AND DATE(entry_date) = DATE(exit_date)
              AND DATE(entry_date) >= ?
              AND DATE(entry_date) <= ?
            GROUP BY strategy
            ORDER BY same_bar_count DESC
            """,
            (window_start_str, window_end_str),
        ).fetchall()

        # For each strategy in the same-bar set, get total round-trips in window
        per_strategy: list[dict[str, Any]] = []
        for strat_row in strat_agg:
            strat = strat_row["strategy"]
            strat_total = conn.execute(
                """
                SELECT COUNT(*) AS n
                FROM trades
                WHERE status = 'closed'
                  AND superseded = 0
                  AND strategy = ?
                  AND DATE(entry_date) >= ?
                  AND DATE(entry_date) <= ?
                """,
                (strat, window_start_str, window_end_str),
            ).fetchone()["n"]

            strat_rate = (
                strat_row["same_bar_count"] / strat_total
                if strat_total > 0
                else 0.0
            )
            per_strategy.append(
                {
                    "strategy": strat,
                    "count": strat_row["same_bar_count"],
                    "total_pnl": round(float(strat_row["total_pnl"] or 0.0), 2),
                    "avg_pnl": round(float(strat_row["avg_pnl"] or 0.0), 2),
                    "total_strategy_round_trips": strat_total,
                    "rate_for_strategy": round(strat_rate, 4),
                }
            )

    overall_rate = same_bar_total / total_round_trips if total_round_trips > 0 else 0.0

    return {
        "window_start": window_start_str,
        "window_end": window_end_str,
        "total_round_trips": total_round_trips,
        "same_bar_total": same_bar_total,
        "rate": round(overall_rate, 4),
        "per_strategy": per_strategy,
        "same_bar_trades": same_bar_trades,
    }


# ── Formatting helpers ─────────────────────────────────────────────────────────

def _format_human(data: dict[str, Any], threshold: float, min_events: int) -> str:
    """Return a human-readable summary string."""
    lines: list[str] = [
        f"=== Same-Bar Stop Monitor ({data['window_start']} → {data['window_end']}) ===",
        f"  Total round-trips : {data['total_round_trips']}",
        f"  Same-bar stops    : {data['same_bar_total']}",
        f"  Rate              : {data['rate']:.1%}",
        f"  Alert threshold   : >{threshold:.0%} AND ≥{min_events} events",
        "",
    ]
    if data["per_strategy"]:
        lines.append("  Per-strategy breakdown:")
        for s in data["per_strategy"]:
            lines.append(
                f"    {s['strategy']:<30} {s['count']:>3}/{s['total_strategy_round_trips']:<3} "
                f"({s['rate_for_strategy']:.1%})  PnL ${s['total_pnl']:+.2f}"
            )
        lines.append("")

    if data["same_bar_trades"]:
        lines.append("  Individual same-bar trades:")
        for t in data["same_bar_trades"]:
            pnl_str = f"${float(t['pnl'] or 0):+.2f}" if t.get("pnl") is not None else "PnL=N/A"
            lines.append(
                f"    [{t['entry_date'][:10]}] {t['ticker']:<6} {t['strategy']:<25} "
                f"{pnl_str}  exit_reason={t.get('exit_reason') or 'n/a'}"
            )
    else:
        lines.append("  No same-bar stops in window.")

    return "\n".join(lines)


def _format_telegram(data: dict[str, Any], threshold: float, min_events: int) -> str:
    """Return Telegram HTML alert message."""
    rate_pct = f"{data['rate']:.1%}"
    total = data["total_round_trips"]
    count = data["same_bar_total"]
    window = f"{data['window_start']} → {data['window_end']}"

    lines: list[str] = [
        f"⚠️ <b>Same-Bar Stop Alert</b>",
        f"Rate <b>{rate_pct}</b> ({count}/{total} trades) in trailing 30d window ({window}).",
        f"Threshold: &gt;{threshold:.0%} AND ≥{min_events} events",
        "",
    ]
    if data["per_strategy"]:
        lines.append("<b>By strategy:</b>")
        for s in data["per_strategy"]:
            lines.append(
                f"  • {s['strategy']}: {s['count']}/{s['total_strategy_round_trips']} "
                f"({s['rate_for_strategy']:.1%}) PnL ${s['total_pnl']:+.2f}"
            )
        lines.append("")

    lines += [
        "Action: Review <code>scripts/monitor_same_bar_stops.py</code> output.",
        "Re-evaluate <code>entry_delay_minutes=15</code> (blocked on #316 5-min backfill).",
    ]
    return "\n".join(lines)


# ── Main ───────────────────────────────────────────────────────────────────────

def run_monitor(
    days: int = DEFAULT_DAYS,
    threshold: float = DEFAULT_THRESHOLD,
    min_events: int = DEFAULT_MIN_EVENTS,
    quiet: bool = False,
    state_file: Path = STATE_FILE,
) -> int:
    """
    Run the same-bar stop monitor.

    Returns:
        0  — success, no alert fired
        1  — alert fired (rate above threshold)
        2  — error
    """
    try:
        data = query_same_bar_stops(days=days)
    except Exception as exc:
        log.error("Failed to query same-bar stops: %s", exc, exc_info=True)
        return 2

    human_summary = _format_human(data, threshold=threshold, min_events=min_events)
    print(human_summary)
    print()

    # JSON summary to stdout
    json_summary = {
        "monitor": "same_bar_stops",
        "run_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "threshold_pct": threshold,
        "min_events": min_events,
        "result": {
            "total_round_trips": data["total_round_trips"],
            "same_bar_total": data["same_bar_total"],
            "rate": data["rate"],
            "alert_would_fire": (
                data["rate"] > threshold
                and data["same_bar_total"] >= min_events
            ),
        },
        "per_strategy": data["per_strategy"],
    }
    print(json.dumps(json_summary, indent=2))

    # ── Alert logic ────────────────────────────────────────────────────────────
    should_alert = (
        data["rate"] > threshold
        and data["same_bar_total"] >= min_events
    )

    if not should_alert:
        log.info(
            "Same-bar rate %.1f%% (%d/%d) — below alert threshold (>%.0f%% AND ≥%d events). No alert.",
            data["rate"] * 100,
            data["same_bar_total"],
            data["total_round_trips"],
            threshold * 100,
            min_events,
        )
        return 0

    # ── Suppression: de-duplicate known/unchanged conditions, then cooldown ──────
    # The same-bar anti-pattern is a *known, deferred* issue (entry_delay_minutes
    # fix blocked on #316 5-min backfill). Re-alerting every day for the SAME set
    # of same-bar stops is pure noise — it was the source of daily Telegram spam.
    # Only (re)alert when the situation has materially ESCALATED, i.e. there are
    # strictly MORE same-bar events than at the last alert. The time-based cooldown
    # remains as a secondary backstop against intra-day duplicate sends.
    state = _load_state(state_file=state_file)
    last_count = int(state.get("last_count") or 0)
    escalated = data["same_bar_total"] > last_count

    if not escalated:
        log.info(
            "Alert suppressed — known same-bar condition unchanged "
            "(count=%d ≤ last alerted=%d). No new same-bar stops since last alert.",
            data["same_bar_total"],
            last_count,
        )
        return 1  # Still exit 1 to signal the alert-worthy condition persists

    # Check cooldown (backstop)
    if _is_within_cooldown(state, cooldown_hours=COOLDOWN_HOURS):
        log.info(
            "Alert suppressed — within %dh cooldown (last sent: %s).",
            COOLDOWN_HOURS,
            state.get("last_alert_at"),
        )
        return 1  # Still exit 1 to signal alert-worthy condition

    # Fire alert
    if not quiet:
        tg_msg = _format_telegram(data, threshold=threshold, min_events=min_events)
        try:
            _tg_notify(tg_msg, level="WARNING", category="same_bar_stops")
            log.info("Telegram alert sent.")
        except Exception as exc:
            log.warning("Telegram send failed: %s", exc)
    else:
        log.info("--quiet: Telegram alert suppressed.")

    # Update cooldown state
    new_state = dict(state)
    new_state["last_alert_at"] = datetime.now(timezone.utc).isoformat()
    new_state["last_rate"] = data["rate"]
    new_state["last_count"] = data["same_bar_total"]
    _save_state(new_state, state_file=state_file)

    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Monitor same-bar stop-out rate in live trading."
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAYS,
        help=f"Trailing window in days (default: {DEFAULT_DAYS})",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Alert if rate exceeds this fraction (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=DEFAULT_MIN_EVENTS,
        dest="min_events",
        help=f"Minimum same-bar events before alerting (default: {DEFAULT_MIN_EVENTS})",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress all Telegram alerts (for testing / dry-run).",
    )
    args = parser.parse_args(argv)

    return run_monitor(
        days=args.days,
        threshold=args.threshold,
        min_events=args.min_events,
        quiet=args.quiet,
    )


if __name__ == "__main__":
    sys.exit(main())
