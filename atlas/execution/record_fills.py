"""Reconcile recorded orders against actual broker fills -> fills.jsonl.

Closes the G6/G7 data gap in the go-live gate (board memo 2026-06-09): slippage
(fill price vs decision price) and broker-error rate can only be scored from real
fill data. Runs daily in the forward-paper cycle, AFTER record_returns and BEFORE
the new rebalance, reconciling any not-yet-reconciled run rows (fault-tolerant:
a missed day is picked up on the next).

For each order with an order_id in runs.jsonl that has no row in fills.jsonl yet,
query the broker and write:
    {date, ticker, side, qty, decision_px, fill_px, filled_qty, status,
     slippage_bps (signed; + = adverse), order_id}

Usage: python3 -m atlas.execution.record_fills [--days 5]
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from atlas.kernel.paths import LIVE_DATA_DIR as LIVE_DATA
from atlas.execution.registry import deployed

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 5  # reconcile anything missed in the last week of runs


def _jsonl(p: Path) -> list:
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _slippage_bps(side: str, decision_px: float, fill_px: float) -> float:
    """Signed slippage in basis points; positive = adverse (paid more / received less)."""
    if not decision_px or not fill_px:
        return 0.0
    raw = (fill_px - decision_px) / decision_px * 1e4
    return raw if side == "BUY" else -raw


def reconcile_book(name: str, broker) -> int:
    d = LIVE_DATA / name
    runs = _jsonl(d / "runs.jsonl")[-LOOKBACK_DAYS * 3:]
    done = {f["order_id"] for f in _jsonl(d / "fills.jsonl") if f.get("order_id")}
    pending = []
    for run in runs:
        if run.get("dry_run") or run.get("blocked"):
            continue
        for o in run.get("orders", []):
            oid = o.get("order_id")
            if oid and oid not in done:
                pending.append((run["date"], o))
    if not pending:
        return 0

    n = 0
    with (d / "fills.jsonl").open("a") as fh:
        for date, o in pending:
            try:
                res = broker.get_order_status(o["order_id"])
            except Exception as e:
                logger.warning("fill query failed %s %s: %s", name, o["order_id"], e)
                continue
            status = getattr(getattr(res, "status", None), "value", None) or str(getattr(res, "status", "?"))
            fill_px = float(getattr(res, "fill_price", 0.0) or 0.0)
            rec = {"date": date, "ticker": o["ticker"], "side": o["side"], "qty": o["qty"],
                   "decision_px": o.get("px"), "fill_px": fill_px or None,
                   "filled_qty": int(getattr(res, "filled_qty", 0) or 0),
                   "status": status,
                   "slippage_bps": round(_slippage_bps(o["side"], o.get("px") or 0.0, fill_px), 2)
                                   if fill_px else None,
                   "order_id": o["order_id"]}
            fh.write(json.dumps(rec) + "\n")
            n += 1
    return n


def main() -> int:
    from atlas.execution.daily import _build_broker
    total = 0
    for s in deployed():
        broker = _build_broker(s)
        if broker is None or not getattr(broker, "is_connected", False):
            logger.warning("record_fills: broker unavailable for %s — will retry next cycle", s.name)
            continue
        n = reconcile_book(s.name, broker)
        logger.info("record_fills %s: %d fills reconciled", s.name, n)
        print(f"[record_fills] {s.name}: {n} fills reconciled")
        total += n
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
