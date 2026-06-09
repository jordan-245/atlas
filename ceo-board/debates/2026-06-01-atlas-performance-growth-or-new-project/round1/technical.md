## Vote: HYBRID

### Top Recommendation
Close the open infrastructure debt first (#219, #267, #354), then add fractional-Kelly sizing as the highest-leverage return lever inside Atlas. Run one additive SP500 strategy research cycle only after the repaired pipeline (#392-#396) proves it produces non-zero signals. In parallel, greenlight a *low-infrastructure* new project that reuses existing tech.

---

### Reasoning (Technical Lens)

**Atlas is not ready to expand, but is close.**

The research pipeline repairs (#392-#396) are days old. We don't yet know if they resolved the 0/32 promotion problem or simply fixed the plumbing — the pipe may still be empty. Building new strategies on top of an untested pipeline wastes engineering cycles. The correct move is: run one research sweep, inspect the signal count, validate against known-good baselines, *then* proceed.

The three open infrastructure tasks are not optional housekeeping — they are active technical risk:
- **#267 (dual-write)**: Two codepaths writing state creates race conditions and silent data divergence. Every day this is open, debugging complexity compounds.
- **#219 (regression harness)**: Without it, any strategy or sizing change is unverified. We're flying without instruments.
- **#354 (stale tests)**: Green CI with stale tests is worse than red CI — it creates false confidence.

**Fractional-Kelly sizing (#387)** is the highest-leverage technical change available: it's arithmetic applied to existing position logic, zero new data dependencies, self-contained test surface. It compounds on whatever strategy signal we have without touching the research or overlay layers.

**Universe expansion is blocked.** SQLite isolation gates (#267, #276) must pass first. Expanding to new universes while dual-write bridges are live is a data integrity risk we cannot accept.

---

### Explicit Stop / Deprioritize

| Item | Why |
|------|-----|
| Universe expansion | Blocked on #267/#276 SQLite gates |
| Live config promotion | Blocked on #215 overlay review completion |
| Threshold softening / signal hacking | Red line per board guidance |
| New project with new tech stack | No infrastructure leverage, high ramp cost |
| NRL/sports expansion | Existing NRL-Predict handles this; don't fork attention |
| External data integrations | Pipeline trust not yet re-established |

---

### 7-Day Actions I Would Fund

1. **Days 1-2**: Ship #219 regression harness. This unlocks safe iteration on everything else.
2. **Days 1-2** (parallel): Run one full research sweep and measure signal count post-repair. If still 0/32 → stop, diagnose before proceeding.
3. **Days 2-4**: Implement fractional-Kelly sizing with harness validation. No live promotion yet.
4. **Days 3-5**: Close #354 stale SP500 tests. Unblock CI trust.
5. **Days 4-6**: Begin #267 SQLite sole-writer cutover with shadow validation.
6. **Days 5-7**: If research sweep shows >0 promotable signals, scope one additive strategy (OOS + correlation gate required before any promotion).

**New project**: Greenlight a prediction-market or credibility-intelligence *research spike only* — no production infrastructure, no new data pipeline. 1-2 days of exploration to assess signal quality. If signal quality is clear in that window, scope a Phase 1. Otherwise park it.

---

### Main Risks / Gates

- **Research pipeline still broken**: #392-#396 fixes are unvalidated. If sweep still produces 0/32, stop strategy work entirely until root cause is clear.
- **Sizing without harness**: Do not merge fractional-Kelly until #219 regression harness exists. Untested sizing math on live capital is unacceptable.
- **Scope creep**: Hybrid path is only safe if the new project stays in research-spike mode. Any new project that needs its own database, broker integration, or deployment pipeline is too expensive right now.
- **Gate**: Overlay #215 log-only review window must complete before live approval changes — no exceptions.
