"""Tests for the research knowledge layer (Phase 0).

Covers:
  - Schema creation (5 tables, 3 views, 9 indexes)
  - CRUD round-trips for sources, claims, contradictions, lifecycle, digest
  - sync_contradictions: severity classification, idempotency, recheck
  - FK cascade on source delete
  - View correctness (v_candidate_contradictions, v_open_contradictions,
    v_strategy_summary)

Run:
    python3 -m pytest tests/test_knowledge_schema.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

import db.atlas_db as atlas_db_module
from db.atlas_db import init_db
from db import knowledge as kn
from db.research import upsert_research_best


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def db_file(tmp_path):
    return tmp_path / "test_knowledge.db"


@pytest.fixture(autouse=True)
def db(db_file, monkeypatch):
    """Isolated DB per test."""
    monkeypatch.setattr(atlas_db_module, "DB_PATH", db_file)
    monkeypatch.setattr(atlas_db_module, "_db_path_override", None)
    init_db()
    yield db_file


# ═══════════════════════════════════════════════════════════════════════════════
# Schema
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchema:
    def test_tables_created(self):
        expected = {"sources", "claims", "contradictions", "digest_history"}
        with atlas_db_module.get_db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        actual = {r["name"] for r in rows}
        assert expected <= actual, f"Missing tables: {expected - actual}"

    def test_views_created(self):
        expected = {"v_candidate_contradictions",
                    "v_open_contradictions",
                    "v_strategy_summary"}
        with atlas_db_module.get_db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            ).fetchall()
        actual = {r["name"] for r in rows}
        assert expected <= actual, f"Missing views: {expected - actual}"

    def test_indexes_created(self):
        expected = {
            "idx_sources_kind",
            "idx_sources_published",
            "idx_claims_strategy",
            "idx_claims_source",
            "idx_claims_active",
            "idx_contradictions_unresolved",
            "idx_contradictions_recent",
            "idx_digest_sent",
        }
        with atlas_db_module.get_db() as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        actual = {r["name"] for r in rows}
        assert expected <= actual, f"Missing indexes: {expected - actual}"


# ═══════════════════════════════════════════════════════════════════════════════
# sources CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestSources:
    def test_insert_and_get(self):
        kn.insert_source(
            id="src-arxiv-2402.01234",
            kind="paper",
            title="A Momentum Strategy That Works",
            url="https://arxiv.org/abs/2402.01234",
            authors=["Smith, A.", "Jones, B."],
            venue="arxiv",
            published_at="2024-02-15",
            sha256="abc123" * 10 + "def4",
            extracted_by="pdf_vision",
        )
        s = kn.get_source("src-arxiv-2402.01234")
        assert s is not None
        assert s["kind"] == "paper"
        assert s["title"] == "A Momentum Strategy That Works"
        assert s["authors"] == ["Smith, A.", "Jones, B."]
        assert s["venue"] == "arxiv"
        assert s["ingested_at"] is not None

    def test_get_missing_returns_none(self):
        assert kn.get_source("does-not-exist") is None

    def test_insert_or_ignore_dedup(self):
        """Re-inserting same id is a no-op (no error)."""
        kn.insert_source(id="src-a", kind="paper", title="First")
        kn.insert_source(id="src-a", kind="paper", title="Second")  # should be ignored
        s = kn.get_source("src-a")
        assert s["title"] == "First"

    def test_list_by_kind(self):
        kn.insert_source(id="src-p1", kind="paper", title="P1")
        kn.insert_source(id="src-p2", kind="paper", title="P2")
        kn.insert_source(id="src-b1", kind="blog", title="B1")
        papers = kn.list_sources(kind="paper")
        assert len(papers) == 2
        blogs = kn.list_sources(kind="blog")
        assert len(blogs) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# claims CRUD
# ═══════════════════════════════════════════════════════════════════════════════

class TestClaims:
    def _seed_source(self, sid="src-1"):
        kn.insert_source(id=sid, kind="paper", title="T")

    def test_insert_and_list(self):
        self._seed_source()
        kn.insert_claim(
            id="clm-1",
            source_id="src-1",
            strategy="donchian_breakout",
            universe="sp500",
            claimed_sharpe=1.4,
            claimed_max_dd_pct=12.0,
            claimed_trades=120,
        )
        claims = kn.list_claims(strategy="donchian_breakout")
        assert len(claims) == 1
        assert claims[0]["claimed_sharpe"] == 1.4
        assert claims[0]["status"] == "active"

    def test_dismiss(self):
        self._seed_source()
        kn.insert_claim(id="clm-1", source_id="src-1", strategy="x", claimed_sharpe=1.0)
        kn.dismiss_claim("clm-1", reason="extraction error")

        active = kn.list_claims(strategy="x", status="active")
        assert len(active) == 0
        dismissed = kn.list_claims(strategy="x", status="dismissed")
        assert len(dismissed) == 1
        assert dismissed[0]["dismissed_reason"] == "extraction error"

    def test_fk_cascade_on_source_delete(self):
        self._seed_source()
        kn.insert_claim(id="clm-1", source_id="src-1", strategy="x")
        kn.insert_claim(id="clm-2", source_id="src-1", strategy="y")

        with atlas_db_module.get_db() as conn:
            conn.execute("DELETE FROM sources WHERE id = 'src-1'")

        assert kn.get_claim("clm-1") is None
        assert kn.get_claim("clm-2") is None


# ═══════════════════════════════════════════════════════════════════════════════
# contradictions: sync + severity
# ═══════════════════════════════════════════════════════════════════════════════

class TestContradictionSync:
    def _seed(self, claimed_sharpe, measured_sharpe,
              claimed_dd=None, measured_dd=None,
              strategy="donchian_breakout", universe="sp500"):
        kn.insert_source(id="src-1", kind="paper", title="T")
        kn.insert_claim(
            id="clm-1",
            source_id="src-1",
            strategy=strategy,
            universe=universe,
            claimed_sharpe=claimed_sharpe,
            claimed_max_dd_pct=claimed_dd,
        )
        upsert_research_best(
            strategy=strategy,
            universe=universe,
            params={"p": 1},
            solo_sharpe=measured_sharpe,
            max_dd_pct=measured_dd,
        )

    def test_minor_severity(self):
        # |1.0 - 0.7| = 0.3 -> minor.
        # Phase 2: upsert_research_best fires sync via hook, so the row exists
        # before the explicit sync call below.  The assertion checks final state.
        self._seed(claimed_sharpe=1.0, measured_sharpe=0.7)
        kn.sync_contradictions()

        opens = kn.get_open_contradictions()
        sharpes = [o for o in opens if o["metric"] == "sharpe"]
        assert len(sharpes) == 1
        assert sharpes[0]["severity"] == "minor"

    def test_major_severity(self):
        # |1.4 - 0.6| = 0.8 -> major
        self._seed(claimed_sharpe=1.4, measured_sharpe=0.6)
        kn.sync_contradictions()
        sharpes = [o for o in kn.get_open_contradictions() if o["metric"] == "sharpe"]
        assert sharpes[0]["severity"] == "major"

    def test_critical_severity(self):
        # |1.8 - 0.5| = 1.3 -> critical
        self._seed(claimed_sharpe=1.8, measured_sharpe=0.5)
        kn.sync_contradictions()
        sharpes = [o for o in kn.get_open_contradictions() if o["metric"] == "sharpe"]
        assert sharpes[0]["severity"] == "critical"

    def test_below_threshold_no_contradiction(self):
        # |1.0 - 0.85| = 0.15 -> below minor cutoff (0.3)
        self._seed(claimed_sharpe=1.0, measured_sharpe=0.85)
        result = kn.sync_contradictions()
        assert result["inserted"] == 0
        assert kn.get_open_contradictions() == []

    def test_max_dd_severity(self):
        # |20 - 12| = 8 -> major (cutoff 8)
        self._seed(claimed_sharpe=1.0, measured_sharpe=0.95,  # sharpe Δ below threshold
                   claimed_dd=12.0, measured_dd=20.0)
        kn.sync_contradictions()
        dds = [o for o in kn.get_open_contradictions() if o["metric"] == "max_dd_pct"]
        assert len(dds) == 1
        assert dds[0]["severity"] == "major"

    def test_idempotent_sync(self):
        # After Phase 2: the upsert hook already inserts the contradiction, so
        # every manual sync call after that is a recheck (inserted=0).  The
        # property being tested is "calling sync twice produces no duplicate
        # rows" -- assert against the final state, not the inserted count.
        self._seed(claimed_sharpe=1.4, measured_sharpe=0.6)
        opens_first = kn.get_open_contradictions()
        kn.sync_contradictions()
        kn.sync_contradictions()
        opens_third = kn.get_open_contradictions()

        assert len(opens_first) == len(opens_third)
        assert {o["contradiction_id"] for o in opens_first} == \
               {o["contradiction_id"] for o in opens_third}

        # Manual sync call after the hook fired is always a recheck.
        result = kn.sync_contradictions()
        assert result["inserted"] == 0
        assert result["rechecked"] >= 1

    def test_resolve_hides_from_open(self):
        self._seed(claimed_sharpe=1.4, measured_sharpe=0.6)
        kn.sync_contradictions()
        opens = kn.get_open_contradictions(metric_filter := None)  # noqa: F841
        opens = kn.get_open_contradictions()
        cid = opens[0]["contradiction_id"]
        kn.resolve_contradiction(cid, resolution="retested", note="Backtested, matches.")
        assert kn.get_open_contradictions() == []

    def test_resolve_invalid_resolution_raises(self):
        self._seed(claimed_sharpe=1.4, measured_sharpe=0.6)
        kn.sync_contradictions()
        cid = kn.get_open_contradictions()[0]["contradiction_id"]
        with pytest.raises(ValueError):
            kn.resolve_contradiction(cid, resolution="bogus")

    def test_dismissed_claim_not_in_view(self):
        self._seed(claimed_sharpe=1.4, measured_sharpe=0.6)
        kn.dismiss_claim("clm-1", reason="duplicate")
        result = kn.sync_contradictions()
        assert result["inserted"] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# digest history
# ═══════════════════════════════════════════════════════════════════════════════

class TestDigest:
    def test_log_and_get_last(self):
        rid = kn.log_digest(
            kind="daily",
            new_papers=3,
            new_contradictions=2,
            summary="3 papers, 2 contradictions",
            delivery_status="ok",
            payload={"foo": "bar"},
        )
        assert rid > 0

        last = kn.get_last_digest(kind="daily")
        assert last is not None
        assert last["new_papers"] == 3
        assert last["new_contradictions"] == 2
        assert last["payload"] == {"foo": "bar"}

    def test_get_last_returns_most_recent(self):
        kn.log_digest(kind="daily", new_papers=1)
        kn.log_digest(kind="daily", new_papers=2)
        kn.log_digest(kind="daily", new_papers=3)
        last = kn.get_last_digest(kind="daily")
        assert last["new_papers"] == 3


# ═══════════════════════════════════════════════════════════════════════════════
# v_strategy_summary view
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategySummary:
    def test_summary_row(self):
        # Seed: source + 2 active claims + 1 dismissed + research_best + 1 contradiction
        kn.insert_source(id="src-1", kind="paper", title="T")
        kn.insert_claim(id="clm-1", source_id="src-1", strategy="x",
                        universe="sp500", claimed_sharpe=1.4)
        kn.insert_claim(id="clm-2", source_id="src-1", strategy="x",
                        universe="sp500", claimed_sharpe=1.0)
        kn.insert_claim(id="clm-3", source_id="src-1", strategy="x",
                        universe="sp500", claimed_sharpe=0.5)
        kn.dismiss_claim("clm-3", reason="superseded")

        upsert_research_best(
            strategy="x", universe="sp500",
            params={}, solo_sharpe=0.5, max_dd_pct=10.0,
        )
        kn.sync_contradictions()
        # Seed a lifecycle transition via the canonical write path -- Phase 3
        # consolidated lifecycle history into strategy_lifecycle_history.
        from db.atlas_db import set_lifecycle_state
        set_lifecycle_state(strategy="x", universe="sp500", new_state="RESEARCH",
                            reason="seed", operator="seed")

        with atlas_db_module.get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM v_strategy_summary WHERE strategy='x'"
            ).fetchall()

        assert len(rows) == 1
        r = dict(rows[0])
        assert r["active_claims"] == 2  # clm-1, clm-2 (clm-3 dismissed)
        assert r["open_contradictions"] >= 1
        assert r["lifecycle_state"] == "RESEARCH"
