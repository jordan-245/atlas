"""Phase 2 integration tests: sync_contradictions hooks on the write paths.

Covers:
  - upsert_research_best fires sync after each upsert (per-regime + cross-regime).
  - update_claim_metrics fires sync after each update.
  - A sync exception inside the hook does NOT propagate -- the parent write
    remains successful (13+ callers of upsert_research_best depend on this).
  - End-to-end: paper claim + measured row + extraction => populated
    contradiction row visible in v_open_contradictions.

Run:
    python3 -m pytest tests/test_contradiction_hooks.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

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
    return tmp_path / "test_contradiction_hooks.db"


@pytest.fixture(autouse=True)
def db(db_file, monkeypatch):
    monkeypatch.setattr(atlas_db_module, "DB_PATH", db_file)
    monkeypatch.setattr(atlas_db_module, "_db_path_override", None)
    init_db()
    yield db_file


def _seed_claim_with_metric(
    *,
    strategy: str,
    universe: str | None = "sp500",
    claimed_sharpe: float = 1.4,
    claimed_max_dd: float | None = None,
) -> str:
    """Insert a source + claim with the given claimed metrics.  Returns claim_id."""
    src_id = f"src-test-{strategy}"
    kn.insert_source(id=src_id, kind="paper", title=f"Paper {strategy}")
    claim_id = f"clm-{strategy}-0"
    # Insert with shell, then update to populate metrics via the public path.
    # This exercises insert_claim + update_claim_metrics together.
    kn.insert_claim(
        id=claim_id,
        source_id=src_id,
        strategy=strategy,
        universe=universe,
    )
    kn.update_claim_metrics(
        id=claim_id,
        claimed_sharpe=claimed_sharpe,
        claimed_max_dd_pct=claimed_max_dd,
        extraction_confidence="high",
    )
    return claim_id


# ═══════════════════════════════════════════════════════════════════════════════
# upsert_research_best -> sync
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpsertResearchBestHook:
    def test_upsert_triggers_sync_and_creates_contradiction(self):
        # Order matters: seed the claim FIRST, then upsert measured row to
        # exercise the upsert -> sync hook.  (Reverse order is tested below.)
        _seed_claim_with_metric(strategy="strat_a", claimed_sharpe=1.4)

        upsert_research_best(
            strategy="strat_a",
            universe="sp500",
            params={},
            solo_sharpe=0.5,  # |1.4 - 0.5| = 0.9 -> major
            max_dd_pct=10.0,
        )

        opens = kn.get_open_contradictions(strategy="strat_a")
        assert len(opens) == 1
        assert opens[0]["metric"] == "sharpe"
        assert opens[0]["severity"] == "major"

    def test_claim_metric_update_triggers_sync_when_measured_already_exists(self):
        # Reverse order: measured row first, then claim with metric.
        upsert_research_best(
            strategy="strat_b",
            universe="sp500",
            params={},
            solo_sharpe=0.5,
            max_dd_pct=10.0,
        )
        # No claim yet -> no contradiction yet.
        assert kn.get_open_contradictions(strategy="strat_b") == []

        # Seed shell, then UPDATE metrics -- update_claim_metrics fires sync.
        _seed_claim_with_metric(strategy="strat_b", claimed_sharpe=1.4)

        opens = kn.get_open_contradictions(strategy="strat_b")
        assert len(opens) == 1
        assert opens[0]["severity"] == "major"

    def test_re_upsert_refreshes_severity(self):
        _seed_claim_with_metric(strategy="strat_c", claimed_sharpe=1.0)

        # First upsert: |1.0 - 0.7| = 0.3 -> minor
        upsert_research_best(strategy="strat_c", universe="sp500",
                             params={}, solo_sharpe=0.7)
        opens = kn.get_open_contradictions(strategy="strat_c")
        assert opens and opens[0]["severity"] == "minor"

        # Re-upsert with worse measured: |1.0 - 0.2| = 0.8 -> major
        upsert_research_best(strategy="strat_c", universe="sp500",
                             params={}, solo_sharpe=0.2)
        opens = kn.get_open_contradictions(strategy="strat_c")
        # Same row, severity bumped via UPDATE path inside sync_contradictions
        assert len(opens) == 1
        assert opens[0]["severity"] == "major"


# ═══════════════════════════════════════════════════════════════════════════════
# update_claim_metrics -> sync
# ═══════════════════════════════════════════════════════════════════════════════

class TestUpdateClaimMetricsHook:
    def test_metric_update_after_measured_exists_creates_row(self):
        # Measured first.
        upsert_research_best(strategy="strat_d", universe="sp500",
                             params={}, solo_sharpe=0.4)

        # Shell claim (no metrics yet) -- no contradiction yet.
        kn.insert_source(id="src-d", kind="paper", title="P")
        kn.insert_claim(id="clm-d", source_id="src-d", strategy="strat_d",
                        universe="sp500")
        assert kn.get_open_contradictions(strategy="strat_d") == []

        # Populating the metric MUST trigger sync.
        kn.update_claim_metrics(id="clm-d", claimed_sharpe=1.6,
                                extraction_confidence="high")
        opens = kn.get_open_contradictions(strategy="strat_d")
        assert len(opens) == 1
        assert opens[0]["claimed_value"] == 1.6
        assert opens[0]["severity"] == "critical"  # |1.6 - 0.4| = 1.2 >= 1.0

    def test_update_unknown_claim_id_is_noop(self):
        # No exception, no rows.
        kn.update_claim_metrics(id="clm-does-not-exist",
                                claimed_sharpe=1.0)
        assert kn.get_open_contradictions() == []


# ═══════════════════════════════════════════════════════════════════════════════
# Defensive: sync failure must not break the parent write
# ═══════════════════════════════════════════════════════════════════════════════

class TestHookIsDefensive:
    def test_sync_raises_does_not_break_upsert_research_best(self):
        """If sync_contradictions ever raises, upsert_research_best must succeed.

        Any of the 13+ callers of upsert_research_best would be broken if the
        hook propagated -- including the autoresearch nightly loop.
        """
        # Patch the sync entry point that db.research imports.  The import
        # happens inside the function, so patch the source module.
        with patch("db.knowledge.sync_contradictions",
                   side_effect=RuntimeError("simulated sync failure")):
            # The upsert must still succeed -- no exception.
            upsert_research_best(strategy="strat_e", universe="sp500",
                                 params={"p": 1}, solo_sharpe=0.5)

        # And the row is written.
        from db.research import get_research_best
        rows = get_research_best(strategy="strat_e", universe="sp500")
        assert len(rows) == 1
        assert rows[0]["solo_sharpe"] == 0.5

    def test_sync_raises_does_not_break_update_claim_metrics(self):
        kn.insert_source(id="src-f", kind="paper", title="P")
        kn.insert_claim(id="clm-f", source_id="src-f", strategy="strat_f")

        with patch("db.knowledge.sync_contradictions",
                   side_effect=RuntimeError("simulated sync failure")):
            kn.update_claim_metrics(id="clm-f", claimed_sharpe=1.4,
                                    extraction_confidence="high")

        c = kn.get_claim("clm-f")
        assert c["claimed_sharpe"] == 1.4
        assert c["extraction_confidence"] == "high"


# ═══════════════════════════════════════════════════════════════════════════════
# End-to-end: full populated contradiction visible in v_open_contradictions
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEnd:
    def test_full_flow(self):
        # 1. Paper claim arrives (with metric extracted).
        _seed_claim_with_metric(
            strategy="donchian_breakout",
            universe="sp500",
            claimed_sharpe=1.6,
            claimed_max_dd=15.0,
        )

        # 2. Atlas measures the strategy and writes research_best.
        upsert_research_best(
            strategy="donchian_breakout",
            universe="sp500",
            params={"lookback": 20},
            solo_sharpe=0.4,    # Δ = 1.2 -> critical
            max_dd_pct=28.0,    # Δ = 13 -> major
            trades=85,
        )

        # 3. Operator queries v_open_contradictions.
        opens = kn.get_open_contradictions(strategy="donchian_breakout")
        # Two metrics: sharpe + max_dd_pct
        metrics = {o["metric"]: o for o in opens}
        assert set(metrics.keys()) == {"sharpe", "max_dd_pct"}
        assert metrics["sharpe"]["severity"] == "critical"
        assert metrics["max_dd_pct"]["severity"] == "major"

        # 4. Source info is joined in.
        assert metrics["sharpe"]["source_title"] == "Paper donchian_breakout"
        assert metrics["sharpe"]["source_id"] == "src-test-donchian_breakout"

        # 5. Ordering: critical comes before major.
        # v_open_contradictions ORDER BY severity DESC -> critical, then major.
        assert opens[0]["severity"] == "critical"
        assert opens[1]["severity"] == "major"
