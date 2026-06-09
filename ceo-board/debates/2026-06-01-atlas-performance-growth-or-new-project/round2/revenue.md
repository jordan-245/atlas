## Revenue Final Position

**Vote: CONDITIONAL_ACCEPT (Hybrid — Atlas-First with Time-Boxed Parallel Spike)**

---

### Revenue Allocation — Next 7 Days

| Track | % Time | What Gets Done |
|-------|--------|----------------|
| Atlas Infrastructure | 50% | #219 regression harness, #354 stale tests — these unlock safe alpha iteration |
| Atlas Return Expansion | 30% | #387 fractional-Kelly sizing (highest revenue-per-engineering-hour inside Atlas) |
| Parallel Scoping | 20% | 1-day NRL-Predict accuracy audit + 1-day prediction market API feasibility — no build |

**Concession to Operations:** I'm dropping my push for #267/#276 deferral. Operations is right that dual-write creates silent data risk that corrupts any P&L attribution we try to validate. Begin SQLite cutover in week 2, not this week.

---

### Revenue Reasoning (Final)

The $283 realized PnL is real signal: the infrastructure works. The exhausted momentum_breakout is a strategy problem, not a system problem. Fractional-Kelly sizing on *any* functioning strategy compounds returns multiplicatively at zero incremental strategy risk — that's the fastest path to higher dollar returns on existing capital.

The 0/32 promotion signal has two possible causes: genuine exhaustion or pipeline noise. The regression harness (#219) resolves this binary in 2-3 days. **Every other decision gates on that answer.** If pipeline noise → existing strategy backlog may be intact. If genuine exhaustion → we need an additive strategy *and* the parallel spike becomes higher priority.

At $1.37K AUM, Atlas's primary value right now is **infrastructure equity**, not trading returns. The parallel scoping sprint isn't about replacing Atlas — it's about identifying whether we have a *second* monetizable application of the regime-detection and signal-aggregation machinery we've already built. Two days of scoping is not a bandwidth risk; it's an options position.

---

### Exact Gates / Kill Criteria

| Gate | Threshold | Consequence |
|------|-----------|-------------|
| Research pipeline post-repair | ≥1 non-zero promotion in 3 cycles | If still 0/32 → stop all strategy work, diagnose pipeline |
| Regression harness (#219) | Shipped before any sizing change goes live | No harness = no sizing merge, period |
| Fractional-Kelly backtest | Positive Sharpe on 2024-2025 drawdown periods | Fail → park #387, don't force it |
| NRL accuracy audit | >55% OOS accuracy | Below threshold → kill parallel track, no further cycles |
| Prediction market scoping | Identifiable liquid market + Atlas signal overlap in 1 day | No fit found → defer indefinitely |
| New project build gate | Atlas #215 + #219 both closed | Hard stop — no build starts before both close |

---

### Bottom Line

Fractional-Kelly is the only revenue lever with near-zero downside risk available this week. Fund it. The parallel spike is 2 days of optionality, not a commitment. The regression harness is the week's critical path — everything else is conditional on what it tells us.
