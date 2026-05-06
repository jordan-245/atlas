"""Tests for scripts/backfill_oos_metrics_research_best.py.

Sets up a minimal isolated DB with research_best + research_experiments,
runs the backfill, and asserts correct UPDATE behaviour.
All operations use in-memory or tmp_path SQLite — never touches prod atlas.db.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    """Create a minimal SQLite DB with research_best and research_experiments."""
    db_path = tmp_path / "test_atlas.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE research_best (
            strategy      TEXT NOT NULL,
            universe      TEXT NOT NULL,
            regime_state  TEXT,
            params        TEXT NOT NULL DEFAULT '{}',
            sharpe        REAL,
            trades        INTEGER,
            max_dd_pct    REAL,
            metric_type   TEXT NOT NULL DEFAULT 'unknown',
            solo_sharpe   REAL,
            portfolio_sharpe REAL,
            updated_at    TEXT,
            oos_sharpe    REAL,
            oos_trades    INTEGER,
            oos_cagr      REAL,
            oos_max_dd    REAL,
            PRIMARY KEY (strategy, universe, regime_state)
        )
    """)
    conn.execute("""
        CREATE TABLE research_experiments (
            id              TEXT PRIMARY KEY,
            strategy        TEXT,
            universe        TEXT DEFAULT 'sp500',
            experiment_type TEXT,
            description     TEXT,
            sharpe          REAL,
            trades          INTEGER,
            cagr_pct        REAL,
            max_dd_pct      REAL,
            status          TEXT DEFAULT 'running',
            created_at      TEXT DEFAULT (datetime('now')),
            completed_at    TEXT
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_best(db_path: Path, strategy: str, universe: str,
                 regime_state: object = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO research_best "
        "(strategy, universe, regime_state, params, sharpe) VALUES (?,?,?,?,?)",
        (strategy, universe, regime_state, '{}', 0.5),
    )
    conn.commit()
    conn.close()


def _insert_exp(db_path: Path, strategy: str, universe: str,
                sharpe: float = 0.6, trades: int = 55,
                cagr_pct: float = 9.0, max_dd_pct: float = 20.0,
                exp_type: str = "sweeper", status: str = "kept",
                exp_id: str = "exp-001") -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO research_experiments "
        "(id, strategy, universe, experiment_type, sharpe, trades, cagr_pct, max_dd_pct, status) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (exp_id, strategy, universe, exp_type, sharpe, trades, cagr_pct, max_dd_pct, status),
    )
    conn.commit()
    conn.close()


def _read_best(db_path: Path, strategy: str, universe: str,
               regime_state: object = None) -> dict | None:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    if regime_state is None:
        row = conn.execute(
            "SELECT * FROM research_best WHERE strategy=? AND universe=? "
            "AND regime_state IS NULL",
            (strategy, universe),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM research_best WHERE strategy=? AND universe=? AND regime_state=?",
            (strategy, universe, regime_state),
        ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_backfill_updates_matching_row(tmp_db: Path) -> None:
    """Backfill writes oos_sharpe/trades/cagr/max_dd when matching experiment exists."""
    _insert_best(tmp_db, "test_strat", "sp500")
    _insert_exp(tmp_db, "test_strat", "sp500",
                sharpe=0.65, trades=77, cagr_pct=11.5, max_dd_pct=25.0)

    from scripts.backfill_oos_metrics_research_best import run
    n_updated, n_already, n_skipped = run(dry_run=False, db_path=str(tmp_db))

    assert n_updated == 1
    assert n_skipped == 0

    row = _read_best(tmp_db, "test_strat", "sp500")
    assert row is not None
    assert abs(row["oos_sharpe"] - 0.65) < 1e-9
    assert row["oos_trades"] == 77
    assert abs(row["oos_cagr"] - 11.5) < 1e-9
    assert abs(row["oos_max_dd"] - 25.0) < 1e-9


def test_backfill_skips_when_no_experiment(tmp_db: Path) -> None:
    """Backfill leaves row unchanged when no experiment matches."""
    _insert_best(tmp_db, "no_exp_strat", "sp500")
    # No research_experiments row inserted

    from scripts.backfill_oos_metrics_research_best import run
    n_updated, n_already, n_skipped = run(dry_run=False, db_path=str(tmp_db))

    assert n_skipped == 1
    assert n_updated == 0

    row = _read_best(tmp_db, "no_exp_strat", "sp500")
    assert row is not None
    assert row["oos_sharpe"] is None


def test_backfill_idempotent(tmp_db: Path) -> None:
    """Running backfill twice: second run reports already-set, values unchanged."""
    _insert_best(tmp_db, "idem_strat", "sp500")
    _insert_exp(tmp_db, "idem_strat", "sp500",
                sharpe=0.5, trades=40, cagr_pct=6.0, max_dd_pct=18.0)

    from scripts.backfill_oos_metrics_research_best import run

    # First run
    n_updated, n_already, _ = run(dry_run=False, db_path=str(tmp_db))
    assert n_updated == 1
    assert n_already == 0

    # Second run — same data, no net change
    n_updated2, n_already2, _ = run(dry_run=False, db_path=str(tmp_db))
    assert n_updated2 == 0
    assert n_already2 == 1

    # Values unchanged
    row = _read_best(tmp_db, "idem_strat", "sp500")
    assert row is not None
    assert abs(row["oos_sharpe"] - 0.5) < 1e-9
    assert row["oos_trades"] == 40


def test_backfill_dry_run_does_not_write(tmp_db: Path) -> None:
    """Dry-run does not write to DB."""
    _insert_best(tmp_db, "dry_strat", "sp500")
    _insert_exp(tmp_db, "dry_strat", "sp500", sharpe=0.7, trades=60)

    from scripts.backfill_oos_metrics_research_best import run
    n_updated, _, _ = run(dry_run=True, db_path=str(tmp_db))

    # Reports would-be update
    assert n_updated == 1

    # But DB is unchanged
    row = _read_best(tmp_db, "dry_strat", "sp500")
    assert row is not None
    assert row["oos_sharpe"] is None


def test_backfill_prefers_oos_validation_experiment_type(tmp_db: Path) -> None:
    """Prefers oos_validation experiment over generic sweeper when both exist."""
    _insert_best(tmp_db, "oos_pref", "sp500")
    # Generic sweeper kept first (lower sharpe)
    _insert_exp(tmp_db, "oos_pref", "sp500",
                sharpe=0.3, trades=20, cagr_pct=3.0, max_dd_pct=30.0,
                exp_type="sweeper", exp_id="exp-sweep")
    # OOS validation kept (higher sharpe)
    _insert_exp(tmp_db, "oos_pref", "sp500",
                sharpe=0.8, trades=45, cagr_pct=12.0, max_dd_pct=15.0,
                exp_type="oos_validation", exp_id="exp-oos")

    from scripts.backfill_oos_metrics_research_best import run
    run(dry_run=False, db_path=str(tmp_db))

    row = _read_best(tmp_db, "oos_pref", "sp500")
    assert row is not None
    # Should pick the oos_validation row
    assert abs(row["oos_sharpe"] - 0.8) < 1e-9
    assert row["oos_trades"] == 45


def test_backfill_per_regime_row(tmp_db: Path) -> None:
    """Backfill correctly targets per-regime rows."""
    # Insert cross-regime and per-regime rows
    _insert_best(tmp_db, "regime_strat", "sp500", regime_state=None)
    _insert_best(tmp_db, "regime_strat", "sp500", regime_state="bull_risk_on")
    _insert_exp(tmp_db, "regime_strat", "sp500",
                sharpe=0.55, trades=38, cagr_pct=7.5, max_dd_pct=22.0)

    from scripts.backfill_oos_metrics_research_best import run
    n_updated, n_already, n_skipped = run(dry_run=False, db_path=str(tmp_db))

    assert n_updated == 2  # both rows updated
    assert n_skipped == 0

    cross = _read_best(tmp_db, "regime_strat", "sp500", regime_state=None)
    per_r = _read_best(tmp_db, "regime_strat", "sp500", regime_state="bull_risk_on")
    assert cross["oos_sharpe"] is not None
    assert per_r["oos_sharpe"] is not None
