## Position
**Vote: CONDITIONAL**
Increase returns only after the three foundational stability items close — overlay review, regression harness, SQLite cutover — because rushing return levers before those complete recreates the exact failure modes just fixed.

---

## Arguments

- **The 0/32 promotion rate is a feature, not a bug.** The gate system is working. The risk is that pressure to "increase returns" leads to softening promotion thresholds rather than finding genuinely better strategies. Watch for threshold creep.

- **Live capital is small but policy is permanent.** At $1.3K the dollar loss is bounded, but any config promotion policy adopted now will persist when capital scales. Bad habits formed here multiply.

- **SQLite cutover (#267/#276) is an active destabilization event.** Promoting new strategies or configs during a database migration means two independent sources of live-trade attribution risk simultaneously. This is how cascading failures begin.

- **Overlay log-only review (#215) is incomplete.** Two weeks of log-only data on a system that just had its semantics corrected is insufficient signal. Activating overlay before #215 closes is premature given the recent `vision signals as metadata` fix — we don't yet know if the corrected behavior is net positive.

- **Cross-universe leakage was fixed days ago.** Institutional memory on why it failed is still fresh but not yet encoded into regression tests. Universe expansion before #219 (regression harness) is live means we cannot detect regressions automatically.

- **Momentum breakout in a single universe with no regime diversity is concentration risk.** This is the correct risk *right now*, but the fix is validated diversification, not rushed expansion.

---

## Required Gates

1. **#219 regression harness live and green** before any research scope expansion or threshold changes — this is the canary for silent research failures.
2. **#215 overlay review closed with net-positive determination** before any overlay mode change from log-only.
3. **#267/#276 SQLite cutover complete and 7-day shadow clean** before promoting any new strategy config to live.
4. **Mandatory OOS walk-forward** (minimum 6-month hold-out) on any promoted momentum_breakout variant, not just IS metrics.
5. **Drawdown circuit breaker defined and enforced in config** before increasing max positions above 10 or adding strategies.
6. **Human approval gate re-enabled** (`approval: true`) before any non-trivial parameter change goes live — current `false` setting removes a critical human checkpoint.

---

## Roadmap Recommendations

**Days 0–30 (Stabilize before accelerating):**
- Close #219 regression harness — mandatory before touching research parameters
- Complete #267/#276 SQLite cutover — no config promotions until this is stable
- Define explicit drawdown circuit breaker thresholds and encode in config gate checks
- Document the current momentum_breakout promotion criteria so threshold creep is detectable

**Days 30–60 (Controlled single-lever experiment):**
- With regression harness live, run a properly gated parameter sweep for momentum_breakout; promote at most one variant if OOS clears all gates
- Close #215 overlay review — if log-only shows net positive signal, plan conditional activation with kill switch
- Do NOT expand to additional SP500 strategies until at least one clean sweep cycle completes without incident

**Days 60–90 (Conditional diversification):**
- If and only if: SQLite stable, overlay healthy, regression harness green, no live incidents — consider one additional validated SP500 strategy
- Universe expansion remains off the table until cross-universe isolation is regression-tested
- Position sizing increase (beyond max 10) requires drawdown circuit breaker proof-of-function first

---

## No-Go Items

- ❌ **Softening OOS validation thresholds to push variants through** — the 0/32 rate is correct behavior
- ❌ **Universe reactivation before regression harness (#219) exists** — cross-universe leakage will silently recur
- ❌ **Overlay activation before #215 closes** — two-week log-only on a recently-corrected semantic is insufficient
- ❌ **Any config promotion during active SQLite migration window** — dual-source attribution failure risk
- ❌ **Removing or weakening the human approval gate** to accelerate promotions — at scale this is the last line of defense
- ❌ **Position sizing increases before drawdown circuit breaker is defined and tested** — upside capped, downside isn't