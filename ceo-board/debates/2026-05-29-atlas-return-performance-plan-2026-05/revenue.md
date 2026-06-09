## Position
**Vote: CONDITIONAL**
The growth path is clear — the biggest revenue leak isn't missing strategies, it's a built-but-idle overlay and a research pipeline promoting zero variants. Fix the pipeline first, activate the overlay second, then scale.

---

## Arguments

- **The 0/32 research promotion rate is the #1 revenue signal.** Either the search space is wrong, the gates are miscalibrated, or both. Every nightly cycle that promotes nothing is dead compute — fix this before adding more strategies.
- **Overlay log-only is pure opportunity cost.** It's already built and validated architecturally. Two weeks of log-only data should be enough to assess directional value. The longer it sits idle, the more alpha we're leaving on the table.
- **Single-strategy concentration is a return drag.** Momentum_breakout is regime-dependent. In mean-reverting or low-volatility regimes, it underperforms. Even one complementary SP500 strategy (mean-reversion, vol-breakout) would smooth the equity curve and improve Sharpe — which directly enables future capital deployment at scale.
- **Position sizing is an underrated lever.** With max positions at 10 and presumably equal weighting, we're not optimizing capital allocation. A Kelly-lite or risk-parity layer on top of existing strategies could increase risk-adjusted returns without touching the strategy layer at all.
- **Universe expansion is a distraction right now.** The recent cross-universe leakage bugs prove the infra isn't ready. Multi-universe = multi-failure modes. SP500 is a $44T market — there's plenty of return here before we need to go elsewhere.

---

## Required Gates

- **Research pipeline fix before any new strategy promotion:** Must demonstrate ≥1 promoted variant in 3 consecutive nightly runs with a clear audit trail of why variants pass/fail gate thresholds
- **Overlay activation gate:** Requires 14-day log-only period showing positive signal correlation (not just "not wrong"), documented edge cases where overlay would have helped/hurt, and explicit human approval sign-off
- **New SP500 strategy gate:** Walk-forward OOS Sharpe ≥0.6, max DD ≤15%, profit factor ≥1.3 — same as current gates but must also show regime-diversification benefit (i.e., correlation with momentum_breakout returns < 0.7)
- **Position sizing changes:** Require backtested evidence of improvement in risk-adjusted returns, not just raw returns; any Kelly-based sizing must cap at 50% full Kelly to prevent ruin scenarios

---

## Roadmap Recommendations

- **Days 0–30 (Fix the Revenue Engine):** Diagnose and fix the 0/32 research promotion failure — is it gate thresholds, search space, or data quality? Run regression harness (#219) to establish baseline. Complete overlay log-only review (#215) and generate go/no-go recommendation. No new strategies, no universe expansion.

- **Days 30–60 (Activate What's Built):** Activate overlay if gate passes — this is the highest-ROI move because the build cost is already sunk. Promote first research-validated SP500 variant if gates are met. Implement basic position sizing optimization (risk-parity or volatility-scaled weights) within existing max-positions constraint. Track incremental Sharpe and realized PnL delta vs. baseline.

- **Days 60–90 (Expand Safely):** If overlay is active and performing, add one complementary SP500 strategy (mean-reversion or vol-breakout) with full OOS validation. Begin SQLite cutover (#267/#276) to clean up the data layer — this is a prerequisite for reliable multi-strategy tracking. Consider modest capital increase if risk metrics hold.

---

## No-Go Items

- **No universe expansion until cross-universe infra is proven for ≥60 days clean** — the recent XLE/UNG leakage bugs are too fresh
- **No disabling approval gates** on config promotion, even if it feels like friction — the $1.3K portfolio is the test bed for a larger capital deployment, and one bad promotion at scale is catastrophic
- **No "boost returns" by loosening drawdown limits** — max DD ≤15% is a constraint, not a negotiating position; violating it destroys the risk profile needed for scaling
- **No parallel research runs without the regression harness in place** — silent failures are worse than no research
- **No overlay activation without the 14-day log-only data** — I'm growth-oriented but I won't bet on a black box