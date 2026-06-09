## Operations Position — Extend Atlas (Hardening First) / HYBRID Deferred

**Vote: EXTEND_ATLAS** (with explicit stop list and no new project until Atlas is stable)

---

### Top Recommendation
Close the open critical/high Atlas work first — this is blocking everything else. The research pipeline was *just* repaired (#392-#396). We don't yet know if it produces reliable signals. Starting a new project or expanding Atlas strategies before the repair is validated would be building on unverified ground.

---

### Reasoning (Operations Lens)

The team has five open critical/high tasks plus a freshly-repaired research pipeline that hasn't run a complete validated cycle. This is not a position from which to safely expand scope — either within Atlas or into a new project.

**Bandwidth math is simple:** If the team has ~10 effective engineering days in the next 2 weeks, and #215 (overlay log-only review), #219 (regression harness), #354 (stale tests), and #267/#276 (SQLite cutover) each cost 1-2 days to close properly — that's 6-8 days consumed before any return-expansion work begins. A parallel new project would cannibalize the remaining days and both initiatives would ship half-baked.

**The research repair (#392-#396) needs a confidence burn-in.** Running the full nightly research loop once or twice after the fix is not enough to trust signal quality. We need to see at least 3-5 research cycles with non-zero promotions before making strategy decisions based on outputs. Otherwise sizing or strategy additions are tuned against garbage.

---

### Explicit Stop / Deprioritize List

- ❌ **Universe expansion** — no new universes until SQLite isolation gates pass (#267/#276)
- ❌ **Overlay live promotion** — approval=false, don't touch until #215 closes positively
- ❌ **New project initiation** — do not start until Atlas open critical/high backlog is under 2 items
- ❌ **Strategy parameter tuning** — exhausted signal + unvalidated research = noise chasing
- ❌ **Simultaneous strategy + sizing changes** — board red line, operationally unacceptable

---

### 7-Day Actions I Would Fund

| Day | Action | Owner Dependency |
|-----|--------|-----------------|
| 1-2 | Close #354 (stale SP500 tests) — clear, bounded, unblocks CI trust | Solo |
| 1-2 | Run 3 nightly research cycles post-repair; log promotion counts | Automated |
| 2-3 | Close #219 (regression harness) — needed before any strategy change | Solo |
| 3-4 | #215 overlay log-only: compile 2-week signal log, formal review | Review session |
| 4-5 | #267/#276 SQLite cutover + reconcile retirement (scope-contained) | Solo |
| 5-6 | *Only if* research shows non-zero promotions: begin #387 sizing analysis | Research-gated |
| 6-7 | Decision gate: evaluate new project feasibility based on Atlas state | CEO |

---

### Main Risks / Gates

**Risk 1 — Research repair doesn't hold.** If nightly runs still produce 0/32 kept after #392-#396, sizing and strategy work are premature. Gate: require ≥2 non-zero promotion cycles before #387/#388.

**Risk 2 — Open tasks cascade.** #267/#276 SQLite cutover is operationally complex; if it slips, it blocks Phase 5 and drags engineering time. Gate: time-box to 3 days; escalate if blocked.

**Risk 3 — New project FOMO pulls focus.** Starting a prediction-market or sports-forecasting project while Atlas is unstable creates two half-functioning systems. Gate: no new project kick-off until Atlas critical/high backlog ≤2 items AND one research promotion cycle validated.

**Risk 4 — Small capital limits signal.** With ~$1.37K live equity, statistical validation of strategy additions will take months of live trading. OOS backtest validation must substitute — do not skip it to move faster.
