#!/usr/bin/env python3
"""Verify that weekly strategy health reports were generated on schedule.

Runs Sunday 22:00 AEST (12:00 UTC) as a second-layer file-existence check,
orthogonal to the heartbeat watchdog.  Confirms that the expected
``logs/health_reports/health_<market>_<saturday-date>.json`` file(s) were
written after the Friday-night/Saturday health-check cron.

Usage:
    python3 scripts/verify_weekly_health_reports.py           # sends Telegram if missing
    python3 scripts/verify_weekly_health_reports.py --dry-run # prints alert without sending

Exit codes:
    0  — all expected reports found
    1  — one or more reports missing (Telegram alert sent unless --dry-run)

Cron entry (atlas.crontab, TZ=Australia/Brisbane):
    # Sunday 22:00 AEST (Sun 12:00 UTC) — verify weekly health reports exist
    0 22 * * 0 /usr/bin/flock -n /tmp/verify_health_reports.lock \\
        bash -c 'cd /root/atlas && timeout 5m python3 scripts/verify_weekly_health_reports.py' \\
        >> /root/atlas/logs/verify_health_reports.log 2>&1
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

# ─── Paths ────────────────────────────────────────────────────────────────────

ATLAS_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = ATLAS_ROOT / "logs" / "health_reports"

sys.path.insert(0, str(ATLAS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("verify_weekly_health_reports")

# ─── Configuration ────────────────────────────────────────────────────────────

# Markets that have an active weekly health-check cron entry.
# Extend this tuple when new markets get health-check coverage.
TRACKED_MARKETS: tuple[str, ...] = ("sp500",)


# ─── Core logic ───────────────────────────────────────────────────────────────

def _most_recent_saturday() -> date:
    """Return the date of the most recently elapsed Saturday in server local time (AEST).

    If today IS Saturday (e.g. manual run before the cron fires), returns today —
    the health-check cron runs at 09:00 AEST so by run-time (22:00 AEST Sunday)
    it will always have had a chance to fire.

    weekday() mapping: Monday=0, Tuesday=1, …, Saturday=5, Sunday=6
    """
    today = datetime.now().date()  # server local = AEST (TZ=Australia/Brisbane)
    days_since_saturday = (today.weekday() - 5) % 7
    return today - timedelta(days=days_since_saturday)


def check_reports(
    markets: tuple[str, ...] = TRACKED_MARKETS,
    reports_dir: Optional[Path] = None,
) -> list[str]:
    """Check that weekly health reports exist for the most recent Saturday.

    Args:
        markets: Market identifiers to check.
        reports_dir: Override the default reports directory (used by tests).

    Returns:
        List of market names whose report is missing (empty = all present).
    """
    effective_dir = reports_dir if reports_dir is not None else REPORTS_DIR
    saturday = _most_recent_saturday()
    missing: list[str] = []

    for market in markets:
        expected = effective_dir / f"health_{market}_{saturday.isoformat()}.json"
        if expected.exists():
            logger.info("✓ %s — found", expected.name)
        else:
            logger.warning("✗ %s — MISSING", expected.name)
            missing.append(market)

    return missing


def _build_alert_message(missing: list[str], saturday: date) -> str:
    """Build the Telegram HTML alert message for missing reports."""
    lines = [
        "🚨 <b>Missing weekly health reports</b>",
        "",
        f"Expected date: <b>{saturday.isoformat()}</b> (Saturday)",
        f"Missing: <b>{len(missing)}</b> of {len(TRACKED_MARKETS)} tracked market(s)",
        "",
    ]
    for m in missing:
        lines.append(f"  ❌ <code>health_{m}_{saturday.isoformat()}.json</code>")
    lines += [
        "",
        "<b>Likely causes:</b>",
        "  • Health-check cron was disabled or not scheduled",
        "  • Script crashed before writing output",
        "  • Disk write error",
        "",
        "Re-run manually:",
        "  <code>python3 scripts/strategy_health_cron.py --market sp500</code>",
    ]
    return "\n".join(lines)


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: Optional[list[str]] = None) -> int:
    """Entry point. Returns exit code (0 = all present, 1 = missing)."""
    parser = argparse.ArgumentParser(
        description="Verify weekly strategy health reports exist",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be alerted without sending to Telegram",
    )
    args = parser.parse_args(argv)

    saturday = _most_recent_saturday()
    logger.info(
        "Checking health reports for most-recent Saturday: %s (markets: %s)",
        saturday.isoformat(),
        ", ".join(TRACKED_MARKETS),
    )

    missing = check_reports(markets=TRACKED_MARKETS)

    if not missing:
        logger.info(
            "✅ All %d health report(s) present for %s",
            len(TRACKED_MARKETS),
            saturday.isoformat(),
        )
        return 0

    msg = _build_alert_message(missing, saturday)

    if args.dry_run:
        print("=== DRY RUN — would send Telegram alert: ===")
        print(msg)
        return 1

    # Send Telegram alert
    try:
        from utils.telegram import send_message  # type: ignore[import]

        ok = send_message(msg)
        if ok:
            logger.info("Telegram alert sent for %d missing report(s)", len(missing))
        else:
            logger.warning(
                "Telegram send returned False — check credentials/connectivity"
            )
    except Exception as exc:
        logger.error("Telegram send failed: %s", exc)

    return 1


if __name__ == "__main__":
    sys.exit(main())
