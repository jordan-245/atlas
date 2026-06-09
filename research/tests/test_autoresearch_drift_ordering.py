"""Focused tests for Tasks #389 / #390.

Covers three additions to ``research/autoresearch_runner.py``:

1. **Budget-aware sweep ordering** (#390) — ``build_sweep_plan`` prioritises the
   param that produced the current best, then recently-kept params, then
   unexplored params, then stale high-history params last.  Regression guard
   for #386: ``profit_target_atr_mult`` for momentum_breakout must NOT be last.
2. **Research-best vs live-active drift check** (#389) — read-only
   ``compare_research_best_vs_active``: detects drift, ignores non-param
   metadata, emits a gate warning + recommendation, never promotes.
3. **Solo-discard telemetry** (#390) — ``build_solo_discard_record`` /
   ``format_solo_discard_description`` / ``_persist_solo_discards`` capture the
   solo baseline + rejection rationale/deltas for post-mortems.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

import research.autoresearch_runner as ar
from research.autoresearch_runner import (
    build_sweep_plan,
    compare_research_best_vs_active,
    _priority_param_from_description,
    build_solo_discard_record,
    format_solo_discard_description,
    _persist_solo_discards,
)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _param_order(plan):
    """Collapse a sweep plan into the order params are first reached."""
    seen = []
    for _display, key, _val in plan:
        if key not in seen:
            seen.append(key)
    return seen


# ─── Task #390 — sweep ordering ──────────────────────────────────────────────


class TestPriorityParamParsing:
    def test_leading_colon_form(self):
        keys = ["profit_target_atr_mult", "atr_period", "atr_stop_mult"]
        desc = "profit_target_atr_mult: 0 -> 2.2 (manual override DSR: doubles Sharpe)"
        assert _priority_param_from_description(desc, keys) == "profit_target_atr_mult"

    def test_keep_equals_form(self):
        keys = ["rsi_period", "rsi_oversold"]
        desc = "autoresearch_runner keep: rsi_period=7"
        assert _priority_param_from_description(desc, keys) == "rsi_period"

    def test_no_param_returns_none(self):
        keys = ["rsi_period"]
        assert _priority_param_from_description("autoresearch keep", keys) is None
        assert _priority_param_from_description("", keys) is None
        assert _priority_param_from_description(None, keys) is None

    def test_substring_key_not_falsely_matched(self):
        # 'atr_period' must not match inside 'profit_target_atr_mult'.
        keys = ["atr_period"]
        desc = "profit_target_atr_mult: 0 -> 2.2"
        assert _priority_param_from_description(desc, keys) is None


class TestSweepOrdering:
    """build_sweep_plan budget-aware ordering with deterministic history."""

    @pytest.fixture
    def patched_history(self, monkeypatch):
        # Never skip a candidate value on brain history for these tests.
        monkeypatch.setattr(ar, "check_brain_history", lambda *a, **k: None)

        # Deterministic tiers:
        #   a_stale   — lots of history, never kept   → stale (last)
        #   b_recent  — kept recently                 → recently-kept
        #   c_new     — no history                    → unexplored
        #   d_priority— no history, names the best    → priority (first)
        keeps = {"b_recent": datetime(2026, 5, 1, 12, 0)}
        counts = {"a_stale": 50, "b_recent": 5, "c_new": 0, "d_priority": 0}
        monkeypatch.setattr(
            ar, "_last_keep_timestamp",
            lambda strat, k: keeps.get(k),
        )
        monkeypatch.setattr(
            ar, "_brain_history_count",
            lambda strat, k: counts.get(k, 0),
        )

    def _current_best(self):
        # dict insertion order is stale, recent, new, priority
        return {"a_stale": 100, "b_recent": 10, "c_new": 5, "d_priority": 2.0}

    def test_priority_param_first_and_stale_last(self, patched_history):
        best_record = {"description": "d_priority: 0 -> 2.0 (manual override)"}
        plan = build_sweep_plan(
            "demo", "sp500", self._current_best(), best_record=best_record,
        )
        order = _param_order(plan)
        assert order == ["d_priority", "b_recent", "c_new", "a_stale"], order
        assert order[0] == "d_priority"
        assert order[-1] == "a_stale"

    def test_recently_kept_and_new_before_stale_without_record(self, patched_history):
        plan = build_sweep_plan("demo", "sp500", self._current_best())
        order = _param_order(plan)
        # recently-kept first, then unexplored, then stale last
        assert order[0] == "b_recent", order
        assert order[-1] == "a_stale", order
        assert order.index("c_new") < order.index("a_stale")
        assert order.index("d_priority") < order.index("a_stale")

    def test_momentum_profit_target_not_last_regression(self):
        """#386 regression: profit_target_atr_mult must not end up last."""
        best_path = ATLAS_ROOT / "research" / "best" / "momentum_breakout.json"
        if not best_path.exists():
            pytest.skip("research/best/momentum_breakout.json not present")
        best = json.loads(best_path.read_text())
        plan = build_sweep_plan(
            "momentum_breakout", "sp500", best["params"], best_record=best,
        )
        order = _param_order(plan)
        assert order, "empty sweep plan"
        # With the best-record hint, the param that produced the best is first.
        assert order[0] == "profit_target_atr_mult", order
        assert order[-1] != "profit_target_atr_mult", order

    def test_ordering_preserves_candidate_values(self, patched_history):
        """Reordering params must not drop or duplicate candidate values."""
        cb = self._current_best()
        plan_a = build_sweep_plan("demo", "sp500", cb)
        plan_b = build_sweep_plan(
            "demo", "sp500", cb,
            best_record={"description": "d_priority: 0 -> 2.0"},
        )
        # Same multiset of (key, value) regardless of ordering.
        assert sorted((k, str(v)) for _d, k, v in plan_a) == \
            sorted((k, str(v)) for _d, k, v in plan_b)


