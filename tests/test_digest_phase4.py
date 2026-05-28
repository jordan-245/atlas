"""Phase 4 digest tests.

Unit tests for the knowledge-layer hooks added to discovery's daily digest:
  - _enrich_report_with_knowledge_counts populates the new fields correctly
  - _format_top_contradictions renders the right block
  - log_digest persists one row per send and stamps delivery_status

No real Telegram or LLM calls -- _send_telegram_digest's alerting import is
monkeypatched.

Run:
    python3 -m pytest tests/test_digest_phase4.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from research.discovery import discovery as disc


def _seed_contradiction(strategy="strat_a", claimed=1.4, measured=0.4):
    from db import knowledge as kn
    from db.research import upsert_research_best
    sid = f"src-{strategy}"
    cid = f"clm-{strategy}-0"
    kn.insert_source(id=sid, kind="paper", title=f"Paper {strategy}")
    kn.insert_claim(id=cid, source_id=sid, strategy=strategy, universe="sp500")
    kn.update_claim_metrics(id=cid, claimed_sharpe=claimed,
                            extraction_confidence="high")
    upsert_research_best(strategy=strategy, universe="sp500", params={},
                        solo_sharpe=measured)
    return sid, cid


def _new_report(date="2026-05-28"):
    return disc.DailyReport(
        date=date, source="arxiv", method="api",
        papers_found=10, papers_filtered=4, specs_extracted=3,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# _enrich_report_with_knowledge_counts
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnrichReport:
    def test_zero_when_no_data(self):
        report = _new_report()
        disc._enrich_report_with_knowledge_counts(report)
        assert report.new_contradictions == 0
        assert report.new_lifecycle_transitions == 0
        assert report.top_contradictions == []

    def test_counts_contradictions_and_transitions(self):
        from db.lifecycle import set_lifecycle_state
        _seed_contradiction(strategy="strat_x", claimed=1.4, measured=0.4)
        set_lifecycle_state(strategy="strat_x", universe="sp500",
                            new_state="RESEARCH", operator="system")

        report = _new_report()
        disc._enrich_report_with_knowledge_counts(report)
        # Critical contradiction surfaced.
        assert report.new_contradictions >= 1
        # One lifecycle event recorded above.
        assert report.new_lifecycle_transitions >= 1
        # top_contradictions limited to 3, content non-empty.
        assert 1 <= len(report.top_contradictions) <= 3
        top = report.top_contradictions[0]
        assert top["strategy"] == "strat_x"
        assert top["metric"] in ("sharpe", "max_dd_pct")

    def test_since_last_digest_window(self):
        """After log_digest fires, the next enrich call only counts new rows."""
        from db.knowledge import log_digest
        _seed_contradiction(strategy="strat_y", claimed=1.4, measured=0.4)

        first = _new_report()
        disc._enrich_report_with_knowledge_counts(first)
        assert first.new_contradictions >= 1

        # Stamp a digest send.
        log_digest(kind="daily", new_contradictions=first.new_contradictions,
                   delivery_status="ok")

        # Now enrich again with no new data -- should report zero new.
        second = _new_report()
        disc._enrich_report_with_knowledge_counts(second)
        assert second.new_contradictions == 0


# ═══════════════════════════════════════════════════════════════════════════════
# _format_top_contradictions
# ═══════════════════════════════════════════════════════════════════════════════

class TestFormatTopContradictions:
    def test_empty_returns_empty_string(self):
        assert disc._format_top_contradictions([]) == ""
        assert disc._format_top_contradictions(None) == ""

    def test_renders_each_row(self):
        top = [
            {"strategy": "strat_a", "metric": "sharpe",
             "claimed": 1.4, "measured": 0.4, "severity": "critical"},
            {"strategy": "strat_b", "metric": "max_dd_pct",
             "claimed": 10.0, "measured": 25.0, "severity": "major"},
        ]
        out = disc._format_top_contradictions(top)
        assert "strat_a" in out
        assert "strat_b" in out
        assert "critical" in out
        assert "major" in out
        # Numbers formatted to 2dp.
        assert "1.40" in out
        assert "0.40" in out

    def test_handles_none_values_gracefully(self):
        out = disc._format_top_contradictions([
            {"strategy": "strat_x", "metric": "sharpe",
             "claimed": None, "measured": None, "severity": "minor"},
        ])
        assert "strat_x" in out
        assert "?" in out


# ═══════════════════════════════════════════════════════════════════════════════
# log_digest persistence
# ═══════════════════════════════════════════════════════════════════════════════

class TestLogDigest:
    def test_log_digest_writes_row(self):
        from db.knowledge import log_digest, get_last_digest
        rid = log_digest(
            kind="daily",
            new_papers=5, new_contradictions=2, lifecycle_transitions=1,
            summary="test",
            delivery_status="ok",
            payload={"top_contradictions": [{"strategy": "x"}]},
        )
        assert rid > 0

        last = get_last_digest(kind="daily")
        assert last["new_papers"] == 5
        assert last["new_contradictions"] == 2
        assert last["lifecycle_transitions"] == 1
        assert last["delivery_status"] == "ok"
        assert last["payload"]["top_contradictions"][0]["strategy"] == "x"

    def test_failed_send_tagged_in_delivery_status(self):
        from db.knowledge import log_digest, get_last_digest
        log_digest(kind="daily", delivery_status="failed:ConnectionError")
        last = get_last_digest(kind="daily")
        assert last["delivery_status"] == "failed:ConnectionError"
