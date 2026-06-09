## Position
**Vote: CONDITIONAL**
Atlas is running a single vanilla momentum strategy on $1.3K capital with a 0/32 promotion rate — we're not optimizing a system, we're optimizing a rounding error. The real question isn't "how do we tune momentum_breakout" — it's "why does our AI overlay exist at all if it's been log-only for weeks?"

---

## Arguments

- **The 0/32 promotion rate is the loudest signal in this brief.** That's not a healthy gate — that's a search space that's either exhausted, misconfigured, or selecting for the wrong fitness function. Tuning parameters on a strategy class that can't promote a single variant in 32 tries is incrementalism on a dead end.

- **The AI overlay is the actual moonshot, and it's been benched.** Log-only validation is reasonable for two weeks — but the brief says #215 is still open with no completion date. The overlay is the differentiator. Every day it's passive is a day of forgone alpha and learning signal. Activate it with conservative position-level constraints, not indefinite deferral.

- **Small capital is the time to learn aggressively, not conservatively.** At $1.3K, a 30% drawdown is $400. That's tuition money. The architecture is supposed to scale — but you can't validate a scaling architecture by never stress-testing it. Run real experiments now while the stakes are low.

- **Position sizing is the highest-leverage, lowest-risk lever and it's not even mentioned.** A single momentum strategy with max 10 positions but no volatility-scaled sizing is leaving risk-adjusted returns on the table by default. Kelly-fractional or volatility-targeting sizing changes expected CAGR without adding a single new strategy.

- **Multi-strategy SP500 (not universe expansion) is the asymmetric bet within constraints.** Mean reversion + momentum is not a novel idea, but running them simultaneously with regime-aware allocation weights is a defensible step-change, not a moonshot. The existing infrastructure already supports it.

---

## Required Gates

- **Overlay activation gate**: 10 trading days of log-only with documented divergence analysis (not just "no errors") before conditional live activation with ≤25% position size cap per overlay signal
- **Promotion criteria audit**: Before running another research sweep, audit *why* 0/32 were promoted — is the threshold miscalibrated or is the strategy class genuinely tapped out? This audit is a prerequisite, not a follow-up
- **Position sizing backtest**: Any sizing change must show walk-forward Sharpe improvement with drawdown within ±5% of current before live application
- **Second strategy gate**: OOS validation on a non-overlapping sample, minimum Sharpe ≥ 0.5, profit factor ≥ 1.3, before SP500 second strategy promotion

---

## Roadmap Recommendations

- **Days 0-30**: (1) Audit the 0/32 promotion failure — determine if search space, fitness function, or strategy class is the bottleneck. (2) Implement volatility-targeted position sizing on existing momentum_breakout — highest leverage, lowest risk. (3) Complete overlay log-only review and set a hard activation date; log-only without an exit date is a permanent state.

- **Days 30-60**: (1) Activate overlay with position-level tightening only, 25% cap, monitor vs. log-only baseline. (2) Screen one contrarian strategy class for SP500 (mean reversion or stat-arb on SP500 components) — full OOS gate before touching live config. (3) Research harness (#219) as infrastructure, not optional.

- **Days 60-90**: (1) If overlay shows net positive: remove the cap and let it run fully. (2) If second strategy passes OOS: promote to live alongside momentum_breakout with regime-aware allocation. (3) Decide on universe expansion based on evidence, not calendar.

---

## No-go Items

- **Do not run another 32-variant momentum_breakout sweep without first diagnosing why the last one produced zero promotions.** That's compute waste and false confidence.
- **Do not defer overlay activation past Day 30 without a documented reason.** "Still validating" is not a reason after two weeks of clean logs.
- **Do not expand universes before the second SP500 strategy is live and stable.** The recent cross-universe leakage failures are a direct warning against jumping the sequencing.
- **Do not treat SQLite cutover as a prerequisite for return improvements.** Infrastructure and alpha research are parallel tracks — conflating them serializes what should be concurrent.
- **Do not optimize for zero promotions as a sign of safety.** A research system that never promotes anything isn't conservative — it's broken.