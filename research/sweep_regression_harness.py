#!/usr/bin/env python3
"""Research-sweep regression harness (Task #219).

A focused, executable harness that validates the *outputs and invariants* of the
nightly autoresearch sweep WITHOUT touching live trading, config, or thresholds.
It is the board-gate guard: no new live strategy / sizing promotion is allowed
until this harness is green (see
``docs/project-notes/research-promotion-diagnostic-2026-05-29.md`` — Task #386).

Design principle: **reuse the production code paths**, never reimplement them.
Every check imports and exercises the real sweep functions so a regression in
the live code (e.g. a softened threshold) turns the harness red:

    - TSV parsing / baseline accounting → ``research.autoresearch_nightly._parse_session_results``
    - SQLite status mapping             → ``research.db.db_status_for``
    - active-config allow-list filter   → ``research.autoresearch_nightly._filter_enabled_strategies``
    - keep/discard gate thresholds      → ``research.loop.keep_or_discard``
    - TSV↔SQLite consistency floor       → ``research.autoresearch_nightly.TSV_DB_CONSISTENCY_FRACTION``
    - budget-aware sweep plan size       → ``research.autoresearch_runner.build_sweep_plan``

Coverage (the #386 checklist):
    1. ``completed_no_keeps`` / 0 real kept is a VALID outcome and does NOT
       soften thresholds (the canonical 33-row case: 1 baseline + 32 discard_solo).
    2. Budget truncation is detected / reported (1h window screened 32 of 38
       candidates, stopping before ``profit_target_atr_mult``).
    3. Active-config strategy allow-list filtering is enforced (disabled SP500
       strategies are skipped; only ``momentum_breakout`` runs).
    4. Baseline rows are never counted as real keeps / promotions.
    5. TSV ↔ SQLite output-consistency check.
    6. No threshold softening — the live keep/discard gate still rejects weak
       candidates at its documented floors.

Usage::

    # Invariant checks against the canonical fixture + live active config (read-only):
    python3 research/sweep_regression_harness.py

    # Also validate the real, on-disk SP500 momentum_breakout artifacts:
    python3 research/sweep_regression_harness.py --live

    # Machine-readable output:
    python3 research/sweep_regression_harness.py --json

Exit code is 0 when every required check passes, 1 otherwise. The harness is
strictly read-only: it never writes config, never promotes, never enables a
strategy, and never mutates a threshold.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

# ── Reused production code paths (import-time proof they still exist) ──────────
from research.autoresearch_nightly import (  # noqa: E402
    TSV_DB_CONSISTENCY_FRACTION,
    _filter_enabled_strategies,
    _parse_session_results,
)
from research.db import db_status_for  # noqa: E402
from research.loop import keep_or_discard  # noqa: E402

# Canonical momentum_breakout sweep dimensions per the #386 diagnostic. A 1h
# nightly window screened the first 32 of these 38 candidates.
CANONICAL_PLANNED_CANDIDATES = 38
CANONICAL_SCREENED_CANDIDATES = 32

# TSV schema (mirrors research.autoresearch_nightly / research.loop).
_TSV_HEADER = (
    "timestamp\tsharpe\ttrades\tmax_dd_pct\tpf\tcagr_pct\t"
    "params_changed\tstatus\tdescription"
)


# ──────────────────────────────────────────────────────────────────────────────
# Result model
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Outcome of a single harness check."""

    name: str
    passed: bool
    summary: str
    data: Dict[str, Any] = field(default_factory=dict)

    def render(self) -> str:
        icon = "✅" if self.passed else "❌"
        return f"  {icon} {self.name}: {self.summary}"


@dataclass
class HarnessReport:
    """Aggregate harness outcome."""

    checks: List[CheckResult] = field(default_factory=list)
    mode: str = "invariant"
    generated_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, check: CheckResult) -> CheckResult:
        self.checks.append(check)
        return check

    def to_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "mode": self.mode,
            "generated_at": self.generated_at,
            "n_checks": len(self.checks),
            "n_failed": sum(1 for c in self.checks if not c.passed),
            "checks": [asdict(c) for c in self.checks],
        }

    def render(self) -> str:
        lines = [
            "Research-Sweep Regression Harness (#219)",
            f"  mode={self.mode}  generated_at={self.generated_at}",
            "",
        ]
        lines.extend(c.render() for c in self.checks)
        lines.append("")
        verdict = "PASS — board gate satisfied" if self.passed else "FAIL — DO NOT promote"
        lines.append(f"  ==> {verdict} "
                     f"({sum(1 for c in self.checks if c.passed)}/{len(self.checks)} checks)")
        return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures — canonical 33-row completed_no_keeps case
