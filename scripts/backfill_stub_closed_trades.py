#!/usr/bin/env python3
"""Backfill stub closed_trades rows in live state files.

Stubs are rows where entry_price=0, pnl=0, strategy='unknown' — written by
reconcile_exit_fills when the entry context was missing from TradeLedger at
the time of fill detection.

This script:
 1. Reads live_sp500.json closed_trades stubs.
 2. For each stub, looks up real entry data from atlas.db trades table
    (matched by ticker + exit_price + shares).
 3. Backfills entry_price, strategy, pnl, pnl_pct, holding_days, entry_date.
 4. For tickers that belong to a different universe, MOVES the row to the
    correct live_<universe>.json file (deduping against existing rows).
 5. Mirrors the same fix into atlas.db trades rows matching the stub's
    (ticker, exit_price) pair if strategy was reconciled/unknown.

Universe resolution priority:
  1. atlas.db trades.universe (authoritative — knows which universe managed it)
  2. universe.membership.derive_universe()

Usage:
    python3 scripts/backfill_stub_closed_trades.py --dry-run   # preview
    python3 scripts/backfill_stub_closed_trades.py --apply     # write changes
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("backfill_stub_closed_trades")

STATE_DIR = PROJECT / "brokers" / "state"
DB_PATH = PROJECT / "data" / "atlas.db"

# Valid live state file market IDs
VALID_MARKETS = ("sp500", "sector_etfs", "commodity_etfs")


def _load_state(market_id: str) -> dict:
    path = STATE_DIR / f"live_{market_id}.json"
    if not path.exists():
        return {"market_id": market_id, "closed_trades": [], "positions": []}
    with open(path) as f:
        return json.load(f)


def _save_state(market_id: str, state: dict, dry_run: bool) -> None:
    path = STATE_DIR / f"live_{market_id}.json"
    state["last_saved"] = datetime.now().isoformat()
    if dry_run:
        log.info("[DRY-RUN] Would save %s (%d closed_trades)",
                 path.name, len(state.get("closed_trades", [])))
        return
    with open(path, "w") as f:
        json.dump(state, f, indent=2)
    log.info("Saved %s (%d closed_trades)", path.name, len(state.get("closed_trades", [])))


def _is_stub(trade: dict) -> bool:
    return (
        trade.get("entry_price") in (0, None, 0.0)
        or trade.get("pnl") in (0, None, 0.0)
        or trade.get("strategy") in ("unknown", None, "")
    )


def _lookup_db_entry(ticker: str, exit_price: float) -> Optional[dict]:
    """Find the best matching closed trade in atlas.db by ticker + exit_price.

    Preference order:
      1. Non-reconciled/unknown strategy (real trade)
      2. Best exit_price match (closest to exact)
      3. Lowest id (first-inserted = canonical)
    """
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, ticker, universe, strategy, entry_price, exit_price,
                   pnl, shares, entry_date, exit_date, status, superseded
            FROM trades
            WHERE ticker=? AND abs(exit_price - ?) < 0.02
              AND status='closed' AND superseded=0
            ORDER BY
              CASE WHEN strategy NOT IN ('reconciled','unknown','') THEN 0 ELSE 1 END,
              abs(exit_price - ?) ASC,
              id ASC
            """,
            (ticker, exit_price, exit_price),
        ).fetchall()
        if rows:
            return dict(rows[0])
        return None
    finally:
        conn.close()


def _resolve_target_universe(ticker: str, db_row: dict, current_market: str) -> str:
    """Determine the correct target universe.

    Priority:
      1. atlas.db trades.universe field (authoritative — records which market managed it)
      2. universe.membership.derive_universe()
      3. Fallback: keep in current_market
    """
    db_universe = db_row.get("universe") or ""
    if db_universe in VALID_MARKETS:
        return db_universe

    try:
        from universe.membership import derive_universe
        result = derive_universe(ticker)
        if result and result in VALID_MARKETS:
            return result
    except Exception as exc:
        log.debug("derive_universe failed for %s: %s", ticker, exc)

    return current_market


def _is_duplicate_in_target(trade: dict, target_closed: list[dict]) -> bool:
    """True if a real (non-stub) row for this ticker+exit already exists."""
    for t in target_closed:
        if (
            t.get("ticker") == trade.get("ticker")
            and abs(float(t.get("exit_price") or 0) - float(trade.get("exit_price") or 0)) < 0.02
            and float(t.get("entry_price") or 0) > 0
        ):
            return True
    return False


