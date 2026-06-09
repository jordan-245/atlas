"""Focused tests for the 2026-06-01 research-workflow audit fixes (#392).

Covers:
  1. Accounting   — baseline rows are NOT counted as real keeps in the TSV
                    parser, SQLite write path, or the API summary.
  2. Degraded     — the nightly DEGRADED warning self-calibrates to the active
                    allow-list (a healthy 1-strategy ~38-row run is not flagged),
                    a real DB-write degradation IS flagged, and a genuine silent
                    failure (TSV=0 AND DB=0) still alerts.
  3. LLM prompt   — leaderboard tolerates null metrics; the prompt documents the
                    real experiment() result schema (metrics under r['metrics']).
  4. Exhaustion   — repeated zero-keep sweeps surface a #387/#388 rotation
                    recommendation without enabling strategies or promoting configs.
  5. Queue        — director status reports a stranded backlog when the consumer
                    service is disabled.
"""
from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ──────────────────────────────────────────────────────────────────────────────
# 1. Accounting — baseline vs real keeps
# ──────────────────────────────────────────────────────────────────────────────

_TSV_HEADER = (
    "timestamp\tsharpe\ttrades\tmax_dd_pct\tpf\tcagr_pct\t"
    "params_changed\tstatus\tdescription"
)


def _row(sharpe, status, description, params="", trades=100):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return "\t".join([
        ts, f"{sharpe:.4f}", str(trades), "10.00", "1.5000", "20.00",
        params, status, description,
    ])


def test_parse_session_results_excludes_baseline_from_keeps(tmp_path, monkeypatch):
    """Baseline rows go in the `baseline` bucket, never `kept`/`promoted` (#392)."""
    import research.autoresearch_nightly as nightly

    monkeypatch.setattr(nightly, "RESULTS_DIR", tmp_path)
    tsv = tmp_path / "momentum_breakout.tsv"
    tsv.write_text(
        _TSV_HEADER + "\n"
        + _row(1.00, "keep", "baseline") + "\n"          # baseline -> not a keep
        + _row(0.50, "discard_solo", "x=1 solo fail") + "\n"
        + _row(0.90, "discard", "x=3 combined fail", params="x=3") + "\n"
        + _row(1.20, "keep", "real improvement", params="x=2") + "\n"
    )

    start = time.time() - 600  # all rows are newer than this
    r = nightly._parse_session_results("momentum_breakout", start)

    assert r["baseline"] == 1
    assert r["kept"] == 1                # only the real improvement
    assert r["screened"] == 3            # discard_solo + discard + real keep
    assert r["promoted"] == 2            # discard + real keep
    assert r["starting_sharpe"] == pytest.approx(1.00)
    assert r["final_sharpe"] == pytest.approx(1.20)


def test_parse_session_results_recognizes_baseline_status(tmp_path, monkeypatch):
    """A row whose STATUS is literally 'baseline' is also treated as baseline."""
    import research.autoresearch_nightly as nightly

    monkeypatch.setattr(nightly, "RESULTS_DIR", tmp_path)
    tsv = tmp_path / "momentum_breakout.tsv"
    tsv.write_text(
        _TSV_HEADER + "\n"
        + _row(1.10, "baseline", "baseline") + "\n"
        + _row(0.40, "discard_solo", "x=1") + "\n"
    )
    start = time.time() - 600
    r = nightly._parse_session_results("momentum_breakout", start)
    assert r["baseline"] == 1
    assert r["kept"] == 0
    assert r["screened"] == 1
    assert r["starting_sharpe"] == pytest.approx(1.10)


def test_db_status_for_baseline_not_kept():
    """db_status_for maps a baseline 'keep' to 'baseline', not 'kept' (#392)."""
    from research.db import db_status_for

    assert db_status_for("keep", "baseline") == "baseline"
    assert db_status_for("keep", "BASELINE") == "baseline"   # case-insensitive
    assert db_status_for("keep", " baseline ") == "baseline" # whitespace-tolerant
    assert db_status_for("keep", "real improvement") == "kept"
    assert db_status_for("keep", "") == "kept"
    assert db_status_for("discard", "x=3") == "discarded"
    assert db_status_for("discard_solo", "x=4") == "discard_solo"  # passthrough


