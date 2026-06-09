# Research-Sweep Regression Harness — Task #219

**Date:** 2026-06-01
**Status:** complete — board gate guard is green.
**Scope:** A focused, executable regression harness that validates the *outputs
and invariants* of the nightly autoresearch sweep without touching live trading,
config, or thresholds. This is the gate the board required before any new live
strategy / sizing promotion (#387/#388).

## What it guards

The harness reuses the **production code paths** (never reimplements them), so a
regression in the live sweep code turns the harness red. Coverage maps 1:1 to
the #386 diagnostic checklist
(`docs/project-notes/research-promotion-diagnostic-2026-05-29.md`):

| Check | Invariant | Reused production code |
|-------|-----------|------------------------|
| `completed_no_keeps_valid` | The canonical 33-row case (1 baseline + 32 `discard_solo`) is a VALID `completed_no_keeps` no-op, not a failure | `research.autoresearch_nightly._parse_session_results` |
| `no_threshold_softening` | The keep/discard gate still rejects sub-threshold (+0.01 Sharpe floor), weak, trade-collapse, and DD-explosion candidates — and still keeps a genuine improvement | `research.loop.keep_or_discard` |
| `baseline_not_counted` | Baseline rows map to `baseline` (never `kept`); the hardened keep-accounting query excludes legacy `status='kept'`/`description='baseline'` rows (identity: `naive_kept == hardened_kept + legacy_baseline`) | `research.db.db_status_for` + accounting query |
| `active_config_allowlist` | Disabled SP500 strategies are dropped; only `momentum_breakout` runs under the current active config | `research.autoresearch_nightly._filter_enabled_strategies` |
| `budget_truncation_detected` | A window that screened fewer candidates than the sweep plan is detected/reported (1h window: 32 of 38) | `research.autoresearch_runner.build_sweep_plan` |
| `tsv_sqlite_consistency` | DB rows mirror TSV screened count above the production floor; a write degradation (32 screened / 2 DB rows) is flagged | `research.autoresearch_nightly.TSV_DB_CONSISTENCY_FRACTION` |
| `stale_runner_noise` *(live only)* | Legacy multi-strategy `silent_failure` sessions are surfaced (report-only) | `research_sessions` query |

## How to run

```bash
# Deterministic invariant checks (canonical 33-row fixture + live active config, read-only).
# This is the CI / pre-promotion gate. Exit 0 = green.
python3 research/sweep_regression_harness.py

# Also validate the REAL on-disk artifacts (latest completed SP500 window +
# SQLite accounting + stale-runner noise). Read-only.
python3 research/sweep_regression_harness.py --live

# Machine-readable (pipeable to jq); filter diagnostics are suppressed.
python3 research/sweep_regression_harness.py --live --json
```

Focused tests:

```bash
python3 -m pytest tests/test_research_sweep_regression_harness.py \
                  tests/test_research_workflow_audit_392.py -q
```

## Design notes (why it is sound, not flaky)

- **Read-only by construction.** The harness never writes config, never promotes,
  never enables a strategy, never mutates a threshold. It only reads the active
  config, TSV files, and SQLite.
- **Allow-list enforcement is validated at the code layer**, against the *current*
  active config — i.e. what the *next* sweep will spawn. It deliberately does
  NOT compare against historical `research_experiments` breadth: research
  legitimately explores strategies that are not live-enabled, and the allow-list
  changes over time, so historical rows are not a valid leak signal.
- **Baseline accounting follows the #392 contract.** #392 excludes baseline rows
  at the *query* layer rather than re-tagging legacy rows. The production SP500
  DB still contains ~945 legacy `status='kept'`/`description='baseline'` rows;
  the harness asserts the hardened query excludes them, not that they are
  physically absent.
- **Budget truncation and consistency are session-scoped in live mode.** They use
  the authoritative `research_sessions.experiments_run` / `experiments_kept`
  (written by `end_session`) and count the window's `research_experiments` rows
  with correctly-normalised timestamps (the documented #216 ISO-`T` vs SQLite-
  space mismatch is handled by `_to_sqlite_ts`). Lifetime totals are NOT used.

## Current live result (2026-06-01)

`--live` is green, 7/7:

- completed_no_keeps valid (33-row canonical case).
- gate floors intact (no threshold softening).
- baseline accounting: `naive_kept=1293, hardened_kept=348, legacy_baseline_excluded=945`.
- allow-list: `['momentum_breakout']`; 7 disabled strategies dropped.
- latest completed SP500 window (session 225) screened 38/38 — full plan (no
  truncation; budget-aware ordering from #390 is working).
- consistency: 38 screened / 39 DB rows.
- stale-runner noise: 66 legacy multi-strategy `silent_failure` sessions (report-only).

## Operator guidance

- Run the **invariant** mode (no `--live`) as the pre-promotion gate. If it is not
  green, **do not promote** any strategy/sizing change — a sweep invariant has
  regressed.
- Run `--live` before promotions to confirm the most recent real window is
  healthy and to see the stale-runner noise count.
- A red `no_threshold_softening` check means a keep/discard floor was weakened —
  treat as a hard block and investigate `research/loop.py`.
- A red `tsv_sqlite_consistency` check means `log_experiment` write degradation —
  the sweep ran but DB rows are far below the TSV output.
- `budget_truncation_detected` is **report-only**: truncation is not a failure,
  but it tells operators that high-value parameter dimensions may not have been
  reached in the window.
