"""Tests for equity-curve normalization in _get_portfolio_history (Fix 1).

Verifies that cash-flow events (deposits/withdrawals) are removed from the
visible chart: the curve is expressed as `baseline + cumulative_profit_loss`
so funding spikes are invisible and the Y-axis is tight.

All tests use synthetic data and a MagicMock broker — no live Alpaca calls.
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_response(rows: list[tuple]) -> SimpleNamespace:
    """Create a fake Alpaca portfolio history response.

    Args:
        rows: list of (unix_ts, equity, profit_loss) tuples
    """
    return SimpleNamespace(
        timestamp=[r[0] for r in rows],
        equity=[r[1] for r in rows],
        profit_loss=[r[2] for r in rows],
    )


def _make_fake_broker(response: SimpleNamespace) -> MagicMock:
    broker = MagicMock()
    broker._broker_call = lambda fn, req: response
    broker._trade_client = MagicMock()
    return broker


def _call_fn(broker) -> list:
    """Import and call _get_portfolio_history directly."""
    from services.api.dashboard import _get_portfolio_history
    return _get_portfolio_history(broker)


# Base timestamp: 2026-01-01 00:00 UTC → sequential daily increments
_BASE_TS = 1735689600  # 2026-01-01 00:00:00 UTC
_DAY_SEC = 86400


def _ts(day_offset: int) -> int:
    return _BASE_TS + day_offset * _DAY_SEC


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNormalizedEquityContinuity:
    """Last normalized row must equal current live equity (continuity)."""

    def test_normalized_equity_continuity_with_today(self) -> None:
        """The last row's normalized equity == current raw equity (last raw_equity)."""
        rows = [
            (_ts(0), 5000.00,  10.00),
            (_ts(1), 5010.00,  10.00),
            (_ts(2), 5008.00,  -2.00),
            (_ts(3), 5020.00,  12.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        assert len(result) == 4
        # Last normalized equity == last raw_equity (current broker equity)
        assert result[-1]["equity"] == result[-1]["raw_equity"]
        assert result[-1]["equity"] == 5020.00


class TestNoFundingSpike:
    """A deposit (raw equity spike with profit_loss=0) should be invisible."""

    def test_normalized_equity_no_funding_spike(self) -> None:
        """$1,499 deposit on day 3 (profit_loss=0) produces no normalized jump."""
        # Day 0-2: trading activity only
        # Day 3: $1,499 deposit lands — raw equity jumps from 1000→2499, but pl=0
        # Day 4: normal trading
        rows = [
            (_ts(0), 1000.00,   0.00),
            (_ts(1), 1010.00,  10.00),
            (_ts(2), 1005.00,  -5.00),
            (_ts(3), 2499.00,   0.00),   # $1,499 deposit, NO trading P&L
            (_ts(4), 2515.00,  16.00),
            (_ts(5), 2510.00,  -5.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        assert len(result) == 6
        # No single day-over-day delta in normalized equity should exceed $50
        # (the deposit was $1,499 in raw equity but $0 in P&L → normalized delta = 0)
        for i in range(1, len(result)):
            delta = abs(result[i]["equity"] - result[i - 1]["equity"])
            assert delta <= 50, (
                f"Funding spike not removed on day {i}: "
                f"normalized delta=${delta:.2f} "
                f"(prev={result[i-1]['equity']}, curr={result[i]['equity']})"
            )

    def test_funding_day_normalized_delta_is_zero(self) -> None:
        """The specific deposit day's normalized delta == that day's profit_loss."""
        rows = [
            (_ts(0), 1000.00,   5.00),
            (_ts(1), 2499.00,   0.00),   # deposit day, pl=0 → delta must be 0
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        delta = result[1]["equity"] - result[0]["equity"]
        # delta in normalized series == profit_loss (0.0) not raw jump ($1499)
        assert abs(delta - 0.00) < 0.02, (
            f"Deposit-day normalized delta should be 0, got ${delta:.2f}"
        )


class TestBaselineCalculation:
    """baseline = current_equity - total_cumulative_pnl."""

    def test_normalized_equity_baseline_correct(self) -> None:
        """Baseline = last raw_equity - sum(all profit_loss values)."""
        rows = [
            (_ts(0), 5100.00,  10.00),
            (_ts(1), 5090.00, -10.00),
            (_ts(2), 5115.00,  25.00),
            (_ts(3), 5110.00,  -5.00),
        ]
        # total_cum_pnl = 10 - 10 + 25 - 5 = 20.00
        # current_equity = 5110.00
        # baseline = 5110.00 - 20.00 = 5090.00
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        total_cum_pnl = sum(r[2] for r in rows)  # 20.00
        current_equity = rows[-1][1]  # 5110.00
        expected_baseline = round(current_equity - total_cum_pnl, 2)  # 5090.00

        # First row's equity = baseline + day0_pnl = 5090 + 10 = 5100
        assert result[0]["equity"] == round(expected_baseline + rows[0][2], 2)
        # Last row's equity = baseline + total_cum_pnl = current_equity
        assert result[-1]["equity"] == current_equity

    def test_normalized_day_0_equals_baseline_plus_first_pnl(self) -> None:
        """Day 0 normalized equity = baseline + day0_pnl (not raw_equity)."""
        rows = [
            (_ts(0), 5200.00, 50.00),   # day 0: big gain
            (_ts(1), 5195.00, -5.00),
            (_ts(2), 5200.00,  5.00),
        ]
        # total_cum_pnl = 50 - 5 + 5 = 50
        # baseline = 5200 - 50 = 5150
        # day0_normalized = 5150 + 50 = 5200 (same as raw here, coincidentally)
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))
        total_cum_pnl = 50.0 - 5.0 + 5.0
        baseline = round(5200.00 - total_cum_pnl, 2)
        assert result[0]["equity"] == round(baseline + rows[0][2], 2)


class TestDayPnlPreservation:
    """day_pnl values must pass through unchanged from input."""

    def test_normalized_day_pnl_preserved(self) -> None:
        """day_pnl in output == profit_loss from Alpaca (no modification)."""
        rows = [
            (_ts(0), 5000.00,  12.34),
            (_ts(1), 5010.00, -56.78),
            (_ts(2), 5000.00,   0.00),
            (_ts(3), 5005.00,   5.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        expected_pnl = [12.34, -56.78, 0.00, 5.00]
        for i, exp in enumerate(expected_pnl):
            assert result[i]["day_pnl"] == round(exp, 2), (
                f"day_pnl mismatch on row {i}: expected {exp}, got {result[i]['day_pnl']}"
            )

    def test_normalized_raw_equity_preserved_in_output(self) -> None:
        """raw_equity field in each row matches the original Alpaca equity value."""
        rows = [
            (_ts(0), 5000.00, 10.00),
            (_ts(1), 5010.00, 10.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))
        assert result[0]["raw_equity"] == 5000.00
        assert result[1]["raw_equity"] == 5010.00


class TestPreFundingZerosSkipped:
    """Equity values <= 0 (pre-funding) must be filtered before normalization."""

    def test_normalized_equity_pre_funding_zeros_skipped(self) -> None:
        """Rows with equity=0 (or None) are excluded from the normalized series."""
        rows = [
            (_ts(0),    0.00,  0.00),   # pre-funding, equity=0 → skip
            (_ts(1),    0.00,  0.00),   # pre-funding, equity=0 → skip
            (_ts(2), 5000.00,  0.00),   # first funded day
            (_ts(3), 5010.00, 10.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        # Only 2 rows returned (the two zero-equity rows are skipped)
        assert len(result) == 2
        assert result[0]["raw_equity"] == 5000.00
        assert result[1]["raw_equity"] == 5010.00

    def test_pre_funding_none_equity_skipped(self) -> None:
        """Rows with equity=None are also excluded."""
        rows = [
            (_ts(0), None,    0.00),
            (_ts(1), 5100.00, 5.00),
        ]
        # SimpleNamespace with mixed None/float equity
        fake = SimpleNamespace(
            timestamp=[_ts(0), _ts(1)],
            equity=[None, 5100.00],
            profit_loss=[0.00, 5.00],
        )
        result = _call_fn(_make_fake_broker(fake))
        assert len(result) == 1
        assert result[0]["raw_equity"] == 5100.00


class TestEdgeCases:
    """Edge cases: empty responses, broker failures, single-row series."""

    def test_empty_response_returns_empty_list(self) -> None:
        """Alpaca returns no timestamps → _get_portfolio_history returns []."""
        fake = SimpleNamespace(timestamp=[], equity=[], profit_loss=[])
        result = _call_fn(_make_fake_broker(fake))
        assert result == []

    def test_broker_failure_returns_empty_list(self) -> None:
        """broker._broker_call raises → returns [] without propagating."""
        broker = MagicMock()
        broker._broker_call = MagicMock(side_effect=RuntimeError("Alpaca API down"))
        broker._trade_client = MagicMock()
        result = _call_fn(broker)
        assert result == []

    def test_all_zero_equity_returns_empty_list(self) -> None:
        """All rows have equity=0 → all filtered → returns []."""
        rows = [(_ts(i), 0.00, 0.00) for i in range(5)]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))
        assert result == []

    def test_single_row_returns_one_entry(self) -> None:
        """Single non-zero row returns a 1-element list with equity == raw_equity."""
        rows = [(_ts(0), 5000.00, 25.00)]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))
        assert len(result) == 1
        assert result[0]["equity"] == result[0]["raw_equity"]

    def test_missing_profit_loss_defaults_to_zero(self) -> None:
        """If profit_loss list is shorter than equity list, missing entries default to 0."""
        fake = SimpleNamespace(
            timestamp=[_ts(0), _ts(1), _ts(2)],
            equity=[5000.00, 5010.00, 5005.00],
            profit_loss=[10.00],   # only 1 entry for 3 rows
        )
        result = _call_fn(_make_fake_broker(fake))
        # Row 0: pl=10, row 1: pl=0 (missing), row 2: pl=0 (missing)
        assert result[0]["day_pnl"] == 10.00
        assert result[1]["day_pnl"] == 0.00
        assert result[2]["day_pnl"] == 0.00


class TestNormalizedRange:
    """Y-axis tightness: range should be driven by P&L, not funding events."""

    def test_y_axis_range_tight_after_normalization(self) -> None:
        """After normalization, (max-min)/max < 0.10 for a realistic scenario."""
        # Simulate: $5188 base + $1499 deposit mid-way + small trading swings
        rows = [
            (_ts(0),    0.00,   0.00),   # pre-funding
            (_ts(1), 5188.00,   0.00),   # initial deposit, no P&L
            (_ts(2), 5195.00,   7.00),
            (_ts(3), 5190.00,  -5.00),
            (_ts(4), 5200.00,  10.00),
            (_ts(5), 6699.00,   0.00),   # $1499 deposit, no P&L (funding event)
            (_ts(6), 6710.00,  11.00),
            (_ts(7), 6705.00,  -5.00),
            (_ts(8), 6715.00,  10.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        equities = [r["equity"] for r in result]
        mn, mx = min(equities), max(equities)
        assert mx > 0
        range_pct = (mx - mn) / mx
        assert range_pct < 0.10, (
            f"Y-axis range too wide after normalization: "
            f"min=${mn:.2f}, max=${mx:.2f}, range={range_pct:.1%}"
        )

    def test_consecutive_day_deltas_match_day_pnl(self) -> None:
        """Each consecutive equity delta should equal that day's day_pnl (±rounding)."""
        rows = [
            (_ts(0), 5000.00,  10.00),
            (_ts(1), 5010.00,  10.00),
            (_ts(2), 6509.00,   0.00),   # $1499 deposit — no P&L change
            (_ts(3), 6514.00,   5.00),
            (_ts(4), 6509.00,  -5.00),
        ]
        fake = _make_fake_response(rows)
        result = _call_fn(_make_fake_broker(fake))

        for i in range(1, len(result)):
            delta = round(result[i]["equity"] - result[i - 1]["equity"], 2)
            expected = result[i]["day_pnl"]
            assert abs(delta - expected) <= 0.02, (
                f"Row {i}: equity delta=${delta:.2f} != day_pnl=${expected:.2f}"
            )