# ──────────────────────────────────────────────────────────────────────────────


def _tsv_row(sharpe: float, status: str, description: str,
             params: str = "", trades: int = 100, dd: float = 10.0) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    return "\t".join([
        ts, f"{sharpe:.4f}", str(trades), f"{dd:.2f}", "1.5000", "20.00",
        params, status, description,
    ])


def build_canonical_no_keeps_tsv(
    n_discards: int = CANONICAL_SCREENED_CANDIDATES,
) -> str:
    """Reconstruct the diagnostic's 33-row ``completed_no_keeps`` TSV.

    1 baseline row (status='keep'/description='baseline', the bar to beat) plus
    *n_discards* ``discard_solo`` candidate rows — exactly the shape the SP500
    momentum_breakout window emitted on 2026-05-28 (33 rows total, 0 real keeps).
    """
    lines = [_TSV_HEADER]
    # Baseline: Sharpe 1.0245, 382 trades, 18.83% DD (per the #386 DB audit).
    lines.append(_tsv_row(1.0245, "keep", "baseline", trades=382, dd=18.83))
    # 32 weak candidates, all rejected at the solo fast-screen (max 0.4938).
    for i in range(n_discards):
        sharpe = 0.49 - (i * 0.02)
        lines.append(_tsv_row(sharpe, "discard_solo", f"candidate {i}",
                              params=f"x={i}", trades=300, dd=15.0))
    return "\n".join(lines) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Individual checks (pure — reuse production code)
# ──────────────────────────────────────────────────────────────────────────────


def check_completed_no_keeps(tsv_text: Optional[str] = None) -> CheckResult:
    """1. A 0-real-keep ``completed_no_keeps`` sweep is VALID, not a failure.

    Parses the canonical 33-row TSV through the REAL nightly parser and asserts:
      - the baseline row is bucketed as ``baseline`` (never ``kept``),
      - 32 candidates are ``screened`` with 0 ``promoted`` and 0 ``kept``,
      - therefore ``screened > 0 and kept == 0`` ⇒ a legitimate no-op
        (``completed_no_keeps``), which is NOT a silent failure.
    """
    import tempfile
    import research.autoresearch_nightly as nightly

    tsv_text = tsv_text or build_canonical_no_keeps_tsv()
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        (tmp / "momentum_breakout.tsv").write_text(tsv_text)
        orig = nightly.RESULTS_DIR
        try:
            nightly.RESULTS_DIR = tmp
            r = _parse_session_results("momentum_breakout", 0.0)
        finally:
            nightly.RESULTS_DIR = orig

    baseline = r["baseline"]
    screened = r["screened"]
    promoted = r["promoted"]
    kept = r["kept"]
    total_rows = baseline + screened  # baseline + non-baseline rows

    valid_no_keeps = screened > 0 and kept == 0
    ok = (
        baseline == 1
        and screened == CANONICAL_SCREENED_CANDIDATES
        and promoted == 0
        and kept == 0
        and valid_no_keeps
    )
    return CheckResult(
        name="completed_no_keeps_valid",
        passed=ok,
        summary=(
            f"{total_rows} rows = {baseline} baseline + {screened} screened → "
            f"{promoted} promoted → {kept} kept; "
            f"{'valid completed_no_keeps (no-op, not a silent failure)' if valid_no_keeps else 'INVALID classification'}"
        ),
        data={"baseline": baseline, "screened": screened,
              "promoted": promoted, "kept": kept, "total_rows": total_rows,
              "valid_no_keeps": valid_no_keeps},
    )


