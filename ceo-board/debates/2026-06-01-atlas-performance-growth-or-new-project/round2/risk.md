## Risk Final Position

**Vote: CONDITIONAL_ACCEPT**
Condition: parallel new-project work is research-only, zero-capital, and auto-kills if Atlas harness (#219) slips past Day 3.

---

### Resource Allocation — Next 7 Days

| Bucket | % | What |
|--------|---|------|
| Atlas critical-path | 65% | #219 regression harness, #354 stale tests, #215 log-only review compilation, post-repair research baseline |
| Atlas sizing analysis | 15% | #387 fractional-Kelly — paper mode only, harness must exist first |
| New project research spike | 15% | Prediction-market or NRL signal feasibility — read-only, no infra build |
| Buffer / incident response | 5% | Any research pipeline re-break after #392–#396 |

**Hard rule:** If #219 harness is not shippable by Day 3, the 15% new-project allocation folds immediately into Atlas recovery.

---

### Exact Gates / Kill Criteria

| Gate | Threshold | Action if Missed |
|------|-----------|-----------------|
| Post-repair research baseline | ≥1 non-zero promotion in first clean sweep | Stop all strategy/sizing work; diagnose pipeline before proceeding |
| #219 regression harness | Shipped by Day 3 | Kill new-project spike; reallocate |
| #215 overlay review | 14-day log compiled and reviewed | No live approval change of any kind before this closes |
| New project research spike | OOS accuracy >55% on held-out data by Day 7 | Abandon — no Phase 1 build |
| Sizing (#387) | Backtested through 2024–2025 drawdown periods before any config touch | Hard stop — no live sizing change without this |
| Bandwidth | Atlas critical/high backlog drops to ≤2 open | Only then can new-project build begin |

**Non-negotiables carried forward:** approval=false, no threshold softening, no simultaneous strategy+sizing+overlay changes, no live-capital exposure in any new project.

---

### One Concession to Operations

Operations is right that the research pipeline burn-in period matters and that three validated cycles should precede strategy decisions — not just one. I'll concede: the post-repair baseline requirement should be **≥2 non-zero promotion cycles** before #387 sizing or #388 strategy work begins, not just one clean sweep. One cycle could be noise; two establishes a pattern. This costs at most 48 hours and prevents sizing changes tuned against a single potentially-anomalous data point.

---

**Bottom line:** The risk-adjusted path is harness first, baseline second, sizing third — in that strict order. The parallel new-project spike is permissible only because it is genuinely zero-capital and auto-terminates if it competes with the critical path. Any sign of bandwidth compression and it dies immediately. The alternative — another month of unvalidated Atlas expansion — is the higher expected-loss scenario.
