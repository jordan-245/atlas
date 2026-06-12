#!/usr/bin/env python3
"""End-to-end smoke for the IB paper stack (Phase B step 5 of IB_MICRO_ADAPTER_PLAN).

Runs the full chain through the ATLAS ADAPTER (not raw ib_insync) so what we verify
is what production uses:

  connect -> account info -> MES price -> 1-lot LIMIT far from market -> SUBMITTED
  -> cancel -> CANCELLED -> positions parse

Read-only-ish: the limit order is placed 20% below market so it cannot fill, then
cancelled. Safe to re-run. Requires the gateway container up + authenticated.

Usage: python3 ops/ib-gateway/smoke_e2e.py [--port 4002]
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/root/atlas")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=4002)
    args = ap.parse_args()

    from atlas.brokers.base import OrderSide, OrderStatus, OrderType
    from atlas.brokers.ib.broker import IBBroker

    cfg = {"trading": {"mode": "paper"}, "ib": {"host": "127.0.0.1", "port": args.port}}
    b = IBBroker(cfg)

    def step(name, ok, detail=""):
        print(f"{'✅' if ok else '❌'} {name}{': ' + str(detail) if detail else ''}")
        if not ok:
            sys.exit(1)

    step("connect", b.connect())

    acct = b.get_account_info()
    step("account info", acct.equity > 0, f"equity ${acct.equity:,.0f} ({acct.currency})")

    px = b.get_prices(["MES"]).get("MES")
    step("MES price", px is not None and px > 1000, px)

    limit_px = round(px * 0.80 / 0.25) * 0.25          # 20% below market, tick-aligned
    res = b.place_order("MES", OrderSide.BUY, qty=1, price=limit_px,
                        order_type=OrderType.LIMIT, remark="phaseB-smoke")
    step("place far LIMIT", res.success and res.status in
         (OrderStatus.SUBMITTED, OrderStatus.PENDING),
         f"id={res.order_id} status={res.status} @ {limit_px}")

    time.sleep(2)
    c = b.cancel_order(res.order_id)
    step("cancel", c.success, c.message or "")

    time.sleep(2)
    st = b.get_order_status(res.order_id)
    step("status=CANCELLED", st.status == OrderStatus.CANCELLED, st.status)

    pos = b.get_positions()
    step("positions parse", isinstance(pos, list), f"{len(pos)} open")

    print("\n🎉 IB paper stack end-to-end PASS — adapter is live-verified (Phase B step 5).")
    b.disconnect()
    return 0


if __name__ == "__main__":
    sys.exit(main())
