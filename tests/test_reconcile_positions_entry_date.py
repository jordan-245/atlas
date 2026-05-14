"""tests/test_reconcile_positions_entry_date.py — entry_date fix for reconcile_positions.py:376

Verifies that the --fix path in reconcile_positions() uses SQLite trades.entry_date
as the authoritative source for entry_date, rather than defaulting to datetime.now().

The bug (Worker B flag, #FIX-PMEQ-002 residual): line 376 of reconcile_positions.py had:
    "entry_date": internal_pos.get("entry_date", datetime.now().strftime("%Y-%m-%d"))

When internal_pos had no entry_date, it defaulted to today's date — masking the real
trade open date.  The fix pre-queries SQLite and uses a priority chain:
  Source 0 (highest): SQLite trades.entry_date (authoritative)
  Source 1: internal state file entry_date
  Source 2 (fallback): today + WARNING log

Run with: python3 -m pytest tests/test_reconcile_positions_entry_date.py -v --timeout=30
"""
from __future__ import annotations

import json
import logging
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Test helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_config(tmp_path: Path, market_id: str) -> None:
    """Write minimal active config for market_id under tmp_path."""
    cfg_dir = tmp_path / "config" / "active"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {"trading": {"broker": "alpaca", "mode": "live"}}
    (cfg_dir / f"{market_id}.json").write_text(json.dumps(cfg))


def _make_state_file(tmp_path: Path, market_id: str, positions: list[dict]) -> Path:
    """Write live_{market_id}.json with the given positions."""
    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "market_id": market_id,
        "mode": "live",
        "positions": positions,
        "closed_trades": [],
        "equity_history": [],
        "last_saved": "2026-05-14T00:00:00",
    }
    path = state_dir / f"live_{market_id}.json"
    path.write_text(json.dumps(state))
    return state_dir


def _broker_position(ticker: str, entry_price: float = 835.0, shares: int = 1):
    return types.SimpleNamespace(
        ticker=ticker,
        entry_price=entry_price,
        shares=shares,
        current_price=entry_price,
        market_value=entry_price * shares,
        stop_price=entry_price * 0.95,
        strategy="",
        entry_date="",
    )


def _mock_broker(positions: list) -> MagicMock:
    b = MagicMock()
    b.connect.return_value = True
    b.get_positions.return_value = positions
    b.disconnect.return_value = None
    return b


def _seed_sqlite(ticker: str, entry_date: str, universe: str = "sp500") -> None:
    """Insert an open trade into the isolated test DB and backdate entry_date."""
    import db.atlas_db as _adb
    _adb.init_db()
    _adb.record_trade_entry(
        ticker=ticker,
        strategy="momentum_breakout",
        universe=universe,
        entry_price=835.0,
        shares=1,
        stop_price=793.25,
        take_profit=900.0,
        confidence=0.8,
        regime_state=None,
        direction="long",
    )
    with _adb.get_db() as conn:
        conn.execute(
            "UPDATE trades SET entry_date=? WHERE ticker=? AND status='open'",
            (entry_date, ticker),
        )


