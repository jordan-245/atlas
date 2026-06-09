"""Focused tests for the research-sweep regression harness (Task #219).

The harness is the board-gate guard: no new live strategy / sizing promotion is
allowed until these checks are green. Each test proves one of the #386
diagnostic coverage items, exercising the REAL production code paths (no parallel
reimplementation):

  1. completed_no_keeps / 0 real kept is VALID and does NOT soften thresholds
     (the canonical 33-row case: 1 baseline + 32 discard_solo).
  2. Budget truncation is detected / reported.
  3. Active-config strategy allow-list filtering is enforced for the current
     SP500 active config.
  4. Baseline rows are never counted as real keeps / promotions (mapping +
     accounting-exclusion identity on real-shaped DB rows).
  5. TSV ↔ SQLite output-consistency check.
  6. No threshold softening — the live keep/discard gate still rejects weak
     candidates at its documented floors.

These tests rely on the autouse DB-isolation fixture in tests/conftest.py, so
all SQLite writes hit a throw-away per-test database.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

import research.sweep_regression_harness as harness


# ──────────────────────────────────────────────────────────────────────────────
# 1. completed_no_keeps validity + canonical fixture
# ──────────────────────────────────────────────────────────────────────────────


def test_canonical_fixture_is_the_33_row_case():
    """The canonical fixture reproduces the diagnostic's 33-row shape."""
    tsv = harness.build_canonical_no_keeps_tsv()
    rows = [ln for ln in tsv.strip().split("\n")[1:] if ln]  # skip header
    assert len(rows) == 33  # 1 baseline + 32 candidates
    assert rows[0].split("\t")[8] == "baseline"
    assert all(r.split("\t")[7] == "discard_solo" for r in rows[1:])


def test_completed_no_keeps_is_valid():
    """0 real keeps over 32 screened is a legitimate no-op, parsed by real code."""
    res = harness.check_completed_no_keeps()
    assert res.passed
    assert res.data["baseline"] == 1
    assert res.data["screened"] == 32
    assert res.data["promoted"] == 0
    assert res.data["kept"] == 0
    assert res.data["valid_no_keeps"] is True


def test_completed_no_keeps_detects_a_real_keep_breaking_the_invariant():
    """If a keep row sneaks in, the canonical-shape assertion fails (guard works)."""
    # Inject one genuine keep into an otherwise-canonical TSV.
    tsv = harness.build_canonical_no_keeps_tsv(n_discards=31)
    tsv += harness._tsv_row(1.4, "keep", "real improvement", params="x=99",
                            trades=350, dd=12.0) + "\n"
    res = harness.check_completed_no_keeps(tsv_text=tsv)
    # screened becomes 32 (31 discard_solo + 1 keep) but kept==1 → not the
    # 0-keep canonical case, so the strict canonical check fails.
    assert res.passed is False
    assert res.data["kept"] == 1


# ──────────────────────────────────────────────────────────────────────────────
# 2. Budget truncation detection
# ──────────────────────────────────────────────────────────────────────────────


def test_budget_truncation_detected_for_partial_window():
    res = harness.check_budget_truncation(screened_candidates=32, planned_candidates=38)
    assert res.passed  # detection succeeds
    assert res.data["truncated"] is True
    assert res.data["unreached"] == 6


def test_budget_truncation_not_flagged_for_full_window():
    res = harness.check_budget_truncation(screened_candidates=38, planned_candidates=38)
    assert res.passed
    assert res.data["truncated"] is False
    assert res.data["unreached"] == 0


def test_live_sweep_plan_size_matches_diagnostic():
    """The real budget-aware planner produces 38 momentum_breakout candidates."""
    assert harness.live_sweep_plan_size("momentum_breakout", "sp500") == 38


# ──────────────────────────────────────────────────────────────────────────────
# 3. Active-config allow-list enforcement
# ──────────────────────────────────────────────────────────────────────────────


def test_active_config_allowlist_enforced_for_sp500():
    """Disabled SP500 strategies are dropped; only momentum_breakout survives."""
    requested = [
        "momentum_breakout", "mean_reversion", "trend_following",
        "opening_gap", "sector_rotation", "connors_rsi2",
    ]
    res = harness.check_active_config_allowlist(requested, "sp500")
    assert res.passed
    assert res.data["allowed"] == ["momentum_breakout"]
    # Every disabled strategy was dropped.
    assert set(res.data["dropped"]) == set(requested) - {"momentum_breakout"}