def test_api_summary_excludes_baseline_keeps(tmp_path):
    """Historical baseline rows stored as status='kept' must not count as keeps."""
    from db.atlas_db import get_db

    # Simulate a legacy baseline row (status='kept', description='baseline')
    # plus one genuine keep.
    with get_db() as db:
        db.execute(
            "INSERT INTO research_experiments (id, strategy, universe, status, "
            "description, created_at) VALUES (?,?,?,?,?,?)",
            ("legacy-base", "mr", "sp500", "kept", "baseline",
             datetime.now(timezone.utc).isoformat()),
        )
        db.execute(
            "INSERT INTO research_experiments (id, strategy, universe, status, "
            "description, created_at) VALUES (?,?,?,?,?,?)",
            ("real-keep", "mr", "sp500", "kept", "real improvement",
             datetime.now(timezone.utc).isoformat()),
        )

    # The hardened summary query excludes description='baseline'.
    with get_db() as db:
        kept = db.execute(
            "SELECT COUNT(*) as c FROM research_experiments "
            "WHERE status='kept' AND COALESCE(LOWER(TRIM(description)), '') != 'baseline'"
        ).fetchone()["c"]
    assert kept == 1


# ──────────────────────────────────────────────────────────────────────────────
# 2. Degraded threshold — consistency-based DEGRADED decision
# ──────────────────────────────────────────────────────────────────────────────


def _patch_nightly_common(monkeypatch, screened, kept, rows_added, baseline=1):
    """Patch run_nightly's heavy dependencies; control TSV + DB counts."""
    import research.autoresearch_nightly as nightly

    monkeypatch.setattr(nightly, "_filter_enabled_strategies",
                        lambda strategies, *a, **kw: strategies)
    monkeypatch.setattr(nightly, "_spawn_workers",
                        lambda *a, **kw: [{"strategy": "momentum_breakout",
                                           "exit_code": 0, "log_path": "x"}])
    monkeypatch.setattr(nightly, "_run_promotion_sweep", lambda *a, **kw: [])
    monkeypatch.setattr(nightly, "_find_latest_snapshot", lambda *a, **kw: "snap")
    monkeypatch.setattr(nightly, "assess_exhaustion_for_strategies",
                        lambda *a, **kw: {"any_exhausted": False})
    monkeypatch.setattr(nightly, "_count_rows_added", lambda *a, **kw: rows_added)
    monkeypatch.setattr(
        nightly, "_parse_session_results",
        lambda strat, start: {
            "strategy": strat, "screened": screened, "promoted": 0,
            "kept": kept, "baseline": baseline,
            "starting_sharpe": 1.0, "final_sharpe": 1.0,
        },
    )
    return nightly


def test_healthy_single_strategy_run_not_flagged_degraded(monkeypatch, caplog):
    """38 screened / ~40 DB rows / 0 keeps must NOT emit RESEARCH_NIGHTLY_DEGRADED."""
    nightly = _patch_nightly_common(monkeypatch, screened=38, kept=0, rows_added=40)

    with caplog.at_level(logging.WARNING, logger="research.autoresearch_nightly"):
        result = nightly.run_nightly(
            strategies=["momentum_breakout"], market="sp500", hours=0.01,
            workers=1, universe="sp500", dry_run_telegram=True,
        )

    assert result["silent_failure"] is False
    assert result["status"] == "completed_no_keeps"   # 0 keeps but healthy
    assert not any("RESEARCH_NIGHTLY_DEGRADED" in rec.message for rec in caplog.records), (
        "A healthy 1-strategy low-yield run must not log RESEARCH_NIGHTLY_DEGRADED"
    )


def test_db_write_degradation_is_flagged(monkeypatch, caplog):
    """TSV shows 38 screened but only 2 DB rows -> DEGRADED warning + no-keeps."""
    nightly = _patch_nightly_common(monkeypatch, screened=38, kept=0, rows_added=2)

    with caplog.at_level(logging.WARNING, logger="research.autoresearch_nightly"):
        result = nightly.run_nightly(
            strategies=["momentum_breakout"], market="sp500", hours=0.01,
            workers=1, universe="sp500", dry_run_telegram=True,
        )

    assert result["status"] == "completed_no_keeps"
    assert result["silent_failure"] is False
    assert any("RESEARCH_NIGHTLY_DEGRADED" in rec.message for rec in caplog.records), (
        "A real DB-write degradation (TSV>>DB) must log RESEARCH_NIGHTLY_DEGRADED"
    )


