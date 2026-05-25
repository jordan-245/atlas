"""Regression test: reconcile_shadow does NOT emit WARNING when trade already open."""
import logging
import sqlite3
import sys
from pathlib import Path
from datetime import datetime

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tmp_atlas_db(tmp_path, monkeypatch):
    """Point atlas_db at a clean tmp database with the trades schema."""
    from db import atlas_db
    db_path = tmp_path / "atlas_test.db"
    monkeypatch.setattr(atlas_db, "_db_path_override", str(db_path))
    atlas_db.init_db()
    yield db_path


def _seed_open_amd(db_path):
    """Insert an open AMD/sp500 row directly."""
    con = sqlite3.connect(str(db_path))
    con.execute(
        """INSERT INTO trades (ticker, strategy, universe, direction, entry_date,
                               entry_price, shares, stop_price, take_profit, confidence,
                               status)
           VALUES ('AMD', 'momentum_breakout', 'sp500', 'long', ?, 100.0, 10, 95.0,
                   NULL, 0.5, 'open')""",
        (datetime.now().isoformat(),),
    )
    con.commit()
    con.close()


def test_pre_check_skips_when_open_row_exists(tmp_atlas_db, caplog):
    """When AMD/sp500 has an open row, the shadow path must NOT warn."""
    from scripts import reconcile_shadow
    _seed_open_amd(tmp_atlas_db)

    with caplog.at_level(logging.WARNING):
        if hasattr(reconcile_shadow, "_open_trade_exists"):
            # Strategy A: standalone helper
            assert reconcile_shadow._open_trade_exists("AMD", "sp500") is True
        elif hasattr(reconcile_shadow, "_ShadowAtlasDB"):
            # Strategy B: shim intercept
            from db import atlas_db
            shim = reconcile_shadow._ShadowAtlasDB(atlas_db)
            result = shim.record_trade_entry(
                "AMD", "momentum_breakout", "sp500", 100.0, 10, 95.0, None, 0.5, None,
            )
            assert result is None  # skipped
        else:
            pytest.fail("Fix must expose either _open_trade_exists or _ShadowAtlasDB")

    # Critical assertion — no WARNING fired during the dedup pre-check
    warn_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warn_msgs, f"unexpected warnings: {[(r.name, r.message) for r in warn_msgs]}"

    # Row count unchanged
    con = sqlite3.connect(str(tmp_atlas_db))
    n = con.execute("SELECT COUNT(*) FROM trades WHERE ticker='AMD'").fetchone()[0]
    con.close()
    assert n == 1, "no new row inserted"


def test_inserts_when_no_open_row(tmp_atlas_db, caplog):
    """When trades table is empty, the shadow path must insert normally."""
    from scripts import reconcile_shadow
    from db import atlas_db

    with caplog.at_level(logging.WARNING):
        if hasattr(reconcile_shadow, "_open_trade_exists"):
            assert reconcile_shadow._open_trade_exists("AMD", "sp500") is False
            new_id = atlas_db.record_trade_entry(
                "AMD", "momentum_breakout", "sp500", 100.0, 10, 95.0, None, 0.5, None,
            )
        elif hasattr(reconcile_shadow, "_ShadowAtlasDB"):
            shim = reconcile_shadow._ShadowAtlasDB(atlas_db)
            new_id = shim.record_trade_entry(
                "AMD", "momentum_breakout", "sp500", 100.0, 10, 95.0, None, 0.5, None,
            )
        else:
            pytest.fail("Fix must expose either _open_trade_exists or _ShadowAtlasDB")

    assert new_id is not None
    warn_msgs = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warn_msgs, "no warning expected on fresh insert"


def test_shadow_db_shim_forwards_other_methods(tmp_atlas_db):
    """_ShadowAtlasDB must forward all non-record_trade_entry attrs to real atlas_db."""
    from scripts import reconcile_shadow
    from db import atlas_db

    if not hasattr(reconcile_shadow, "_ShadowAtlasDB"):
        pytest.skip("_ShadowAtlasDB not present — Strategy A in use")

    shim = reconcile_shadow._ShadowAtlasDB(atlas_db)
    # get_db and get_open_positions should be forwarded unchanged
    assert callable(shim.get_db)
    assert shim.get_db is atlas_db.get_db


def test_open_trade_exists_returns_false_for_closed_trade(tmp_atlas_db):
    """A closed trade must NOT be treated as an existing open trade."""
    from scripts import reconcile_shadow

    if not hasattr(reconcile_shadow, "_open_trade_exists"):
        pytest.skip("_open_trade_exists not present — Strategy B in use")

    # Insert a CLOSED AMD row
    con = sqlite3.connect(str(tmp_atlas_db))
    con.execute(
        """INSERT INTO trades (ticker, strategy, universe, direction, entry_date,
                               entry_price, shares, stop_price, take_profit, confidence,
                               status, exit_date, exit_price)
           VALUES ('AMD', 'momentum_breakout', 'sp500', 'long', ?, 100.0, 10, 95.0,
                   NULL, 0.5, 'closed', ?, 105.0)""",
        (datetime.now().isoformat(), datetime.now().isoformat()),
    )
    con.commit()
    con.close()

    # Should return False because the trade is closed, not open
    assert reconcile_shadow._open_trade_exists("AMD", "sp500") is False
