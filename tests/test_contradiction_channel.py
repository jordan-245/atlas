"""Tests for the Phase 5 contradiction-driven ideation channel.

Run:
    python3 -m pytest tests/test_contradiction_channel.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def _seed(strategy="strat_x", universe="sp500",
          claimed_sharpe=1.6, measured_sharpe=0.4,
          source_title="Test Paper", source_url="https://arxiv.org/abs/2401.99999"):
    """Seed source + claim + research_best so an open contradiction exists."""
    from db import knowledge as kn
    from db.research import upsert_research_best
    src_id = f"src-{strategy}"
    claim_id = f"clm-{strategy}-0"
    kn.insert_source(id=src_id, kind="paper", title=source_title, url=source_url)
    kn.insert_claim(id=claim_id, source_id=src_id, strategy=strategy,
                    universe=universe)
    kn.update_claim_metrics(id=claim_id, claimed_sharpe=claimed_sharpe,
                            extraction_confidence="high")
    upsert_research_best(strategy=strategy, universe=universe, params={},
                        solo_sharpe=measured_sharpe)
    return src_id, claim_id


@pytest.fixture
def queue_in_tmp(tmp_path, monkeypatch):
    """Redirect queue.json to a per-test tmp file so append_to_queue works."""
    import research.models as rm
    qpath = tmp_path / "queue.json"
    monkeypatch.setattr(rm, "QUEUE_PATH", qpath)
    yield qpath


# ═══════════════════════════════════════════════════════════════════════════════
# generate_candidates: severity filter, dry-run safety
# ═══════════════════════════════════════════════════════════════════════════════

class TestGenerateCandidates:
    def test_empty_when_no_contradictions(self):
        from research.discovery.contradiction_channel import generate_candidates
        assert generate_candidates() == []

    def test_only_major_critical_by_default(self):
        from research.discovery.contradiction_channel import generate_candidates
        # critical: |1.6-0.4|=1.2
        _seed(strategy="strat_crit", claimed_sharpe=1.6, measured_sharpe=0.4)
        # minor: |1.0-0.65|=0.35 (just above 0.3 minor floor; not eligible)
        _seed(strategy="strat_minor", claimed_sharpe=1.0, measured_sharpe=0.65)

        cands = generate_candidates()
        names = {c.queue_entry.strategy_name for c in cands}
        assert "strat_crit" in names
        assert "strat_minor" not in names

    def test_minor_included_when_requested(self):
        from research.discovery.contradiction_channel import generate_candidates
        _seed(strategy="strat_minor", claimed_sharpe=1.0, measured_sharpe=0.65)
        cands = generate_candidates(severities=("critical", "major", "minor"))
        assert any(c.queue_entry.strategy_name == "strat_minor" for c in cands)

    def test_queue_entry_shape(self):
        from research.discovery.contradiction_channel import generate_candidates
        _seed(strategy="donchian_breakout", claimed_sharpe=1.6,
              measured_sharpe=0.4, source_title="Turtle Trading Rules",
              source_url="https://arxiv.org/abs/2401.11111")
        cands = generate_candidates()
        assert len(cands) >= 1
        c = cands[0]
        qe = c.queue_entry

        # Core fields
        assert qe.strategy_name == "donchian_breakout"
        assert qe.market == "sp500"
        assert qe.category == "contradiction"
        assert qe.priority == "P3"
        assert qe.method.value == "single_strategy_test"
        # Title mentions severity + metric
        assert "critical" in qe.title or "major" in qe.title
        assert "sharpe" in qe.title or "max_dd_pct" in qe.title
        # Hypothesis carries source + numbers
        assert "Turtle Trading Rules" in qe.hypothesis
        assert "donchian_breakout" in qe.hypothesis
        # Tags carry source + claim back-refs
        tag_set = set(qe.tags)
        assert f"source:{c.source_id}" in tag_set
        assert f"claim:{c.claim_id}" in tag_set
        assert f"contradiction:{c.contradiction_id}" in tag_set
        assert "channel:contradiction" in tag_set
        # Acceptance criteria uses paper's claim as the bar
        assert "min_sharpe" in qe.acceptance_criteria
        assert qe.acceptance_criteria["min_sharpe"] > 0

    def test_limit_caps_output(self):
        from research.discovery.contradiction_channel import generate_candidates
        for i in range(5):
            _seed(strategy=f"strat_lim_{i}", claimed_sharpe=1.6,
                  measured_sharpe=0.4)
        cands = generate_candidates(limit=2)
        assert len(cands) <= 2


# ═══════════════════════════════════════════════════════════════════════════════
# Decay rule: skip strategies tested in the last N days
# ═══════════════════════════════════════════════════════════════════════════════

class TestDecay:
    def _record_recent_experiment(self, strategy: str, universe: str):
        """Insert a row in research_experiments dated today."""
        from db.research import record_experiment
        from research.models import generate_experiment_id
        record_experiment(
            id=generate_experiment_id(),
            strategy=strategy,
            universe=universe,
            experiment_type="single_strategy_test",
            status="running",
        )

    def test_recently_tested_strategy_skipped(self):
        from research.discovery.contradiction_channel import generate_candidates
        _seed(strategy="strat_recent", claimed_sharpe=1.6, measured_sharpe=0.4)
        self._record_recent_experiment("strat_recent", "sp500")

        cands = generate_candidates()
        assert not any(c.queue_entry.strategy_name == "strat_recent"
                       for c in cands)

    def test_decay_does_not_affect_other_strategies(self):
        from research.discovery.contradiction_channel import generate_candidates
        _seed(strategy="strat_blocked", claimed_sharpe=1.6, measured_sharpe=0.4)
        _seed(strategy="strat_clear", claimed_sharpe=1.6, measured_sharpe=0.4)
        self._record_recent_experiment("strat_blocked", "sp500")

        names = {c.queue_entry.strategy_name for c in generate_candidates()}
        assert "strat_blocked" not in names
        assert "strat_clear" in names

    def test_decay_window_zero_disables_decay(self):
        """decay_days=0 means cutoff is now -- only experiments from the future
        block, which can't exist.  Recently-tested strategies should appear."""
        from research.discovery.contradiction_channel import generate_candidates
        _seed(strategy="strat_x", claimed_sharpe=1.6, measured_sharpe=0.4)
        self._record_recent_experiment("strat_x", "sp500")
        # With decay_days=0 the cutoff is "now", so an experiment created at
        # the same moment is on the boundary; safer to test "very small window".
        # We pass decay_days=-1 to make cutoff future-dated.
        cands = generate_candidates(decay_days=-1)
        assert any(c.queue_entry.strategy_name == "strat_x" for c in cands)


