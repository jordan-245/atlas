"""Phase 3 tests: strategy_lifecycle_history extensions + promotion-log backfill.

Covers:
  - Migration adds gate_results / experiment_id columns to strategy_lifecycle_history
    (already exercised on schema apply via init_db; this verifies presence).
  - transition() / set_lifecycle_state() persists gate_results JSON and experiment_id.
  - v_strategy_summary reads lifecycle_state from strategy_lifecycle_history.
  - backfill_lifecycle_history: empty case, happy path, idempotency, malformed entries.
  - auto_promote helper: _parse_gate_outcomes returns the right mapping.

Run:
    python3 -m pytest tests/test_lifecycle_phase3.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as atlas_db_module
from db.atlas_db import init_db
from db.lifecycle import set_lifecycle_state, get_lifecycle_state
from monitor.strategy_lifecycle import transition, PromotionState


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test_lifecycle_phase3.db"


@pytest.fixture(autouse=True)
def db(db_file, monkeypatch):
    monkeypatch.setattr(atlas_db_module, "DB_PATH", db_file)
    monkeypatch.setattr(atlas_db_module, "_db_path_override", None)
    init_db()
    yield db_file


def _history_rows(strategy=None, universe=None):
    with atlas_db_module.get_db() as conn:
        sql = "SELECT * FROM strategy_lifecycle_history WHERE 1=1"
        params = []
        if strategy:
            sql += " AND strategy = ?"; params.append(strategy)
        if universe:
            sql += " AND universe = ?"; params.append(universe)
        sql += " ORDER BY id"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchema:
    def test_columns_added(self):
        with atlas_db_module.get_db() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(strategy_lifecycle_history)"
            ).fetchall()]
        assert "gate_results" in cols
        assert "experiment_id" in cols

    def test_lifecycle_events_table_gone(self):
        """Phase 3 removed the redundant lifecycle_events table."""
        with atlas_db_module.get_db() as conn:
            row = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lifecycle_events'"
            ).fetchone()
        assert row is None, "lifecycle_events should be removed (Phase 3 consolidation)"


# ═══════════════════════════════════════════════════════════════════════════════
# set_lifecycle_state / transition pass-through
# ═══════════════════════════════════════════════════════════════════════════════

class TestWritePathPassThrough:
    def test_set_lifecycle_state_persists_gate_results(self):
        set_lifecycle_state(
            strategy="strat_a",
            universe="sp500",
            new_state="RESEARCH",
            reason="seed",
            operator="system",
            gate_results={"A": "pass", "B": "pass"},
            experiment_id="exp-001",
        )
        rows = _history_rows(strategy="strat_a")
        assert len(rows) == 1
        assert json.loads(rows[0]["gate_results"]) == {"A": "pass", "B": "pass"}
        assert rows[0]["experiment_id"] == "exp-001"

    def test_transition_forwards_kwargs(self):
        # Seed at RESEARCH via the canonical entry, then transition RESEARCH -> PAPER
        # with structured gate_results (allowed in the graph).
        set_lifecycle_state(strategy="strat_b", universe="sp500",
                            new_state="RESEARCH", operator="system")
        transition(
            strategy="strat_b",
            universe="sp500",
            new_state=PromotionState.PAPER,
            reason="auto promote candidate",
            auto_promotion_id="promo-xyz",
            operator="system",
            gate_results={"A": "pass", "B": "pass", "C": "fail"},
            experiment_id="exp-paper-1",
        )
        rows = _history_rows(strategy="strat_b")
        # Two rows: initial seed, then RESEARCH->PAPER
        assert len(rows) == 2
        last = rows[-1]
        assert last["from_state"] == "RESEARCH"
        assert last["to_state"] == "PAPER"
        assert json.loads(last["gate_results"]) == \
               {"A": "pass", "B": "pass", "C": "fail"}
        assert last["experiment_id"] == "exp-paper-1"
        assert last["auto_promotion_id"] == "promo-xyz"

    def test_kwargs_default_to_none(self):
        """Existing callers that don't pass gate_results/experiment_id still work."""
        set_lifecycle_state(strategy="strat_c", universe="sp500",
                            new_state="RESEARCH", operator="system")
        rows = _history_rows(strategy="strat_c")
        assert rows[-1]["gate_results"] is None
        assert rows[-1]["experiment_id"] is None


# ═══════════════════════════════════════════════════════════════════════════════
# v_strategy_summary now reads from strategy_lifecycle_history
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategySummaryView:
    def test_view_reflects_lifecycle_state(self):
        from db.research import upsert_research_best
        upsert_research_best(strategy="strat_d", universe="sp500",
                             params={}, solo_sharpe=0.4)
        set_lifecycle_state(strategy="strat_d", universe="sp500",
                            new_state="RESEARCH", operator="system")
        set_lifecycle_state(strategy="strat_d", universe="sp500",
                            new_state="PAPER", operator="system")

        with atlas_db_module.get_db() as conn:
            row = conn.execute(
                "SELECT lifecycle_state FROM v_strategy_summary "
                "WHERE strategy='strat_d' AND universe='sp500'"
            ).fetchone()
        assert row is not None
        assert row["lifecycle_state"] == "PAPER"


# ═══════════════════════════════════════════════════════════════════════════════
# backfill_lifecycle_history
# ═══════════════════════════════════════════════════════════════════════════════

