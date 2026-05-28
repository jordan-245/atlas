"""Tests for the Phase 7 wiki materializer.

Run:
    python3 -m pytest tests/test_wiki_materializer.py -v
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))


def _seed(strategy="strat_a", universe="sp500",
          claimed_sharpe=1.6, measured_sharpe=0.4,
          source_title="Test Paper",
          source_url="https://arxiv.org/abs/2401.99999"):
    from db import knowledge as kn
    from db.research import upsert_research_best
    sid = f"src-{strategy}"
    cid = f"clm-{strategy}-0"
    kn.insert_source(id=sid, kind="paper", title=source_title, url=source_url)
    kn.insert_claim(id=cid, source_id=sid, strategy=strategy, universe=universe)
    kn.update_claim_metrics(id=cid, claimed_sharpe=claimed_sharpe,
                            extraction_confidence="high")
    upsert_research_best(strategy=strategy, universe=universe, params={},
                        solo_sharpe=measured_sharpe)
    return sid, cid


# ═══════════════════════════════════════════════════════════════════════════════
# Empty state
# ═══════════════════════════════════════════════════════════════════════════════

class TestEmptyState:
    def test_no_strategies_produces_overview_only(self, tmp_path):
        from research.wiki.materializer import materialize
        result = materialize(out_dir=tmp_path, write=True)
        assert result.strategies_rendered == 0
        assert result.contradictions_emitted == 0

        # overview.md and contradictions.jsonl always written
        assert (tmp_path / "overview.md").exists()
        assert (tmp_path / "contradictions.jsonl").exists()
        # Empty contradictions file is permissible (zero bytes or just a newline).
        assert (tmp_path / "contradictions.jsonl").read_text() == ""

        # No per-strategy files
        strat_dir = tmp_path / "strategies"
        assert not strat_dir.exists() or list(strat_dir.glob("*.md")) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Single-strategy happy path
# ═══════════════════════════════════════════════════════════════════════════════

class TestSingleStrategy:
    def test_renders_strategy_page(self, tmp_path):
        from research.wiki.materializer import materialize
        _seed(strategy="donchian_breakout", universe="sp500",
              claimed_sharpe=1.6, measured_sharpe=0.4,
              source_title="Turtle Trading Rules")

        result = materialize(out_dir=tmp_path, write=True)
        assert result.strategies_rendered == 1
        assert result.contradictions_emitted >= 1

        md_path = tmp_path / "strategies" / "donchian_breakout__sp500.md"
        assert md_path.exists()
        content = md_path.read_text(encoding="utf-8")

        # Headline + sections present.
        assert "donchian_breakout" in content
        assert "sp500" in content
        assert "Measured" in content
        assert "Open Contradictions" in content
        assert "Recent Lifecycle Transitions" in content
        # The contradiction row appears.
        assert "Turtle Trading Rules" in content
        # Measured value rendered.
        assert "0.400" in content

    def test_contradictions_jsonl_contains_critical(self, tmp_path):
        from research.wiki.materializer import materialize
        _seed(strategy="strat_x", claimed_sharpe=1.6, measured_sharpe=0.4)

        materialize(out_dir=tmp_path, write=True)
        jsonl = (tmp_path / "contradictions.jsonl").read_text()
        assert jsonl.strip() != ""

        rows = [json.loads(line) for line in jsonl.strip().splitlines()]
        assert any(r["severity"] == "critical" for r in rows)
        assert any(r["strategy"] == "strat_x" for r in rows)


# ═══════════════════════════════════════════════════════════════════════════════
# Multi-strategy + overview
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiStrategy:
    def test_overview_counts(self, tmp_path):
        from research.wiki.materializer import materialize
        from db.lifecycle import set_lifecycle_state
        _seed(strategy="strat_a", claimed_sharpe=1.6, measured_sharpe=0.4)
        _seed(strategy="strat_b", claimed_sharpe=1.6, measured_sharpe=0.4)
        # Give strat_a a lifecycle state.
        set_lifecycle_state(strategy="strat_a", universe="sp500",
                            new_state="RESEARCH", operator="seed")

        materialize(out_dir=tmp_path, write=True)
        overview = (tmp_path / "overview.md").read_text(encoding="utf-8")
        assert "Strategies tracked: **2**" in overview
        assert "Total open contradictions: **2**" in overview
        # The "By Lifecycle State" table lists at least RESEARCH | 1 and UNKNOWN | 1.
        assert "RESEARCH" in overview


# ═══════════════════════════════════════════════════════════════════════════════
# Idempotency: re-runs produce identical files
# ═══════════════════════════════════════════════════════════════════════════════

class TestIdempotency:
    def test_rerun_produces_identical_output(self, tmp_path):
        from research.wiki.materializer import materialize
        _seed(strategy="strat_idem", claimed_sharpe=1.6, measured_sharpe=0.4)

        def _snapshot() -> dict:
            out = {}
            for p in tmp_path.rglob("*"):
                if p.is_file() and p.suffix in (".md", ".jsonl"):
                    out[p.relative_to(tmp_path).as_posix()] = (
                        p.read_text(encoding="utf-8")
                    )
            return out

        materialize(out_dir=tmp_path, write=True)
        first = _snapshot()

        # last_measured_at on the research_best row is set on upsert; the
        # materializer just reads it back -- so consecutive runs without a
        # re-upsert produce byte-identical output.
        materialize(out_dir=tmp_path, write=True)
        second = _snapshot()

        assert set(first) == set(second)
        for k in first:
            assert first[k] == second[k], f"{k} drifted across re-run"


# ═══════════════════════════════════════════════════════════════════════════════
# Pure renderer unit tests (no DB)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRenderers:
    def test_render_strategy_md_empty_contradictions(self):
        from research.wiki.materializer import render_strategy_md
        md = render_strategy_md(
            summary={"strategy": "x", "universe": "u",
                     "solo_sharpe": 0.5, "max_dd_pct": 12.0,
                     "trades": 50, "lifecycle_state": "RESEARCH",
                     "open_contradictions": 0, "active_claims": 0},
            contradictions=[],
            lifecycle=[],
            journal=[],
        )
        assert "_None._" in md
        assert "x @ u" in md

    def test_render_contradictions_jsonl_stable_order(self):
        from research.wiki.materializer import render_contradictions_jsonl
        # Same data, different orders -> identical output.
        rows1 = [
            {"contradiction_id": 2, "claim_id": "b", "severity": "major",
             "delta_abs": 0.6, "strategy": "s", "universe": "u",
             "metric": "sharpe", "claimed_value": 1.0, "measured_value": 0.4,
             "delta": -0.6, "first_seen_at": "t1", "source_id": "s1",
             "source_title": "T", "source_url": None},
            {"contradiction_id": 1, "claim_id": "a", "severity": "critical",
             "delta_abs": 1.5, "strategy": "s", "universe": "u",
             "metric": "sharpe", "claimed_value": 1.8, "measured_value": 0.3,
             "delta": -1.5, "first_seen_at": "t0", "source_id": "s0",
             "source_title": "T0", "source_url": None},
        ]
        rows2 = list(reversed(rows1))
        assert render_contradictions_jsonl(rows1) == render_contradictions_jsonl(rows2)
        # Critical comes first.
        first_line = render_contradictions_jsonl(rows1).splitlines()[0]
        assert json.loads(first_line)["severity"] == "critical"
