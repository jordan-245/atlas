## Position
**Vote: CONDITIONAL**
Returns cannot reliably improve until the research pipeline's zero-promotion signal is diagnosed and the test/validation infrastructure is trustworthy enough to gate promotion decisions.

## Arguments

- **The 0/32 promotion rate is a diagnostic, not a success.** Either the search space is wrong, the promotion gates are miscalibrated, or both. Without #219 (regression harness), we cannot distinguish "engine correctly rejected weak variants" from "engine is broken and silently discarding good candidates." Running more experiments on a broken pipeline burns compute and produces noise.

- **Dual-write bridges (#267/#276) are active technical debt with data integrity risk.** Any return improvement that depends on accurate historical data or reliable plan state is undermined while SQLite isn't the sole writer. This should block any expanded strategy promotion, not just dashboard cutover.

- **Overlay (#215) is the highest-return lever already in flight.** It's in log-only. The cost of completing the 2-week review is low; the cost of activating it before the review is complete is potential live-trade interference. Don't shortcut this — it's 1–2 weeks from being actionable.

- **Position sizing is underexplored and low-risk.** Fractional Kelly or volatility-scaled sizing on momentum_breakout requires no new strategy, no config promotion, and no universe expansion. It's a well-understood lever with bounded downside.

- **Multi-strategy SP500 is feasible but sequencing matters.** A second SP500 strategy (e.g., mean-reversion or breakout-confirm) can be validated in parallel with overlay, but cannot be promoted until OOS gates and the regression harness are functional.

## Required Gates

- **#219 merged and green** before any new strategy promotion — must be able to reproduce and audit backtest results deterministically.
- **#354 stale phase2 tests fixed** before any parameter changes to momentum_breakout — stale tests mean we can't catch regressions.
- **Full OOS walk-forward** (minimum 6-month hold-out, no in-sample optimization on hold-out period) required for any promoted variant.
- **SQLite sole-writer** (#267/#276 complete) before universe re-expansion — no cross-universe leakage risk while dual-write bridges exist.
- **Human approval gate re-enabled** in active config before live capital scales above $5K.

## Roadmap Recommendations

- **Days 0–30:** Ship #219 (regression harness) + #354 (stale tests). Complete overlay log-only review (#215) — document signal accuracy, false positive rate, and net PnL impact. Audit why 0/32 variants promoted: is the screening threshold correctly calibrated? Fix or widen the SP500 search space if the gate is over-fit to current parameters. Implement volatility-scaled position sizing as a low-risk return lever.

- **Days 30–60:** Execute SQLite cutover (#267/#276) with 7-day shadow validation. With regression harness live, run a clean SP500 strategy sweep — target 3–5 genuine candidates for OOS validation. If overlay log-only review is positive, plan activation with conservative tighten-only bounds. Begin walk-forward validation of one additional SP500 strategy.

- **Days 60–90:** Promote second SP500 strategy if OOS validation clears. Activate overlay if 2-week review + regression harness confirms net positive. Universe expansion (commodities/bonds) enters research queue but not live — requires its own OOS gate and does not share config with SP500. Review position sizing results and tune.

## No-go Items

- **No universe re-expansion** until SQLite is sole writer and regression harness exists. Cross-universe leakage was recently fixed; reintroducing multi-universe without those foundations repeats the failure.
- **No overlay activation** before #215 log-only review is formally closed with documented verdict.
- **No parameter changes to live momentum_breakout** without #354 fixed — can't catch regressions.
- **No disabling fail-closed regime behavior** under any framing of "improving returns" — that's a safety property, not a performance knob.
- **No research experiments promoted** while 0/32 root cause is undiagnosed — running more sweeps before fixing the pipeline is waste.
- **No `approval: false` on live config once capital exceeds current scale** — the current setting is only acceptable at ~$1.3K where manual oversight is cheap.