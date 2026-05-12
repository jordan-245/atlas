"""Trade invariant tests — CHECK constraint + backfill direction guard.

Verifies:
  1. DB CHECK constraint rejects inverted long stop  (stop >= entry)
  2. DB CHECK constraint rejects inverted short stop (stop <= entry)
  3. Valid long stop accepted                        (stop < entry)
  4. Valid short stop accepted                       (stop > entry)
  5. NULL stop accepted                              (no constraint violation)
  6. Backfill refuses inverted stop, writes NULL, logs WARNING

All tests use the conftest autouse _isolate_prod_db fixture so they never
touch data/atlas.db. The fixture calls init_db() which now creates the
stop-direction CHECK via the updated db/schema.sql.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.atlas_db as _adb
from db.atlas_db import get_db, init_db


# ---------------------------------------------------------------------------
# Helper — get the path to the current test's isolated DB
# ---------------------------------------------------------------------------

def _current_db_path() -> str:
    """Return the path that get_db() is currently pointing at."""
    return str(getattr(_adb, "_db_path_override", "data/atlas.db"))


# ---------------------------------------------------------------------------
# 1. Inverted long stop → IntegrityError
# ---------------------------------------------------------------------------

class TestInvertedLongStopRejected:
    def test_stop_above_entry_raises(self) -> None:
        """Long position with stop > entry_price must raise IntegrityError."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO trades (ticker, strategy, direction, entry_date,
                                        entry_price, shares, stop_price, status)
                    VALUES ('AMD_INV', 'test', 'long', '2026-01-01', 100.0, 1, 110.0, 'open')
                    """
                )
        finally:
            conn.close()

    def test_stop_equal_entry_raises(self) -> None:
        """Long position with stop == entry_price must also raise IntegrityError."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO trades (ticker, strategy, direction, entry_date,
                                        entry_price, shares, stop_price, status)
                    VALUES ('AMD_EQ', 'test', 'long', '2026-01-01', 100.0, 1, 100.0, 'open')
                    """
                )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 2. Inverted short stop → IntegrityError
# ---------------------------------------------------------------------------

class TestInvertedShortStopRejected:
    def test_short_stop_below_entry_raises(self) -> None:
        """Short position with stop < entry_price must raise IntegrityError."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO trades (ticker, strategy, direction, entry_date,
                                        entry_price, shares, stop_price, status)
                    VALUES ('TSLA_SH', 'test', 'short', '2026-01-01', 100.0, 1, 90.0, 'open')
                    """
                )
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 3. Valid long stop accepted
# ---------------------------------------------------------------------------

class TestValidLongStopAccepted:
    def test_stop_below_entry_ok(self) -> None:
        """Long position with stop < entry_price must succeed."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO trades (ticker, strategy, direction, entry_date,
                                    entry_price, shares, stop_price, status)
                VALUES ('AAPL_OK', 'test', 'long', '2026-01-01', 100.0, 1, 90.0, 'open')
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='AAPL_OK'"
            ).fetchone()
            assert row is not None
            assert row[0] == 90.0
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='AAPL_OK'")
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# 4. Valid short stop accepted
# ---------------------------------------------------------------------------

class TestValidShortStopAccepted:
    def test_short_stop_above_entry_ok(self) -> None:
        """Short position with stop > entry_price must succeed."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO trades (ticker, strategy, direction, entry_date,
                                    entry_price, shares, stop_price, status)
                VALUES ('QQQ_SH', 'test', 'short', '2026-01-01', 100.0, 1, 110.0, 'open')
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='QQQ_SH'"
            ).fetchone()
            assert row is not None
            assert row[0] == 110.0
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='QQQ_SH'")
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# 5. NULL stop accepted (direction irrelevant when stop IS NULL)
# ---------------------------------------------------------------------------

class TestNullStopAccepted:
    def test_null_stop_long_ok(self) -> None:
        """Long position with stop_price IS NULL must succeed."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO trades (ticker, strategy, direction, entry_date,
                                    entry_price, shares, stop_price, status)
                VALUES ('GLD_NULL', 'test', 'long', '2026-01-01', 100.0, 1, NULL, 'open')
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='GLD_NULL'"
            ).fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='GLD_NULL'")
            conn.commit()
            conn.close()

    def test_null_stop_short_ok(self) -> None:
        """Short position with stop_price IS NULL must succeed."""
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                """
                INSERT INTO trades (ticker, strategy, direction, entry_date,
                                    entry_price, shares, stop_price, status)
                VALUES ('SLV_NULL', 'test', 'short', '2026-01-01', 100.0, 1, NULL, 'open')
                """
            )
            conn.commit()
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='SLV_NULL'"
            ).fetchone()
            assert row is not None
            assert row[0] is None
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='SLV_NULL'")
            conn.commit()
            conn.close()