def _run_fix(
    tmp_path: Path,
    market_id: str,
    broker_positions: list,
    internal_positions: list[dict],
    extra_patches: dict | None = None,
) -> tuple[dict, list[dict]]:
    """Run reconcile_positions --fix with all external deps patched.

    Returns (result_dict, corrected_positions_from_state_file).
    """
    state_dir = _make_state_file(tmp_path, market_id, internal_positions)
    _make_config(tmp_path, market_id)
    broker = _mock_broker(broker_positions)
    tickers_in_scope = [p.ticker for p in broker_positions]

    # Capture what gets written to the state file
    import scripts.reconcile_positions as rp_mod
    original_save = rp_mod.save_internal_state
    captured: list[dict] = []

    def _spy_save(mkt, state_obj):
        captured.clear()
        captured.extend(state_obj.get("positions", []))
        original_save(mkt, state_obj)

    with (
        patch("scripts.reconcile_positions.PROJECT", tmp_path),
        patch("scripts.reconcile_positions._STATE_DIR", state_dir),
        patch("brokers.registry.get_live_broker", return_value=broker),
        patch("universe.builder.get_universe_tickers", return_value=tickers_in_scope),
        patch.object(rp_mod, "save_internal_state", _spy_save),
    ):
        result = rp_mod.reconcile_positions(market_id=market_id, fix=True, dry_run=False)

    return result, captured


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestEntryDateUsesSqliteWhenInternalPosMissing:
    """entry_date in corrected_positions must come from SQLite when internal state lacks it."""

    def test_entry_date_uses_sqlite_when_internal_pos_missing(self, tmp_path):
        """Internal state has empty entry_date; SQLite has canonical historical date.

        Scenario: The broker position state file was rebuilt by a prior --fix run
        that wrote today's date (or empty) as entry_date. SQLite still has the
        correct historical date. The fix must use SQLite.

        Expected: corrected_positions[0]["entry_date"] == "2026-04-24" (SQLite value),
        NOT today's date and NOT empty string.
        """
        market_id = "sp500"
        canonical_date = "2026-04-24"
        ticker = "CAT"

        # SQLite has the real historical entry_date
        _seed_sqlite(ticker, canonical_date, universe=market_id)

        # Internal state has the ticker but entry_date is empty (stale / never set)
        internal_positions = [
            {
                "ticker": ticker,
                "strategy": "momentum_breakout",
                "entry_date": "",          # ← empty: the problematic state
                "entry_price": 835.24,
                "shares": 1,
                "stop_price": 793.78,
                "order_id": "",
                "stop_order_id": "",
                "tp_order_id": "",
            }
        ]
        # Broker also has an EXTRA position to guarantee a discrepancy triggers --fix
        broker_positions = [
            _broker_position(ticker, 835.24),
            _broker_position("EXTRA", 100.0),  # UNTRACKED → triggers fix
        ]

        result, corrected = _run_fix(tmp_path, market_id, broker_positions, internal_positions)

        # Fix path must have run (discrepancy was detected)
        assert result.get("fixed") is True or corrected, (
            f"Fix path did not run — discrepancies={result.get('discrepancies')}"
        )

        # Find CAT in the corrected positions
        cat_pos = next((p for p in corrected if p["ticker"] == ticker), None)
        assert cat_pos is not None, f"{ticker} not found in corrected_positions: {corrected}"

        today = datetime.now().strftime("%Y-%m-%d")
        assert cat_pos["entry_date"] == canonical_date, (
            f"entry_date={cat_pos['entry_date']!r} should be SQLite canonical date "
            f"{canonical_date!r}, not today ({today!r}) or empty"
        )

    def test_entry_date_uses_sqlite_over_internal_stale_today(self, tmp_path):
        """SQLite entry_date beats a stale 'today' already written into internal state.

        Scenario: a prior --fix run wrote today's date as entry_date into the state.
        SQLite still holds the real historical date. SQLite must win.
        """
        market_id = "sp500"
        canonical_date = "2026-03-15"
        stale_today = "2026-05-14"  # wrongly written by a prior bad --fix run
        ticker = "MSFT"

        _seed_sqlite(ticker, canonical_date, universe=market_id)

        internal_positions = [
            {
                "ticker": ticker,
                "strategy": "mean_reversion",
                "entry_date": stale_today,   # ← stale today-date from prior fix
                "entry_price": 420.0,
                "shares": 2,
                "stop_price": 400.0,
                "order_id": "",
                "stop_order_id": "",
                "tp_order_id": "",
            }
        ]
        broker_positions = [
            _broker_position(ticker, 420.0, 2),
            _broker_position("TRIGGER", 50.0),  # creates UNTRACKED discrepancy
        ]

        _, corrected = _run_fix(tmp_path, market_id, broker_positions, internal_positions)

        msft_pos = next((p for p in corrected if p["ticker"] == ticker), None)
        assert msft_pos is not None

        assert msft_pos["entry_date"] == canonical_date, (
            f"SQLite entry_date {canonical_date!r} must override "
            f"stale internal entry_date {stale_today!r}; "
            f"got {msft_pos['entry_date']!r}"
        )


