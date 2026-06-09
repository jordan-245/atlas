## Vote: HYBRID (Atlas Hardening First, Controlled Parallel Exploration)

---

### Top Recommendation
**Do not extend Atlas return-seeking work until #215 and #219 close.** Research trust was just repaired — we have zero validated signal yet. The 0/32 promotion result could mean strategy exhaustion *or* a pipeline that was broken and is now recovering. We cannot tell the difference without a clean regression baseline. Run the harness first; then decide if momentum_breakout is truly exhausted.

---

### Reasoning from Risk Lens

**The single biggest risk I see:** We just fixed research infrastructure (#392–#396) but have not run a clean validation cycle post-fix. Any strategy decision made on pre-repair research output is noise, not signal. Promoting a new strategy, expanding universes, or tuning sizing on top of unvalidated research is compounding unverified assumptions — exactly the failure mode that burns weeks.

**Infrastructure debt is a quiet risk multiplier.** Three open criticals/highs (#267 SQLite sole-writer, #276 reconcile scripts, #354 stale tests) mean the foundation is still dual-write and partially untested. Adding return expansion on a partially-migrated data layer creates hidden failure modes that are hard to trace post-incident.

**Overlay log-only review (#215) is a hard gate.** This is the AI tightening layer. Until it demonstrates net-positive behavior over two weeks, we have no empirical basis to trust it in any expanded configuration. Extending Atlas scope while this review is incomplete is flying partially blind.

**New project risks at this moment:**
- Engineering bandwidth is already stretched across 6 open high-priority tasks
- Any new project with live data/capital (prediction markets, sports betting) adds new regulatory surface area without existing mitigation structure
- A credibility/alpha intelligence or SaaS project avoids capital risk but still costs focus

---

### Explicit Stop / Deprioritize List

| Item | Reason to Stop |
|---|---|
| Universe expansion | Blocked — SQLite sole-writer (#267) not closed; data integrity risk |
| Overlay live promotion | Blocked — #215 log-only review not complete |
| New strategy promotion without OOS gate | Pre-repair research outputs are untrusted baselines |
| Starting any live-capital new project | Bandwidth insufficient; Atlas gates still open |
| Threshold softening of any kind | Red line; non-negotiable |

---

### 7-Day Actions I Would Fund

1. **Day 1–2**: Run clean research cycle post-#392–#396 repair. Produce one verified baseline promotion report. This disambiguates exhaustion vs. pipeline failure.
2. **Day 2–3**: Close regression harness #219 — minimum viable version. Any sizing or strategy change without this is unauditable.
3. **Day 3–4**: Close stale SP500 tests #354. Tests that lie are worse than no tests.
4. **Day 4–5**: Complete #267 SQLite sole-writer cutover. This removes an entire class of data-integrity risk before any expansion.
5. **Day 5–7**: If harness is green and research baseline is clean — run sizing analysis #387 (fractional-Kelly only, paper mode, no live touch).

**New project**: Light-touch feasibility only. One day of structured research into prediction-market or credibility-intelligence edge (no build, no capital). Gate any build decision on Atlas #215 closing first.

---

### Main Risks / Gates

| Risk | Probability × Impact | Gate |
|---|---|---|
| Strategy churn on unvalidated research | High × Medium | Do not promote until post-repair baseline verified |
| Data corruption during dual-write period | Medium × High | #267 must close before expansion |
| Overlay causes unexpected position exits at scale | Low × High | #215 two-week review must complete |
| Engineering bandwidth collapse if new project starts now | High × Medium | No new project build until at least 3 of 5 high-priority tasks close |
| Sizing change masks underlying strategy weakness | Medium × Medium | #219 harness required before #387 goes live |

**Bottom line:** The risk-adjusted path is Atlas hardening for 7 days, clean research validation, then a controlled decision on strategy extension. Starting a new project now is a distraction risk that delays the very validation work that would tell us whether Atlas is worth extending at all.
