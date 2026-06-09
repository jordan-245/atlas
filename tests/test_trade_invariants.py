"""Trade invariant tests — CHECK constraint + backfill direction guard.

Verifies:
  1. DB CHECK constraint rejects inverted long stop  (stop >= entry)
  2. DB CHECK constraint rejects inverted short stop (stop <= entry)
  3. Valid long stop accepted                        (stop < entry)
  4. Valid short stop accepted                       (stop > entry)
  5. NULL stop accepted                              (no constraint violation)

All tests use the conftest autouse _isolate_prod_db fixture so they never
touch data/atlas.db. The fixture calls init_db() which now creates the
stop-direction CHECK via the updated db/schema.sql.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

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