class TestEntryDateTodayFallbackLogged:
    """When neither SQLite nor internal state has entry_date, fall back to today + WARNING."""

    def test_entry_date_today_fallback_logged(self, tmp_path, caplog):
        """No entry_date in SQLite or internal state → today + WARNING emitted.

        The fallback must not crash, and the operator must be alerted via WARNING log.
        """
        market_id = "sp500"
        ticker = "NVDA"
        # No SQLite trade for NVDA → SQLite lookup returns nothing

        internal_positions = [
            {
                "ticker": ticker,
                "strategy": "momentum_breakout",
                "entry_date": "",   # also empty in internal state
                "entry_price": 900.0,
                "shares": 1,
                "stop_price": 855.0,
                "order_id": "",
                "stop_order_id": "",
                "tp_order_id": "",
            }
        ]
        broker_positions = [
            _broker_position(ticker, 900.0),
            _broker_position("TRIGGER", 50.0),  # creates UNTRACKED discrepancy
        ]

        with caplog.at_level(logging.WARNING):
            _, corrected = _run_fix(tmp_path, market_id, broker_positions, internal_positions)

        nvda_pos = next((p for p in corrected if p["ticker"] == ticker), None)
        assert nvda_pos is not None

        today = datetime.now().strftime("%Y-%m-%d")
        assert nvda_pos["entry_date"] == today, (
            f"entry_date={nvda_pos['entry_date']!r} should fallback to today ({today!r})"
        )

        # A WARNING log mentioning the ticker must be emitted
        warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        ticker_warned = any(ticker in m for m in warning_messages)
        assert ticker_warned, (
            f"Expected a WARNING log mentioning {ticker!r} for missing entry_date. "
            f"Got warnings: {warning_messages}"
        )

    def test_entry_date_today_fallback_not_warning_when_sqlite_has_date(self, tmp_path, caplog):
        """No 'defaulting to today' WARNING when SQLite provides a valid entry_date."""
        market_id = "sp500"
        ticker = "AAPL"
        canonical_date = "2026-04-01"

        _seed_sqlite(ticker, canonical_date, universe=market_id)

        internal_positions = [
            {
                "ticker": ticker,
                "strategy": "momentum_breakout",
                "entry_date": "",  # empty — should be filled from SQLite
                "entry_price": 175.0,
                "shares": 3,
                "stop_price": 166.25,
                "order_id": "",
                "stop_order_id": "",
                "tp_order_id": "",
            }
        ]
        broker_positions = [
            _broker_position(ticker, 175.0, 3),
            _broker_position("TRIGGER", 50.0),
        ]

        with caplog.at_level(logging.WARNING):
            _, corrected = _run_fix(tmp_path, market_id, broker_positions, internal_positions)

        # No 'defaulting to today' warning about AAPL specifically
        # (TRIGGER helper ticker may warn — that's expected, only AAPL matters here)
        fallback_warnings_for_aapl = [
            r.message for r in caplog.records
            if r.levelno >= logging.WARNING
            and "defaulting to today" in r.message
            and ticker in r.message
        ]
        assert not fallback_warnings_for_aapl, (
            f"No fallback WARNING should be emitted for {ticker!r} when SQLite has the entry_date. "
            f"Got: {fallback_warnings_for_aapl}"
        )

        aapl_pos = next((p for p in corrected if p["ticker"] == ticker), None)
        assert aapl_pos is not None
        assert aapl_pos["entry_date"] == canonical_date
