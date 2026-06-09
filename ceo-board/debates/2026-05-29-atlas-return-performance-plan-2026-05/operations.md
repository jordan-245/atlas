## Position
**Vote: CONDITIONAL**
Atlas can increase returns, but the research engine is producing zero promotions from 32 variants — fix that diagnostic gap before adding any new levers.

## Arguments

- **Research is broken before strategy.** Zero promotions from 32 screened variants is an operational red flag, not a strategy gap. Either thresholds are miscalibrated or the variant space is genuinely exhausted. We cannot know which until the regression harness (#219) exists. Layering new strategies on top of a research loop with unknown behavior is operationally irresponsible.
- **Overlay is the cheapest lever, already in flight.** #215 log-only review is the lowest-lift, highest-readiness return lever. The data is accumulating now. All we need is a review protocol and a go/no-go meeting. This should be closed in 2 weeks, not left open-ended.
- **SQLite cutover (#267/#276) is a hidden operational dependency.** Dual-write bridges are active complexity that adds failure surface. Any config promotion or new strategy activation on top of unsettled plumbing is asking for attribution and debugging nightmares.
- **Universe expansion operationally failed once already.** XLE/UNG PAPER entries leaked into SP500 plans. That's not a theory — it happened. The team's capacity to manage multi-universe coordination safely has not been demonstrated. Don't reopen that surface until SP500 is fully stable.
- **Position sizing is underrated.** The simplest lever with no new strategy risk: if momentum_breakout has positive expectancy, sizing up within the current risk envelope is a free action. This requires no research, no promotion gates, just a config parameter review.

## Required Gates

- **#219 regression harness complete** before any new strategy variant is promoted — no exceptions, zero-promotion pattern must be explained
- **#215 overlay log-only review completed** with documented signal quality assessment before overlay activation
- **SQLite cutover (#267/#276) shadow validation passed** before introducing new config complexity
- **OOS walk-forward + drawdown gate** for any new strategy: Sharpe ≥ 0.6, max DD ≤ 15%, profit factor ≥ 1.2 on held-out period
- **Human approval required** for any live config change until approval=false is changed by deliberate policy decision, not drift

## Roadmap Recommendations

- **Days 0-30:** Diagnose the zero-promotion problem (threshold audit, parameter space review, log inspection). Complete #215 overlay review with a formal go/no-go memo. Close #219 regression harness. No new strategies, no config changes. Run position sizing analysis within current config — this is the only zero-risk return lever.
- **Days 30-60:** If overlay review is green, activate overlay with tightened position limits. Promote 1–2 momentum_breakout variants if the regression harness validates them. Complete SQLite shadow validation. Begin SP500 strategy #2 design (not live — sandbox only).
- **Days 60-90:** If dual-write bridges are retired and SP500 is stable, promote SP500 strategy #2 to paper. If paper passes 30-day real-time validation, gate review for live. Universe expansion remains off the table unless operational load from SP500 multi-strategy is demonstrably manageable.

## No-go Items

- **No universe expansion** until SP500 runs multiple live strategies cleanly for 60+ days
- **No research activation** (director/runner services) until #219 harness exists and zero-promotion root cause is documented
- **No config promotion without OOS gate** — the current approval=false setting is not a green light for skipping validation
- **No simultaneous multi-axis changes** — changing strategy + overlay + sizing at the same time makes attribution impossible; sequence strictly
- **No "discovery" or paper ingest reactivation** without scoped failure bounds defined upfront — the previous silent failure mode is not acceptable operationally