# ═══════════════════════════════════════════════════════════════════════════════
# queue_candidates: idempotency + error isolation
# ═══════════════════════════════════════════════════════════════════════════════

class TestQueueCandidates:
    def test_queues_candidates(self, queue_in_tmp):
        from research.discovery.contradiction_channel import (
            generate_candidates, queue_candidates,
        )
        from research.models import read_queue
        # Use a real strategy name -- validate_queue_entry checks against
        # STRATEGY_REGISTRY.
        _seed(strategy="momentum_breakout", claimed_sharpe=1.6, measured_sharpe=0.4)

        result = queue_candidates(generate_candidates())
        assert result["queued"] >= 1
        assert result["errors"] == []

        queue = read_queue()
        assert any(e["category"] == "contradiction" for e in queue)

    def test_run_channel_dry_run_does_not_write(self, queue_in_tmp):
        from research.discovery.contradiction_channel import run_channel
        from research.models import read_queue
        _seed(strategy="strat_dry", claimed_sharpe=1.6, measured_sharpe=0.4)

        result = run_channel(apply=False)
        assert result["mode"] == "dry-run"
        assert result["candidates"] >= 1
        assert read_queue() == []

    def test_run_channel_apply_writes(self, queue_in_tmp):
        from research.discovery.contradiction_channel import run_channel
        from research.models import read_queue
        _seed(strategy="connors_rsi2", claimed_sharpe=1.6, measured_sharpe=0.4)

        result = run_channel(apply=True)
        assert result["mode"] == "apply"
        assert result["queued"] >= 1
        queue = read_queue()
        assert any(e["strategy_name"] == "connors_rsi2" for e in queue)
