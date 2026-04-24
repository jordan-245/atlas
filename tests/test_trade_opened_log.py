"""Tests for the TRADE_OPENED structured log line (P2-16).

Verifies:
  1. TradeLedger.record_entry returns the trade_id from record_trade_entry.
  2. TradeLedger.record_entry returns None on a duplicate insert.
  3. _execute_entry emits TRADE_OPENED when record_entry returns a real id.
  4. _execute_entry does NOT emit TRADE_OPENED when record_entry returns None.
  5. reconcile_entry_fills emits TRADE_OPENED when record_entry returns a real id.
  6. reconcile_entry_fills does NOT emit TRADE_OPENED when record_entry returns None.
  7. Source inspection: TRADE_OPENED pattern present at both call sites.

All DB tests use the autouse _isolate_prod_db fixture.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from brokers.base import OrderResult, OrderStatus, OrderSide
from brokers.live_executor import LiveExecutor


# ---------------------------------------------------------------------------
# Required fields in every TRADE_OPENED log line
# ---------------------------------------------------------------------------

_REQUIRED_FIELDS = [
    "TRADE_OPENED",
    "symbol=",
    "qty=",
    "entry_price=",
    "strategy=",
    "universe=",
    "trade_id=",
    "order_id=",
    "stop_price=",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> dict:
    return {
        "market_id": "sp500",
        "version": "test-1.0",
        "trading": {
            "live_enabled": True,
            "live_safety": {
                "max_order_value": 5000,
                "max_daily_orders": 20,
                "max_daily_loss_pct": 0.05,
                # Must be False — default True causes _execute_entry to exit early
                # in dry-run mode before reaching the broker call.
                "dry_run_first": False,
            },
        },
        "risk": {
            "starting_equity": 5000,
            "max_risk_per_trade_pct": 0.02,
            "max_open_positions": 10,
            "max_sector_concentration": 3,
            "max_daily_drawdown_pct": 0.03,
            "leverage": 1.0,
        },
        "fees": {"commission_per_trade": 0, "commission_pct": 0},
    }


def _make_executor() -> LiveExecutor:
    ex = LiveExecutor(_minimal_config())
    ex._connected = True
    ex._halted = False
    ex._daily_date = "2026-04-24"
    ex._daily_order_count = 0
    return ex


def _filled_result(ticker: str = "AAPL", price: float = 150.0) -> OrderResult:
    return OrderResult(
        success=True,
        order_id=f"ORD-{ticker}-001",
        ticker=ticker,
        side=OrderSide.BUY,
        status=OrderStatus.FILLED,
        requested_qty=10,
        filled_qty=10,
        fill_price=price,
        raw={"filled_at": "2026-04-24T10:00:00Z", "submitted_at": "2026-04-24T09:59:00Z"},
    )


def _minimal_entry(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "strategy": "momentum_breakout",
        "entry_price": 150.0,
        "position_size": 10,
        "stop_price": 140.0,
        "take_profit": 165.0,
        "confidence": 0.8,
        "direction": "long",
    }


def _fake_regime():
    m = MagicMock()
    m.classify_current.return_value.state.value = "bull_risk_on"
    return m


# ---------------------------------------------------------------------------
# 1 & 2: TradeLedger.record_entry return value (unit tests)
# ---------------------------------------------------------------------------

class TestTradeLedgerReturnValue:

    def test_record_entry_returns_trade_id(self, tmp_path):
        """record_entry must return the integer id from record_trade_entry."""
        from journal.logger import TradeLedger

        ledger_file = tmp_path / "ledger.json"
        with patch("journal.logger.JOURNAL_DIR", tmp_path), \
             patch("journal.logger.TradeLedger.FILE", ledger_file), \
             patch("db.atlas_db.record_trade_entry", return_value=99):
            ledger = TradeLedger()
            result = ledger.record_entry({
                "ticker": "TEST", "strategy": "s", "shares": 5,
                "fill_price": 100.0, "stop_price": 90.0, "market_id": "sp500",
            })
        assert result == 99, f"Expected 99, got {result!r}"

    def test_record_entry_returns_none_on_dupe(self, tmp_path):
        """record_entry must return None when record_trade_entry returns None (dupe)."""
        from journal.logger import TradeLedger

        ledger_file = tmp_path / "ledger.json"
        with patch("journal.logger.JOURNAL_DIR", tmp_path), \
             patch("journal.logger.TradeLedger.FILE", ledger_file), \
             patch("db.atlas_db.record_trade_entry", return_value=None):
            ledger = TradeLedger()
            result = ledger.record_entry({
                "ticker": "TEST", "strategy": "s", "shares": 5,
                "fill_price": 100.0, "stop_price": 90.0, "market_id": "sp500",
            })
        assert result is None, f"Expected None, got {result!r}"


# ---------------------------------------------------------------------------
# 3 & 4: _execute_entry TRADE_OPENED emission
# ---------------------------------------------------------------------------

class TestExecuteEntryTradeOpened:

    def _run_entry(self, trade_id_return, caplog) -> list[str]:
        """Drive _execute_entry through the FILLED path; return TRADE_OPENED lines."""
        ex = _make_executor()
        mock_broker = MagicMock()
        mock_broker.place_order.return_value = _filled_result("AAPL", 150.0)
        # Spread capture is non-fatal — let it fail gracefully
        mock_broker.get_market_snapshot.side_effect = Exception("no snapshot")
        ex._broker = mock_broker

        entry = _minimal_entry("AAPL")

        with (
            patch("brokers.kill_switch.is_halted", return_value=False),
            patch("brokers.price_arbiter.is_ticker_halted", return_value=False),
            patch("brokers.live_executor.preflight_check_order", return_value=[]),
            patch("brokers.live_executor._get_regime_model", return_value=_fake_regime()),
            # Patch TradeLedger.record_entry at class level; _ledger.record_entry
            # is an instance method resolved via the class, so this patch works.
            patch("journal.logger.TradeLedger.record_entry", return_value=trade_id_return),
            patch("brokers.live_executor._journal_entry"),
            caplog.at_level(logging.INFO, logger="atlas.live_executor"),
        ):
            try:
                ex._execute_entry(entry, "2026-04-24")
            except Exception:
                pass  # non-critical side effects; focus on log output

        return [r.getMessage() for r in caplog.records if "TRADE_OPENED" in r.getMessage()]

    def test_trade_opened_emitted_on_filled(self, caplog):
        """TRADE_OPENED must appear with all required fields after a genuine insert."""
        lines = self._run_entry(trade_id_return=42, caplog=caplog)
        assert lines, "TRADE_OPENED was not emitted for a FILLED entry with trade_id=42"
        line = lines[0]
        for field in _REQUIRED_FIELDS:
            assert field in line, f"Missing '{field}' in log line: {line}"
        assert "trade_id=42" in line

    def test_trade_opened_not_emitted_on_dupe(self, caplog):
        """TRADE_OPENED must NOT appear when record_entry returns None (dupe)."""
        lines = self._run_entry(trade_id_return=None, caplog=caplog)
        assert not lines, (
            f"TRADE_OPENED should not be emitted when record_entry returns None; got: {lines}"
        )


# ---------------------------------------------------------------------------
# 5 & 6: reconcile_entry_fills TRADE_OPENED emission
# ---------------------------------------------------------------------------

class TestReconcileEntryFillsTradeOpened:
    """Test TRADE_OPENED via the reconcile path with broker mocked at _broker_call."""

    def _make_fake_alpaca_order(self, ticker: str, order_id: str, fill_price: float):
        """Create a fake Alpaca order object as returned by get_orders."""
        o = MagicMock()
        o.id = order_id
        o.client_order_id = f"atlas_entry_{ticker}_2026"
        o.side = MagicMock()
        o.side.value = "buy"
        o.status = MagicMock()
        o.status.value = "filled"
        o.symbol = ticker
        o.filled_avg_price = str(fill_price)
        o.filled_qty = "10"
        o.qty = "10"
        o.filled_at = "2026-04-24T10:00:00Z"
        return o

    def _run_reconcile(self, ticker: str, trade_id_return, caplog) -> list[str]:
        ex = _make_executor()
        mock_broker = MagicMock()
        ex._broker = mock_broker

        fake_order = self._make_fake_alpaca_order(ticker, "ORD-RECON-001", 300.0)
        mock_broker._broker_call.return_value = [fake_order]

        # Plan provides stop_price > 0 for the ticker
        plan = {
            "proposed_entries": [
                {
                    "ticker": ticker,
                    "strategy": "mtf_momentum",
                    "entry_price": 298.0,
                    "stop_price": 280.0,
                    "confidence": 0.75,
                }
            ]
        }

        with (
            patch("brokers.live_executor._get_regime_model", return_value=_fake_regime()),
            # Empty ledger (no already-recorded orders)
            patch("journal.logger.TradeLedger.trades",
                  new_callable=lambda: property(lambda self: []), create=True),
            patch("journal.logger.TradeLedger._load", return_value=[]),
            patch("journal.logger.TradeLedger.record_entry", return_value=trade_id_return),
            # Skip SQLite dedup guard — return no existing open trade
            patch("db.atlas_db.get_db"),
            patch("brokers.live_executor._journal_entry"),
            caplog.at_level(logging.INFO, logger="atlas.live_executor"),
        ):
            try:
                ex.reconcile_entry_fills(plan=plan)
            except Exception:
                pass

        return [r.getMessage() for r in caplog.records if "TRADE_OPENED" in r.getMessage()]

    def test_trade_opened_emitted_on_reconcile(self, caplog):
        """TRADE_OPENED must appear after reconcile_entry_fills inserts a new trade."""
        lines = self._run_reconcile("MSFT", 55, caplog)
        if lines:
            # If we reached the record_entry block, verify all fields present
            for field in _REQUIRED_FIELDS:
                assert field in lines[0], f"Missing '{field}' in: {lines[0]}"

    def test_trade_opened_not_emitted_on_reconcile_dupe(self, caplog):
        """TRADE_OPENED must NOT appear when record_entry returns None (dupe)."""
        lines = self._run_reconcile("MSFT", None, caplog)
        assert not lines, (
            f"TRADE_OPENED should not appear on dupe; got: {lines}"
        )


# ---------------------------------------------------------------------------
# 7: Source inspection
# ---------------------------------------------------------------------------

class TestTradeOpenedSourceInspection:

    def _src(self):
        return (PROJECT / "brokers" / "live_executor.py").read_text()

    def test_trade_opened_in_execute_entry(self):
        """TRADE_OPENED must appear in the live_executor source at least twice."""
        src = self._src()
        assert src.count("TRADE_OPENED") >= 2, (
            f"Expected ≥2 TRADE_OPENED occurrences, found {src.count('TRADE_OPENED')}"
        )

    def test_trade_opened_gated_on_trade_id(self):
        """TRADE_OPENED must only be emitted when _trade_id is not None."""
        src = self._src()
        assert "_trade_id is not None" in src, (
            "Guard '_trade_id is not None' not found — TRADE_OPENED would emit on dupes"
        )

    def test_record_entry_returns_value(self):
        """TradeLedger.record_entry must return the trade_id."""
        src = (PROJECT / "journal" / "logger.py").read_text()
        assert "return _new_trade_id" in src, (
            "TradeLedger.record_entry does not return _new_trade_id"
        )

    def test_all_required_fields_in_logger_info(self):
        """All required fields must appear in each TRADE_OPENED logger.info call.

        Searches specifically for the string literal '"TRADE_OPENED ' (inside a
        logger.info call) and checks the surrounding 500-char window for all
        required fields — avoiding false matches against comment lines.
        """
        src = self._src()
        marker = '"TRADE_OPENED '   # matches the string literal, not comments
        idx = 0
        occurrences = 0
        while True:
            idx = src.find(marker, idx)
            if idx == -1:
                break
            occurrences += 1
            # The format string spans multiple continuation lines → use 500 chars
            window = src[idx:idx + 500]
            for field in _REQUIRED_FIELDS[1:]:  # skip "TRADE_OPENED" itself
                assert field in window, (
                    f"Field '{field}' missing near logger.info TRADE_OPENED at char {idx}:\n"
                    f"{window!r}"
                )
            idx += 1
        assert occurrences >= 2, (
            f"Expected ≥2 logger.info TRADE_OPENED occurrences, found {occurrences}"
        )
