#!/usr/bin/env python3
"""Reconcile SQLite-open trades that the broker no longer holds.

Background
----------
Trades.status='open' rows must always have a corresponding broker position
(in brokers/state/live_*.json). When a position exits via stop/TP fill but
the dual-write to SQLite is missed, the trade row stays open forever,
polluting PnL queries and dashboards.

This script audits for that mismatch and offers two modes:
  --report   List orphans, do not modify
  --close    Mark each orphan as closed with exit_reason='reconciled_orphan'

Exit price is derived from the broker's most-recent FILL SELL activity for
that ticker matching the qty when possible. Falls back to the last known
price (entry_price) with a logged warning if no fill activity is found.

Idempotent: re-running on a clean state is a no-op.

Usage
-----
    python3 scripts/reconcile_sqlite_orphan_opens.py --report
    python3 scripts/reconcile_sqlite_orphan_opens.py --close [--ticker MU,UNG]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

logger = logging.getLogger(__name__)

STATE_DIR = PROJECT / "brokers" / "state"


def load_broker_held_tickers() -> set[str]:
    """Return the set of all tickers held across all live_*.json state files."""
    held: set[str] = set()
    for state_file in sorted(STATE_DIR.glob("live_*.json")):
        try:
            data = json.loads(state_file.read_text())
            for pos in data.get("positions", []):
                t = pos.get("ticker", "")
                if t:
                    held.add(t)
        except Exception as exc:
            logger.warning("Failed to read %s: %s", state_file, exc)
    return held


def find_orphan_open_trades() -> list[dict]:
    """Return SQLite trades.status='open' rows whose tickers are NOT in any state file."""
    from db import atlas_db

    held_tickers = load_broker_held_tickers()
    orphans: list[dict] = []

    with atlas_db.get_db() as db:
        rows = db.execute(
            "SELECT id, ticker, strategy, universe, entry_date, entry_price, "
            "shares, stop_price, take_profit FROM trades WHERE status='open' "
            "ORDER BY id"
        ).fetchall()

    for row in rows:
        if row["ticker"] not in held_tickers:
            orphans.append(dict(row))
    return orphans


def fetch_broker_exit_price(ticker: str, qty: int) -> tuple[Optional[float], str]:
    """Return (exit_price, source) for the given ticker.

    Tries Alpaca account activities for the most recent FILL SELL of this
    ticker matching the qty (or any qty if exact match unavailable).
    Returns (None, 'unavailable') on failure.
    """
    try:
        from utils.config import get_active_config
        from brokers.registry import get_live_broker
        # Use any market's broker connection — they all share the Alpaca account
        cfg = get_active_config("sp500")
        broker = get_live_broker(cfg)
        if broker is None:
            return None, "no_broker"
        if not broker.connect():
            return None, "broker_connect_failed"
    except Exception as exc:
        logger.warning("Could not connect broker for exit price lookup: %s", exc)
        return None, f"connect_error:{exc}"

    try:
        from alpaca.broker.requests import GetAccountActivitiesRequest
        from alpaca.trading.enums import ActivityType

        # Pull last 30 days of FILL activities
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(days=30)
        req = GetAccountActivitiesRequest(
            activity_types=[ActivityType.FILL],
            after=since,
        )
        activities = broker._broker_call(
            lambda r: broker._trade_client.get("/account/activities", r.to_request_fields()) or [],
            req,
        ) or []
    except Exception as exc:
        logger.warning("Activities API failed for %s: %s", ticker, exc)
        return None, f"api_error:{exc}"

    # Find most recent SELL fill for this ticker
    candidates: list[tuple[datetime, float, int]] = []
    for act in activities:
        if isinstance(act, dict):
            sym = act.get("symbol")
            side = act.get("side", "").lower()
            qty_str = act.get("qty")
            price_str = act.get("price")
            tx_time = act.get("transaction_time")
        else:
            sym = getattr(act, "symbol", None)
            _side = getattr(act, "side", None)
            side = str(_side).lower() if _side else ""
            qty_str = getattr(act, "qty", None)
            price_str = getattr(act, "price", None)
            tx_time = getattr(act, "transaction_time", None)

        if sym != ticker or "sell" not in side:
            continue
        try:
            tx_dt = (datetime.fromisoformat(str(tx_time).replace("Z", "+00:00"))
                     if not isinstance(tx_time, datetime) else tx_time)
            if tx_dt.tzinfo is None:
                tx_dt = tx_dt.replace(tzinfo=timezone.utc)
            candidates.append((tx_dt, float(price_str), int(float(qty_str))))
        except (ValueError, TypeError) as exc:
            logger.debug("Bad activity row for %s: %s", ticker, exc)
            continue

    if not candidates:
        return None, "no_fills_found"

    # Prefer exact qty match, else most recent
    candidates.sort(key=lambda c: c[0], reverse=True)  # newest first
    exact = [c for c in candidates if c[2] == qty]
    if exact:
        return exact[0][1], "broker_fill_exact_qty"
    return candidates[0][1], "broker_fill_latest"


def close_orphan_trade(
    trade_id: int,
    ticker: str,
    qty: int,
    entry_price: float,
    dry_run: bool,
) -> tuple[bool, dict]:
    """Mark a trade as closed. Returns (success, info_dict)."""
    exit_price, source = fetch_broker_exit_price(ticker, qty)
    if exit_price is None:
        # Fallback: use entry_price (PnL=0). Loud warning.
        logger.warning(
            "No broker fill found for %s — using entry_price=%.2f as exit_price (PnL=0)",
            ticker, entry_price,
        )
        exit_price = entry_price
        source = f"fallback_entry_price ({source})"

    pnl = round((exit_price - entry_price) * qty, 2)

    info = {
        "id": trade_id,
        "ticker": ticker,
        "qty": qty,
        "entry_price": entry_price,
        "exit_price": exit_price,
        "pnl": pnl,
        "source": source,
    }

    if dry_run:
        return True, info

    from db import atlas_db
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        with atlas_db.get_db() as db:
            db.execute(
                """UPDATE trades
                   SET status='closed', exit_date=?, exit_price=?,
                       pnl=?, exit_reason='reconciled_orphan',
                       updated_at=datetime('now')
                   WHERE id=?""",
                (now_iso, exit_price, pnl, trade_id),
            )
        info["status"] = "closed"
    except Exception as exc:
        logger.error("Failed to close trade id=%d: %s", trade_id, exc)
        return False, {**info, "error": str(exc)}

    return True, info


def run(report_only: bool = False, ticker_filter: Optional[set[str]] = None) -> int:
    orphans = find_orphan_open_trades()
    if ticker_filter:
        orphans = [o for o in orphans if o["ticker"] in ticker_filter]

    if not orphans:
        print("No SQLite-orphan open trades found — clean state.")
        return 0

    print(f"Found {len(orphans)} SQLite-orphan open trade(s):\n")
    for o in orphans:
        print(f"  id={o['id']:4d}  {o['ticker']:6s}  {o['strategy']:20s}  "
              f"{o['universe']:15s}  qty={o['shares']}  entry={o['entry_price']}  "
              f"date={o['entry_date']}")

    if report_only:
        print("\nReport-only mode — no changes applied.")
        return 0

    print("\nClosing orphan trades…")
    failures = 0
    for o in orphans:
        ok, info = close_orphan_trade(
            o["id"], o["ticker"], o["shares"], o["entry_price"], dry_run=False,
        )
        if ok:
            print(f"  CLOSED id={info['id']} {info['ticker']}: "
                  f"exit_price=${info['exit_price']:.2f} "
                  f"PnL=${info['pnl']:+.2f} [{info['source']}]")
        else:
            print(f"  FAILED id={info['id']} {info['ticker']}: {info.get('error')}")
            failures += 1
    return 1 if failures else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="store_true",
                        help="Report only, no changes")
    parser.add_argument("--close", action="store_true",
                        help="Close orphan trades (default; explicit for clarity)")
    parser.add_argument("--ticker", default=None,
                        help="Comma-separated tickers to limit to (e.g. MU,UNG)")
    args = parser.parse_args()

    if not (args.report or args.close):
        parser.error("must specify --report or --close")

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    ticker_filter = set(args.ticker.split(",")) if args.ticker else None
    sys.exit(run(report_only=args.report, ticker_filter=ticker_filter))


if __name__ == "__main__":
    main()