def test_genuine_silent_failure_alerts(monkeypatch, capsys):
    """TSV=0 AND DB=0 -> genuine silent failure -> Telegram alert path."""
    nightly = _patch_nightly_common(monkeypatch, screened=0, kept=0, rows_added=0,
                                    baseline=0)

    result = nightly.run_nightly(
        strategies=["momentum_breakout"], market="sp500", hours=0.01,
        workers=1, universe="sp500", dry_run_telegram=True,
    )
    out = capsys.readouterr().out
    assert result["silent_failure"] is True
    assert "[TELEGRAM-DRY-RUN]" in out


# ──────────────────────────────────────────────────────────────────────────────
# 3. LLM prompt + leaderboard robustness
# ──────────────────────────────────────────────────────────────────────────────


def test_leaderboard_tolerates_null_metrics(tmp_path, monkeypatch):
    """Leaderboard must not crash on an explicit null metric (#392)."""
    import json
    import research.loop as loop

    monkeypatch.setattr(loop, "BEST_DIR", tmp_path)
    (tmp_path / "crypto_btc_eth_v1.json").write_text(json.dumps({
        "strategy": "crypto_btc_eth_v1",
        "metrics": {"sharpe": None, "total_trades": None, "max_drawdown_pct": None,
                    "profit_factor": None, "cagr_pct": None},
    }))
    (tmp_path / "momentum_breakout.json").write_text(json.dumps({
        "strategy": "momentum_breakout",
        "metrics": {"sharpe": 1.3, "total_trades": 501, "max_drawdown_pct": 17.6,
                    "profit_factor": 1.8, "cagr_pct": 39.0},
        "experiments_run": 10, "experiments_kept": 2,
    }))

    out = loop.leaderboard()  # must not raise
    assert "crypto_btc_eth_v1" in out
    assert "momentum_breakout" in out


def test_llm_prompt_documents_result_schema(monkeypatch):
    """The LLM prompt must teach the real experiment() result shape (#392)."""
    import research.llm_loop_runner as llm

    monkeypatch.setattr(llm, "_gather_context", lambda *a, **kw: "(ctx)")
    prompt = llm._build_prompt(25, ["momentum_breakout"], universe="sp500")

    assert "r['metrics']['sharpe']" in prompt
    assert "Result Schema" in prompt
    # Must explicitly steer agents away from the top-level sharpe KeyError.
    assert "r['sharpe']" in prompt  # appears in the WRONG example
    assert "recommendation" in prompt


# ──────────────────────────────────────────────────────────────────────────────
# 4. Exhaustion / rotation guard
# ──────────────────────────────────────────────────────────────────────────────


def _seed_experiments(strategy, universe, n, status="discarded", keep_idx=None):
    from db.atlas_db import get_db
    with get_db() as db:
        for i in range(n):
            st = "kept" if (keep_idx is not None and i == keep_idx) else status
            db.execute(
                "INSERT INTO research_experiments (id, strategy, universe, status, "
                "description, created_at) VALUES (?,?,?,?,?,?)",
                (f"{strategy}-{universe}-{i}", strategy, universe, st, f"exp {i}",
                 f"2026-05-{(i % 28) + 1:02d}T00:{i % 60:02d}:00+00:00"),
            )


def test_exhaustion_flagged_after_many_zero_keep_runs():
    from research.autoresearch_nightly import assess_exhaustion

    _seed_experiments("momentum_breakout", "sp500", 60, status="discarded")
    a = assess_exhaustion("momentum_breakout", "sp500")
    assert a["assessed"] is True
    assert a["exhausted"] is True
    assert a["real_keeps"] == 0
    assert a["recent_experiments"] >= 50
    assert a["recommendation"] is not None
    assert "#387" in a["recommendation"] and "#388" in a["recommendation"]


