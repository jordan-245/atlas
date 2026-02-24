---
name: atlas-reoptimize
description: Orchestrate Atlas-ASX degradation checks, re-optimization, validation, and config promotion decisions with human approval. Use when performance degrades, after data refreshes, or when testing candidate parameter updates.
---

# Atlas Reoptimize

Use this skill for the full optimize-and-validate lifecycle.

## Preferred workflow

1. `atlas_jobs_run(job=health_check)`
2. If degraded, `atlas_jobs_run(job=reoptimize_full_universe)`
3. `atlas_jobs_run(job=validate_oos)`
4. Compare candidate vs active metrics from validation artifacts (use artifact tools when available)
5. Ask for explicit approval before any config promotion or rollback action

## Guardrails

- Do not overwrite `config/active_config.json` directly from a heuristic summary.
- Treat `auto_reoptimize` as high-risk until path handling and promotion criteria are standardized.
- Preserve backups and record exact artifact paths used in the decision.

## Current artifact expectations (Atlas repo)

- Health report: `logs/health_check_YYYY-MM-DD.json`
- Reoptimization report: `backtest/results/reoptimization_full_universe.json`
- OOS validation report: `backtest/results/v92_oos_validation.json`

## Known codebase constraints

- Many optimization/validation scripts still use hard-coded `/a0/usr/projects/atlas-asx` paths.
- Path normalization should be completed before relying on unattended execution.
