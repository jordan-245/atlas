#!/usr/bin/env python3
"""Tests for scripts/sync_paper_orders.py — paper-fill poller.

Five core tests covering:
1. PENDING→FILLED transition creates exactly one paper_trades row.
2. Calling sync twice with the same FILLED order creates no duplicate.
3. Filled SELL order closes an open paper_trade.
4. Strategy resolved via plan file when multiple PAPER strategies exist.
5. Ambiguous strategy (multi-PAPER, no plan entry) skipped with WARNING.

The autouse _isolate_prod_db fixture from conftest.py handles DB isolation.

NOTE: Tests seed strategy_lifecycle in a *separate committed transaction*
before calling _record_newly_filled_paper_trades. This is required because
_record_newly_filled_paper_trades internally calls atlas_db.record_paper_trade_entry
which opens a NEW write connection — and SQLite (even in WAL mode) allows
only one writer at a time, so an uncommitted outer write transaction would
block the inner write for the full busy_timeout (30 s = test timeout).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

ATLAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATLAS_ROOT))

import scripts.sync_paper_orders as spo  # noqa: E402


# ── Shared mock helper ────────────────────────────────────────────────────────

def _make_mock_order(
    order_id: str,
    symbol: str,
    side: str,
    qty: float,
    filled_qty: float | None,
    fill_price: float | None,
    status: str,
    submitted_at: str,
    filled_at: str | None = None,
    order_class: str = "simple",
    legs: list | None = None,
) -> MagicMock:
    """Build a MagicMock that looks like an Alpaca alpaca-py Order object."""
    order = MagicMock()
    order.model_dump.return_value = {
        "id":               order_id,
        "client_order_id":  f"atlas-{order_id[:8]}",
        "symbol":           symbol,
        "side":             side,
        "qty":              str(qty),
        "filled_qty":       str(filled_qty) if filled_qty is not None else "0",
        "filled_avg_price": str(fill_price) if fill_price is not None else None,
        "status":           status,
        "submitted_at":     submitted_at,
        "filled_at":        filled_at,
        "order_class":      order_class,
        "replaces":         None,
        "created_at":       submitted_at,
        "updated_at":       submitted_at,
        "asset_class":      "us_equity",
        "time_in_force":    "day",
        "type":             "limit",
        "order_type":       "limit",
        "limit_price":      None,
        "stop_price":       None,
        "legs":             legs or [],
        "extended_hours":   False,
    }
    return order


def _seed_paper_strategy(strategy: str, universe: str = "sp500") -> None:
    """Insert a PAPER lifecycle row in its own committed transaction."""
    import db.atlas_db as _adb
    with _adb.get_db() as db:
        db.execute(
            """INSERT OR REPLACE INTO strategy_lifecycle
               (strategy, universe, state, entered_state_at)
               VALUES (?, ?, 'PAPER', '2026-05-01T00:00:00')""",
            (strategy, universe),
        )
    # Connection exits here → row is committed; safe for subsequent writers.


def _insert_open_paper_trade(
    ticker: str,
    strategy: str,
    universe: str = "sp500",
    entry_price: float = 100.0,
) -> None:
    """Insert a minimal open paper_trade row via its own committed transaction."""
    import db.atlas_db as _adb
    with _adb.get_db() as db:
        db.execute(
            """INSERT INTO paper_trades
               (ticker, strategy, universe, direction,
                entry_date, entry_price, shares, status)
               VALUES (?, ?, ?, 'long', date('now'), ?, 10, 'open')""",
            (ticker, strategy, universe, entry_price),
        )


def _count_paper_trades() -> int:
    import db.atlas_db as _adb
    with _adb.get_db() as db:
        return db.execute("SELECT COUNT(*) FROM paper_trades").fetchone()[0]


def _query_paper_trade(ticker: str) -> dict | None:
    """Fetch the single paper_trades row for a ticker (for assertions)."""
    import db.atlas_db as _adb
    with _adb.get_db() as db:
        row = db.execute(
            "SELECT ticker, strategy, entry_price, exit_price, exit_date, status "
            "FROM paper_trades WHERE ticker=? ORDER BY id DESC LIMIT 1",
            (ticker,),
        ).fetchone()
    return dict(row) if row else None


# ── Shared fixture: mock derive_universe to avoid network calls ───────────────

@pytest.fixture(autouse=True)
def _mock_derive_universe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch universe.membership.derive_universe to avoid sp500 network/disk
    lookup that causes 30-second hangs when a ticker isn't in the local cache.
    """
    import universe.membership as _mem
    monkeypatch.setattr(_mem, "derive_universe", lambda t, h=None: h or "sp500")


