---
id: 2026-05-29-atlas-return-performance-plan-2026-05
title: "How Should Atlas Increase Returns and Operational Performance Without Breaking Risk Discipline?"
created: 2026-05-29T03:45:32.279Z
profile: standard

---

# How Should Atlas Increase Returns and Operational Performance Without Breaking Risk Discipline?

## Situation

Atlas is live on SP500 v3.2.4 with one active strategy, momentum_breakout, and current reported portfolio value around $1,344 with +$283 realized PnL. The active config is live, approval is currently false, max positions 10, and non-SP500 active configs have recently been archived/disabled to stop cross-universe leakage. Nightly SP500 research still runs at 23:00 AEST; the latest run screened 32 momentum_breakout variants with 0 promoted/kept, while broader discovery/paper ingest is disabled and research services are intentionally stopped except the scheduled research window. Recent work fixed fail-closed regime behavior, SP500 plan hygiene, overlay log_only semantics, and cron maintenance. Major open items include overlay log-only review (#215), research sweep regression harness (#219), SQLite cutover (#267/#276), and stale SP500 phase2 tests (#354). The user wants a plan to increase returns and performance.

## Stakes

The upside is materially better capital efficiency: higher CAGR/returns, more robust Sharpe, better use of the current Atlas research engine, and eventually scalable live capital deployment. The downside is overfitting, larger drawdowns, execution drift, broken live-plan safety, and promotion of variants that look good in-sample but fail out-of-sample. Even though current live capital is small (~$1.3K portfolio), the architecture is intended to scale, so mistakes in risk policy or config promotion could have >$1K expected impact once capital is increased. There is also opportunity cost: only one live SP500 strategy may leave return sources unused, but rushing multi-universe or overlay activation could reintroduce recently fixed failures.

## Constraints

Must preserve live-trading safety: no config promotion without gate checks, OOS validation, and human approval where required. Keep live scope SP500 unless/until a deliberate universe expansion is validated. Avoid strategy overfitting; require walk-forward/OOS evidence and drawdown controls. Atlas runs on an 8-core VPS; long-running backtests/research should use parallel/headless workflows. All LLM calls must route through Claude Max OAuth with --system-prompt. Current priority backlog includes overlay review, research regression harness, and SQLite cutover, so plan must sequence around operational stability and not break active cron/live workflows.

## Key Questions

1. 1. Which return levers should Atlas prioritize first: better parameter search for the existing live strategy, reactivating additional SP500 strategies, AI overlay activation, universe expansion, or position sizing/risk changes?
2. 2. What validation gates should be mandatory before increasing exposure or promoting new strategies/configs?
3. 3. How should Atlas balance higher returns against drawdown, turnover, execution costs, and operational complexity?
4. 4. What 30/60/90-day implementation roadmap should we follow, including concrete experiments and stop/go criteria?
5. 5. What should be explicitly ruled out for now to avoid repeating recent failures, especially cross-universe leakage and research silent failures?