class TestBackfill:
    def test_missing_file_returns_empty_summary(self, tmp_path):
        from scripts.backfill_lifecycle_history import run_backfill
        summary = run_backfill(tmp_path / "does_not_exist.json", apply=True)
        assert summary["log_present"] is False
        assert summary["entries"] == 0

    def test_happy_path_inserts_rows(self, tmp_path):
        from scripts.backfill_lifecycle_history import run_backfill
        log = tmp_path / "promotion_log.json"
        log.write_text(json.dumps([
            {
                "ts": "2026-04-01T10:00:00+00:00",
                "strategy": "strat_e",
                "universe": "sp500",
                "from_state": "PAPER",
                "to_state": "LIVE",
                "paper_sharpe": 0.7,
                "research_sharpe": 0.8,
                "gap": 0.13,
                "paper_trades": 45,
                "days_in_paper": 31,
                "auto_promotion_id": "promo-historical-1",
            },
            {
                "ts": "2026-04-10T11:00:00+00:00",
                "strategy": "strat_f",
                "universe": "sector_etfs",
                "from_state": "PAPER",
                "to_state": "RESEARCH",
                "gap": 0.9,
                "consecutive_breach_days": 7,
                "auto_promotion_id": "rollback-historical-1",
                "reason": "auto_rollback: gap > threshold for 7 days",
            },
        ]), encoding="utf-8")

        summary = run_backfill(log, apply=True)
        assert summary["entries"] == 2
        assert summary["inserted"] == 2
        assert summary["skipped_existing"] == 0
        assert summary["skipped_invalid"] == 0
        assert summary.get("errors", []) == []

        rows = _history_rows()
        assert len(rows) == 2
        by_strategy = {r["strategy"]: r for r in rows}

        # Original ts is preserved.
        assert by_strategy["strat_e"]["transitioned_at"] == "2026-04-01T10:00:00+00:00"
        assert by_strategy["strat_e"]["operator"] == "system"
        # gate_results carries the metric payload as JSON.
        gj = json.loads(by_strategy["strat_e"]["gate_results"])
        assert gj["paper_sharpe"] == 0.7
        assert gj["research_sharpe"] == 0.8

        # PAPER->RESEARCH inferred as a rollback.
        assert by_strategy["strat_f"]["operator"] == "rollback"

    def test_idempotent(self, tmp_path):
        from scripts.backfill_lifecycle_history import run_backfill
        log = tmp_path / "promotion_log.json"
        log.write_text(json.dumps([
            {
                "ts": "2026-04-01T10:00:00+00:00",
                "strategy": "strat_g",
                "universe": "sp500",
                "from_state": "PAPER",
                "to_state": "LIVE",
                "auto_promotion_id": "promo-g",
            },
        ]), encoding="utf-8")

        first = run_backfill(log, apply=True)
        second = run_backfill(log, apply=True)
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["skipped_existing"] == 1

        assert len(_history_rows(strategy="strat_g")) == 1

    def test_skips_invalid_entries(self, tmp_path):
        from scripts.backfill_lifecycle_history import run_backfill
        log = tmp_path / "promotion_log.json"
        log.write_text(json.dumps([
            "not a dict",
            {"strategy": "incomplete"},  # missing required fields
            {
                "ts": "2026-04-01T10:00:00+00:00",
                "strategy": "ok",
                "universe": "sp500",
                "to_state": "LIVE",
            },
        ]), encoding="utf-8")

        summary = run_backfill(log, apply=True)
        assert summary["inserted"] == 1
        assert summary["skipped_invalid"] == 2

    def test_natural_key_dedup_when_promo_id_missing(self, tmp_path):
        """If two backfill runs have entries without auto_promotion_id but the
        same (strategy, universe, ts, to_state), the second is a no-op."""
        from scripts.backfill_lifecycle_history import run_backfill
        log = tmp_path / "promotion_log.json"
        log.write_text(json.dumps([
            {
                "ts": "2026-04-01T10:00:00+00:00",
                "strategy": "strat_h",
                "universe": "sp500",
                "from_state": "RESEARCH",
                "to_state": "PAPER",
                # No auto_promotion_id -- triggers natural-key fallback.
            },
        ]), encoding="utf-8")

        first = run_backfill(log, apply=True)
        second = run_backfill(log, apply=True)
        assert first["inserted"] == 1
        assert second["inserted"] == 0
        assert second["skipped_existing"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# auto_promote _parse_gate_outcomes helper
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseGateOutcomes:
    def test_parses_pass_fail_bypass(self):
        from scripts.auto_promote_paper_to_live import _parse_gate_outcomes
        reasons = [
            "Gate A (PASS): days_in_paper=35.0 (need >=30)",
            "Gate B (FAIL): paper_trades=12 (need >=30)",
            "Gate C (PASS): paper_sharpe=0.5 (need >=0.3)",
            "Gate E (BYPASS): insufficient experiments for DSR (3 < 5) -- skipped",
        ]
        out = _parse_gate_outcomes(reasons)
        assert out == {"A": "pass", "B": "fail", "C": "pass", "E": "bypass"}

    def test_ignores_non_gate_lines(self):
        from scripts.auto_promote_paper_to_live import _parse_gate_outcomes
        assert _parse_gate_outcomes(["random log line", ""]) == {}

    def test_empty_input(self):
        from scripts.auto_promote_paper_to_live import _parse_gate_outcomes
        assert _parse_gate_outcomes([]) == {}
