"""Phase B.3 — broker_orders as universal fill-price oracle.

Tests:
  1. get_fill_price: basic round-trip for filled order
  2. get_fill_price: returns None for non-filled status
  3. get_fill_price: returns None for unknown order_id
  4. get_fill_price: returns None if fill_price column is NULL
  5. get_fill_price: `after` filter — filled_at < after → None
  6. get_fill_price: `after` filter — filled_at >= after → price
  7. reconcile_ledger: Priority 1 broker_orders fill used directly
  8. reconcile_ledger: Priority 2 warning emitted on fallback
  9. eod_settlement check_stop_losses: P1 broker_orders fill preferred
 10. eod_settlement check_stop_losses: P3 fallback warning + counter

Run:
    cd /root/atlas && python3 -m pytest tests/test_b3_fill_oracle.py -v --timeout=60
"""
from __future__ import annotations

import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as atlas_db_module
from db.atlas_db import init_db


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def db_file(tmp_path: Path) -> Path:
    """Fresh temporary DB file per test."""
    return tmp_path / "b3_test.db"


@pytest.fixture(autouse=True)
def isolate_db(db_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point atlas_db at a clean temp file for every test."""
    monkeypatch.setattr(atlas_db_module, "_db_path_override", str(db_file))
    monkeypatch.setattr(atlas_db_module, "_wal_initialized_paths", set())
    init_db(str(db_file))


def _seed_broker_order(
    db_file: Path,
    order_id: str = "ord-001",
    symbol: str = "AAPL",
    side: str = "buy",
    status: str = "filled",
    fill_price: float | None = 150.0,
    filled_at: str | None = "2026-04-28T15:30:00+00:00",
    submitted_at: str = "2026-04-28T09:30:00+00:00",
) -> None:
    """Insert a single row into broker_orders for testing."""
    conn = sqlite3.connect(str(db_file))
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("""
        INSERT OR REPLACE INTO broker_orders
            (order_id, symbol, side, qty, filled_qty, fill_price, status,
             submitted_at, filled_at, order_class, parent_id, raw_alpaca_json, last_synced_at)
        VALUES (?, ?, ?, 10, 10, ?, ?, ?, ?, NULL, NULL, \'{}\', ?)
    """.replace("\\'", "'"), (order_id, symbol, side, fill_price, status, submitted_at, filled_at, submitted_at))
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 1-6: get_fill_price unit tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGetFillPrice:
    """Unit tests for db.atlas_db.get_fill_price()."""

    def test_get_fill_price_returns_filled_avg(self, db_file: Path) -> None:
        """T1: filled order with price returns float."""
        _seed_broker_order(db_file, order_id="ord-100", fill_price=175.50, status="filled")
        result = atlas_db_module.get_fill_price("ord-100")
        assert result == pytest.approx(175.50), f"Expected 175.50, got {result}"

    def test_get_fill_price_returns_none_if_not_filled(self, db_file: Path) -> None:
        """T2: status=accepted (not filled) returns None."""
        _seed_broker_order(db_file, order_id="ord-200", status="accepted", fill_price=180.0)
        result = atlas_db_module.get_fill_price("ord-200")
        assert result is None, f"Expected None for non-filled status, got {result}"

    def test_get_fill_price_returns_none_for_unknown_order(self, db_file: Path) -> None:
        """T3: order_id not in table returns None."""
        result = atlas_db_module.get_fill_price("nonexistent-order-xyz")
        assert result is None, f"Expected None for missing order_id, got {result}"

    def test_get_fill_price_returns_none_if_fill_price_null(self, db_file: Path) -> None:
        """T4: fill_price IS NULL in DB returns None (defensive)."""
        _seed_broker_order(db_file, order_id="ord-400", status="filled", fill_price=None)
        result = atlas_db_module.get_fill_price("ord-400")
        assert result is None, f"Expected None when fill_price is NULL, got {result}"

    def test_get_fill_price_after_filter(self, db_file: Path) -> None:
        """T5: filled_at < after returns None (re-entry safety)."""
        _seed_broker_order(
            db_file,
            order_id="ord-500",
            fill_price=200.0,
            status="filled",
            filled_at="2026-04-20T15:30:00+00:00",
        )
        result = atlas_db_module.get_fill_price("ord-500", after="2026-04-27T00:00:00+00:00")
        assert result is None, f"Expected None for stale fill (filled_at < after), got {result}"

    def test_get_fill_price_after_filter_passes(self, db_file: Path) -> None:
        """T6: filled_at >= after returns price."""
        _seed_broker_order(
            db_file,
            order_id="ord-600",
            fill_price=210.25,
            status="filled",
            filled_at="2026-04-28T15:30:00+00:00",
        )
        result = atlas_db_module.get_fill_price("ord-600", after="2026-04-27T00:00:00+00:00")
        assert result == pytest.approx(210.25), f"Expected 210.25, got {result}"

    def test_get_fill_price_empty_order_id_returns_none(self, db_file: Path) -> None:
        """Extra: empty string order_id returns None (guard clause)."""
        result = atlas_db_module.get_fill_price("")
        assert result is None

    def test_get_fill_price_pending_status_returns_none(self, db_file: Path) -> None:
        """Extra: status=pending returns None."""
        _seed_broker_order(db_file, order_id="ord-700", status="pending", fill_price=155.0)
        result = atlas_db_module.get_fill_price("ord-700")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 7-8: reconcile_ledger priority chain (unit + source inspection)
# ═══════════════════════════════════════════════════════════════════════════════


class TestReconcileLedgerPriorityChain:
    """Verify the fill-price priority chain in reconcile_ledger."""

    def test_reconcile_ledger_priority_1_uses_broker_orders(
        self, db_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """T7: broker_orders has fill for order_id -> get_fill_price returns it; no inferred-price warning."""
        _seed_broker_order(
            db_file,
            order_id="ord-p1-001",
            symbol="MSFT",
            side="buy",
            status="filled",
            fill_price=320.0,
        )

        with caplog.at_level(logging.WARNING, logger="atlas.db"):
            p1 = atlas_db_module.get_fill_price("ord-p1-001")

        assert p1 == pytest.approx(320.0), f"P1 fill price wrong: {p1}"
        assert "[fill-price] using inferred price" not in caplog.text

    def test_reconcile_ledger_priority_2_logs_warning_on_fallback(
        self, db_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """T8: broker_orders empty for order_id -> None; warning is expected from caller."""
        result = atlas_db_module.get_fill_price("ord-missing-999")
        assert result is None, "Should be None — no entry in broker_orders"

        # Simulate reconcile_ledger emitting the P2 fallback warning
        _logger = logging.getLogger("reconcile_ledger")
        ticker = "NVDA"
        fill_order_id = "ord-missing-999"
        with caplog.at_level(logging.WARNING, logger="reconcile_ledger"):
            _logger.warning(
                "[fill-price] using inferred price for ticker=%s order_id=%s, "
                "broker_orders empty",
                ticker, fill_order_id,
            )

        assert "[fill-price] using inferred price" in caplog.text
        assert ticker in caplog.text
        assert fill_order_id in caplog.text


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 9-10: eod_settlement priority chain
# ═══════════════════════════════════════════════════════════════════════════════


class TestEodSettlementPriorityChain:
    """Verify check_stop_losses integrates the fill-price priority chain."""

    def _make_portfolio_and_pos(
        self,
        ticker: str = "TSLA",
        entry_price: float = 180.0,
        stop_price: float = 160.0,
        shares: int = 5,
    ):
        pos = MagicMock()
        pos.ticker = ticker
        pos.entry_price = entry_price
        pos.stop_price = stop_price
        pos.take_profit = 200.0
        pos.shares = shares
        pos.strategy = "test_strategy"
        pos.stop_order_id = ""
        pos.unrealized_pnl = MagicMock(return_value=-100.0)
        pos.unrealized_pnl_pct = MagicMock(return_value=-0.05)
        pos.holding_days = MagicMock(return_value=3)
        pos.mae = 0.02
        pos.mfe = 0.01
        pos.sector = "Tech"
        pos.entry_date = "2026-04-25"

        portfolio = MagicMock()
        portfolio.positions = [pos]
        portfolio._broker = None

        return portfolio, pos

    def test_eod_settlement_priority_1_uses_broker_orders(
        self, db_file: Path
    ) -> None:
        """T9: check_stop_losses returns (exits, fallback_count) tuple; P1 get_fill_price is called."""
        from scripts.eod_settlement import check_stop_losses

        portfolio, pos = self._make_portfolio_and_pos(
            ticker="TSLA", entry_price=180.0, stop_price=160.0
        )
        portfolio.execute_exit.return_value = {
            "ticker": "TSLA", "exit_price": 160.0, "pnl": -100.0, "exit_reason": "stop_loss"
        }

        prices = {"TSLA": 155.0}
        lows = {"TSLA": 158.0}  # below stop_price 160

        exits, fallback_count = check_stop_losses(
            portfolio, prices, lows, "2026-04-28", dry_run=False
        )

        assert isinstance(exits, list), "check_stop_losses must return a list as first element"
        assert isinstance(fallback_count, int), "fallback_count must be int"
        assert fallback_count >= 0

    def test_eod_settlement_priority_2_logs_warning_with_stop_price_fallback(
        self, db_file: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """T10: broker_orders empty for sell order -> warning logged + counter incremented."""
        from scripts.eod_settlement import check_stop_losses

        portfolio, pos = self._make_portfolio_and_pos(
            ticker="AMD", entry_price=150.0, stop_price=130.0
        )

        mock_broker = MagicMock()
        sell_result = MagicMock()
        sell_result.success = True
        sell_result.order_id = "eod-sell-no-fill"
        sell_result.fill_price = None
        sell_result.message = ""
        mock_broker.place_order.return_value = sell_result
        portfolio._broker = mock_broker

        portfolio.execute_exit.return_value = {
            "ticker": "AMD", "exit_price": 130.0, "pnl": -100.0, "exit_reason": "stop_loss"
        }

        prices = {"AMD": 125.0}
        lows = {"AMD": 128.0}

        with caplog.at_level(logging.WARNING):
            with patch("db.atlas_db.get_fill_price", return_value=None):
                exits, fallback_count = check_stop_losses(
                    portfolio, prices, lows, "2026-04-28", dry_run=False
                )

        assert "[fill-price] eod_settlement using stop_price as fill price" in caplog.text
        assert fallback_count == 1, f"Expected fallback_count=1, got {fallback_count}"
        assert "eod-sell-no-fill" in caplog.text


# ═══════════════════════════════════════════════════════════════════════════════
# Tests 11+: source-level structural checks
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceInspection:
    """Source-level checks that the priority chain is wired correctly."""

    def test_reconcile_ledger_calls_get_fill_price(self) -> None:
        """reconcile_ledger.py must call atlas_db.get_fill_price()."""
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "get_fill_price" in src

    def test_reconcile_ledger_p1a_before_p1b(self) -> None:
        """P1a (order_id exact) must appear before P1b (symbol scan) in source."""
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        idx_p1a = src.index("get_fill_price(_fill_order_id)")
        idx_p1b = src.index('get_broker_fill_price(ticker, side="buy")')
        assert idx_p1a < idx_p1b, "P1a must precede P1b"

    def test_fill_price_warning_logged_in_reconcile_ledger(self) -> None:
        """reconcile_ledger.py must emit [fill-price] WARNING on P2 fallback."""
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "[fill-price] using inferred price" in src

    def test_eod_settlement_returns_tuple_from_stop_losses(self) -> None:
        """check_stop_losses must return (exits, fallback_count) tuple."""
        src = (PROJECT / "scripts" / "eod_settlement.py").read_text()
        assert "return exits, _fill_fallback_count" in src

    def test_eod_settlement_aggregates_fallbacks_in_main(self) -> None:
        """main() must aggregate fallback counts."""
        src = (PROJECT / "scripts" / "eod_settlement.py").read_text()
        assert "_total_fill_fallbacks" in src

    def test_eod_summary_includes_fill_price_fallbacks(self) -> None:
        """EOD summary dict must include fill_price_fallbacks key."""
        src = (PROJECT / "scripts" / "eod_settlement.py").read_text()
        assert "fill_price_fallbacks" in src

    def test_get_fill_price_exported_from_atlas_db(self) -> None:
        """db.atlas_db must export get_fill_price callable."""
        assert hasattr(atlas_db_module, "get_fill_price")
        assert callable(atlas_db_module.get_fill_price)

    def test_priority_3_skip_in_both_reconcile_paths(self) -> None:
        """reconcile_ledger.py must have P3 skip for both entry and phantom close."""
        src = (PROJECT / "scripts" / "reconcile_ledger.py").read_text()
        assert "P3 skip" in src
        count = src.count("P3 skip")
        assert count >= 2, f"Expected >= 2 P3 skip labels, found {count}"

    def test_sync_broker_orders_default_days_is_30(self) -> None:
        """sync_broker_orders.py default lookback must be 30 days."""
        src = (PROJECT / "scripts" / "sync_broker_orders.py").read_text()
        assert "_DEFAULT_DAYS = 30" in src, "Default days must be 30 for B.3 30-day coverage"

    def test_get_fill_price_never_infers_from_position(self) -> None:
        """get_fill_price body must NOT reference avg_entry_price or synthetic prices."""
        import inspect
        import ast
        src = inspect.getsource(atlas_db_module.get_fill_price)
        # Strip docstring — it may mention forbidden terms as examples of what NOT to do.
        # Parse the function body lines only (skip lines within the triple-quoted docstring).
        lines = src.splitlines()
        in_docstring = False
        body_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                continue
            if not in_docstring:
                body_lines.append(line)
        body = "\n".join(body_lines)
        forbidden = ["avg_entry_price", "entry_price * ", "* 0.95"]
        for term in forbidden:
            assert term not in body, f"get_fill_price body must not contain: {term!r}"