# ─── Task #389 — drift check ─────────────────────────────────────────────────


class TestDriftCheck:
    def _best(self, **params):
        base = {
            "atr_stop_mult": 0.81,
            "lookback_days": 22,
            "profit_target_atr_mult": 2.2,
        }
        base.update(params)
        return {"strategy": "momentum_breakout", "params": base}

    def _active(self, **params):
        strat = {
            "enabled": True,
            "weight": 0.25,
            "atr_stop_mult": 0.61,
            "lookback_days": 14,
            "profit_target_atr_mult": 6.0,
            "earnings_blackout": {"enabled": True, "days_before": 5},
        }
        strat.update(params)
        return {"market": "sp500", "strategies": {"momentum_breakout": strat}}

    def test_drift_detected(self):
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config=self._active(), best_record=self._best(),
        )
        assert out["drift_detected"] is True
        drifted = {d["param"] for d in out["drifted_params"]}
        assert drifted == {"atr_stop_mult", "lookback_days", "profit_target_atr_mult"}
        assert out["gate_warning"] is not None
        assert "#389" in out["recommendation"]
        # Read-only contract: explicit no-promote language in the recommendation.
        assert "auto-promote" in out["recommendation"].lower()

    def test_metadata_keys_ignored(self):
        # research-best record that *also* carries metadata keys
        best = self._best(enabled=True, weight=0.25,
                          earnings_blackout={"enabled": True})
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config=self._active(), best_record=best,
        )
        for meta in ("enabled", "weight", "earnings_blackout"):
            assert meta in out["ignored_keys"], meta
            assert meta not in out["compared_keys"]
            assert all(d["param"] != meta for d in out["drifted_params"])

    def test_aligned_no_drift(self):
        best = self._best(atr_stop_mult=0.61, lookback_days=14,
                          profit_target_atr_mult=6.0)
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config=self._active(), best_record=best,
        )
        assert out["drift_detected"] is False
        assert out["gate_warning"] is None
        assert "ALIGNED" in out["recommendation"]

    def test_missing_in_active_flagged(self):
        best = self._best(some_new_param=1.5)
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config=self._active(), best_record=best,
        )
        rec = next(d for d in out["drifted_params"] if d["param"] == "some_new_param")
        assert rec["reason"] == "missing_in_active"
        assert rec["active"] is None

    def test_unavailable_when_missing_inputs(self):
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config={"strategies": {}}, best_record={"params": {}},
        )
        assert out["drift_detected"] is False
        assert out["research_best_available"] is False
        assert out["active_available"] is False
        assert "unavailable" in out["recommendation"].lower()

    def test_pct_change_reported(self):
        out = compare_research_best_vs_active(
            "momentum_breakout", "sp500",
            active_config=self._active(), best_record=self._best(),
        )
        atr = next(d for d in out["drifted_params"] if d["param"] == "atr_stop_mult")
        # 0.81 -> 0.61 ≈ -24.69%
        assert atr["pct_change"] is not None and atr["pct_change"] < 0


