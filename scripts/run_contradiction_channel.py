#!/usr/bin/env python3
"""Contradiction-driven queue channel CLI (Phase 5).

Reads open major/critical contradictions and (when --apply is passed) appends
matching QueueEntry rows so the backtester picks them up.

Run:
    python3 scripts/run_contradiction_channel.py                    # dry-run
    python3 scripts/run_contradiction_channel.py --apply
    python3 scripts/run_contradiction_channel.py --apply --limit 10
    python3 scripts/run_contradiction_channel.py --apply --severities critical
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

LOG_PATH = _ATLAS_ROOT / "logs" / "contradiction_channel.log"


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
                        help="Actually append to queue (default: dry-run)")
    parser.add_argument("--limit", type=int, default=25,
                        help="Max queue entries to emit (default: 25)")
    parser.add_argument("--severities", nargs="+",
                        default=["critical", "major"],
                        choices=["critical", "major", "minor"],
                        help="Severities to include (default: critical major)")
    parser.add_argument("--decay-days", type=int, default=30,
                        help="Skip strategies tested in the last N days (default: 30)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    _setup_logging(args.verbose)
    log = logging.getLogger("contradiction_channel")
    log.info("Contradiction channel run")
    log.info("  mode:       %s", "APPLY" if args.apply else "DRY-RUN")
    log.info("  limit:      %d", args.limit)
    log.info("  severities: %s", args.severities)
    log.info("  decay_days: %d", args.decay_days)

    from research.discovery.contradiction_channel import run_channel
    result = run_channel(
        apply=args.apply,
        limit=args.limit,
        severities=tuple(args.severities),
        decay_days=args.decay_days,
    )
    print(json.dumps(result, indent=2, default=str))

    if args.apply and result.get("queue_errors"):
        log.warning("%d queue-append errors", len(result["queue_errors"]))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
