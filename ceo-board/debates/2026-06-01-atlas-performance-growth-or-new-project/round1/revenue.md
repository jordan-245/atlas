## Vote: HYBRID

### Top Recommendation
Complete the 3 blocking Atlas infrastructure tasks that unlock live approval, simultaneously identify one validated additive SP500 strategy, and run a 1-week scoping sprint on a fast-feedback parallel project (prediction markets or sports forecasting). Do not let Atlas infrastructure churn consume all engineering cycles while momentum_breakout generates zero alpha.

---

### Revenue Lens Reasoning

**The core problem isn't Atlas infrastructure — it's that the only live strategy is exhausted.** Momentum_breakout at 0/32 promotion signal means the current system is generating $0 in forward alpha. Meanwhile, $1.37K AUM means even a 30% annual Sharpe-1.0 strategy returns ~$410/year. At this capital level, Atlas's primary value is **as a validated framework**, not as a return engine yet.

The real revenue question: **what's the fastest path to either (a) a strategy that scales with capital, or (b) an adjacent revenue stream that compounds the infrastructure investment?**

The recent research trust fixes (#392-#396) are actually the most underappreciated development. A working research pipeline is a durable asset. The 0/32 signal might be pipeline noise, not genuine strategy exhaustion — but we can't know until the regression harness (#219) validates it. That's the critical unlock.

**Parallel project case:** Prediction markets and sports forecasting both have lower capital requirements, faster feedback loops (days not months), and publicly verifiable edges. NRL-Predict already exists — if it has validated model lift, that's a revenue-ready asset we're not monetizing.

---

### Explicit Stop / Deprioritize List

- ❌ **Universe expansion** — low-ROI until approval=true; adds operational overhead with no near-term return
- ❌ **SQLite sole-writer cutover (#267)** — pure infrastructure, zero alpha impact; defer past June 30
- ❌ **Reconcile-script retirement (#276)** — same category; schedule not urgent
- ❌ **Tuning momentum_breakout further** — if #219 confirms exhaustion post-pipeline-fix, declare it archived and move on
- ❌ **Overlay complexity expansion** — stay in log-only mode until #215 review complete; no feature additions

---

### 7-Day Actions I Would Fund

| Day | Action | Expected Output |
|-----|--------|----------------|
| 1-2 | Close #354 (stale SP500 tests) | Unblock research validation pipeline |
| 2-3 | Ship regression harness #219 | Binary: is momentum_breakout genuinely exhausted or pipeline noise? |
| 3-4 | Sizing analysis #387 | Fractional Kelly = multiplicative alpha on existing positions, lowest-risk return lever |
| 4-5 | Identify 2 candidate additive strategies (mean-reversion, sector-rotation) with preliminary OOS stats | Research direction clarity |
| 5-7 | **1-week scoping sprint**: assess NRL-Predict model accuracy + prediction market API access | Go/no-go signal on parallel project |

---

### Main Risks / Gates

- **Risk:** Splitting bandwidth across Atlas + new project stalls both. **Gate:** New project sprint is **scoping only** — no build until regression harness confirms momentum_breakout is genuinely exhausted (if it's a pipeline bug, Atlas strategy backlog is intact).
- **Risk:** Sizing improvements (#387) create false confidence — fractional Kelly amplifies losses too. **Gate:** Backtest sizing on 2024-2025 drawdown periods before any config change.
- **Risk:** NRL/prediction market has no monetizable edge. **Gate:** Require >55% accuracy on held-out data before committing any engineering cycles beyond the scoping sprint.

**Bottom line:** The $283 realized PnL proves the infrastructure works. The exhausted strategy proves we need more alpha sources. Fund both tracks at 70/30 Atlas-hardening/new-project scoping — but make the regression harness the week's critical path.
