# Scripts Triage Inventory — Simplification Sprint Seed

**Date:** 2026-05-26
**Task:** #363
**Status:** Inventory complete; first safe no-reference archive pass committed separately after repo hygiene.

## Rule

Keep `scripts/` entries that are active cron/systemd entrypoints, imported by production code/tests, or current operator runbooks. Move one-off audit/backfill/fix scripts only after confirming no cron, systemd, import, test, or runbook reference.

## Active cron entrypoints

From the installed crontab / `scripts/atlas.crontab`:

- `scripts/sync_protective_orders.py`
- `scripts/healthcheck_tp_coverage.py`
- `scripts/healthz_hourly.sh`
- `scripts/intraday_monitor.py`
- `scripts/healthcheck_pipelines.py`
- `scripts/compute_daily_risk.py`
- `scripts/backfill_hourly_bars.py`
- `scripts/execute_approved.py`
- `scripts/pi-cron.sh`
- `scripts/monitor_same_bar_stops.py`
- `scripts/reconcile_positions.py`
- `scripts/reconcile_ledger.py`
- `scripts/verify_dual_write.py`
- `scripts/sync_broker_orders.py`
- `scripts/sync_paper_orders.py`
- `scripts/healthcheck_paper_executor.py`
- `scripts/cleanup_research_locks.sh`
- `scripts/cleanup_stale_plans.py`
- `scripts/check_macro_freshness.py`
- `scripts/check_live_research_divergence.py`
- `scripts/verify_weekly_health_reports.py`
- `scripts/paper_progress_cli.py`
- `scripts/cleanup_sediment.py`
- `scripts/check_doc_staleness.py`

## Active systemd / service entrypoints

- `ops/backup-all-projects.sh`
- `core/error_monitor.py`
- `core/orchestrator` module
- `scripts/check_fred_health.py`
- `scripts/run_consolidation_closure.sh` (inactive one-shot; keep until consolidation audit closes)
- `scripts/reconcile_shadow.py`
- `scripts/sandbox_9_strategies.sh`
- `scripts/director_cron.py`
- `scripts/precompute_risk.py`
- `scripts/post_sweep_canary_check.py`
- `scripts/rebuild_universe.py`
- `scripts/research_window_universe.sh`
- `scripts/heartbeat_watchdog.py`
- `scripts/silent_failure_watchdog.py`
- `scripts/backfill_intraday_5min.py` (staged timer, not enabled; keep for #316)

## Keep because imported/tested/documented as current tooling

- `scripts/auto_promote_paper_to_live.py`
- `scripts/claude_auth_check.py`
- `scripts/lint_bare_except.py`
- `scripts/lint_pi_system_prompt.py`
- `scripts/validate_oos.py`
- `scripts/research_promote.py`
- `scripts/research_runner.py`
- `scripts/strategy_evaluator.py`
- `scripts/data_integrity_monitor.py`
- `scripts/validate_state_universes.py`
- `scripts/run_graduation_engine.py`
- `scripts/healthz_error_remediation.py`
- `scripts/promote_auto_fix_staging.py`
- `scripts/validate_classifier_30day.py`
- `scripts/cron_stderr_capture.sh`
- `scripts/git-hooks/**`
- `scripts/migrations/**` (schema/history; archive only after migration registry policy exists)

## Archived in first safe pass

These had no live cron/systemd/import/test/runbook references outside their own docstrings at move time. They were moved to `scripts/tools/archive/2026-05-repo-reset/`:

- `scripts/tools/archive/2026-05-repo-reset/audit_equity_history_dual_write.py`
- `scripts/tools/archive/2026-05-repo-reset/audit_overlay_decisions.py`
- `scripts/tools/archive/2026-05-repo-reset/backtest_allocation_compare.py`
- `scripts/tools/archive/2026-05-repo-reset/backtest_mean_reversion_guard_comparison.py`
- `scripts/tools/archive/2026-05-repo-reset/investigate_same_bar_round_trips.py`

## Archive candidates for #363 follow-up

These names match one-off prefixes but still have tests/docs/migration references or need a narrower manual check before moving:

- `scripts/audit_duplicate_trades.py` — migration message reference
- `scripts/audit_health_source_changes.py` — audit doc reference
- `scripts/audit_per_market_equity.py` — data audit doc labels as daily sanity audit
- `scripts/audit_promotion_backlog.py` — tested by canary/research-best tests
- `scripts/audit_promotion_integrity.py` — consumed by `research/integrity.py`
- `scripts/audit_state_order_id_collisions.py` — cleanup-plan reference
- `scripts/backfill_cat_stop_price.py` — explicit regression test requires file presence
- `scripts/backfill_oos_metrics_research_best.py` — strategy-promotion docs + tests
- `scripts/backfill_regime_research_best.py` — tests
- `scripts/backfill_strategy_lifecycle.py` — tests
- `scripts/backfill_stub_closed_trades.py` — no source refs except this doc; verify whether still useful before moving
- `scripts/backtest_overlay_phase3a.py` — schema/runbook reference
- `scripts/backtest_same_bar_mitigation_comparison.py` — active same-bar decision context
- `scripts/dedupe_overlay_decisions.py` — operator tool; no live cron ref
- `scripts/find_dashboard_json_writer.py` — no source refs except this doc; likely next archive candidate
- `scripts/fix_equity_history_divergences_2026-05-14.py` — tests
- `scripts/fix_ledger_sync.py` — historical chat/data reference only; likely next archive candidate after ignoring runtime chat refs
- `scripts/forensic_chtr_fills.py` — tests
- `scripts/investigate_discovery.py` — tests document root cause there
- `scripts/review_vision_ab.py` — tests and chart-vision audit docs
- `scripts/seed_asx_equity.py` — tests instruct operator to run it
- `scripts/trigger_commodity_promotion.py` — referenced by sandbox summary script

## Next action

After #362 is committed and the tree is clean, run a narrow #363 move pass:

1. For each candidate: `rg -n "<script_name>" scripts systemd docs tests services brokers core config`.
2. Move confirmed one-offs to `scripts/tools/archive/2026-05-repo-reset/` or external `/var/atlas/repo-reset-20260526/` depending on whether they are useful historical code.
3. Keep migrations and live runbook scripts in place unless a migration registry replaces path-based references.
4. Run `python3 scripts/git-hooks/check_no_runtime_artifacts.py --all-tracked`, relevant unit tests, and `python3 scripts/cli.py status`.
