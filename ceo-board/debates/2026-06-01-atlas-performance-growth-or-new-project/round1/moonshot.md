## Vote: HYBRID

**Top Recommendation:** Freeze Atlas strategy tuning. Run Atlas critical-path maintenance on autopilot. Launch a 2-week prediction market research sprint as a parallel skunkworks — zero capital risk, fast feedback, directly reuses Atlas's regime-detection and signal-aggregation infrastructure.

---

### Reasoning (Moonshot Lens)

The 0/32 SP500 promotion signal isn't a bug — it's the market telling you that momentum_breakout alpha has been arbitraged away. Spending another month finding "additive SP500 strategies" in the same regime framework is the definition of optimizing a dying strategy. That's not caution, that's slow-motion surrender.

But here's the contrarian read nobody's saying: **Atlas's infrastructure is the actual moat, not the SP500 trade.** You've built a regime model, a signal aggregation overlay, a research loop, and a validation harness — all general-purpose probabilistic tools. The SP500 trading angle is just one application. And it's the one with the most friction: approval gates, broker dependencies, slow feedback cycles, and $1.37K capital that makes every sizing decision meaningless.

**What has 10x more leverage right now?**

Prediction markets (Polymarket, Kalshi, Manifold). Here's why this is the asymmetric bet:
- Faster feedback loops (hours, not weeks)
- No broker approval gates, no live-approval=false problem
- Genuine market inefficiencies — crowd vs. Bayesian regime model
- Atlas's regime overlay is *directly* applicable (macro state → probability adjustment)
- Capital requirements are tiny; Sharpe ratios in skilled prediction market play can exceed 2.0
- We already have NRL infrastructure — sports prediction is a 1-week pivot, not a new project

The NRL angle is even closer: we have the forecasting skeleton. The question is whether the edge is there. A 2-week research sprint with real NRL match data would tell us. That's faster than any SP500 strategy validation cycle.

---

### Explicit Stop / Deprioritize List

❌ **Stop:** Tuning momentum_breakout or searching for substitute SP500 strategies until overlay gate #215 closes — this is low-signal churn  
❌ **Stop:** Universe expansion (XLE, UNG, etc.) — governance overhead with no demonstrated edge  
❌ **Deprioritize:** #267 SQLite sole-writer, #276 reconcile scripts — pure infrastructure maintenance, delegate or defer 2 weeks  
❌ **Deprioritize:** #354 stale SP500 tests — fix only if they block the regression harness  

---

### 7-Day Actions I Would Fund

| Day | Action | Why |
|-----|--------|-----|
| 1-2 | Close overlay log-only review #215 — accept or reject, no more deferring | Gate is blocking everything downstream |
| 1-2 | Prediction market research spike: map Atlas regime signals → Polymarket/Kalshi event categories | Validate the thesis before committing |
| 3-4 | NRL model audit: what's the current accuracy, what data is missing for a betting-calibrated edge? | NRL infrastructure exists — exploit it |
| 3-5 | Regression harness #219 — automate, then stop manually babysitting research runs | This unlocks safe strategy iteration |
| 5-7 | Decision checkpoint: if prediction market or NRL spike shows >0.55 accuracy on held-out data, formalize as Project 2 | Fast kill/continue signal |

---

### Main Risks / Gates

⚠️ **Bandwidth trap:** Hybrid only works if Atlas maintenance is truly minimal. If #215/#219 balloon, kill the prediction market sprint immediately — don't split attention three ways.

⚠️ **Prediction market liquidity:** Edge exists but markets can be illiquid at scale. Start with pure research (no capital) and validate signal before any live play.

⚠️ **Shiny object risk:** NRL + prediction markets are only worth it if the research shows genuine edge in week 1. Set a hard kill criterion: <0.53 accuracy on OOS data → abandon.

**The moonshot thesis:** Atlas's regime model applied to prediction markets could outperform Atlas's own SP500 trading within 60 days. That's the 10x path. The 10% path is another SP500 strategy.
