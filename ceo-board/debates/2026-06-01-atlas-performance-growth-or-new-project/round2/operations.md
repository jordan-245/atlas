## Operations Final Position

**Vote: CONDITIONAL_ACCEPT**

---

### Resource Allocation — Next 7 Days

| Work Stream | % Engineering Time | Concrete Tasks |
|---|---|---|
| Atlas infrastructure closure | 55% | #219 harness, #354 tests, post-repair research cycle validation |
| Atlas return expansion (gated) | 25% | #387 fractional-Kelly sizing — only after harness ships |
| New project scoping | 20% | Research spike only — no build, no infra |

No changes to #267/#276 timeline — defer to Week 2 once the three higher-priority items close.

---

### Exact Gates / Kill Criteria

**Before any sizing or strategy work begins:**
- #219 regression harness must be merged and green
- At least 2 post-repair research cycles must show non-zero promotion counts

**Before any new project gets engineering cycles beyond the 20% spike:**
- #215 overlay review must close (accept or reject — no more deferring)
- Atlas critical/high backlog must be ≤2 open items

**Kill the new-project spike at Day 4 if:**
- No clear signal differentiation from existing Atlas signals, OR
- #219/#354 are slipping and consuming the 20% allocation

**Hard no-touch list regardless of anything else:**
- Live config promotion
- Approval=false flip
- Threshold softening
- Capital scaling

---

### Concession to Moonshot/Hybrid View

I was wrong to defer the new-project spike entirely until Atlas stabilizes. The 20% allocation for a no-build research spike is operationally safe — it doesn't touch the same files, doesn't create merge conflicts, and won't disrupt the harness work. If it surfaces a clear edge signal in 2 days, we have the decision data we need without having committed build time. I accept this as a parallel track provided it stays strictly research-mode.

---

### Bottom Line

The sequencing risk is real: three teams want to run in parallel, but one engineer can't safely ship a regression harness *and* fractional-Kelly sizing *and* a new-project build simultaneously. The 20% scoping spike is fine. Anything beyond scoping before #219 ships is not. The harness is the critical path — everything gates on it.