# ---------------------------------------------------------------------------
# 6. Backfill logic refuses inverted stop → writes NULL + logs WARNING
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="scripts/backfill_orphan_trades.py moved to _attic/2026-05/ on 2026-05-12 per docs/cleanup-plan-2026-05.md")
class TestBackfillRefusesInvertedStop:
    """Test that backfill_orphan_trades._do_insert writes NULL for inverted stops."""

    def _make_broker_pos(
        self,
        ticker: str = "AMD",
        entry_price: float = 278.25,
        stop_price: float | None = 294.80,
        shares: int = 2,
        entry_date: str = "2026-04-18",
    ) -> dict:
        return {
            "ticker": ticker,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "shares": shares,
            "entry_date": entry_date,
            "market_id": "sp500",
        }

    def test_inverted_stop_writes_null(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_do_insert with inverted stop must INSERT with stop_price=NULL."""
        pos = self._make_broker_pos(
            ticker="AMD_TEST", entry_price=278.25, stop_price=294.80
        )
        from scripts.backfill_orphan_trades import _do_insert
        with patch("utils.telegram.send_message", side_effect=Exception("no telegram")):
            with caplog.at_level(logging.WARNING):
                result = _do_insert(pos, "momentum_breakout", "sp500", dry_run=False)
        assert result is True

        # Verify stop_price is NULL in the isolated DB
        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='AMD_TEST'"
            ).fetchone()
            assert row is not None, "AMD_TEST row not inserted"
            assert row[0] is None, f"Expected stop_price=NULL, got {row[0]}"
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='AMD_TEST'")
            conn.commit()
            conn.close()

    def test_inverted_stop_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """_do_insert with inverted stop must emit a WARNING log."""
        pos = self._make_broker_pos(
            ticker="AMD_WARN", entry_price=278.25, stop_price=294.80
        )
        from scripts.backfill_orphan_trades import _do_insert
        with patch("utils.telegram.send_message", side_effect=Exception("no telegram")):
            with caplog.at_level(logging.WARNING):
                _do_insert(pos, "momentum_breakout", "sp500", dry_run=False)

        warning_found = any(
            ("inverted stop" in r.message.lower() or "refusing" in r.message.lower())
            for r in caplog.records
            if r.levelno >= logging.WARNING
        )
        assert warning_found, (
            f"Expected WARNING about inverted stop, got: {[r.message for r in caplog.records]}"
        )

        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM trades WHERE ticker='AMD_WARN'")
        conn.commit()
        conn.close()

    def test_valid_stop_unchanged(self) -> None:
        """_do_insert with valid stop (below entry) must write the stop value."""
        pos = self._make_broker_pos(
            ticker="AAPL_BF", entry_price=100.0, stop_price=90.0
        )
        from scripts.backfill_orphan_trades import _do_insert
        result = _do_insert(pos, "test_strategy", "sp500", dry_run=False)
        assert result is True

        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute(
                "SELECT stop_price FROM trades WHERE ticker='AAPL_BF'"
            ).fetchone()
            assert row is not None
            assert row[0] == 90.0, f"Expected 90.0, got {row[0]}"
        finally:
            conn.execute("DELETE FROM trades WHERE ticker='AAPL_BF'")
            conn.commit()
            conn.close()

    def test_dry_run_skips_db_write(self) -> None:
        """_do_insert in dry_run mode must return True without writing to DB."""
        pos = self._make_broker_pos(
            ticker="DRY_INV", entry_price=200.0, stop_price=295.0
        )
        from scripts.backfill_orphan_trades import _do_insert
        result = _do_insert(pos, "test_strategy", "sp500", dry_run=True)
        assert result is True

        db_path = _current_db_path()
        conn = sqlite3.connect(db_path)
        row = conn.execute(
            "SELECT stop_price FROM trades WHERE ticker='DRY_INV'"
        ).fetchone()
        conn.close()
        assert row is None, "dry_run should not insert rows"