# ════════════════════════════════════════════════════════════════════════════
# Test 1 — PENDING→FILLED transition creates exactly one paper_trades row
# ════════════════════════════════════════════════════════════════════════════

def test_pending_to_filled_transition_creates_paper_trade() -> None:
    """BUY order PENDING on first call → 0 rows; FILLED on second call → 1 row."""
    import db.atlas_db as _adb

    _seed_paper_strategy("mean_reversion")

    pending = _make_mock_order(
        "order-aaa-001", "OMC", "buy", 5.0, 0.0, None,
        status="new",
        submitted_at="2026-05-18T20:00:00+00:00",
    )
    filled = _make_mock_order(
        "order-aaa-001", "OMC", "buy", 5.0, 5.0, 85.50,
        status="filled",
        submitted_at="2026-05-18T20:00:00+00:00",
        filled_at="2026-05-18T20:30:00+00:00",
    )

    # First sync — pending order must NOT create a row
    with _adb.get_db() as db:
        spo._record_newly_filled_paper_trades(db, [pending], dry_run=False)

    assert _count_paper_trades() == 0, "Pending order must not create a paper_trades row"

    # Second sync — filled order must create exactly one row
    with _adb.get_db() as db:
        spo._record_newly_filled_paper_trades(db, [filled], dry_run=False)

    assert _count_paper_trades() == 1, "Filled order must create exactly one paper_trades row"

    row = _query_paper_trade("OMC")
    assert row is not None
    assert row["ticker"] == "OMC"
    assert row["strategy"] == "mean_reversion"
    assert abs(row["entry_price"] - 85.50) < 0.01
    assert row["status"] == "open"


# ════════════════════════════════════════════════════════════════════════════
# Test 2 — Calling sync twice with the same FILLED order yields no duplicate
# ════════════════════════════════════════════════════════════════════════════

def test_idempotent_no_duplicate_paper_trade() -> None:
    """Two syncs with identical FILLED BUY order produce exactly 1 row, not 2."""
    import db.atlas_db as _adb

    _seed_paper_strategy("connors_rsi2")

    filled = _make_mock_order(
        "order-bbb-001", "ZTS", "buy", 3.0, 3.0, 200.00,
        status="filled",
        submitted_at="2026-05-18T21:00:00+00:00",
        filled_at="2026-05-18T21:05:00+00:00",
    )

    # First call — inserts the row
    with _adb.get_db() as db:
        stats1 = spo._record_newly_filled_paper_trades(db, [filled], dry_run=False)
    assert stats1["paper_trades_inserted"] == 1

    # Second call with identical order — idempotency check must block duplicate
    with _adb.get_db() as db:
        stats2 = spo._record_newly_filled_paper_trades(db, [filled], dry_run=False)
    assert stats2["paper_trades_inserted"] == 0, (
        "Second sync with same order must not insert a duplicate"
    )

    assert _count_paper_trades() == 1, "Must have exactly 1 paper_trades row"


# ════════════════════════════════════════════════════════════════════════════
# Test 3 — Filled SELL order closes the matching open paper_trade
# ════════════════════════════════════════════════════════════════════════════

