"""Phase 6 tests: dual-write of queue.json / journal.json -> SQL mirrors.

Default behavior (env vars unset): JSON paths unchanged, no SQL writes.
Opt-in (ATLAS_KNOWLEDGE_DB_QUEUE=1, ATLAS_KNOWLEDGE_DB_JOURNAL=1):
  - Every append_to_queue / update_queue_entry / claim_experiment writes a
    queue_mirror row.
  - Every append_to_journal writes a journal_mirror row.
  - Mirror write failure is logged, does NOT propagate (JSON write succeeded).

Run:
    python3 -m pytest tests/test_phase6_dual_write.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


@pytest.fixture
def queue_in_tmp(tmp_path, monkeypatch):
    import research.models as rm
    monkeypatch.setattr(rm, "QUEUE_PATH", tmp_path / "queue.json")
    monkeypatch.setattr(rm, "JOURNAL_PATH", tmp_path / "journal.json")
    yield tmp_path


def _make_queue_entry(strategy="momentum_breakout", entry_id="q-1"):
    from research.models import QueueEntry, ExperimentType
    return QueueEntry(
        id=entry_id,
        title=f"test {strategy}",
        category="contradiction",
        market="sp500",
        hypothesis="seed",
        method=ExperimentType.SINGLE_STRATEGY_TEST,
        acceptance_criteria={"min_sharpe": 0.3, "min_trades": 15},
        estimated_runtime_min=10,
        priority="P3",
        strategy_name=strategy,
        tags=["channel:contradiction"],
    )


def _make_journal_entry(experiment_id="exp-1", strategy="momentum_breakout"):
    from research.models import JournalEntry
    return JournalEntry(
        experiment_id=experiment_id,
        timestamp="2026-05-28T12:00:00+00:00",
        market="sp500",
        category="contradiction",
        strategy=strategy,
        hypothesis="seed",
        verdict="pass",
        key_metrics={"sharpe": 0.8, "trades": 20},
        delta_vs_baseline={},
        learnings=["learned X"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Default off
# ═══════════════════════════════════════════════════════════════════════════════

class TestDefaultOff:
    def test_queue_mirror_disabled_by_default(self, queue_in_tmp, monkeypatch):
        # Make sure neither env var is set.
        monkeypatch.delenv("ATLAS_KNOWLEDGE_DB_QUEUE", raising=False)
        from research.models import append_to_queue, read_queue
        from db.knowledge import count_queue_mirror_rows
        append_to_queue(_make_queue_entry(), skip_validation=True)
        assert len(read_queue()) == 1
        assert count_queue_mirror_rows() == 0

    def test_journal_mirror_disabled_by_default(self, queue_in_tmp, monkeypatch):
        monkeypatch.delenv("ATLAS_KNOWLEDGE_DB_JOURNAL", raising=False)
        from research.models import append_to_journal, read_journal
        from db.knowledge import count_journal_mirror_rows
        append_to_journal(_make_journal_entry())
        assert len(read_journal()) == 1
        assert count_journal_mirror_rows() == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Dual-write on
# ═══════════════════════════════════════════════════════════════════════════════

class TestDualWriteOn:
    def test_append_to_queue_mirrors(self, queue_in_tmp, monkeypatch):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_QUEUE", "1")
        from research.models import append_to_queue, read_queue
        from db.knowledge import count_queue_mirror_rows, list_queue_mirror_rows

        append_to_queue(_make_queue_entry(entry_id="q-1"), skip_validation=True)
        append_to_queue(_make_queue_entry(entry_id="q-2",
                                          strategy="connors_rsi2"),
                        skip_validation=True)
        assert len(read_queue()) == 2
        assert count_queue_mirror_rows() == 2

        rows = list_queue_mirror_rows()
        by_id = {r["id"]: r for r in rows}
        assert by_id["q-1"]["strategy_name"] == "momentum_breakout"
        assert by_id["q-2"]["strategy_name"] == "connors_rsi2"
        assert by_id["q-1"]["category"] == "contradiction"
        # acceptance_criteria + tags survive the round-trip as parsed dicts/lists.
        assert by_id["q-1"]["acceptance_criteria"]["min_sharpe"] == 0.3
        assert "channel:contradiction" in by_id["q-1"]["tags"]
        # Canonical payload is recoverable.
        assert by_id["q-1"]["payload"]["id"] == "q-1"

    def test_update_queue_entry_overwrites_mirror(self, queue_in_tmp, monkeypatch):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_QUEUE", "1")
        from research.models import append_to_queue, update_queue_entry
        from db.knowledge import list_queue_mirror_rows

        append_to_queue(_make_queue_entry(entry_id="q-1"), skip_validation=True)
        update_queue_entry("q-1", {"status": "claimed", "claimed_by": "tester"})

        rows = list_queue_mirror_rows()
        assert len(rows) == 1
        assert rows[0]["status"] == "claimed"
        assert rows[0]["claimed_by"] == "tester"

    def test_claim_experiment_mirrors(self, queue_in_tmp, monkeypatch):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_QUEUE", "1")
        from research.models import append_to_queue, claim_experiment
        from db.knowledge import list_queue_mirror_rows

        append_to_queue(_make_queue_entry(entry_id="q-1"), skip_validation=True)
        claimed = claim_experiment("q-1", agent_id="atlas-1")
        assert claimed is not None
        assert claimed["claimed_by"] == "atlas-1"

        rows = list_queue_mirror_rows()
        assert rows[0]["status"] == "claimed"
        assert rows[0]["claimed_by"] == "atlas-1"

    def test_append_to_journal_mirrors(self, queue_in_tmp, monkeypatch):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_JOURNAL", "1")
        from research.models import append_to_journal
        from db.knowledge import count_journal_mirror_rows, list_journal_mirror_rows

        append_to_journal(_make_journal_entry(experiment_id="exp-a"))
        append_to_journal(_make_journal_entry(experiment_id="exp-b",
                                              strategy="connors_rsi2"))
        assert count_journal_mirror_rows() == 2

        rows = list_journal_mirror_rows()
        ids = {r["experiment_id"] for r in rows}
        assert ids == {"exp-a", "exp-b"}
        # Verdict + key_metrics round-trip.
        by_id = {r["experiment_id"]: r for r in rows}
        assert by_id["exp-a"]["verdict"] == "pass"
        assert by_id["exp-a"]["key_metrics"]["sharpe"] == 0.8

    def test_journal_idempotent_on_same_ts(self, queue_in_tmp, monkeypatch):
        """UNIQUE(experiment_id, timestamp) -- replaying the same entry is a no-op."""
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_JOURNAL", "1")
        from research.models import append_to_journal
        from db.knowledge import count_journal_mirror_rows

        entry = _make_journal_entry(experiment_id="exp-dup")
        append_to_journal(entry)
        append_to_journal(entry)  # same ts
        assert count_journal_mirror_rows() == 1


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive: mirror failure must not break the canonical JSON write
# ═══════════════════════════════════════════════════════════════════════════════

class TestMirrorFailureIsolated:
    def test_queue_mirror_exception_does_not_break_json_write(
        self, queue_in_tmp, monkeypatch,
    ):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_QUEUE", "1")
        from research.models import append_to_queue, read_queue

        with patch("db.knowledge.upsert_queue_mirror_row",
                   side_effect=RuntimeError("simulated DB failure")):
            # Must NOT raise.
            append_to_queue(_make_queue_entry(entry_id="q-1"),
                            skip_validation=True)

        # JSON write succeeded despite the mirror failure.
        assert len(read_queue()) == 1

    def test_journal_mirror_exception_does_not_break_json_write(
        self, queue_in_tmp, monkeypatch,
    ):
        monkeypatch.setenv("ATLAS_KNOWLEDGE_DB_JOURNAL", "1")
        from research.models import append_to_journal, read_journal

        with patch("db.knowledge.insert_journal_mirror_row",
                   side_effect=RuntimeError("simulated DB failure")):
            append_to_journal(_make_journal_entry(experiment_id="exp-fail"))

        assert len(read_journal()) == 1