def _build_filled_trade(stub: dict, db_row: dict) -> dict:
    """Merge atlas.db data into stub, returning a complete trade record."""
    entry_price = float(db_row["entry_price"] or 0)
    exit_price = float(stub.get("exit_price") or db_row.get("exit_price") or 0)
    shares = int(stub.get("shares") or db_row.get("shares") or 0)

    pnl = round((exit_price - entry_price) * shares, 4) if entry_price else 0.0
    pnl_pct = (
        round((exit_price - entry_price) / entry_price * 100, 4)
        if entry_price else 0.0
    )

    entry_date_str = str(db_row.get("entry_date") or "")[:10]
    exit_date_str = str(stub.get("exit_date") or db_row.get("exit_date") or "")[:10]
    holding_days: Optional[int] = None
    if entry_date_str and exit_date_str:
        try:
            d0 = datetime.strptime(entry_date_str, "%Y-%m-%d")
            d1 = datetime.strptime(exit_date_str, "%Y-%m-%d")
            holding_days = max(0, (d1 - d0).days)
        except ValueError:
            pass

    filled = dict(stub)
    filled["entry_price"] = entry_price
    filled["strategy"] = db_row.get("strategy") or "reconciled"
    filled["pnl"] = pnl
    filled["pnl_pct"] = pnl_pct
    if entry_date_str:
        filled["entry_date"] = entry_date_str
    if holding_days is not None:
        filled["holding_days"] = holding_days
    filled.pop("reconciled", None)
    filled["backfilled"] = True
    return filled


def _update_db_strategy(db_row_id: int, strategy: str, dry_run: bool) -> None:
    if dry_run:
        log.info("[DRY-RUN] Would update trades id=%d strategy→%s", db_row_id, strategy)
        return
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.execute(
            "UPDATE trades SET strategy=?, updated_at=datetime('now') "
            "WHERE id=? AND strategy IN ('reconciled','unknown','')",
            (strategy, db_row_id),
        )
        conn.commit()
        log.info("atlas.db updated trades id=%d strategy→%s", db_row_id, strategy)
    finally:
        conn.close()


def _fix_db_null_pnl(dry_run: bool) -> int:
    """Fix NULL pnl rows in atlas.db where entry+exit+shares are present."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, ticker, entry_price, exit_price, shares
               FROM trades WHERE pnl IS NULL AND exit_price IS NOT NULL
               AND entry_price IS NOT NULL AND entry_price > 0
               AND shares IS NOT NULL"""
        ).fetchall()
        count = 0
        for row in rows:
            pnl = round(
                (float(row["exit_price"]) - float(row["entry_price"])) * int(row["shares"]),
                4,
            )
            if dry_run:
                log.info("[DRY-RUN] Fix NULL pnl: trades id=%d %s pnl→%.4f",
                         row["id"], row["ticker"], pnl)
            else:
                conn.execute(
                    "UPDATE trades SET pnl=?, updated_at=datetime('now') WHERE id=?",
                    (pnl, row["id"]),
                )
            count += 1
        if not dry_run:
            conn.commit()
        return count
    finally:
        conn.close()