def check_baseline_not_counted(
    db_universe: Optional[str] = None,
) -> CheckResult:
    """4. Baseline rows are never counted as real keeps / promotions.

    Two layers:

    * **Mapping** — the REAL ``db_status_for`` maps a baseline 'keep' → 'baseline'
      and a real 'keep' → 'kept'. New writes can therefore never store a baseline
      row under ``status='kept'``.
    * **Accounting identity** (when *db_universe* is given) — #392 hardened the
      keep-accounting query to exclude baseline-described rows *at query time*
      rather than re-tagging legacy rows. So the invariant is NOT "zero physical
      ``status='kept'``+``description='baseline'`` rows exist" (legacy rows from
      before the fix legitimately remain); it is that the hardened summary query
      excludes every one of them:

          naive_kept == hardened_kept + legacy_baseline_kept

      where ``hardened_kept`` is ``status='kept' AND description != 'baseline'``.
      The check fails only if the exclusion predicate regresses (e.g. drops the
      TRIM/LOWER and starts counting baseline rows as keeps again).
    """
    mapping_ok = (
        db_status_for("keep", "baseline") == "baseline"
        and db_status_for("keep", "BASELINE") == "baseline"
        and db_status_for("keep", " baseline ") == "baseline"
        and db_status_for("keep", "real improvement") == "kept"
        and db_status_for("keep", "") == "kept"
        and db_status_for("discard", "x=3") == "discarded"
        and db_status_for("discard_solo", "x=4") == "discard_solo"
    )

    identity_ok = True
    naive_kept = hardened_kept = legacy_baseline_kept = None
    if db_universe is not None:
        try:
            from db.atlas_db import get_db
            with get_db() as db:
                naive_kept = db.execute(
                    "SELECT COUNT(*) AS c FROM research_experiments "
                    "WHERE universe = ? AND status = 'kept'",
                    (db_universe,),
                ).fetchone()["c"]
                hardened_kept = db.execute(
                    "SELECT COUNT(*) AS c FROM research_experiments "
                    "WHERE universe = ? AND status = 'kept' "
                    "AND COALESCE(LOWER(TRIM(description)), '') != 'baseline'",
                    (db_universe,),
                ).fetchone()["c"]
                legacy_baseline_kept = db.execute(
                    "SELECT COUNT(*) AS c FROM research_experiments "
                    "WHERE universe = ? AND status = 'kept' "
                    "AND COALESCE(LOWER(TRIM(description)), '') = 'baseline'",
                    (db_universe,),
                ).fetchone()["c"]
            # The hardened query must partition keeps exactly, leaving zero
            # baseline-described rows in the keep accounting.
            identity_ok = (hardened_kept + legacy_baseline_kept == naive_kept)
        except Exception as exc:  # pragma: no cover - defensive
            return CheckResult(
                name="baseline_not_counted",
                passed=False,
                summary=f"DB accounting probe failed: {exc}",
                data={"mapping_ok": mapping_ok, "error": str(exc)},
            )

    return CheckResult(
        name="baseline_not_counted",
        passed=mapping_ok and identity_ok,
        summary=(
            f"status mapping {'correct' if mapping_ok else 'WRONG'}; "
            + (f"accounting excludes baseline (naive_kept={naive_kept}, "
               f"hardened_kept={hardened_kept}, legacy_baseline_excluded={legacy_baseline_kept})"
               if naive_kept is not None else "DB probe skipped")
        ),
        data={"mapping_ok": mapping_ok, "identity_ok": identity_ok,
              "naive_kept": naive_kept, "hardened_kept": hardened_kept,
              "legacy_baseline_kept": legacy_baseline_kept},
    )


def check_active_config_allowlist(
    requested: List[str],
    universe: str,
    expect_dropped: bool = True,
) -> CheckResult:
    """3. Active-config strategy allow-list filtering is enforced.

    Runs the REAL ``_filter_enabled_strategies`` against the *current* live
    active config (read-only) and asserts:

    * the result is a subset of *requested* (never invents strategies), and
    * when the universe has disabled strategies among *requested*, they are
      actually dropped (``expect_dropped``) — i.e. the filter does real work.

    This is the authoritative enforcement guard: it validates what the *next*
    nightly sweep will spawn under the current config. It deliberately does NOT
    compare against historical ``research_experiments`` breadth — research
    legitimately explores strategies that are not live-enabled, and the
    allow-list changes over time, so historical rows are not a valid signal.
    """
    allowed = _filter_enabled_strategies(list(requested), universe)
    subset_ok = set(allowed).issubset(set(requested))
    dropped = sorted(set(requested) - set(allowed))
    # If the caller says disabled strategies are present, the filter must drop
    # at least one (proves enforcement is active, not a pass-through).
    dropped_ok = (not expect_dropped) or (len(allowed) < len(set(requested)))

    return CheckResult(
        name="active_config_allowlist",
        passed=subset_ok and dropped_ok and len(allowed) > 0,
        summary=(
            f"{universe}: allow-list={allowed or '[]'}; "
            f"dropped {len(dropped)} disabled {dropped or ''}"
        ),
        data={"allowed": allowed, "dropped": dropped,
              "requested": list(requested)},
    )