def test_exhaustion_not_flagged_when_recent_keep_exists():
    from research.autoresearch_nightly import assess_exhaustion

    _seed_experiments("connors_rsi2", "sp500", 60, status="discarded", keep_idx=59)
    a = assess_exhaustion("connors_rsi2", "sp500")
    assert a["real_keeps"] == 1
    assert a["exhausted"] is False


def test_exhaustion_not_flagged_with_insufficient_signal():
    from research.autoresearch_nightly import assess_exhaustion

    _seed_experiments("bb_squeeze", "sp500", 10, status="discarded")
    a = assess_exhaustion("bb_squeeze", "sp500")
    assert a["recent_experiments"] == 10
    assert a["exhausted"] is False  # below EXHAUSTION_MIN_EXPERIMENTS


def test_exhaustion_excludes_baseline_rows():
    """Baseline rows must not count toward the experiment total or keeps."""
    from research.autoresearch_nightly import assess_exhaustion
    from db.atlas_db import get_db

    # 60 baseline rows + 5 real discards: only 5 non-baseline experiments.
    with get_db() as db:
        for i in range(60):
            db.execute(
                "INSERT INTO research_experiments (id, strategy, universe, status, "
                "description, created_at) VALUES (?,?,?,?,?,?)",
                (f"opening_gap-b-{i}", "opening_gap", "sp500", "baseline", "baseline",
                 f"2026-05-{(i % 28) + 1:02d}T01:{i % 60:02d}:00+00:00"),
            )
        for i in range(5):
            db.execute(
                "INSERT INTO research_experiments (id, strategy, universe, status, "
                "description, created_at) VALUES (?,?,?,?,?,?)",
                (f"opening_gap-d-{i}", "opening_gap", "sp500", "discarded", f"exp {i}",
                 f"2026-05-{(i % 28) + 1:02d}T02:{i % 60:02d}:00+00:00"),
            )
    a = assess_exhaustion("opening_gap", "sp500")
    assert a["recent_experiments"] == 5      # baselines excluded
    assert a["exhausted"] is False


# ──────────────────────────────────────────────────────────────────────────────
# 5. Queue diagnostic — stranded backlog reporting
# ──────────────────────────────────────────────────────────────────────────────


def _fake_queue(n_queued):
    return [{"status": "queued", "priority": "P2"} for _ in range(n_queued)]


def test_queue_stats_flags_stranded_backlog_when_consumer_disabled(monkeypatch, caplog):
    import scripts.director_cron as dc

    monkeypatch.setattr(dc, "_read_json", lambda *a, **kw: _fake_queue(50))
    monkeypatch.setattr(
        dc, "_systemctl_state",
        lambda unit, query: False if query == "is-enabled" else False,
    )
    with caplog.at_level(logging.WARNING, logger="director_cron"):
        stats = dc.get_queue_stats()

    assert stats["queued"] == 50
    assert stats["consumer_enabled"] is False
    assert stats["backlog_stranded"] is True
    assert stats["backlog_warning"] and "invisible backlog" in stats["backlog_warning"]


def test_queue_stats_not_stranded_when_consumer_enabled(monkeypatch):
    import scripts.director_cron as dc

    monkeypatch.setattr(dc, "_read_json", lambda *a, **kw: _fake_queue(50))
    monkeypatch.setattr(
        dc, "_systemctl_state",
        lambda unit, query: True if query == "is-enabled" else True,
    )
    stats = dc.get_queue_stats()
    assert stats["consumer_enabled"] is True
    assert stats["backlog_stranded"] is False
    assert stats["backlog_warning"] is None


def test_queue_stats_unknown_probe_does_not_false_alarm(monkeypatch):
    """Inconclusive systemctl probe (None) must NOT raise a stranded alarm."""
    import scripts.director_cron as dc

    monkeypatch.setattr(dc, "_read_json", lambda *a, **kw: _fake_queue(50))
    monkeypatch.setattr(dc, "_systemctl_state", lambda unit, query: None)
    stats = dc.get_queue_stats()
    assert stats["consumer_enabled"] is None
    assert stats["backlog_stranded"] is False
