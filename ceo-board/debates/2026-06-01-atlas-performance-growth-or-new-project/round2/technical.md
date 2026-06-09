## Technical Final Position

**Vote: CONDITIONAL_ACCEPT**

The hybrid path is technically sound *only if* the new project stays off infrastructure. My conditions haven't changed — they've sharpened.

---

### Resource Allocation (7 Days)

| Track | % | Rationale |
|-------|---|-----------|
| #219 Regression harness | 25% | Gate for everything else — ship first |
| Research sweep validation post-#392-#396 | 10% | Must run before any strategy decision |
| #354 Stale SP500 tests | 15% | False-green CI is worse than no CI |
| Fractional-Kelly sizing (#387) | 20% | Highest-leverage return lever; zero new infra |
| #267 SQLite sole-writer (start, not finish) | 20% | Begin shadow validation; time-box 3 days |
| New project research spike | 10% | 1 day max; no build, no infra, no data pipeline |

---

### Exact Gates / Kill Criteria

1. **Research sweep gate**: Run one full sweep post-#392-#396. If still 0/32 → stop all strategy work, diagnose pipeline before any other allocation shifts.

2. **Sizing gate**: Fractional-Kelly does not merge without #219 harness green. Untested sizing math on live capital is a hard no.

3. **New project kill criterion**: The 1-day research spike produces no clear signal edge (no data API access, no obvious inefficiency vs. Atlas regime model) → park indefinitely. No scope creep past the spike.

4. **#267 time-box**: 3 days. If shadow validation reveals unexpected complexity → defer remainder to Phase 5 schedule, do not let it consume the week.

5. **#215 is immovable**: Overlay log-only review must complete its window. No live promotion signals of any kind until it closes.

---

### Concession to Operations

Operations is right that I'm being optimistic about the new project spike. My concession: **the 10% new-project allocation only unlocks if #219 ships by Day 3.** If the regression harness slips, those hours fold back into #267 cutover. No research spike runs in parallel with a broken harness — that's the one Operations condition I'm accepting fully. Infrastructure integrity before exploration, not alongside it.

The fractional-Kelly sizing is the one return lever I'll defend against Operations pushback: it's arithmetic, self-contained, and the test surface is narrow. It does not require a validated new strategy or a clean overlay review. It compounds on whatever signal we already have. That's the right technical bet for this week.

**Bottom line**: Ship the harness. Validate the pipeline. Size correctly. Spike the new project in 1 day, then decide. Everything else is noise until those three gates close.