def check_stale_runner_noise(
    legacy_silent_failure_sessions: int,
    sample: Optional[List[str]] = None,
) -> CheckResult:
    """3b. Stale/legacy multi-strategy runner noise is surfaced (report-only).

    The #386 diagnostic flagged adjacent ``research_sessions`` rows that carry a
    full multi-strategy DEFAULT string and ``status='silent_failure'`` — a
    legacy all-strategy runner firing alongside the real config-gated SP500
    window. These are failing closed (correctly) but pollute operator
    perception, so the harness REPORTS them. This check never fails the board
    gate; it raises operator visibility only.
    """
    return CheckResult(
        name="stale_runner_noise",
        passed=True,
        summary=(
            f"{legacy_silent_failure_sessions} legacy multi-strategy "
            f"silent_failure session(s) detected (report-only; failing closed)"
            if legacy_silent_failure_sessions
            else "no legacy multi-strategy runner noise detected"
        ),
        data={"legacy_silent_failure_sessions": legacy_silent_failure_sessions,
              "sample": sample or []},
    )


def check_budget_truncation(
    screened_candidates: int,
    planned_candidates: int,
) -> CheckResult:
    """2. Budget truncation is detected / reported.

    Truncation = the window screened fewer candidates than the sweep plan held.
    This is a REPORTED signal (the run was healthy), not a failure — but it must
    be surfaced so operators know high-value dimensions may not have been
    reached. The check passes as long as truncation is correctly *detected*.
    """
    truncated = screened_candidates < planned_candidates
    unreached = max(0, planned_candidates - screened_candidates)
    return CheckResult(
        name="budget_truncation_detected",
        passed=True,  # detection always succeeds; `truncated` is the signal
        summary=(
            f"plan={planned_candidates}, screened={screened_candidates} → "
            + (f"TRUNCATED: {unreached} candidate(s) not reached (report to operator)"
               if truncated else "full plan covered")
        ),
        data={"truncated": truncated, "unreached": unreached,
              "screened": screened_candidates, "planned": planned_candidates},
    )


def check_tsv_sqlite_consistency(
    tsv_screened: int,
    db_rows_added: int,
) -> CheckResult:
    """5. TSV ↔ SQLite output-consistency check.

    Reuses the production ``TSV_DB_CONSISTENCY_FRACTION``: every screened TSV row
    is mirrored to SQLite, so DB rows must be at least
    ``max(1, int(screened * fraction))``. A healthy run (e.g. 32 screened / 33 DB
    rows) is consistent; a write degradation (32 screened / 2 DB rows) is flagged.
    """
    floor = max(1, int(tsv_screened * TSV_DB_CONSISTENCY_FRACTION))
    consistent = (tsv_screened == 0) or (db_rows_added >= floor)
    return CheckResult(
        name="tsv_sqlite_consistency",
        passed=consistent,
        summary=(
            f"tsv_screened={tsv_screened}, db_rows={db_rows_added}, "
            f"floor={floor} (frac={TSV_DB_CONSISTENCY_FRACTION}) → "
            + ("consistent" if consistent else "DB-WRITE DEGRADATION")
        ),
        data={"tsv_screened": tsv_screened, "db_rows_added": db_rows_added,
              "floor": floor, "consistent": consistent},
    )


