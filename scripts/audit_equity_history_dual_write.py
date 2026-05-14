#!/usr/bin/env python3
"""Cross-market equity_history dual-write audit.

Compares JSON equity_history (broker-authoritative) against SQLite for every
live_*.json state file.  Deduplicates JSON rows by date taking the LAST entry
(matches verify_dual_write Check-7 semantics).

Usage:
    python3 scripts/audit_equity_history_dual_write.py

Output:
    stdout — human-readable divergence report
    logs/equity_audit_2026-05-14.json — machine-readable findings
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BROKER_STATE_DIR = PROJECT_ROOT / "brokers" / "state"
CONFIG_ACTIVE_DIR = PROJECT_ROOT / "config" / "active"
DB_PATH = PROJECT_ROOT / "data" / "atlas.db"
LOGS_DIR = PROJECT_ROOT / "logs"
OUT_FILE = LOGS_DIR / "equity_audit_2026-05-14.json"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

EQUITY_DELTA_THRESHOLD = 0.01  # $-tolerance before flagging a divergence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_live_enabled_markets() -> list[str]:
    """Return market_ids whose config has trading.live_enabled=true."""
    live = []
    for cfg_path in sorted(CONFIG_ACTIVE_DIR.glob("*.json")):
        if cfg_path.stem.endswith(".bak") or "-" in cfg_path.stem:
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
            market_id = cfg.get("market_id") or cfg_path.stem
            if cfg.get("trading", {}).get("live_enabled", False):
                live.append(market_id)
        except Exception as exc:
            logger.warning("Could not parse %s: %s", cfg_path, exc)
    return live


def _load_all_markets() -> dict[str, bool]:
    """Return {market_id: live_enabled} for every active config."""
    markets: dict[str, bool] = {}
    for cfg_path in sorted(CONFIG_ACTIVE_DIR.glob("*.json")):
        if cfg_path.stem.endswith(".bak") or "-" in cfg_path.stem:
            continue
        try:
            cfg = json.loads(cfg_path.read_text())
            market_id = cfg.get("market_id") or cfg_path.stem
            markets[market_id] = bool(cfg.get("trading", {}).get("live_enabled", False))
        except Exception as exc:
            logger.warning("Could not parse %s: %s", cfg_path, exc)
    return markets


def _json_equity_deduped(market_id: str) -> dict[str, float]:
    """Load live_{market}.json equity_history, deduplicate by date (last wins)."""
    state_file = BROKER_STATE_DIR / f"live_{market_id}.json"
    if not state_file.exists():
        return {}
    try:
        state = json.loads(state_file.read_text())
    except Exception as exc:
        logger.error("Could not parse %s: %s", state_file, exc)
        return {}
    result: dict[str, float] = {}
    for row in state.get("equity_history", []):
        dt = row.get("date")
        eq = row.get("equity")
        if dt and eq is not None:
            result[dt] = float(eq)  # last write wins
    return result


def _json_equity_raw_count(market_id: str) -> int:
    """Return raw (pre-dedup) equity_history row count from JSON."""
    state_file = BROKER_STATE_DIR / f"live_{market_id}.json"
    if not state_file.exists():
        return 0
    try:
        state = json.loads(state_file.read_text())
        return len(state.get("equity_history", []))
    except Exception:
        return 0


def _sqlite_equity(market_id: str) -> dict[str, float]:
    """Load equity_history rows for market from SQLite."""
    if not DB_PATH.exists():
        return {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT date, equity FROM equity_history WHERE market_id = ? ORDER BY date",
            (market_id,),
        ).fetchall()
        conn.close()
        return {r[0]: float(r[1]) for r in rows}
    except Exception as exc:
        logger.error("SQLite read failed for %s: %s", market_id, exc)
        return {}


def _audit_market(market_id: str, live_enabled: bool) -> dict[str, Any]:
    """Audit one market.  Returns a findings dict."""
    json_deduped = _json_equity_deduped(market_id)
    json_raw_n = _json_equity_raw_count(market_id)
    sql_map = _sqlite_equity(market_id)

    all_dates = sorted(set(json_deduped) | set(sql_map))
    divergences: list[dict] = []
    json_only: list[str] = []
    sqlite_only: list[str] = []
    matches: int = 0

    for dt in all_dates:
        j = json_deduped.get(dt)
        s = sql_map.get(dt)
        if j is None:
            sqlite_only.append(dt)
        elif s is None:
            json_only.append(dt)
        elif abs(j - s) > EQUITY_DELTA_THRESHOLD:
            divergences.append({
                "date": dt,
                "json_equity": j,
                "sqlite_equity": s,
                "diff": round(j - s, 2),
            })
        else:
            matches += 1

    status = "PASS" if (not divergences and not json_only) else "FAIL"

    return {
        "market_id": market_id,
        "live_enabled": live_enabled,
        "status": status,
        "json_raw_rows": json_raw_n,
        "json_deduped_rows": len(json_deduped),
        "sqlite_rows": len(sql_map),
        "matches": matches,
        "divergences": divergences,
        "json_only_dates": json_only,
        "sqlite_only_dates": sqlite_only,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    """Run audit across all markets.  Returns exit code (0=clean, 1=divergences)."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    all_markets = _load_all_markets()
    live_enabled_markets = [m for m, e in all_markets.items() if e]

    print("=" * 60)
    print("  Atlas equity_history dual-write audit")
    print(f"  Run: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    print()

    findings: list[dict] = []
    total_divergences = 0
    total_missing_json = 0
    total_missing_sqlite = 0

    # Primary pass: live_enabled markets (per spec)
    print("── Primary scan (live_enabled markets) ──")
    for market_id in sorted(live_enabled_markets):
        result = _audit_market(market_id, live_enabled=True)
        findings.append(result)
        _print_market_report(result)
        total_divergences += len(result["divergences"])
        total_missing_json += len(result["json_only_dates"])
        total_missing_sqlite += len(result["sqlite_only_dates"])

    if not live_enabled_markets:
        print("  (no live_enabled markets found)")

    # Supplementary pass: disabled markets
    disabled_markets = [m for m, e in all_markets.items() if not e]
    if disabled_markets:
        print()
        print("── Supplementary scan (disabled markets — informational) ──")
        for market_id in sorted(disabled_markets):
            result = _audit_market(market_id, live_enabled=False)
            findings.append(result)
            _print_market_report(result)
            # disabled divergences do NOT count toward exit code
    print()

    # Summary
    primary_fails = [f for f in findings if f["live_enabled"] and f["status"] == "FAIL"]
    supp_fails = [f for f in findings if not f["live_enabled"] and f["status"] == "FAIL"]

    print("=" * 60)
    print(f"  Primary (live_enabled): {len(live_enabled_markets)} markets, "
          f"{total_divergences} equity divergences, "
          f"{total_missing_json} JSON-only, {total_missing_sqlite} SQLite-only")
    if primary_fails:
        print(f"  ⚠  FAIL markets: {[f['market_id'] for f in primary_fails]}")
    else:
        print("  ✅ All live_enabled markets PASS")

    if supp_fails:
        print(f"  ℹ  Disabled-market divergences (supplementary): "
              f"{[f['market_id'] for f in supp_fails]}")
    print("=" * 60)

    # Write JSON audit
    audit_payload = {
        "run_at": datetime.now().isoformat(),
        "db_path": str(DB_PATH),
        "threshold": EQUITY_DELTA_THRESHOLD,
        "markets": findings,
        "summary": {
            "live_enabled_markets": sorted(live_enabled_markets),
            "disabled_markets": sorted(disabled_markets),
            "primary_equity_divergences": total_divergences,
            "primary_json_only_dates": total_missing_json,
            "primary_sqlite_only_dates": total_missing_sqlite,
            "primary_status": "PASS" if not primary_fails else "FAIL",
        },
    }
    OUT_FILE.write_text(json.dumps(audit_payload, indent=2))
    print(f"\n  Audit written → {OUT_FILE.relative_to(PROJECT_ROOT)}")

    return 1 if primary_fails else 0


def _print_market_report(result: dict) -> None:
    tag = "✅" if result["status"] == "PASS" else "⚠ "
    enabled_tag = "[live]" if result["live_enabled"] else "[disabled]"
    print(
        f"  {tag} {result['market_id']} {enabled_tag}: "
        f"JSON {result['json_deduped_rows']} (raw {result['json_raw_rows']}) | "
        f"SQLite {result['sqlite_rows']} | "
        f"matches={result['matches']} diverg={len(result['divergences'])} "
        f"json_only={len(result['json_only_dates'])} sqlite_only={len(result['sqlite_only_dates'])}"
    )
    for div in result["divergences"]:
        print(
            f"      ↳ DIVERGE {div['date']}: "
            f"JSON={div['json_equity']} SQLite={div['sqlite_equity']} "
            f"diff={div['diff']:+.2f}"
        )
    for dt in result["json_only_dates"]:
        print(f"      ↳ JSON-ONLY  {dt}")
    for dt in result["sqlite_only_dates"]:
        print(f"      ↳ SQLITE-ONLY {dt}")


if __name__ == "__main__":
    sys.exit(main())
