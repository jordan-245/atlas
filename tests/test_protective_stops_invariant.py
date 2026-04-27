"""Invariant test: every live broker position must have exactly one open
SELL stop order (stop / stop_limit / trailing_stop).

The broker is mocked — no live API calls.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ─── helpers ───────────────────────────────────────────────────────────────

def _make_position(ticker: str, qty: int = 10, entry: float = 100.0, current: float = 100.0):
    p = MagicMock()
    p.ticker = ticker
    p.shares = qty
    p.entry_price = entry
    p.current_price = current
    return p


def _make_stop_order(ticker: str, order_id: str, order_type: str = "trailing_stop",
                     status: str = "new", stop_price: float = 90.0) -> MagicMock:
    o = MagicMock()
    o.ticker = ticker
    o.order_id = order_id
    o.raw = {
        "side": "sell",
        "order_type": order_type,
        "status": status,
        "stop_price": str(stop_price),
    }
    return o


def _make_limit_order(ticker: str, order_id: str, status: str = "new",
                      limit_price: float = 110.0) -> MagicMock:
    o = MagicMock()
    o.ticker = ticker
    o.order_id = order_id
    o.raw = {
        "side": "sell",
        "order_type": "limit",
        "status": status,
        "limit_price": str(limit_price),
    }
    return o


def _collect_stop_orders(orders: list) -> dict[str, list]:
    """Return {ticker: [order_id, ...]} for SELL stop-type orders."""
    result: dict[str, list] = {}
    _STOP_TYPES = {"stop", "stop_limit", "trailing_stop"}
    for o in orders:
        raw = getattr(o, "raw", {}) or {}
        side = raw.get("side", "")
        otype = raw.get("order_type", "")
        if side == "sell" and otype in _STOP_TYPES:
            result.setdefault(o.ticker, []).append(o.order_id)
    return result


def _assert_invariant(positions, orders, *, allow_missing: list[str] | None = None):
    """Core invariant: each position has exactly 1 sell-stop order."""
    stop_map = _collect_stop_orders(orders)
    allow_missing = allow_missing or []
    violations = []
    for pos in positions:
        stops = stop_map.get(pos.ticker, [])
        n = len(stops)
        if pos.ticker in allow_missing:
            continue
        if n != 1:
            violations.append(f"{pos.ticker}: expected 1 stop, got {n}")
    assert not violations, "Stop-coverage invariant violated:\n  " + "\n  ".join(violations)


# ─── tests ─────────────────────────────────────────────────────────────────

class TestProtectiveStopsInvariant:
    """Invariant: every live position has exactly one SELL stop order."""

    def test_all_positions_covered(self):
        """Happy path — every position has exactly 1 trailing stop."""
        positions = [
            _make_position("ADI", entry=403.88, current=399.00),
            _make_position("AVGO", entry=422.57, current=424.00),
            _make_position("FCX", qty=5, entry=61.48, current=61.13),
        ]
        orders = [
            _make_stop_order("ADI", "order-adi-1", "trailing_stop", stop_price=388.13),
            _make_stop_order("AVGO", "order-avgo-1", "trailing_stop", stop_price=407.66),
            _make_stop_order("FCX", "order-fcx-1", "trailing_stop", stop_price=58.40),
        ]
        _assert_invariant(positions, orders)

    def test_missing_stop_raises(self):
        """Position without a stop triggers invariant violation."""
        positions = [
            _make_position("XLI", entry=173.97, current=172.71),
        ]
        orders = []  # no stops at all
        with pytest.raises(AssertionError, match="XLI: expected 1 stop, got 0"):
            _assert_invariant(positions, orders)

    def test_duplicate_stop_raises(self):
        """Position with two stops (duplicate) triggers invariant violation."""
        positions = [
            _make_position("CAT", entry=835.24, current=834.65),
        ]
        orders = [
            _make_stop_order("CAT", "order-cat-1", "stop", stop_price=799.47),
            _make_stop_order("CAT", "order-cat-2", "stop", stop_price=790.00),
        ]
        with pytest.raises(AssertionError, match="CAT: expected 1 stop, got 2"):
            _assert_invariant(positions, orders)

    def test_oco_pair_counts_as_one(self):
        """OCO pair (LIMIT + STOP) should count as exactly 1 stop order."""
        positions = [
            _make_position("CAT", entry=835.24, current=834.65),
        ]
        orders = [
            # OCO: limit (TP leg) + stop (SL leg) — only the stop counts
            _make_limit_order("CAT", "order-cat-limit", limit_price=978.33),
            _make_stop_order("CAT", "order-cat-stop", "stop", status="held", stop_price=799.47),
        ]
        _assert_invariant(positions, orders)

    def test_buy_orders_not_counted(self):
        """BUY entry orders for a ticker must not be counted as protective stops."""
        positions = [
            _make_position("ADI", entry=403.88),
        ]
        buy_order = MagicMock()
        buy_order.ticker = "ADI"
        buy_order.order_id = "buy-order-1"
        buy_order.raw = {"side": "buy", "order_type": "limit", "status": "new"}

        stop_order = _make_stop_order("ADI", "stop-order-1", "trailing_stop")
        _assert_invariant(positions, [buy_order, stop_order])

    def test_limit_sell_alone_not_counted(self):
        """A lone LIMIT SELL order (TP without SL) is not a stop — should fail invariant."""
        positions = [
            _make_position("ADI", entry=403.88),
        ]
        orders = [
            _make_limit_order("ADI", "limit-order-1", limit_price=450.00),
        ]
        with pytest.raises(AssertionError, match="ADI: expected 1 stop, got 0"):
            _assert_invariant(positions, orders)

    def test_held_status_counts_as_stop(self):
        """Stop order with status=HELD (pre-market) must still count as a stop."""
        positions = [
            _make_position("XLK", entry=156.77, current=160.90),
        ]
        orders = [
            _make_stop_order("XLK", "stop-xlk-held", "stop", status="held", stop_price=153.52),
        ]
        _assert_invariant(positions, orders)

    def test_trailing_stop_counts_as_stop(self):
        """TRAILING_STOP type must satisfy the stop-coverage invariant."""
        positions = [_make_position("GLD", entry=442.80, current=431.79)]
        orders = [_make_stop_order("GLD", "trail-gld-1", "trailing_stop", stop_price=418.11)]
        _assert_invariant(positions, orders)

    def test_stop_limit_counts_as_stop(self):
        """STOP_LIMIT type must satisfy the stop-coverage invariant."""
        positions = [_make_position("UNG", qty=60, entry=10.34, current=10.58)]
        orders = [_make_stop_order("UNG", "sl-ung-1", "stop_limit", stop_price=10.16)]
        _assert_invariant(positions, orders)

    def test_multiple_positions_mixed_state(self):
        """Full portfolio: some covered, one missing → violation reported correctly."""
        positions = [
            _make_position("ADI", entry=403.88),
            _make_position("AMD", entry=278.25, current=354.20),
            _make_position("FCX", entry=61.48),
        ]
        orders = [
            _make_stop_order("ADI", "stop-adi", "trailing_stop", stop_price=388.13),
            # AMD has no stop — should trigger violation
            _make_stop_order("FCX", "stop-fcx", "trailing_stop", stop_price=58.40),
        ]
        with pytest.raises(AssertionError, match="AMD: expected 1 stop, got 0"):
            _assert_invariant(positions, orders)

    def test_allow_missing_skips_ticker(self):
        """allow_missing parameter exempts specific tickers (e.g., inverted-stop AMD)."""
        positions = [
            _make_position("ADI"),
            _make_position("AMD"),
        ]
        orders = [
            _make_stop_order("ADI", "stop-adi", "trailing_stop"),
            # AMD intentionally missing — covered by allow_missing
        ]
        # Should NOT raise
        _assert_invariant(positions, orders, allow_missing=["AMD"])

    def test_empty_portfolio_passes(self):
        """No positions → invariant trivially satisfied."""
        _assert_invariant([], [])


class TestStopOrderCollection:
    """Unit tests for the _collect_stop_orders helper."""

    def test_collects_stop_type(self):
        orders = [_make_stop_order("ADI", "s1", "stop")]
        assert _collect_stop_orders(orders) == {"ADI": ["s1"]}

    def test_collects_trailing_stop_type(self):
        orders = [_make_stop_order("GLD", "s1", "trailing_stop")]
        assert _collect_stop_orders(orders) == {"GLD": ["s1"]}

    def test_collects_stop_limit_type(self):
        orders = [_make_stop_order("UNG", "s1", "stop_limit")]
        assert _collect_stop_orders(orders) == {"UNG": ["s1"]}

    def test_ignores_limit_sell(self):
        orders = [_make_limit_order("CAT", "lim1")]
        assert _collect_stop_orders(orders) == {}

    def test_ignores_buy_orders(self):
        buy = MagicMock()
        buy.ticker = "AMD"
        buy.order_id = "buy1"
        buy.raw = {"side": "buy", "order_type": "stop", "status": "new"}
        assert _collect_stop_orders([buy]) == {}

    def test_multiple_stops_same_ticker(self):
        orders = [
            _make_stop_order("CAT", "s1", "stop"),
            _make_stop_order("CAT", "s2", "stop"),
        ]
        result = _collect_stop_orders(orders)
        assert result["CAT"] == ["s1", "s2"]

    def test_mixed_tickers(self):
        orders = [
            _make_stop_order("ADI", "s-adi", "trailing_stop"),
            _make_stop_order("FCX", "s-fcx", "stop"),
            _make_limit_order("CAT", "lim-cat"),
        ]
        result = _collect_stop_orders(orders)
        assert set(result.keys()) == {"ADI", "FCX"}