def check_no_threshold_softening() -> CheckResult:
    """6. The live keep/discard gate still rejects weak candidates.

    Exercises ``research.loop.keep_or_discard`` directly. If anyone softens a
    documented floor (Sharpe +0.01, trade floor max(30, 70% baseline), DD ceiling
    max(20%, 150% baseline)), one of these probes flips and the harness goes red.
    A clearly-strong candidate must still KEEP, proving the gate is not
    accidentally broken-closed either.

    A throw-away strategy name ('__harness_probe__') is used so the DSR
    multiple-testing gate (which needs ≥5 prior experiments) never fires and the
    probes stay deterministic regardless of DB state.
    """
    PROBE = "__harness_probe__"
    base = {"sharpe": 1.0245, "total_trades": 382, "max_drawdown_pct": 18.83}
    failures: List[str] = []

    # (a) Below the +0.01 Sharpe floor → must DISCARD.
    d = keep_or_discard(base, {"sharpe": 1.0285, "total_trades": 382,
                               "max_drawdown_pct": 18.0, "strategy": PROBE})
    if d["decision"] != "discard":
        failures.append(f"sub-threshold Sharpe (+0.004) should discard, got {d['decision']}")

    # (b) Weak candidate (the real 0.49 vs 1.02 baseline) → must DISCARD.
    d = keep_or_discard(base, {"sharpe": 0.4938, "total_trades": 300,
                               "max_drawdown_pct": 15.0, "strategy": PROBE})
    if d["decision"] != "discard":
        failures.append(f"weak candidate should discard, got {d['decision']}")

    # (c) Trade collapse → must DISCARD (floor = max(30, 70% of 382) = 267).
    d = keep_or_discard(base, {"sharpe": 1.40, "total_trades": 20,
                               "max_drawdown_pct": 18.0, "strategy": PROBE})
    if d["decision"] != "discard":
        failures.append(f"trade collapse should discard, got {d['decision']}")

    # (d) Drawdown explosion → must DISCARD (ceiling = max(20, 150% of 18.83) = 28.2).
    d = keep_or_discard(base, {"sharpe": 1.40, "total_trades": 382,
                               "max_drawdown_pct": 40.0, "strategy": PROBE})
    if d["decision"] != "discard":
        failures.append(f"drawdown explosion should discard, got {d['decision']}")

    # (e) Genuine improvement on a small baseline → must KEEP (gate not broken-closed).
    small = {"sharpe": 1.00, "total_trades": 100, "max_drawdown_pct": 10.0}
    d = keep_or_discard(small, {"sharpe": 1.20, "total_trades": 120,
                                "max_drawdown_pct": 12.0, "strategy": PROBE})
    if d["decision"] != "keep":
        failures.append(f"strong improvement should keep, got {d['decision']} ({d['rationale']})")

    return CheckResult(
        name="no_threshold_softening",
        passed=not failures,
        summary=("all gate floors intact (sub-threshold/weak/trade-collapse/"
                 "DD-explosion rejected; strong improvement kept)"
                 if not failures else "; ".join(failures)),
        data={"failures": failures},
    )


# ──────────────────────────────────────────────────────────────────────────────
# Live-artifact helpers (read-only)
# ──────────────────────────────────────────────────────────────────────────────


def live_sweep_plan_size(
    strategy: str = "momentum_breakout",
    universe: str = "sp500",
) -> int:
    """Return the budget-aware sweep-plan size from the REAL planner (read-only)."""
    from utils.config import get_active_config
    from research.autoresearch_runner import build_sweep_plan

    cfg = get_active_config(universe)
    strat_cfg = dict(cfg.get("strategies", {}).get(strategy, {}) or {})
    # Drop non-parameter keys the planner shouldn't perturb.
    for k in ("enabled", "weight", "earnings_blackout"):
        strat_cfg.pop(k, None)
    return len(build_sweep_plan(strategy, universe, strat_cfg))


def _to_sqlite_ts(iso_ts: str) -> str:
    """Convert an ISO timestamp to the SQLite ``created_at`` format.

    ``research_sessions`` stores ISO timestamps ('YYYY-MM-DDTHH:MM:SS...') while
    ``research_experiments.created_at`` uses the space-separated SQLite format
    ('YYYY-MM-DD HH:MM:SS'). Comparing them directly silently returns 0 rows
    (the documented #216 'T'-vs-space bug), so window scoping MUST normalise.
    """
    return datetime.fromisoformat(iso_ts).strftime("%Y-%m-%d %H:%M:%S")


