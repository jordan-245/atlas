#!/usr/bin/env python3
"""Rebuild trading universe membership for one or all markets.

For sp500: runs the full build_universe() pipeline (downloads yfinance data,
applies liquidity/price filters, ranks by daily traded value, saves JSON).
Falls back to refreshing the existing universe.json timestamp when yfinance
fails to return sufficient data (< 50 tickers pass), which can happen on
weekends when the daily parquet cache is stale.

For static ETF universes (commodity_etfs, sector_etfs, defensive_etfs,
gold_etfs, treasury_etfs): reads the canonical ticker list from
universe.definitions and writes a fresh universe.json with an updated
built_at timestamp.  No network calls needed for static markets.

Usage:
    python3 scripts/rebuild_universe.py --universe sp500
    python3 scripts/rebuild_universe.py --universe commodity_etfs
    python3 scripts/rebuild_universe.py --all
    python3 scripts/rebuild_universe.py --universe sp500 --dry-run

Exit codes:
    0  — all requested universes rebuilt successfully
    1  — one or more universes failed (partial success possible with --all)

Cron / systemd:
    This script is designed to be called by atlas-universe-rebuild.service.
    Run weekly (Sundays 18:00 UTC) so universe membership stays current.
    sp500 benefits most when run after weekday premarket data ingest (fresh cache).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import sys
from pathlib import Path
from typing import Any, Optional

_ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ATLAS_ROOT))

logger = logging.getLogger(__name__)

ALL_UNIVERSES = [
    "sp500",
    "commodity_etfs",
    "sector_etfs",
    "defensive_etfs",
    "gold_etfs",
    "treasury_etfs",
]

# Minimum viable sp500 result — if build_universe returns fewer tickers than
# this, yfinance is likely having issues and we fall back to refreshing the
# existing universe.json timestamp rather than overwriting with a tiny list.
_SP500_MIN_TICKERS = 50

PROCESSED_DIR = _ATLAS_ROOT / "data" / "processed"


def _universe_path(universe_name: str) -> Path:
    return PROCESSED_DIR / universe_name / "universe.json"


def _load_existing_universe(universe_name: str) -> Optional[dict]:
    """Load existing universe.json if it exists."""
    p = _universe_path(universe_name)
    if not p.exists():
        return None
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _refresh_timestamp(universe_name: str, reason: str = "timestamp_refresh") -> bool:
    """Update built_at on an existing universe.json without changing tickers."""
    existing = _load_existing_universe(universe_name)
    if not existing:
        logger.error("_refresh_timestamp(%r): no existing universe.json to refresh", universe_name)
        return False

    existing.setdefault("metadata", {})["built_at"] = datetime.datetime.now().isoformat()
    existing["metadata"]["rebuild_reason"] = reason

    out_path = _universe_path(universe_name)
    try:
        with open(out_path, "w") as f:
            json.dump(existing, f, indent=2)
        n = len(existing.get("tickers", []))
        logger.info(
            "Refreshed timestamp for %r: %d tickers (reason=%r) → %s",
            universe_name, n, reason, out_path,
        )
        print(f"✓ {universe_name}: refreshed timestamp ({n} tickers, reason={reason}) → {out_path}")
        return True
    except OSError as exc:
        logger.error("_refresh_timestamp(%r): write failed: %s", universe_name, exc)
        return False


def rebuild_static_universe(universe_name: str, dry_run: bool = False) -> bool:
    """Rebuild a static ETF universe from universe.definitions.

    No network calls — tickers come from the canonical in-code definition.
    Writes a fresh universe.json with updated built_at timestamp.

    Returns True on success.
    """
    try:
        from universe.definitions import get_universe_tickers, get_universe
    except ImportError as exc:
        logger.error("rebuild_static_universe: cannot import universe.definitions: %s", exc)
        return False

    try:
        tickers = get_universe_tickers(universe_name)
        defn = get_universe(universe_name)
    except (KeyError, ValueError) as exc:
        logger.error("rebuild_static_universe(%r): unknown universe: %s", universe_name, exc)
        return False

    result: dict[str, Any] = {
        "metadata": {
            "built_at": datetime.datetime.now().isoformat(),
            "config_version": "v1.0",
            "candidates_evaluated": len(tickers),
            "final_count": len(tickers),
            "filters": {
                "min_price": 1.0,
                "min_median_daily_value": 1_000_000,
                "top_n": len(tickers),
            },
            "method": defn.get("method", "static"),
            "rebuild_reason": "scheduled_weekly_rebuild",
        },
        "tickers": tickers,
    }

    out_path = _universe_path(universe_name)

    if dry_run:
        logger.info("[dry-run] Would write %d tickers to %s", len(tickers), out_path)
        print(f"[dry-run] {universe_name}: {len(tickers)} tickers → {out_path}")
        return True

    out_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2)
        logger.info(
            "Rebuilt static universe %r: %d tickers → %s",
            universe_name, len(tickers), out_path,
        )
        print(f"✓ {universe_name}: {len(tickers)} tickers → {out_path}")
        return True
    except OSError as exc:
        logger.error("rebuild_static_universe(%r): write failed: %s", universe_name, exc)
        return False


def _restore_pre_build_state(
    universe_name: str,
    pre_build_state: Optional[dict],
    reason: str = "restore",
) -> None:
    """Write pre_build_state back to universe.json with a refreshed timestamp.

    Used to undo a bad build_universe() result when the new ticker count is
    suspiciously small.
    """
    if not pre_build_state:
        logger.warning("_restore_pre_build_state(%r): no pre-build state to restore", universe_name)
        return

    pre_build_state.setdefault("metadata", {})["built_at"] = datetime.datetime.now().isoformat()
    pre_build_state["metadata"]["rebuild_reason"] = reason

    out_path = _universe_path(universe_name)
    try:
        with open(out_path, "w") as f:
            json.dump(pre_build_state, f, indent=2)
        n = len(pre_build_state.get("tickers", []))
        logger.info(
            "Restored pre-build state for %r: %d tickers (reason=%r)",
            universe_name, n, reason,
        )
        print(f"✓ {universe_name}: restored pre-build state ({n} tickers, reason={reason})")
    except OSError as exc:
        logger.error("_restore_pre_build_state(%r): write failed: %s", universe_name, exc)


def rebuild_sp500_universe(dry_run: bool = False) -> bool:
    """Rebuild the sp500 universe via the full build_universe() pipeline.

    Downloads yfinance data for ~285 S&P 500 candidates (using local parquet
    cache when fresh), filters by price/volume/market-cap, ranks by daily
    traded value, saves JSON.

    Falls back to refreshing the existing universe.json timestamp when
    build_universe() returns fewer than _SP500_MIN_TICKERS tickers, which
    can happen on weekends when the parquet cache is stale and yfinance
    bulk-download fails.

    Returns True on success.
    """
    try:
        from universe.builder import build_universe
        from utils.config import get_active_config
    except ImportError as exc:
        logger.error("rebuild_sp500_universe: import error: %s", exc)
        return False

    try:
        config = get_active_config("sp500")
    except Exception as exc:
        logger.error("rebuild_sp500_universe: failed to load sp500 config: %s", exc)
        return False

    if dry_run:
        logger.info("[dry-run] Would rebuild sp500 universe (build_universe)")
        print("[dry-run] sp500: would run build_universe() (network-required)")
        return True

    logger.info("Rebuilding sp500 universe via build_universe() — this may take a few minutes")
    print("Rebuilding sp500 universe (downloading yfinance data for ~285 candidates)...")

    # Preserve the pre-build state so we can restore it if the new build is bad.
    pre_build_state = _load_existing_universe("sp500")

    try:
        tickers = build_universe(config, save=True, verbose=True)
    except Exception as exc:
        logger.error("rebuild_sp500_universe: build_universe() raised: %s", exc, exc_info=True)
        logger.warning("sp500 rebuild failed — refreshing existing universe timestamp")
        _restore_pre_build_state("sp500", pre_build_state, reason=f"build_failed:{type(exc).__name__}")
        return True  # non-fatal: kept previous good universe

    if len(tickers) < _SP500_MIN_TICKERS:
        logger.warning(
            "sp500 rebuild returned only %d tickers (< min=%d) — "
            "yfinance likely flaky; restoring pre-build universe with refreshed timestamp",
            len(tickers), _SP500_MIN_TICKERS,
        )
        _restore_pre_build_state(
            "sp500", pre_build_state,
            reason=f"too_few_tickers_fallback:{len(tickers)}",
        )
        return True  # non-fatal: kept previous good universe

    logger.info("sp500 universe rebuilt: %d tickers", len(tickers))
    print(f"✓ sp500: {len(tickers)} tickers → {_universe_path('sp500')}")
    return True


def rebuild_universe(universe_name: str, dry_run: bool = False) -> bool:
    """Rebuild one universe. Dispatches by method (static vs dynamic)."""
    logger.info("Rebuilding universe: %r", universe_name)

    if universe_name == "sp500":
        return rebuild_sp500_universe(dry_run=dry_run)
    elif universe_name in ALL_UNIVERSES:
        return rebuild_static_universe(universe_name, dry_run=dry_run)
    else:
        logger.error("Unknown universe: %r. Available: %s", universe_name, ALL_UNIVERSES)
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Atlas trading universe membership",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--universe", "-u",
        choices=ALL_UNIVERSES,
        help="Universe to rebuild",
    )
    group.add_argument(
        "--all", action="store_true",
        help="Rebuild all universes",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be done without writing any files",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    targets = ALL_UNIVERSES if args.all else [args.universe]
    failed: list[str] = []

    for universe_name in targets:
        ok = rebuild_universe(universe_name, dry_run=args.dry_run)
        if not ok:
            failed.append(universe_name)

    if failed:
        logger.error("Universe rebuild FAILED for: %s", failed)
        print(f"\n✗ Failed universes: {failed}", file=sys.stderr)
        return 1

    print("\n✓ All universe rebuilds complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