# ─── Task #390 — solo-discard telemetry ──────────────────────────────────────


class TestSoloDiscardTelemetry:
    def _verdict(self):
        return {
            "decision": "discard",
            "delta_sharpe": -0.74,
            "delta_trades": -262,
            "delta_dd": 3.1,
            "rationale": "DISCARD: Sharpe +-0.7400 below threshold +0.010",
        }

    def test_build_record_fields(self):
        rec = build_solo_discard_record(
            "profit_target_atr_mult: 2.2 -> 1.1",
            "profit_target_atr_mult", 1.1,
            {"sharpe": 0.2827, "total_trades": 120, "runtime_s": 22.0},
            {"sharpe": 1.0245, "total_trades": 382},
            self._verdict(),
        )
        assert rec["param"] == "profit_target_atr_mult"
        assert rec["candidate_value"] == 1.1
        assert rec["baseline_sharpe"] == 1.0245
        assert rec["solo_sharpe"] == 0.2827
        assert rec["delta_sharpe"] == -0.74
        assert rec["delta_trades"] == -262
        assert rec["baseline_trades"] == 382
        assert rec["solo_trades"] == 120
        assert "DISCARD" in rec["rationale"]

    def test_record_handles_missing_metrics(self):
        rec = build_solo_discard_record(
            "x: 1 -> 2", "x", 2, {}, None,
            {"delta_sharpe": -0.1, "delta_trades": -5, "delta_dd": 0.0,
             "rationale": "r"},
        )
        assert rec["baseline_sharpe"] == 0.0
        assert rec["solo_sharpe"] == 0.0

    def test_format_description_has_deltas_and_rationale(self):
        rec = build_solo_discard_record(
            "profit_target_atr_mult: 2.2 -> 1.1",
            "profit_target_atr_mult", 1.1,
            {"sharpe": 0.2827, "total_trades": 120, "runtime_s": 22.0},
            {"sharpe": 1.0245, "total_trades": 382},
            self._verdict(),
        )
        desc = format_solo_discard_description(
            "profit_target_atr_mult: 2.2 -> 1.1", rec,
        )
        assert desc.startswith("[solo screen]")
        assert "baseline_sharpe=1.0245" in desc
        assert "solo_sharpe=0.2827" in desc
        assert "Δsharpe=-0.7400" in desc
        assert "Δtrades=-262" in desc
        assert "Δdd=+3.10" in desc
        assert "DISCARD" in desc
        # Must remain single-line/tab-free for clean TSV append.
        assert "\t" not in desc and "\n" not in desc

    def test_format_description_tolerates_none_deltas(self):
        rec = build_solo_discard_record(
            "x: 1 -> 2", "x", 2, {"sharpe": 0.1}, {"sharpe": 0.2},
            {"rationale": "no deltas"},
        )
        desc = format_solo_discard_description("x: 1 -> 2", rec)
        assert "Δsharpe=+0.0000" in desc
        assert "Δtrades=+0" in desc

    def test_persist_writes_json(self, tmp_path):
        discards = [
            build_solo_discard_record(
                "x: 1 -> 2", "x", 2,
                {"sharpe": 0.1, "total_trades": 50},
                {"sharpe": 1.0, "total_trades": 380},
                self._verdict(),
            )
        ]
        out = tmp_path / "solo_discards_demo_sp500.json"
        result = _persist_solo_discards(
            "demo", "sp500",
            {"sharpe": 1.0, "total_trades": 380, "max_drawdown_pct": 18.8},
            discards, out_path=out,
        )
        assert result == out
        assert out.exists()
        payload = json.loads(out.read_text())
        assert payload["count"] == 1
        assert payload["strategy"] == "demo"
        assert payload["solo_baseline"]["sharpe"] == 1.0
        assert payload["discards"][0]["param"] == "x"

    def test_persist_empty_returns_none(self, tmp_path):
        out = tmp_path / "none.json"
        result = _persist_solo_discards("demo", "sp500", {}, [], out_path=out)
        assert result is None
        assert not out.exists()
