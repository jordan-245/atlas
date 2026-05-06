#!/usr/bin/env python3
"""Daily live-vs-research Sharpe divergence monitor.

For each (universe, strategy) in research_best, compute the live trade-level
Sharpe over the last 30 days and compare to research_best.sharpe. Alert via
Telegram if the gap exceeds threshold.

Per audit 2026-05-06 Recommendation 4.

Usage:
    python3 scripts/check_live_research_divergence.py
    python3 scripts/check_live_research_divergence.py --dry-run-telegram
    python3 scripts/check_live_research_divergence.py --window-days 60 --gap-threshold 0.3
"""
from __future__ import annotations

import argparse
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

logger = logging.getLogger("divergence")

DEFAULT_WINDOW_DAYS = 30
DEFAULT_GAP_THRESHOLD = 0.5  # alert if research_sharpe - live_sharpe > 0.5
MIN_TRADES_FOR_LIVE_SHARPE = 5


def _compute_live_sharpe(pnl_pcts: List[float]) -> Optional[float]:
    """Trade-level Sharpe (NOT annualised — directional only).

    Returns None if fewer than 2 trades or stdev is zero.
    """
    if len(pnl_pcts) < 2:
        return None
    mean = sum(pnl_pcts) / len(pnl_pcts)
    var = sum((x - mean) ** 2 for x in pnl_pcts) / (len(pnl_pcts) - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return None
    return mean / sd


def _fetch_research_best_rows() -> List[Dict]:
    """Return list of row dicts from research_best where sharpe IS NOT NULL."""
    from db.atlas_db import get_db

    with get_db() as db:
        cur = db.execute(
            "SELECT universe, strategy, sharpe, trades, updated_at "
            "FROM research_best "
            "WHERE sharpe IS NOT NULL"
        )
        return [dict(r) for r in cur.fetchall()]


def _fetch_live_trades(universe: str, strategy: str, window_days: int) -> List[float]:
    """Return list of pnl_pct for closed, non-superseded trades within window."""
    from db.atlas_db import get_db

    cutoff = (
        datetime.now(timezone.utc) - timedelta(days=window_days)
    ).strftime("%Y-%m-%d")
    with get_db() as db:
        cur = db.execute(
            "SELECT pnl_pct FROM trades "
            "WHERE universe = ? AND strategy = ? "
            "AND status = 'closed' AND COALESCE(superseded, 0) = 0 "
            "AND exit_date IS NOT NULL "
            "AND DATE(exit_date) > ? "
            "AND pnl_pct IS NOT NULL",
            (universe, strategy, cutoff),
        )
        return [float(row[0]) for row in cur.fetchall()]


def compute_divergences(
    window_days: int = DEFAULT_WINDOW_DAYS,
    gap_threshold: float = DEFAULT_GAP_THRESHOLD,
) -> List[Dict]:
    """Return list of divergence records sorted by gap descending.

    Each record: {universe, strategy, research_sharpe, live_sharpe, gap,
                  live_trades, trust_score, severity}

    Only records with >= MIN_TRADES_FOR_LIVE_SHARPE live trades are included.
    """
    out: List[Dict] = []
    for r in _fetch_research_best_rows():
        universe = r["universe"]
        strategy = r["strategy"]
        research_sharpe = float(r["sharpe"]) if r["sharpe"] is not None else 0.0

        pnls = _fetch_live_trades(universe, strategy, window_days)
        n = len(pnls)
        if n < MIN_TRADES_FOR_LIVE_SHARPE:
            continue  # insufficient live data

        live_sharpe = _compute_live_sharpe(pnls)
        if live_sharpe is None:
            continue

        gap = research_sharpe - live_sharpe

        # Trust score: live / research, clamped [0, 2].
        # Undefined when research_sharpe <= 0 (would invert or divide-by-zero).
        if research_sharpe > 0:
            trust: Optional[float] = max(0.0, min(2.0, live_sharpe / research_sharpe))
        else:
            trust = None

        if gap >= gap_threshold or (research_sharpe > 0 and live_sharpe < 0):
            severity = "🔴" if (gap >= 1.0 or live_sharpe < -1.0) else "🟡"
        else:
            severity = "🟢"

        out.append(
            {
                "universe": universe,
                "strategy": strategy,
                "research_sharpe": research_sharpe,
                "live_sharpe": live_sharpe,
                "gap": gap,
                "live_trades": n,
                "trust_score": trust,
                "severity": severity,
            }
        )

    out.sort(key=lambda d: d["gap"], reverse=True)
    return out


def format_telegram(divergences: List[Dict], gap_threshold: float) -> str:
    """Build Telegram HTML message. Alerting rows listed first."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    alerting = [d for d in divergences if d["severity"] in ("🔴", "🟡")]
    healthy = [d for d in divergences if d["severity"] == "🟢"]

    lines = [f"⚠️ <b>Research-Live Divergence ({today})</b>"]
    if alerting:
        lines.append(f"Threshold: gap > {gap_threshold:.2f}")
        lines.append("")
        lines.append("<b>Divergent strategies:</b>")
        for d in alerting:
            trust_str = (
                f"trust {d['trust_score']:.2f}"
                if d["trust_score"] is not None
                else "trust n/a"
            )
            lines.append(
                f"{d['severity']} {d['universe']}/{d['strategy']}: "
                f"research {d['research_sharpe']:+.2f}, "
                f"live {d['live_sharpe']:+.2f} "
                f"(gap {d['gap']:+.2f}, {trust_str}, n={d['live_trades']})"
            )
    else:
        lines.append(
            "✅ No divergence alerts. "
            "All live strategies tracking research within threshold."
        )

    if healthy:
        lines.append("")
        lines.append(
            f"<i>Healthy: {len(healthy)} strategies (gap &lt; {gap_threshold:.2f})</i>"
        )

    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help="Lookback window for live trades (default: 30)",
    )
    parser.add_argument(
        "--gap-threshold",
        type=float,
        default=DEFAULT_GAP_THRESHOLD,
        help="Alert if research_sharpe - live_sharpe > threshold (default: 0.5)",
    )
    parser.add_argument(
        "--dry-run-telegram",
        action="store_true",
        help="Print message instead of sending to Telegram",
    )
    parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Skip Telegram entirely (for CI / cron health checks)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
    )

    divergences = compute_divergences(args.window_days, args.gap_threshold)
    msg = format_telegram(divergences, args.gap_threshold)

    print(msg)
    n_alerting = sum(1 for d in divergences if d["severity"] != "🟢")
    print(
        f"\n[summary] {len(divergences)} (universe, strategy) combos checked, "
        f"{n_alerting} alerting"
    )

    if args.dry_run_telegram:
        print("\n[DRY RUN] Telegram skipped.")
        return 0

    if args.no_telegram:
        return 0

    has_alerts = any(d["severity"] != "🟢" for d in divergences)
    if has_alerts:
        try:
            from utils.telegram import notify

            notify(msg, category="research_divergence")
        except Exception as exc:
            logger.error("Telegram send failed: %s", exc)
            return 1

    # Heartbeat record (non-fatal)
    try:
        from db.atlas_db import record_heartbeat

        record_heartbeat("check_live_research_divergence", status="ok")
    except Exception as exc:
        logger.warning("Heartbeat record failed: %s", exc)

    return 0


if __name__ == "__main__":
    sys.exit(main())
