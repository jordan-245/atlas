## MOONSHOT — Final Position

**Vote: CONDITIONAL_ACCEPT (Hybrid with Hard Gates)**

---

### Final Resource Allocation — Next 7 Days

| Track | Allocation | What It Funds |
|-------|-----------|---------------|
| Atlas critical-path closure | **60%** | #219 harness, #354 tests, #215 overlay review completion |
| Prediction market / NRL research spike | **25%** | Zero-infrastructure signal research only — no build, no capital |
| Sizing analysis #387 | **15%** | Fractional-Kelly scoping (harness-gated, paper only) |

No budget for #267/#276 this week. Defer to week 2 unless something breaks.

---

### Exact Gates / Kill Criteria

**Research spike kill switch:** If the prediction market / NRL spike doesn't show >0.55 OOS accuracy signal within 5 days of work — kill it cleanly. No second chances. No "let's run another cycle."

**Atlas strategy gate:** Do not begin additive strategy work (#388) until regression harness (#219) ships AND post-repair research sweep shows ≥1 non-zero promotion. If still 0/32 after harness validates, momentum_breakout is officially dead — declare it and pivot 100% to parallel track.

**Sizing gate:** No fractional-Kelly config changes until harness exists. Paper-mode analysis only this week.

**#215 overlay gate:** Non-negotiable. Two-week log-only window must complete before anything downstream changes.

---

### Concession to Operations

Operations is right that the research repair (#392-#396) needs a confidence burn-in before any strategy decision. I'm conceding the sequencing: **the regression harness ships before the prediction market spike gets more than 1 day of attention.** If #219 isn't done by day 3, the parallel spike pauses and all bandwidth goes back to Atlas critical-path. The hybrid only works if the critical path doesn't slip.

---

### Why I'm Still Holding the Hybrid Line

The 0/32 SP500 signal is the board's most important data point — not a noise artifact to debug away. Even if the pipeline repair resolves to 1/32 or 2/32, that's a signal quality crisis, not a pipeline bug. The prediction market spike costs 2 days of structured research and reuses existing regime infrastructure. The upside is asymmetric: either we find genuine edge in a faster-feedback market, or we rule it out cheaply and double down on Atlas with confidence. That's a call option worth buying with 25% of one week.

Atlas-only thinking optimizes for not losing. The hybrid path optimizes for finding the next leverage point before we need it.

**The 7-day binary:** By day 7, we know (1) whether Atlas research produces trustworthy signals post-repair, and (2) whether prediction markets show exploitable signal. That's two resolved uncertainties for the price of one week. That's the right bet.
