"""Tests for OOS columns in research_best table and upsert_research_best().

Covers:
  - Persisting OOS fields through upsert_research_best
  - OOS fields being optional (NULL when not provided)
  - Cross-regime (NULL regime_state) and per-regime paths both get OOS cols
  - get_research_best returns the new columns

All DB operations use the global _isolate_prod_db autouse fixture from conftest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ── Helper ────────────────────────────────────────────────────────────────────

def _upsert(
    strategy: str = "test_strat",
    universe: str = "sp500",
    regime_state: str | None = None,
    **kwargs,
) -> None:
    from db.atlas_db import upsert_research_best
    upsert_research_best(
        strategy=strategy,
        universe=universe,
        params={"window": 10},
        sharpe=0.6,
        trades=50,
        regime_state=regime_state,
        **kwargs,
    )


def _fetch(
    strategy: str = "test_strat",
    universe: str = "sp500",
    regime_state: str | None = None,
) -> dict | None:
    from db.atlas_db import get_research_best
    rows = get_research_best(strategy=strategy, universe=universe,
                              regime_state=regime_state)
    return rows[0] if rows else None


# ── Test: OOS fields persisted (cross-regime path) ───────────────────────────

def test_upsert_research_best_persists_oos_fields_cross_regime():
    """Write row with all OOS fields via cross-regime (NULL regime_state) path; read back."""
    _upsert(
        oos_sharpe=0.5,
        oos_trades=42,
        oos_cagr=8.3,
        oos_max_dd=15.0,
    )
    row = _fetch()
    assert row is not None
    assert abs(row["oos_sharpe"] - 0.5) < 1e-9
    assert row["oos_trades"] == 42
    assert abs(row["oos_cagr"] - 8.3) < 1e-9
    assert abs(row["oos_max_dd"] - 15.0) < 1e-9


def test_upsert_research_best_persists_oos_fields_per_regime():
    """Write row with all OOS fields via per-regime (non-NULL regime_state) path; read back."""
    _upsert(
        regime_state="bull_risk_on",
        oos_sharpe=0.72,
        oos_trades=88,
        oos_cagr=12.5,
        oos_max_dd=22.1,
    )
    row = _fetch(regime_state="bull_risk_on")
    assert row is not None
    assert abs(row["oos_sharpe"] - 0.72) < 1e-9
    assert row["oos_trades"] == 88
    assert abs(row["oos_cagr"] - 12.5) < 1e-9
    assert abs(row["oos_max_dd"] - 22.1) < 1e-9


def test_upsert_research_best_oos_fields_optional():
    """Write row WITHOUT oos_* kwargs; all four OOS columns should be NULL."""
    _upsert()  # no oos_* kwargs
    row = _fetch()
    assert row is not None
    assert row["oos_sharpe"] is None
    assert row["oos_trades"] is None
    assert row["oos_cagr"] is None
    assert row["oos_max_dd"] is None


def test_upsert_research_best_oos_fields_overwrite():
    """Write then overwrite OOS fields; second write wins."""
    _upsert(oos_sharpe=0.4, oos_trades=30)
    _upsert(oos_sharpe=0.9, oos_trades=100)
    row = _fetch()
    assert row is not None
    assert abs(row["oos_sharpe"] - 0.9) < 1e-9
    assert row["oos_trades"] == 100


def test_upsert_research_best_oos_can_be_set_to_none():
    """Write OOS fields, then overwrite with None; they should become NULL."""
    _upsert(oos_sharpe=0.6, oos_trades=55)
    # Re-upsert without passing oos_* → NULL (None default)
    _upsert()
    row = _fetch()
    assert row is not None
    assert row["oos_sharpe"] is None
    assert row["oos_trades"] is None


def test_get_research_best_returns_oos_columns():
    """get_research_best() result dict contains all four OOS keys."""
    _upsert(oos_sharpe=0.45, oos_trades=60, oos_cagr=7.2, oos_max_dd=18.0)
    row = _fetch()
    assert row is not None
    for key in ("oos_sharpe", "oos_trades", "oos_cagr", "oos_max_dd"):
        assert key in row, f"Key {key!r} missing from get_research_best() result"


def test_cross_regime_and_per_regime_oos_fields_independent():
    """Cross-regime and per-regime rows have independent OOS columns."""
    _upsert(regime_state=None, oos_sharpe=0.3, oos_trades=35)
    _upsert(regime_state="transition_uncertain", oos_sharpe=0.7, oos_trades=99)

    cross = _fetch(regime_state=None)
    per_r = _fetch(regime_state="transition_uncertain")

    assert cross is not None
    assert per_r is not None
    assert abs(cross["oos_sharpe"] - 0.3) < 1e-9
    assert abs(per_r["oos_sharpe"] - 0.7) < 1e-9
    assert cross["oos_trades"] == 35
    assert per_r["oos_trades"] == 99
