#!/usr/bin/env python3
"""Validate that all live_*.json state files have positions in their canonical universe.

A "cross-market" position occurs when a ticker (e.g. FCX) is listed in the
wrong state file (e.g. live_sp500.json) instead of its canonical universe file
(e.g. live_commodity_etfs.json).  This causes phantom drawdowns because the
FIX-PMEQ-001 per-market equity formula reads positions from the state file and
computes position MV only for tickers whose canonical universe matches the file.

When a cross-market position exists:
  - The affected market's per-market equity is understated (missing MV)
  - The HWM (set when the position WAS correctly attributed) is inflated
  - The resulting phantom drawdown can trigger a false HALT

Usage:
    python3 scripts/validate_state_universes.py          # check only
    python3 scripts/validate_state_universes.py --verbose

Exit codes:
    0  All positions are in their canonical universe state file (clean).
    1  One or more cross-market positions detected.
    2  Unexpected error (import failure, file read error, etc.)

Wire-up note:
    This script can be called from pi-cron.sh preamble or heartbeat watchdog.
    Example (non-blocking, logs result):
        python3 scripts/validate_state_universes.py || \\
            python3 -c "from utils.telegram import send_message; send_message('⚠️ Cross-market state positions detected')"
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("validate_state_universes")


def main(argv: list[str] | None = None) -> int:
    """Return 0 if clean, 1 if violations found, 2 on error."""
    parser = argparse.ArgumentParser(
        description="Check live_*.json state files for cross-market positions."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show all positions checked (not just violations)",
    )
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=None,
        help="Override state directory (default: brokers/state/)",
    )
    args = parser.parse_args(argv)

    try:
        from universe.membership import check_state_file_universes, clear_cache
        clear_cache()  # ensure fresh cache for this run
    except ImportError as exc:
        logger.error("Failed to import membership module: %s", exc)
        return 2

    state_dir = args.state_dir or (_ATLAS_ROOT / "brokers" / "state")
    if not state_dir.is_dir():
        logger.error("State directory not found: %s", state_dir)
        return 2

    logger.info("Scanning state files in %s ...", state_dir)

    try:
        violations = check_state_file_universes(state_dir)
    except Exception as exc:
        logger.error("check_state_file_universes raised: %s", exc)
        return 2

    # Count state files scanned
    state_files = sorted(state_dir.glob("live_*.json"))
    logger.info("Scanned %d state file(s)", len(state_files))

    if not violations:
        logger.info("✓ All positions are in their canonical universe state file")
        print("✓ validate_state_universes: CLEAN (no cross-market positions)")
        return 0

    # Report violations
    print(f"✗ validate_state_universes: {len(violations)} CROSS-MARKET position(s) detected")
    print()
    for v in violations:
        print(
            f"  [{v['file']}] ticker={v['ticker']!r} "
            f"found in market={v['market_id']!r} "
            f"but canonical_universe={v['canonical_universe']!r}"
        )

    print()
    print("Root cause: Position was entered under the wrong market's state file.")
    print(
        "Fix: Move the position entry from live_{wrong}.json to live_{canonical}.json\n"
        "     using the Python snippet in docs/ops/cross-market-position-fix.md\n"
        "     (or run this script again after manual correction to verify)."
    )

    # Optional: Telegram alert
    try:
        from utils.telegram import send_message
        lines = ["⚠️ *Cross-market state positions detected*"]
        for v in violations:
            lines.append(
                f"• {v['ticker']} in `{v['file']}` → should be `{v['canonical_universe']}`"
            )
        lines.append("Run `scripts/validate_state_universes.py` for details.")
        send_message("\n".join(lines))
        logger.info("Telegram alert sent")
    except Exception as exc:
        logger.debug("Telegram alert skipped (non-fatal): %s", exc)

    return 1


if __name__ == "__main__":
    sys.exit(main())
