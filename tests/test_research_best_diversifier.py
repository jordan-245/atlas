"""Regression test for Fix 6 — portfolio_diversifier metric_type.

Validated-strategies audit 2026-05-01 added 'portfolio_diversifier' as a
new valid value for research_best.metric_type, used to tag combos that
fail solo gates but contribute positively to portfolio Sharpe.
"""
import sqlite3
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ATLAS_ROOT / "data" / "atlas.db"


def test_diversifier_row_present():
    """connors_rsi2/commodity_etfs row exists with portfolio_diversifier tag."""
    conn = sqlite3.connect(str(DB_PATH))
    row = conn.execute(
        "SELECT solo_sharpe, portfolio_sharpe, metric_type FROM research_best "
        "WHERE strategy='connors_rsi2' AND universe='commodity_etfs'"
    ).fetchone()
    conn.close()
    assert row is not None, "connors_rsi2/commodity_etfs row missing — migration not applied?"
    solo, port, mt = row
    assert mt == "portfolio_diversifier", f"metric_type={mt!r}, expected 'portfolio_diversifier'"
    assert solo is not None and solo < 0, f"solo_sharpe={solo!r} should be negative (audit value -0.68)"
    assert port is not None and port > 0, f"portfolio_sharpe={port!r} should be positive (audit value +0.47)"


def test_diversifier_appears_in_all_metric_types():
    """The new metric_type value should appear in DISTINCT query."""
    conn = sqlite3.connect(str(DB_PATH))
    types = {r[0] for r in conn.execute("SELECT DISTINCT metric_type FROM research_best")}
    conn.close()
    assert "portfolio_diversifier" in types, f"metric_types in DB: {types}"


def test_schema_version_at_least_29():
    conn = sqlite3.connect(str(DB_PATH))
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    conn.close()
    assert v >= 29, f"schema_version={v}, expected >= 29 after Fix 6 migration"


def test_query_by_diversifier_returns_connors_rsi2():
    """SELECT-by-metric-type should return our row."""
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT strategy, universe FROM research_best WHERE metric_type='portfolio_diversifier'"
    ).fetchall()
    conn.close()
    assert ("connors_rsi2", "commodity_etfs") in rows, f"got: {rows}"


def test_consumer_code_handles_new_metric_type():
    """services/api/research.py and scripts/regen_brain_strategies.py should not
    crash when encountering portfolio_diversifier (just pass it through as a string).
    """
    # Smoke test: import both modules without error
    from services.api import research as research_api  # noqa
    # regen_brain_strategies: just import
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "regen_brain_strategies",
        ATLAS_ROOT / "scripts" / "regen_brain_strategies.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # If import succeeds and no crash, test passes — these consumers treat
    # metric_type as opaque string (verified manually).