def test_sell_records_paper_exit() -> None:
    """Open paper_trade + matching filled SELL → status='closed', exit_price set."""
    import db.atlas_db as _adb

    _seed_paper_strategy("short_term_mr")
    _insert_open_paper_trade("ZTS", "short_term_mr", entry_price=200.0)

    # Verify setup
    assert _count_paper_trades() == 1
    row_before = _query_paper_trade("ZTS")
    assert row_before["status"] == "open"

    sell = _make_mock_order(
        "order-ccc-001", "ZTS", "sell", 3.0, 3.0, 210.00,
        status="filled",
        submitted_at="2026-05-19T14:00:00+00:00",
        filled_at="2026-05-19T14:01:00+00:00",
    )

    with _adb.get_db() as db:
        stats = spo._record_newly_filled_paper_trades(db, [sell], dry_run=False)

    assert stats["paper_exits_recorded"] == 1, (
        f"Expected 1 exit recorded, got {stats['paper_exits_recorded']}"
    )

    row = _query_paper_trade("ZTS")
    assert row is not None
    assert row["status"] == "closed", f"Expected status='closed', got {row['status']!r}"
    assert row["exit_price"] is not None, "exit_price must be populated after SELL"
    assert abs(row["exit_price"] - 210.0) < 0.01


# ════════════════════════════════════════════════════════════════════════════
# Test 4 — Strategy resolved via plan file when multiple PAPER strategies exist
# ════════════════════════════════════════════════════════════════════════════

def test_strategy_lookup_via_plan_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When 2+ PAPER strategies share same universe, plan file resolves which one."""
    import db.atlas_db as _adb

    _seed_paper_strategy("mean_reversion")
    _seed_paper_strategy("connors_rsi2")

    # Plan file returns 'mean_reversion' for OMC
    monkeypatch.setattr(
        spo, "_lookup_strategy_from_plans",
        lambda ticker, universe, date: "mean_reversion" if ticker == "OMC" else None,
    )

    filled = _make_mock_order(
        "order-ddd-001", "OMC", "buy", 5.0, 5.0, 85.00,
        status="filled",
        submitted_at="2026-05-18T20:00:00+00:00",
        filled_at="2026-05-18T20:30:00+00:00",
    )

    with _adb.get_db() as db:
        stats = spo._record_newly_filled_paper_trades(db, [filled], dry_run=False)

    assert stats["paper_trades_inserted"] == 1, (
        f"Expected 1 inserted via plan fallback, got {stats['paper_trades_inserted']}"
    )
    row = _query_paper_trade("OMC")
    assert row is not None
    assert row["strategy"] == "mean_reversion", (
        f"Expected 'mean_reversion' from plan fallback, got {row['strategy']!r}"
    )


# ════════════════════════════════════════════════════════════════════════════
# Test 5 — Ambiguous strategy (multiple PAPER, no plan entry) → skipped + WARNING
# ════════════════════════════════════════════════════════════════════════════

def test_unknown_strategy_skipped_with_warning(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Multiple PAPER strategies + no plan entry → 0 paper_trades + WARNING logged."""
    import db.atlas_db as _adb

    _seed_paper_strategy("mean_reversion")
    _seed_paper_strategy("connors_rsi2")

    # Plan-file lookup returns None (no matching entry for this ticker)
    monkeypatch.setattr(
        spo, "_lookup_strategy_from_plans",
        lambda ticker, universe, date: None,
    )

    filled = _make_mock_order(
        "order-eee-001", "OMC", "buy", 5.0, 5.0, 85.00,
        status="filled",
        submitted_at="2026-05-18T20:00:00+00:00",
        filled_at="2026-05-18T20:30:00+00:00",
    )

    with caplog.at_level(logging.WARNING):
        with _adb.get_db() as db:
            stats = spo._record_newly_filled_paper_trades(db, [filled], dry_run=False)

    # Must not create any paper_trades row
    assert _count_paper_trades() == 0, (
        f"Expected 0 paper_trades rows when strategy ambiguous, got {_count_paper_trades()}"
    )
    assert stats["paper_trades_inserted"] == 0

    # Must emit a WARNING about the unresolvable strategy
    warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any(
        "multiple" in w.lower() or "ambiguous" in w.lower() or "cannot" in w.lower()
        for w in warnings
    ), f"Expected WARNING about ambiguous strategy; got warnings: {warnings}"