def test_active_config_allowlist_never_invents_strategies():
    """The filter result is always a subset of the request."""
    res = harness.check_active_config_allowlist(["momentum_breakout"], "sp500",
                                               expect_dropped=False)
    assert res.passed
    assert set(res.data["allowed"]).issubset({"momentum_breakout"})


def test_stale_runner_noise_is_reported_not_failed():
    res = harness.check_stale_runner_noise(66, sample=["a,b,c,d,e,f,g,h"])
    assert res.passed  # report-only, never fails the gate
    assert res.data["legacy_silent_failure_sessions"] == 66


# ──────────────────────────────────────────────────────────────────────────────
# 4. Baseline rows never counted as keeps (mapping + accounting identity)
# ──────────────────────────────────────────────────────────────────────────────


def test_baseline_mapping_invariant():
    """Mapping layer: baseline 'keep' → 'baseline', real 'keep' → 'kept'."""
    res = harness.check_baseline_not_counted(db_universe=None)
    assert res.passed
    assert res.data["mapping_ok"] is True


def _seed_experiment(db, eid, status, description, universe="sp500"):
    db.execute(
        "INSERT INTO research_experiments (id, strategy, universe, status, "
        "description, created_at) VALUES (?,?,?,?,?,?)",
        (eid, "momentum_breakout", universe, status, description,
         datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )


def test_baseline_accounting_identity_excludes_legacy_baseline_rows():
    """Legacy status='kept'/description='baseline' rows are excluded from keeps."""
    from db.atlas_db import get_db
    with get_db() as db:
        # 3 legacy baseline-as-kept rows + 2 genuine keeps.
        for i in range(3):
            _seed_experiment(db, f"legacy-base-{i}", "kept", "baseline")
        _seed_experiment(db, "real-1", "kept", "real improvement")
        _seed_experiment(db, "real-2", "kept", "x=2 better")

    res = harness.check_baseline_not_counted(db_universe="sp500")
    assert res.passed
    assert res.data["naive_kept"] == 5
    assert res.data["hardened_kept"] == 2          # only the genuine keeps
    assert res.data["legacy_baseline_kept"] == 3   # excluded, not recounted
    # Identity must hold exactly.
    assert (res.data["hardened_kept"] + res.data["legacy_baseline_kept"]
            == res.data["naive_kept"])


# ──────────────────────────────────────────────────────────────────────────────
# 5. TSV ↔ SQLite consistency
# ──────────────────────────────────────────────────────────────────────────────


def test_consistency_healthy_run_passes():
    """32 screened / 33 DB rows is consistent (DB mirrors the TSV)."""
    res = harness.check_tsv_sqlite_consistency(tsv_screened=32, db_rows_added=33)
    assert res.passed
    assert res.data["consistent"] is True


def test_consistency_flags_db_write_degradation():
    """32 screened but only 2 DB rows → write degradation is flagged."""
    res = harness.check_tsv_sqlite_consistency(tsv_screened=32, db_rows_added=2)
    assert res.passed is False
    assert res.data["consistent"] is False


def test_consistency_floor_uses_production_fraction():
    """The floor is derived from the production TSV_DB_CONSISTENCY_FRACTION."""
    from research.autoresearch_nightly import TSV_DB_CONSISTENCY_FRACTION
    res = harness.check_tsv_sqlite_consistency(tsv_screened=100, db_rows_added=60)
    assert res.data["floor"] == max(1, int(100 * TSV_DB_CONSISTENCY_FRACTION))


# ──────────────────────────────────────────────────────────────────────────────
# 6. No threshold softening — live gate probes
# ──────────────────────────────────────────────────────────────────────────────


def test_no_threshold_softening_passes_against_live_gate():
    res = harness.check_no_threshold_softening()
    assert res.passed, res.data.get("failures")


def test_live_gate_rejects_sub_threshold_sharpe():
    """Contract the harness depends on: +0.004 Sharpe is below the +0.01 floor."""
    from research.loop import keep_or_discard
    base = {"sharpe": 1.0245, "total_trades": 382, "max_drawdown_pct": 18.83}
    d = keep_or_discard(base, {"sharpe": 1.0285, "total_trades": 382,
                               "max_drawdown_pct": 18.0, "strategy": "__probe__"})
    assert d["decision"] == "discard"


def test_live_gate_keeps_genuine_improvement():
    """The gate is not broken-closed: a clean improvement is kept."""
    from research.loop import keep_or_discard
    base = {"sharpe": 1.00, "total_trades": 100, "max_drawdown_pct": 10.0}
    d = keep_or_discard(base, {"sharpe": 1.20, "total_trades": 120,
                               "max_drawdown_pct": 12.0, "strategy": "__probe__"})
    assert d["decision"] == "keep"


# ──────────────────────────────────────────────────────────────────────────────
# Live session scoping (authoritative per-window counts)
# ──────────────────────────────────────────────────────────────────────────────


def _ensure_sessions_table(db):
    """research_sessions lives in a migration, not schema.sql — create it for tests."""
    db.execute(
        "CREATE TABLE IF NOT EXISTS research_sessions ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL,"
        " ended_at TEXT, mode TEXT NOT NULL, strategy TEXT,"
        " experiments_run INTEGER DEFAULT 0, experiments_kept INTEGER DEFAULT 0,"
        " duration_minutes REAL, status TEXT DEFAULT 'running')"
    )


def _seed_session(db, strategy, status, run, kept, start, end):
    db.execute(
        "INSERT INTO research_sessions (started_at, ended_at, mode, strategy, "
        "experiments_run, experiments_kept, status) VALUES (?,?,?,?,?,?,?)",
        (start, end, "nightly_sweep", strategy, run, kept, status),
    )


def test_load_live_session_uses_authoritative_window_counts():
    from db.atlas_db import get_db
    start = datetime(2026, 5, 31, 13, 0, 0, tzinfo=timezone.utc)
    end = start + timedelta(minutes=30)
    with get_db() as db:
        _ensure_sessions_table(db)
        _seed_session(db, "harness_strat", "completed", run=38, kept=0,
                      start=start.isoformat(), end=end.isoformat())
        # 39 in-window rows (38 candidates + baseline), space-format created_at.
        for i in range(39):
            ts = (start + timedelta(seconds=30 * i)).strftime("%Y-%m-%d %H:%M:%S")
            db.execute(
                "INSERT INTO research_experiments (id, strategy, universe, status, "
                "description, created_at) VALUES (?,?,?,?,?,?)",
                (f"w-{i}", "harness_strat", "sp500",
                 "baseline" if i == 0 else "discarded",
                 "baseline" if i == 0 else f"c{i}", ts),
            )
        # Legacy stale-runner noise sessions.
        for i in range(3):
            _seed_session(db, "a,b,c,d,e,f,g,h", "silent_failure", run=0, kept=0,
                          start=start.isoformat(), end=end.isoformat())

    snap = harness._load_live_session("harness_strat", "sp500")
    assert snap["available"] is True
    assert snap["screened"] == 38
    assert snap["kept"] == 0
    assert snap["window_db_rows"] == 39          # correct ISO→SQLite ts scoping
    assert snap["legacy_noise"] == 3


def test_to_sqlite_ts_normalises_iso_separator():
    """The window-scoping helper converts the 'T' separator to a space (#216)."""
    assert harness._to_sqlite_ts("2026-05-31T13:00:03.089183+00:00") == "2026-05-31 13:00:03"


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator + CLI
# ──────────────────────────────────────────────────────────────────────────────


def test_run_harness_invariant_mode_passes():
    report = harness.run_harness(live=False)
    assert report.passed, report.render()
    names = {c.name for c in report.checks}
    # All six coverage items are represented.
    assert {
        "completed_no_keeps_valid",
        "no_threshold_softening",
        "baseline_not_counted",
        "active_config_allowlist",
        "budget_truncation_detected",
        "tsv_sqlite_consistency",
    }.issubset(names)


def test_report_to_dict_is_json_serialisable():
    import json
    report = harness.run_harness(live=False)
    blob = json.dumps(report.to_dict())
    assert '"passed": true' in blob


def test_cli_invariant_exit_zero(capsys):
    rc = harness.main([])
    out = capsys.readouterr().out
    assert rc == 0
    assert "PASS — board gate satisfied" in out


def test_cli_json_mode(capsys):
    rc = harness.main(["--json"])
    out = capsys.readouterr().out
    assert rc == 0
    assert '"mode": "invariant"' in out
