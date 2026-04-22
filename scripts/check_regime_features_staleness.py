#!/usr/bin/env python3
"""Regime-history feature staleness monitor.

Queries the ``regime_history`` table for the last 7 days and checks
whether key FRED-derived features (credit, yield curve, trend, risk)
are present and non-zero in the ``reasoning`` text.

Exit codes:
    0  — all features populated (or table absent — treated as skipped)
    1  — one or more features are NULL/missing for ≥7 consecutive days

Usage:
    python3 scripts/check_regime_features_staleness.py
    python3 scripts/check_regime_features_staleness.py --days 14
    python3 scripts/check_regime_features_staleness.py --json
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve()
_ATLAS_ROOT = _HERE.parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

# ── Features to track ─────────────────────────────────────────────────────────
# Each entry is (canonical_name, regex_pattern_in_reasoning).
# Regex must capture a numeric value from the reasoning string.
_FEATURE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("trend",       re.compile(r"\btrend\s+([+-]?\d+\.\d+)")),
    ("risk",        re.compile(r"\brisk\s+([+-]?\d+\.\d+)")),
    ("credit",      re.compile(r"\bcredit\s+([+-]?\d+\.\d+)")),
    ("yield_curve", re.compile(r"yield curve[^(]*\(([+-]?\d+\.\d+)\)")),
]

_ALERT_WINDOW_DAYS = 7   # alert if feature absent for this many consecutive days
LOG_FILE_NAME = "regime_staleness.log"


# ── Logging ────────────────────────────────────────────────────────────────────

def _setup_logging(log_dir: Path) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    fmt = "%(asctime)s [regime-staleness] %(levelname)s %(message)s"
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


# ── Telegram ───────────────────────────────────────────────────────────────────

def _send_telegram(msg: str) -> None:
    """Fire-and-forget alert; never raises."""
    try:
        from utils.telegram import send_message
        send_message(msg)
    except Exception as exc:
        logging.getLogger(__name__).warning("Telegram alert failed: %s", exc)


# ── Feature extraction ─────────────────────────────────────────────────────────

def _extract_features(reasoning: str | None) -> dict[str, float | None]:
    """Parse feature values from a regime_history.reasoning string."""
    out: dict[str, float | None] = {name: None for name, _ in _FEATURE_PATTERNS}
    if not reasoning:
        return out
    for name, pattern in _FEATURE_PATTERNS:
        m = pattern.search(reasoning)
        if m:
            try:
                out[name] = float(m.group(1))
            except ValueError:
                pass
    return out


# ── DB query ───────────────────────────────────────────────────────────────────

def _resolve_db_path() -> Path:
    import os
    env_override = os.environ.get("ATLAS_DB_PATH")
    if env_override:
        return Path(env_override)
    try:
        from db import atlas_db
        override = getattr(atlas_db, "_db_path_override", None)
        if override:
            return Path(override)
    except Exception:
        pass
    return _ATLAS_ROOT / "data" / "atlas.db"


def query_regime_features(db_path: Path, days: int) -> list[dict[str, Any]]:
    """Return per-row feature extractions from regime_history for last ``days`` days.

    Returns [] if the table is absent (treated as skipped by the caller).
    """
    try:
        with sqlite3.connect(str(db_path), timeout=30) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT date, regime_state, trend_score, risk_score, reasoning
                FROM regime_history
                WHERE date >= date('now', ?)
                ORDER BY date ASC
                """,
                (f"-{days} days",),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return []
        raise

    results: list[dict[str, Any]] = []
    for row in rows:
        features = _extract_features(row["reasoning"])
        results.append({
            "date": row["date"],
            "regime_state": row["regime_state"],
            "trend_score": row["trend_score"],
            "risk_score": row["risk_score"],
            **{f"feat_{k}": v for k, v in features.items()},
        })
    return results


# ── Staleness analysis ─────────────────────────────────────────────────────────

def analyze_staleness(
    rows: list[dict[str, Any]],
    alert_window: int,
) -> dict[str, Any]:
    """Check each feature for consecutive NULL days.

    Returns a dict:
        {
            "ok": bool,
            "n_rows": int,
            "features": {
                "<name>": {"populated": N, "missing": M, "consecutive_missing": K, "alert": bool}
            }
        }
    """
    feature_names = [name for name, _ in _FEATURE_PATTERNS]
    feature_stats: dict[str, dict[str, Any]] = {}

    for fname in feature_names:
        col = f"feat_{fname}"
        populated = sum(1 for r in rows if r.get(col) is not None)
        missing = len(rows) - populated

        # Count trailing consecutive missing (most recent streak)
        streak = 0
        for row in reversed(rows):
            if row.get(col) is None:
                streak += 1
            else:
                break

        alert = streak >= alert_window
        feature_stats[fname] = {
            "populated": populated,
            "missing": missing,
            "consecutive_missing": streak,
            "alert": alert,
        }

    all_ok = not any(s["alert"] for s in feature_stats.values())
    return {
        "ok": all_ok,
        "n_rows": len(rows),
        "features": feature_stats,
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def run(
    days: int,
    log_dir: Path,
    db_path: Path | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Run the staleness check. Returns (ok, report_dict)."""
    logger = logging.getLogger(__name__)

    resolved_db = db_path or _resolve_db_path()
    rows = query_regime_features(resolved_db, days)

    if rows == [] and not resolved_db.exists():
        logger.info("regime_history: table absent or DB missing — skipped")
        return True, {"skipped": True, "reason": "table absent"}

    if not rows:
        logger.info("regime_history: no rows in last %d days — skipped", days)
        return True, {"skipped": True, "reason": f"no rows in last {days} days"}

    report = analyze_staleness(rows, alert_window=_ALERT_WINDOW_DAYS)
    report["days_queried"] = days
    report["db_path"] = str(resolved_db)

    if report["ok"]:
        logger.info(
            "regime features OK: %d rows, all features populated in last %d days",
            len(rows), days,
        )
    else:
        for fname, stats in report["features"].items():
            if stats["alert"]:
                msg = (
                    f"⚠️ Regime feature '{fname}' missing for "
                    f"{stats['consecutive_missing']} consecutive days "
                    f"(last {days}d window) — FRED data may be stale"
                )
                logger.warning(msg)
                _send_telegram(msg)

    return report["ok"], report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=7,
        help="Lookback window in days (default: 7).",
    )
    parser.add_argument(
        "--log-dir", type=Path, default=_ATLAS_ROOT / "logs",
        help="Directory for regime_staleness.log (default: <atlas>/logs/).",
    )
    parser.add_argument(
        "--json", dest="as_json", action="store_true",
        help="Output machine-readable JSON to stdout.",
    )
    args = parser.parse_args(argv)

    logger = _setup_logging(args.log_dir)
    ok, report = run(days=args.days, log_dir=args.log_dir)

    if args.as_json:
        report["checked_at"] = datetime.now(tz=timezone.utc).isoformat()
        print(json.dumps(report, indent=2))

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
