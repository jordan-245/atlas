"""brokers/execution_analytics.py — Post-trade fee, slippage and history analysis.

Extracted from brokers/live_executor.py (decomposition #2 PR1.3).
Pure functions that take a connected broker as their only state dependency.

These 3 methods previously had zero unit-test coverage; extraction enables
future test additions without LiveExecutor instantiation.

Public surface
--------------
    get_fee_analysis(broker, days=90) -> dict
    get_slippage_analysis(broker, days=90) -> dict
    get_execution_history(broker, days=30) -> dict
"""
from __future__ import annotations

import logging

from brokers.base import OrderStatus
from brokers.execution_journal import journal_entry as _journal_entry

logger = logging.getLogger("atlas.execution_analytics")

def get_fee_analysis(broker, days: int = 90) -> dict:
    """Analyse actual fees vs assumed fees in config.

    Returns comparison report for backtest fee calibration.
    """
    if not broker:
        return {"error": "Not connected"}

    # Get filled orders to find order IDs
    orders = broker.get_history_orders(days=days)
    filled_ids = [
        o.order_id for o in orders
        if o.status.value in ("FILLED", "PARTIAL_FILLED") and o.order_id
    ]

    if not filled_ids:
        return {"total_orders": 0, "message": "No filled orders in period"}

    # Query actual fees
    fees = broker.get_order_fees(filled_ids[:50])  # API limit safety

    if not fees:
        return {"total_orders": len(filled_ids), "message": "Fee query returned no data"}

    # Compute actuals
    total_actual = sum(f.total_fee for f in fees)
    avg_actual = total_actual / len(fees) if fees else 0

    # Compute fee breakdown
    fee_breakdown = {}
    for f in fees:
        for name, amount in f.fee_details:
            fee_breakdown.setdefault(name, {"count": 0, "total": 0.0})
            fee_breakdown[name]["count"] += 1
            fee_breakdown[name]["total"] += amount

    # Compare with config assumptions
    config_flat = {}.get("fees", {}).get("commission_per_trade", 3.0)
    config_pct = {}.get("fees", {}).get("commission_pct", 0.0003)

    # Get average order value from filled orders
    order_map = {o.order_id: o for o in orders}
    order_values = []
    for f in fees:
        o = order_map.get(f.order_id)
        if o and o.fill_price > 0 and o.filled_qty > 0:
            order_values.append(o.fill_price * o.filled_qty)
    avg_order_value = sum(order_values) / len(order_values) if order_values else 0

    # Expected fee per config
    expected_per_trade = max(config_flat, avg_order_value * config_pct)

    report = {
        "period_days": days,
        "total_orders_filled": len(filled_ids),
        "orders_with_fees": len(fees),
        "total_actual_fees": round(total_actual, 2),
        "avg_actual_fee": round(avg_actual, 2),
        "fee_breakdown": {
            name: {"count": v["count"], "avg": round(v["total"] / v["count"], 2)}
            for name, v in fee_breakdown.items()
        },
        "config_commission_flat": config_flat,
        "config_commission_pct": config_pct,
        "avg_order_value": round(avg_order_value, 2),
        "expected_fee_per_config": round(expected_per_trade, 2),
        "fee_delta": round(avg_actual - expected_per_trade, 2),
        "fee_delta_pct": round(
            ((avg_actual - expected_per_trade) / expected_per_trade * 100)
            if expected_per_trade > 0 else 0, 1
        ),
    }

    _journal_entry("fee_analysis", report)
    return report


def get_slippage_analysis(broker, days: int = 90) -> dict:
    """Analyse actual slippage vs assumed slippage in config.

    Returns comparison report for backtest slippage calibration.
    """
    if not broker:
        return {"error": "Not connected"}

    slippage_data = broker.get_slippage_report(days=days)
    if not slippage_data:
        return {"total_orders": 0, "message": "No filled orders for slippage analysis"}

    buy_slips = [s for s in slippage_data if s.side == "BUY"]
    sell_slips = [s for s in slippage_data if s.side == "SELL"]

    config_slip = {}.get("fees", {}).get("slippage_pct", 0.001)

    def _slip_stats(slips):
        if not slips:
            return {"count": 0}
        pcts = [s.slippage_pct for s in slips]
        costs = [s.slippage_cost for s in slips]
        return {
            "count": len(slips),
            "avg_slippage_pct": round(sum(pcts) / len(pcts), 4),
            "max_slippage_pct": round(max(pcts), 4),
            "min_slippage_pct": round(min(pcts), 4),
            "total_slippage_cost": round(sum(costs), 2),
            "avg_slippage_cost": round(sum(costs) / len(costs), 2),
        }

    report = {
        "period_days": days,
        "total_orders": len(slippage_data),
        "config_slippage_pct": config_slip * 100,
        "buy_slippage": _slip_stats(buy_slips),
        "sell_slippage": _slip_stats(sell_slips),
        "all_slippage": _slip_stats(slippage_data),
        "details": [
            {
                "ticker": s.ticker, "side": s.side,
                "requested": s.requested_price, "filled": s.fill_price,
                "slip_pct": s.slippage_pct, "cost": s.slippage_cost,
            }
            for s in slippage_data
        ],
    }

    # Calibration recommendation
    actual_avg = report["all_slippage"].get("avg_slippage_pct", 0)
    if actual_avg != 0:
        report["recommendation"] = (
            f"Config slippage: {config_slip*100:.2f}% | "
            f"Actual avg: {actual_avg:.4f}% | "
            f"{'Config is conservative' if config_slip*100 > actual_avg else 'Config may underestimate slippage'}"
        )

    _journal_entry("slippage_analysis", report)
    return report


def get_execution_history(broker, days: int = 30) -> dict:
    """Full execution history with fees, slippage, and P&L per trade."""
    if not broker:
        return {"error": "Not connected"}

    orders = broker.get_history_orders(days=days)
    deals = broker.get_history_deals(days=days)

    # Get fees for filled orders
    filled_ids = [
        o.order_id for o in orders
        if o.status.value in ("FILLED", "PARTIAL_FILLED") and o.order_id
    ]
    fees = {}
    if filled_ids:
        fee_list = broker.get_order_fees(filled_ids[:50])
        fees = {f.order_id: f for f in fee_list}

    # Build per-order summary
    history = []
    for order in orders:
        order_deals = [d for d in deals if d.order_id == order.order_id]
        total_qty = sum(d.qty for d in order_deals)
        vwap = (
            sum(d.price * d.qty for d in order_deals) / total_qty
            if total_qty > 0 else 0
        )
        fee_info = fees.get(order.order_id)

        history.append({
            "order_id": order.order_id,
            "ticker": order.ticker,
            "side": order.side.value,
            "status": order.status.value,
            "requested_qty": order.requested_qty,
            "filled_qty": total_qty,
            "requested_price": order.requested_price,
            "fill_vwap": round(vwap, 4),
            "fee": fee_info.total_fee if fee_info else 0,
            "fee_details": fee_info.fee_details if fee_info else [],
            "deal_count": len(order_deals),
            "create_time": order.raw.get("create_time", ""),
            "error_msg": order.message if order.status == OrderStatus.FAILED else "",
        })

    return {
        "period_days": days,
        "total_orders": len(orders),
        "filled": sum(1 for h in history if h["status"] in ("FILLED", "PARTIAL_FILLED")),
        "cancelled": sum(1 for h in history if h["status"] == "CANCELLED"),
        "failed": sum(1 for h in history if h["status"] == "FAILED"),
        "total_fees": round(sum(h["fee"] for h in history), 2),
        "orders": history,
    }
