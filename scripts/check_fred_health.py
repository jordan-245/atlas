#!/usr/bin/env python3
"""FRED API health check.

Verifies that FRED data is reachable, returns non-empty series, and
is not stale (latest data point within acceptable lag per series frequency).

Exit codes:
    0  — all series healthy
    1  — one or more series failed / stale / key missing

Usage:
    python3 scripts/check_fred_health.py
    python3 scripts/check_fred_health.py --json
    python3 scripts/check_fred_health.py --log-dir logs/

Expected cron / systemd entry (managed by atlas-fred-health.timer):
    /usr/bin/flock -n /tmp/atlas_fred_health.lock \\
        /usr/bin/timeout 60 /usr/bin/python3 /root/atlas/scripts/check_fred_health.py
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from utils.telegram import notify

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

# ── Optional top-level import (enables test patching) ────────────────────────
# Imported at module level so tests can patch scripts.check_fred_health.FREDClient.
try:
    from data.fred import FREDClient as FREDClient  # noqa: F401
except ImportError:
    FREDClient = None  # type: ignore[assignment,misc]

# ── Checked series: (method_name, display_name, max_lag_days) ─────────────────
# max_lag_days is generous for monthly series (FEDFUNDS published end-of-month)
# and tight for daily series (T10Y2Y, BAMLC0A0CM close on weekends/holidays).
_CHECKS: list[tuple[str, str, int]] = [
    ("get_yield_curve_slope", "Yield Curve (T10Y2Y)",  5),   # daily
    ("get_credit_oas",        "Credit OAS (BAMLC0A0CM)", 5), # daily
    ("get_fed_funds_rate",    "Fed Funds (FEDFUNDS)",  60),  # monthly (FRED publishes ~6w lag)
]

LOG_FILE_NAME = "fred_health.log"


# ── Logging setup ──────────────────────────────────────────────────────────────

def _setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    fmt = "%(asctime)s [fred-health] %(levelname)s %(message)s"
    logging.basicConfig(
        level=logging.INFO,
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(str(log_path)),
        ],
    )
    return logging.getLogger(__name__)


# ── Telegram helper ────────────────────────────────────────────────────────────

# ── Core checks ────────────────────────────────────────────────────────────────

def _check_key(logger: logging.Logger) -> bool:
    """Return True iff a FRED API key is present in secrets."""
    try:
        if FREDClient is None:
            logger.warning("FREDClient not available")
            return False
        client = FREDClient()
        if not client.api_key:
            logger.warning("FRED API key not found in ~/.atlas-secrets.json")
            return False
        return True
    except Exception as exc:
        logger.warning("FREDClient import/init failed: %s", exc)
        return False


def _check_series(
    method_name: str,
    display_name: str,
    max_lag_days: int,
    logger: logging.Logger,
) -> dict[str, Any]:
    """Run a single series check.

    Returns a result dict with keys:
        ok (bool), name (str), latest_date (str|None), n_obs (int), reason (str)
    """
    result: dict[str, Any] = {
        "ok": False,
        "name": display_name,
        "latest_date": None,
        "n_obs": 0,
        "reason": "",
    }
    try:
        if FREDClient is None:
            result["reason"] = "FREDClient not importable"
            return result
        client = FREDClient()
        method = getattr(client, method_name)
        series = method()

        if series is None or len(series) == 0:
            result["reason"] = "empty series returned"
            logger.warning("%s — empty series returned", display_name)
            return result

        # Drop any remaining NaN tail
        series = series.dropna()
        if len(series) == 0:
            result["reason"] = "all values are NaN"
            logger.warning("%s — all values NaN", display_name)
            return result

        result["n_obs"] = len(series)
        latest_ts = series.index[-1]
        # Normalise to a plain date
        if hasattr(latest_ts, "date"):
            latest_date = latest_ts.date()
        else:
            latest_date = datetime.strptime(str(latest_ts)[:10], "%Y-%m-%d").date()

        result["latest_date"] = latest_date.isoformat()
        today = datetime.now(tz=timezone.utc).date()
        lag_days = (today - latest_date).days

        if lag_days > max_lag_days:
            result["reason"] = (
                f"data stale: latest={latest_date} is {lag_days}d old (max {max_lag_days}d)"
            )
            logger.warning("%s — %s", display_name, result["reason"])
            return result

        result["ok"] = True
        result["reason"] = f"OK ({lag_days}d lag, {len(series)} obs)"
        logger.info("%s — %s", display_name, result["reason"])
        return result

    except Exception as exc:
        result["reason"] = f"exception: {exc}"
        logger.warning("%s — exception during check: %s", display_name, exc)
        return result


# ── Main ───────────────────────────────────────────────────────────────────────

def run_checks(log_dir: Path) -> tuple[bool, list[dict[str, Any]]]:
    """Run all FRED health checks.

    Returns:
        (all_ok: bool, results: list of check result dicts)
    """
    logger = logging.getLogger(__name__)
    results: list[dict[str, Any]] = []

    # Step 1 — key check
    key_ok = _check_key(logger)
    key_result: dict[str, Any] = {
        "ok": key_ok,
        "name": "API Key",
        "latest_date": None,
        "n_obs": None,
        "reason": "present" if key_ok else "missing from ~/.atlas-secrets.json",
    }
    results.append(key_result)

    if not key_ok:
        return False, results

    # Step 2 — series checks
    for method_name, display_name, max_lag_days in _CHECKS:
        r = _check_series(method_name, display_name, max_lag_days, logger)
        results.append(r)

    all_ok = all(r["ok"] for r in results)
    return all_ok, results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="FRED API health check — verifies key, data freshness, series availability.",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=_ATLAS_ROOT / "logs",
        help="Directory for fred_health.log (default: <atlas>/logs/).",
    )
    parser.add_argument(
        "--json",
        dest="as_json",
        action="store_true",
        help="Output machine-readable JSON to stdout.",
    )
    args = parser.parse_args(argv)

    logger = _setup_logging(args.log_dir)
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    all_ok, results = run_checks(args.log_dir)

    # ── Summary line ───────────────────────────────────────────────────────────
    healthy = sum(1 for r in results if r["ok"])
    total = len(results)
    latest_dates = [r["latest_date"] for r in results if r["latest_date"]]
    latest = max(latest_dates) if latest_dates else "n/a"

    if all_ok:
        logger.info("OK: %d/%d series healthy, latest %s", healthy, total, latest)
    else:
        failures = [r for r in results if not r["ok"]]
        for f in failures:
            msg = f"⚠️ FRED health check failed: {f['name']} — {f['reason']}"
            logger.warning(msg)
            notify(msg, category="health")

    # ── JSON output ────────────────────────────────────────────────────────────
    if args.as_json:
        payload = {
            "ok": all_ok,
            "checked_at": now_iso,
            "healthy": healthy,
            "total": total,
            "latest_date": latest,
            "results": results,
        }
        print(json.dumps(payload, indent=2))

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