def run(dry_run: bool) -> dict:
    sp500_state = _load_state("sp500")
    sector_state = _load_state("sector_etfs")
    commodity_state = _load_state("commodity_etfs")

    market_states: dict[str, dict] = {
        "sp500": sp500_state,
        "sector_etfs": sector_state,
        "commodity_etfs": commodity_state,
    }

    sp500_closed: list[dict] = sp500_state.get("closed_trades", [])
    stubs = [(i, t) for i, t in enumerate(sp500_closed) if _is_stub(t)]

    log.info("live_sp500.json: %d closed_trades, %d stubs to process",
             len(sp500_closed), len(stubs))

    stats: dict = {
        "stubs_found": len(stubs),
        "backfilled": [],
        "moved": [],
        "deduped_removed": [],
        "orphaned": [],
        "db_updates": 0,
        "null_pnl_fixed": 0,
    }

    rows_to_remove_from_sp500: list[int] = []
    _modified_markets: set[str] = set()

    for idx, stub in stubs:
        ticker = stub.get("ticker", "???")
        exit_price = float(stub.get("exit_price") or 0)
        shares = int(stub.get("shares") or 0)
        exit_date = stub.get("exit_date", "")

        log.info("Stub idx=%d %s exit=%.4f shares=%d date=%s",
                 idx, ticker, exit_price, shares, exit_date)

        db_row = _lookup_db_entry(ticker, exit_price)
        if db_row is None:
            log.warning("No atlas.db match for %s exit=%.4f → reconciled-orphan",
                        ticker, exit_price)
            if not dry_run:
                sp500_closed[idx]["strategy"] = "reconciled-orphan"
            stats["orphaned"].append(
                {"ticker": ticker, "exit_price": exit_price, "exit_date": exit_date,
                 "reason": "no_atlas_db_match"}
            )
            continue

        log.info("  DB match id=%d entry=%.4f strategy=%s universe=%s",
                 db_row["id"], db_row["entry_price"], db_row["strategy"], db_row["universe"])

        target_universe = _resolve_target_universe(ticker, db_row, "sp500")
        filled = _build_filled_trade(stub, db_row)

        stats["backfilled"].append({
            "ticker": ticker, "exit_price": exit_price,
            "entry_price": filled["entry_price"], "strategy": filled["strategy"],
            "target_universe": target_universe, "db_id": db_row["id"],
        })

        if target_universe == "sp500":
            if not dry_run:
                sp500_closed[idx] = filled
            log.info("  → sp500 in-place: entry=%.4f strategy=%s pnl=%.4f",
                     filled["entry_price"], filled["strategy"], filled["pnl"])
        else:
            target_closed = market_states[target_universe].setdefault("closed_trades", [])

            if _is_duplicate_in_target(filled, target_closed):
                log.info("  → %s already has real row for %s — dedup/remove from sp500",
                         target_universe, ticker)
                stats["deduped_removed"].append(
                    {"ticker": ticker, "from": "sp500", "to": target_universe,
                     "reason": "already_in_target"}
                )
            else:
                log.info("  → moving to %s: entry=%.4f strategy=%s pnl=%.4f",
                         target_universe, filled["entry_price"], filled["strategy"],
                         filled["pnl"])
                if not dry_run:
                    target_closed.append(filled)
                stats["moved"].append({
                    "ticker": ticker, "from": "sp500", "to": target_universe,
                    "entry_price": filled["entry_price"], "pnl": filled["pnl"],
                })
                _modified_markets.add(target_universe)

            rows_to_remove_from_sp500.append(idx)

        # Update atlas.db strategy if it was reconciled/unknown
        db_strat = db_row.get("strategy") or ""
        if db_strat in ("reconciled", "unknown", ""):
            real_strat = filled.get("strategy") or "reconciled"
            _update_db_strategy(db_row["id"], real_strat, dry_run)
            stats["db_updates"] += 1

    # Remove cross-universe / deduped stubs from sp500
    if not dry_run:
        for idx in sorted(rows_to_remove_from_sp500, reverse=True):
            sp500_closed.pop(idx)
        sp500_state["closed_trades"] = sp500_closed

    # Fix NULL pnl rows in atlas.db
    stats["null_pnl_fixed"] = _fix_db_null_pnl(dry_run)

    # Save all modified state files
    _save_state("sp500", sp500_state, dry_run)
    for mkt in ("sector_etfs", "commodity_etfs"):
        if mkt in _modified_markets or not dry_run:
            _save_state(mkt, market_states[mkt], dry_run)

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview without writing (default if neither flag given)")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes")
    args = parser.parse_args()

    # Default to dry-run if neither flag
    dry_run = not args.apply

    if dry_run:
        log.info("=== DRY-RUN mode (pass --apply to commit) ===")
    else:
        log.info("=== APPLY mode ===")

    stats = run(dry_run=dry_run)

    print("\n=== Backfill Summary ===")
    print(f"Stubs found:         {stats['stubs_found']}")
    print(f"Backfilled total:    {len(stats['backfilled'])}")
    sp500_stay = sum(1 for s in stats["backfilled"] if s["target_universe"] == "sp500")
    print(f"  Stayed in sp500:   {sp500_stay}")
    print(f"  Moved to other:    {len(stats['moved'])}")
    print(f"  Deduped (removed): {len(stats['deduped_removed'])}")
    print(f"Orphaned:            {len(stats['orphaned'])}")
    print(f"DB strategy updates: {stats['db_updates']}")
    print(f"DB null-pnl fixed:   {stats['null_pnl_fixed']}")

    if stats["moved"]:
        print("\nMoved:")
        for m in stats["moved"]:
            print(f"  {m['ticker']:6s} sp500→{m['to']:<16} "
                  f"entry={m['entry_price']:.2f} pnl={m['pnl']:.2f}")
    if stats["deduped_removed"]:
        print("\nDeduped (sp500 stub removed, real row exists in target):")
        for d in stats["deduped_removed"]:
            print(f"  {d['ticker']:6s} → {d['to']} ({d['reason']})")
    if stats["orphaned"]:
        print("\nOrphaned (no atlas.db match → marked reconciled-orphan):")
        for o in stats["orphaned"]:
            print(f"  {o['ticker']:6s} exit={o['exit_price']:.4f} date={o['exit_date']}")

    if dry_run:
        print("\n[DRY-RUN] Pass --apply to commit changes.")


if __name__ == "__main__":
    main()