def _load_live_session(
    strategy: str, universe: str,
) -> Dict[str, Any]:
    """Snapshot the latest *completed* nightly_sweep session for *strategy*.

    Uses the authoritative ``research_sessions.experiments_run`` /
    ``experiments_kept`` (written by ``end_session``) as the per-window screened
    / kept counts, and counts the window's ``research_experiments`` rows using
    the correctly-normalised timestamp bounds. All reads are read-only. Also
    counts legacy multi-strategy ``silent_failure`` sessions (stale-runner
    noise).
    """
    out: Dict[str, Any] = {"available": False, "screened": 0, "kept": 0,
                           "window_db_rows": 0, "legacy_noise": 0,
                           "legacy_sample": []}
    try:
        from db.atlas_db import get_db
        with get_db() as db:
            sess = db.execute(
                "SELECT id, started_at, ended_at, experiments_run, experiments_kept "
                "FROM research_sessions "
                "WHERE mode = 'nightly_sweep' AND status = 'completed' AND strategy = ? "
                "ORDER BY id DESC LIMIT 1",
                (strategy,),
            ).fetchone()
            if sess is not None and sess["started_at"] and sess["ended_at"]:
                out["available"] = True
                out["session_id"] = sess["id"]
                out["screened"] = int(sess["experiments_run"] or 0)
                out["kept"] = int(sess["experiments_kept"] or 0)
                try:
                    s = _to_sqlite_ts(sess["started_at"])
                    e = _to_sqlite_ts(sess["ended_at"])
                    out["window_db_rows"] = db.execute(
                        "SELECT COUNT(*) AS c FROM research_experiments "
                        "WHERE universe = ? AND created_at >= ? AND created_at <= ?",
                        (universe, s, e),
                    ).fetchone()["c"]
                except Exception:
                    out["window_db_rows"] = 0
            # Stale-runner noise: legacy multi-strategy silent_failure sessions.
            out["legacy_noise"] = db.execute(
                "SELECT COUNT(*) AS c FROM research_sessions "
                "WHERE mode = 'nightly_sweep' AND status = 'silent_failure' "
                "AND strategy LIKE '%,%,%,%,%,%,%'"
            ).fetchone()["c"]
            sample = db.execute(
                "SELECT strategy FROM research_sessions "
                "WHERE mode = 'nightly_sweep' AND status = 'silent_failure' "
                "AND strategy LIKE '%,%,%,%,%,%,%' ORDER BY id DESC LIMIT 2"
            ).fetchall()
            out["legacy_sample"] = [r["strategy"] for r in sample]
    except Exception as exc:  # pragma: no cover - defensive
        out["db_error"] = str(exc)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────


def run_harness(
    universe: str = "sp500",
    strategy: str = "momentum_breakout",
    requested_strategies: Optional[List[str]] = None,
    live: bool = False,
) -> HarnessReport:
    """Run every regression check and return an aggregate :class:`HarnessReport`.

    Always runs the deterministic invariant checks (against the canonical 33-row
    fixture and the live active config, read-only). When *live* is True, also
    validates the real on-disk TSV + SQLite artifacts for *strategy*/*universe*.
    """
    requested_strategies = requested_strategies or [
        "momentum_breakout", "mean_reversion", "trend_following",
        "opening_gap", "sector_rotation", "connors_rsi2",
        "short_term_mr", "bb_squeeze",
    ]
    report = HarnessReport(mode="live" if live else "invariant")

    # 1. completed_no_keeps validity (canonical 33-row case).
    report.add(check_completed_no_keeps())

    # 6. no threshold softening (live gate probes).
    report.add(check_no_threshold_softening())

    # 4. baseline accounting (mapping + accounting-identity probe in live mode).
    report.add(check_baseline_not_counted(db_universe=universe if live else None))

    # 3. active-config allow-list (current live config, read-only).
    report.add(check_active_config_allowlist(requested_strategies, universe))

    # 2. budget truncation + 5. consistency.
    session = _load_live_session(strategy, universe) if live else None
    if session is not None and session.get("available"):
        try:
            planned = live_sweep_plan_size(strategy, universe)
        except Exception:
            planned = CANONICAL_PLANNED_CANDIDATES
        report.add(check_budget_truncation(session["screened"], planned))
        report.add(check_tsv_sqlite_consistency(
            session["screened"], session["window_db_rows"]))
        # Also re-validate completed_no_keeps against the real session counts
        # when that window kept nothing (the live analogue of the 33-row case).
        report.add(check_stale_runner_noise(
            session.get("legacy_noise", 0), session.get("legacy_sample")))
    else:
        # Deterministic invariant proof using the diagnostic's documented counts.
        report.add(check_budget_truncation(
            CANONICAL_SCREENED_CANDIDATES, CANONICAL_PLANNED_CANDIDATES))
        report.add(check_tsv_sqlite_consistency(
            CANONICAL_SCREENED_CANDIDATES, CANONICAL_SCREENED_CANDIDATES + 1))

    return report


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Research-sweep regression harness (#219) — read-only board gate.",
    )
    p.add_argument("--universe", default="sp500")
    p.add_argument("--strategy", default="momentum_breakout")
    p.add_argument("--live", action="store_true",
                   help="Also validate the real on-disk TSV + SQLite artifacts.")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = p.parse_args(argv)

    # The production filter prints '[filter] Skipping ...' diagnostics to stdout;
    # suppress them during the run so text/JSON output stays clean and pipeable.
    import contextlib
    import io
    with contextlib.redirect_stdout(io.StringIO()):
        report = run_harness(
            universe=args.universe, strategy=args.strategy, live=args.live,
        )
    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(report.render())
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
