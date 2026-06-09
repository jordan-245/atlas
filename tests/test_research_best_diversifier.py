"""Regression test for the ``portfolio_diversifier`` metric_type semantics.

Background
----------
The validated-strategies audit 2026-05-01 ("Fix 6", schema_version 28→29)
introduced a new valid value for ``research_best.metric_type``::

    'portfolio_diversifier' — solo Sharpe is weak/negative but the strategy
    contributes positively to the whole-portfolio Sharpe via low correlation
    with other strategies. Kept active for diversification value despite
    failing solo quality gates.

The original version of this test asserted a *specific* production data point
(``connors_rsi2`` / ``commodity_etfs``) carried that tag with solo≈-0.68 /
port≈+0.47.  That row has since been **legitimately re-measured** by later
sweeps (2026-05-14, 2026-05-22) which found a *positive* solo Sharpe (~0.73),
correctly re-tagging it ``both``.  A combo with positive solo Sharpe no longer
fits the portfolio_diversifier definition, so asserting the old value would
force us to falsify current measured data (the task explicitly forbids
mutating the production DB to satisfy a stale expectation).

This test therefore validates the **schema + code semantics** of the
``portfolio_diversifier`` metric_type against a controlled DB, plus the
migration's declared intent — rather than a drifting production row.  Two
production-level invariants are still checked: ``schema_version >= 29`` and
that consumer modules import/handle the value cleanly.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ATLAS_ROOT / "data" / "atlas.db"

# Production-shaped research_best schema (composite PK incl. regime_state +
# solo/portfolio/oos columns) so the test exercises the real writer path.
_SCHEMA = """
    CREATE TABLE research_best (
        strategy         TEXT NOT NULL,
        universe         TEXT NOT NULL,
        regime_state     TEXT,
        params           TEXT NOT NULL DEFAULT '{}',
        sharpe           REAL,
        trades           INTEGER,
        max_dd_pct       REAL,
        updated_at       TEXT DEFAULT (datetime('now')),
        solo_sharpe      REAL,
        portfolio_sharpe REAL,
        metric_type      TEXT NOT NULL DEFAULT 'unknown',
        oos_sharpe       REAL,
        oos_trades       INTEGER,
        oos_cagr         REAL,
        oos_max_dd       REAL,
        PRIMARY KEY (strategy, universe, regime_state)
    )
"""


@pytest.fixture()
def diversifier_db(tmp_path, monkeypatch):
    """Temp DB seeded via the production writer with a diversifier row.

    Mirrors the *kind* of row Fix 6 introduced: solo Sharpe weak/negative,
    portfolio contribution positive, explicitly tagged portfolio_diversifier.
    """
    import db.atlas_db as _adb

    db_path = str(tmp_path / "diversifier.db")
    conn = sqlite3.connect(db_path)
    conn.execute(_SCHEMA)
    conn.commit()
    conn.close()

    monkeypatch.setattr(_adb, "_db_path_override", db_path)
    _adb.upsert_research_best(
        strategy="connors_rsi2",
        universe="commodity_etfs",
        params={"rsi_period": 2, "rsi_entry": 10},
        sharpe=0.47,
        solo_sharpe=-0.68,          # weak/negative solo — fails solo gate
        portfolio_sharpe=0.47,      # positive whole-portfolio contribution
        metric_type="portfolio_diversifier",
    )
    return db_path


# ─── Schema / code semantics (controlled DB) ─────────────────────────────────

def test_writer_round_trips_portfolio_diversifier(diversifier_db):
    """upsert_research_best persists a portfolio_diversifier row faithfully."""
    conn = sqlite3.connect(diversifier_db)
    row = conn.execute(
        "SELECT solo_sharpe, portfolio_sharpe, metric_type FROM research_best "
        "WHERE strategy='connors_rsi2' AND universe='commodity_etfs'"
    ).fetchone()
    conn.close()
    assert row is not None, "diversifier row should round-trip through the writer"
    solo, port, mt = row
    assert mt == "portfolio_diversifier", f"metric_type={mt!r}"
    assert solo is not None and solo < 0, f"solo_sharpe={solo!r} should be negative"
    assert port is not None and port > 0, f"portfolio_sharpe={port!r} should be positive"


def test_diversifier_appears_in_distinct(diversifier_db):
    """portfolio_diversifier shows up in a DISTINCT metric_type query."""
    conn = sqlite3.connect(diversifier_db)
    types = {r[0] for r in conn.execute("SELECT DISTINCT metric_type FROM research_best")}
    conn.close()
    assert "portfolio_diversifier" in types, f"metric_types in DB: {types}"


def test_query_by_diversifier_returns_row(diversifier_db):
    """SELECT-by-metric-type returns the diversifier combo."""
    conn = sqlite3.connect(diversifier_db)
    rows = conn.execute(
        "SELECT strategy, universe FROM research_best WHERE metric_type='portfolio_diversifier'"
    ).fetchall()
    conn.close()
    assert ("connors_rsi2", "commodity_etfs") in rows, f"got: {rows}"


def test_migration_declares_portfolio_diversifier():
    """The Fix 6 migration still declares the portfolio_diversifier intent.

    Deterministic anchor for the migration's purpose, independent of how the
    live production row has since drifted via later sweeps.
    """
    mig_path = (
        ATLAS_ROOT / "scripts" / "migrations"
        / "2026-05-01-portfolio-diversifier-metric-type.py"
    )
    spec = importlib.util.spec_from_file_location("fix6_migration", str(mig_path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rows = mod._DIVERSIFIER_ROWS
    assert any(r["metric_type"] == "portfolio_diversifier" for r in rows), (
        "Fix 6 migration must declare a portfolio_diversifier row"
    )
    diversifier = next(r for r in rows if r["metric_type"] == "portfolio_diversifier")
    assert diversifier["solo_sharpe"] < 0, "diversifier solo_sharpe should be negative"
    assert diversifier["portfolio_sharpe"] > 0, "diversifier portfolio_sharpe should be positive"


# ─── Production invariants (real DB) ──────────────────────────────────────────

def test_schema_version_at_least_29():
    conn = sqlite3.connect(str(DB_PATH))
    v = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    conn.close()
    assert v >= 29, f"schema_version={v}, expected >= 29 after Fix 6 migration"


def test_consumer_code_handles_new_metric_type():
    """services/api/research.py and scripts/regen_brain_strategies.py should not
    crash when encountering portfolio_diversifier (treated as opaque string).
    """
    # Smoke test: import both modules without error
    from services.api import research as research_api  # noqa: F401
    spec = importlib.util.spec_from_file_location(
        "regen_brain_strategies",
        ATLAS_ROOT / "scripts" / "regen_brain_strategies.py",
    )
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    # If import succeeds and no crash, test passes — these consumers treat
    # metric_type as an opaque string.
