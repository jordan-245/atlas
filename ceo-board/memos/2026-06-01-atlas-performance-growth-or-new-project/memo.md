# Decision Memo: Should We Extend Atlas for Higher Returns or Start a New Project?

**Date**: 2026-06-01  
**Decision**: CONDITIONAL ACCEPT — Atlas-first hybrid  
**Confidence**: High  
**Vote**: 5 for, 0 against, 0 abstain

## Executive Summary

The board unanimously recommends a conditional hybrid: keep Atlas as the primary return-improvement vehicle this week, but buy a small, strictly research-only option on a new prediction-market/NRL edge project. Do not increase live exposure, promote configs, expand universes, or continue tuning `momentum_breakout`. The critical path is to prove the repaired Atlas research system is trustworthy, then evaluate fractional-Kelly sizing and one additive strategy only behind gates.

## Context

Atlas has useful infrastructure but currently small live capital (~$1.37K SP500 equity) and live approval remains false. The live SP500 config has one strategy, `momentum_breakout`, and recent nightly research repeatedly produced no kept promotions. In the last cycle, the research/knowledge plumbing was repaired (#392-#396), so the next decision must distinguish genuine alpha exhaustion from previously broken measurement.

The board treated capital scaling as off the table. The question was engineering allocation: extend Atlas, start a new project, or both.

## Board Positions

### Revenue — CONDITIONAL ACCEPT
Revenue supports a hybrid because Atlas alone cannot produce meaningful dollar returns at current AUM unless it finds scalable alpha. The highest near-term Atlas return lever is fractional-Kelly sizing, but only after the regression harness validates the research pipeline. Revenue also supports a short scoping sprint for prediction markets or NRL because the infrastructure may have monetizable uses outside SP500 trading.

### Risk — CONDITIONAL ACCEPT
Risk accepts only if the new-project work is zero-capital and research-only. The risk ordering is strict: #219 regression harness first, post-repair research baseline second, sizing third. If #219 slips past Day 3, the new-project spike is killed and bandwidth returns to Atlas recovery. No threshold softening, live approval changes, config promotion, or exposure increase.

### Technical — CONDITIONAL ACCEPT
Technical agrees the hybrid is feasible if no new infrastructure is created. The technical priority is #219, one post-repair research sweep, #354 stale tests, and then #387 sizing in paper/backtest mode. Technical argues universe expansion remains blocked by SQLite isolation/dual-write cleanup and that the new project should be a one-day signal/API feasibility check, not a build.

### Moonshot — CONDITIONAL ACCEPT
Moonshot argues the biggest upside may be applying Atlas's regime/signal machinery to faster-feedback markets such as prediction markets or NRL, where validation cycles are shorter and capital requirements are lower. Moonshot concedes the Atlas harness must ship first and that the spike must pause if critical-path Atlas work slips.

### Operations — CONDITIONAL ACCEPT
Operations initially preferred Atlas-only, but accepts a bounded research spike because it does not touch production code, config, or live capital. Operations insists the harness is the critical path and that any new project build is forbidden until #215 closes and the Atlas critical/high backlog is reduced.

## Decision Rationale

The board's key distinction is between **research optionality** and **execution commitment**. Starting a new project build now would be premature and likely fragment engineering focus. But spending 10-20% of a week on a zero-infrastructure research spike creates useful information at low cost.

Atlas remains the primary system because its infrastructure is already built and recent repairs may unlock trustworthy research again. However, the board is explicit that `momentum_breakout` parameter churn should stop. The next Atlas return work must be either risk-adjusted sizing (#387) or genuinely additive, OOS-validated strategy work (#388), not more tuning of the same exhausted signal.

## Approved 7-Day Allocation

1. **60-65% — Atlas critical path**
   - Ship #219 regression harness.
   - Fix/close #354 stale SP500 tests.
   - Compile/finish #215 overlay log-only review.
   - Run clean post-repair research baselines.

2. **15-25% — Atlas return work, gated**
   - Scope/backtest #387 fractional-Kelly or volatility-target sizing.
   - Paper/backtest only; no live config change.
   - Do not start additive strategy promotion unless harness + post-repair research evidence are clean.

3. **10-20% — New-project research spike only**
   - Compare prediction-market and NRL opportunities.
   - Check API/data access, market liquidity, held-out model signal, and whether Atlas regime features add edge.
   - No database, no deployment, no capital, no trading.

## Gates and Kill Criteria

- **#219 gate**: regression harness must ship by Day 3. If not, pause/kill the new-project spike and reallocate to Atlas.
- **Research baseline gate**: if post-repair sweeps still show 0 kept/non-zero promotions, stop strategy/sizing promotion work and diagnose the pipeline or declare current signal exhausted.
- **Sizing gate**: #387 cannot merge/promote without harness validation and 2024-2025 drawdown-period backtests.
- **Overlay gate**: #215 must close before any live approval or overlay behavior change.
- **New-project gate**: no build unless the spike shows clear edge potential, e.g. >55% OOS accuracy or a liquid market with identifiable Atlas-signal overlap.
- **Hard red lines**: no live config promotion, no approval=false flip, no exposure increase, no threshold softening, no simultaneous strategy+sizing+overlay changes.

## Concrete Next Steps

1. Make #219 the week's primary task.
2. Close #354 or at least remove stale-test false confidence blocking research validation.
3. Run and record post-repair research baselines.
4. Start #387 sizing analysis only after the harness is green.
5. Run a bounded research spike comparing prediction markets vs NRL as a possible Project 2.
6. Revisit in 7 days with: harness status, research promotion counts, sizing backtest results, and spike findings.

## Review Date

**2026-06-08**, or earlier if #219 slips past Day 3 or the research spike finds a strong/weak signal requiring immediate kill/commit decision.
