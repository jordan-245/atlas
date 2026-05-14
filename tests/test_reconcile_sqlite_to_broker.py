"""tests/test_reconcile_sqlite_to_broker.py — Stop-guard tests for reconcile_sqlite_to_broker.py

Tests that invalid stop_price values (None, zero, inverted) are skipped
and valid stops proceed to INSERT.

Run with: python3 -m pytest tests/test_reconcile_sqlite_to_broker.py -v --timeout=30
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_broker_state(tmp_path: Path, positions: list[dict[str, Any]]) -> Path:
    """Write a fake live_sp500.json to tmp_path and return its parent dir."""
    state_dir = tmp_path / "brokers" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "live_sp500.json"
    state_file.write_text(json.dumps({
        "market_id": "sp500",
        "positions": positions,
    }))
    return state_dir


def _make_empty_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with a trades table."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT,
            universe TEXT,
            direction TEXT DEFAULT 'long',
            entry_date TEXT,
            entry_price REAL,
            shares INTEGER,
            stop_price REAL,
            take_profit REAL,
            status TEXT DEFAULT 'open',
            stop_order_id TEXT DEFAULT '',
            tp_order_id TEXT DEFAULT '',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _run_reconcile(
    tmp_path: Path,
    positions: list[dict[str, Any]],
    dry_run: bool = False,
) -> tuple[int, list[dict]]:
    """Run reconcile() with patched BROKER_STATE_DIR and atlas_db, return (changes, inserts)."""
    import importlib
    state_dir = _make_broker_state(tmp_path, positions)
    db_path = _make_empty_db(tmp_path)

    # We need to patch BROKER_STATE_DIR in the module and atlas_db.get_db
    import scripts.reconcile_sqlite_to_broker as mod

    inserted_rows: list[dict] = []

    class FakeConn:
        def __init__(self):
            self._conn = sqlite3.connect(str(db_path))
            self._conn.row_factory = sqlite3.Row

        def execute(self, sql, params=()):
            # Track INSERTs
            if sql.strip().startswith("INSERT INTO trades"):
                inserted_rows.append(dict(zip(
                    ["ticker", "strategy", "universe", "direction", "entry_date",
                     "entry_price", "shares", "stop_price", "take_profit",
                     "status", "stop_order_id", "tp_order_id", "created_at", "updated_at"],
                    params,
                )))
            return self._conn.execute(sql, params)

        def commit(self):
            self._conn.commit()

        def fetchone(self):
            return None

    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        conn = FakeConn()
        yield conn

    original_state_dir = mod.BROKER_STATE_DIR
    try:
        mod.BROKER_STATE_DIR = state_dir
        with patch("scripts.reconcile_sqlite_to_broker.atlas_db") as mock_db:
            mock_db.get_db = fake_get_db

            changes = mod.reconcile(dry_run=dry_run)
    finally:
        mod.BROKER_STATE_DIR = original_state_dir

    return changes, inserted_rows


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestStopGuards:
    """Stop-price invariant guards in reconcile_sqlite_to_broker.reconcile()."""

    def test_skip_none_stop(self, tmp_path: Path, caplog) -> None:
        """A position with stop_price=None is skipped — nothing inserted."""
        pos = {
            "ticker": "AAPL",
            "strategy": "momentum",
            "entry_price": 150.0,
            "shares": 10,
            "stop_price": None,  # ← None stop
        }

        with caplog.at_level(logging.WARNING, logger="__main__"):
            changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=False)

        assert changes == 0, f"Expected 0 changes, got {changes}"
        assert inserts == [], f"Expected no inserts, got {inserts}"
        # Check warning was logged
        warning_msgs = [r.message for r in caplog.records if "stop_price" in r.message.lower()]
        assert any("None" in m or "none" in m.lower() for m in warning_msgs), (
            f"Expected 'stop_price is None' warning. Got warnings: {warning_msgs}"
        )

    def test_skip_zero_stop(self, tmp_path: Path, caplog) -> None:
        """A position with stop_price=0 is skipped (no-zero-stop guard)."""
        pos = {
            "ticker": "AAPL",
            "strategy": "momentum",
            "entry_price": 150.0,
            "shares": 10,
            "stop_price": 0.0,  # ← zero stop
        }

        with caplog.at_level(logging.WARNING, logger="__main__"):
            changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=False)

        assert changes == 0, f"Expected 0 changes, got {changes}"
        assert inserts == [], f"Expected no inserts, got {inserts}"
        warning_msgs = [r.message for r in caplog.records if "stop_price" in r.message.lower()]
        assert any("<= 0" in m or "zero" in m.lower() or "0" in m for m in warning_msgs), (
            f"Expected '<= 0' warning. Got: {warning_msgs}"
        )

    def test_skip_inverted_stop(self, tmp_path: Path, caplog) -> None:
        """A position with stop_price >= entry_price is skipped with ERROR log."""
        pos = {
            "ticker": "TSLA",
            "strategy": "momentum",
            "entry_price": 200.0,
            "shares": 5,
            "stop_price": 250.0,  # ← INVERTED: stop > entry
        }

        with caplog.at_level(logging.ERROR, logger="__main__"):
            changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=False)

        assert changes == 0, f"Expected 0 changes, got {changes}"
        assert inserts == [], f"Expected no inserts, got {inserts}"
        # Should emit at ERROR level for inverted stop
        error_msgs = [
            r.message for r in caplog.records
            if r.levelno >= logging.ERROR and "stop_price" in r.message.lower()
        ]
        assert error_msgs, (
            f"Expected ERROR log for inverted stop. All records: "
            f"{[(r.levelname, r.message) for r in caplog.records]}"
        )

    def test_skip_inverted_stop_equal(self, tmp_path: Path, caplog) -> None:
        """A position with stop_price == entry_price is also skipped (boundary)."""
        pos = {
            "ticker": "MSFT",
            "strategy": "momentum",
            "entry_price": 300.0,
            "shares": 3,
            "stop_price": 300.0,  # ← equal to entry — still invalid
        }

        with caplog.at_level(logging.ERROR, logger="__main__"):
            changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=False)

        assert changes == 0, f"Expected 0 changes for equal stop/entry, got {changes}"
        assert inserts == [], f"Expected no inserts, got {inserts}"

    def test_valid_stop_proceeds(self, tmp_path: Path) -> None:
        """A position with a valid stop (0 < stop < entry) IS inserted."""
        pos = {
            "ticker": "NVDA",
            "strategy": "momentum",
            "entry_price": 400.0,
            "shares": 2,
            "stop_price": 370.0,  # ← valid: stop < entry, stop > 0
        }

        changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=False)

        assert changes == 1, f"Expected 1 change for valid stop, got {changes}"
        assert len(inserts) == 1, f"Expected 1 insert, got {inserts}"
        assert inserts[0]["ticker"] == "NVDA"
        assert inserts[0]["stop_price"] == 370.0

    def test_valid_stop_dry_run_not_inserted(self, tmp_path: Path) -> None:
        """In dry-run mode, a valid stop is counted but NOT inserted to DB."""
        pos = {
            "ticker": "AMZN",
            "strategy": "momentum",
            "entry_price": 180.0,
            "shares": 4,
            "stop_price": 165.0,
        }

        changes, inserts = _run_reconcile(tmp_path, [pos], dry_run=True)

        assert changes == 1, f"Expected 1 planned change in dry-run, got {changes}"
        assert inserts == [], f"Expected no actual inserts in dry-run, got {inserts}"

    def test_mixed_positions_only_valid_inserted(self, tmp_path: Path, caplog) -> None:
        """With a mix of valid and invalid positions, only valid ones are inserted."""
        positions = [
            {
                "ticker": "GOOD",
                "strategy": "momentum",
                "entry_price": 100.0,
                "shares": 5,
                "stop_price": 90.0,  # valid
            },
            {
                "ticker": "BAD_NULL",
                "strategy": "momentum",
                "entry_price": 100.0,
                "shares": 5,
                "stop_price": None,  # invalid
            },
            {
                "ticker": "BAD_ZERO",
                "strategy": "momentum",
                "entry_price": 100.0,
                "shares": 5,
                "stop_price": 0,  # invalid
            },
            {
                "ticker": "BAD_INVERTED",
                "strategy": "momentum",
                "entry_price": 100.0,
                "shares": 5,
                "stop_price": 110.0,  # invalid
            },
        ]

        with caplog.at_level(logging.WARNING, logger="__main__"):
            changes, inserts = _run_reconcile(tmp_path, positions, dry_run=False)

        assert changes == 1, f"Expected 1 change (only GOOD), got {changes}"
        assert len(inserts) == 1, f"Expected 1 insert (only GOOD), got {inserts}"
        assert inserts[0]["ticker"] == "GOOD"
