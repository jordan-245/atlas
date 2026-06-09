# Overlay Log-Only Go/No-Go Review — Task #215

**Date:** 2026-06-01
**Author:** Atlas research agent (read-only / reporting only)
**Gate:** #215 [critical] — "Overlay log-only review — net positive over 2 weeks"
**Recommendation:** **INSUFFICIENT_DATA** → gate **NOT PASSED** → #215 **remains BLOCKED**

---

## 0. Live-Safety Statement

This review was conducted **strictly read-only**. No live config, overlay mode,
trading, or systemd state was changed.

- All analysis used **read-only `sqlite3 ... "SELECT ..."` queries** against
  `data/atlas.db` and read-only inspection of `overlay/*.py` and `db/overlay.py`.
- The mutating evaluator path (`python3 -m overlay.cron --evaluate`) was
  **deliberately NOT run**, because it (a) writes outcome scores into
  `overlay_decisions` and (b) sends a Telegram alert via `evaluate_and_report()`.
  Instead, the evaluator's scoring logic was reproduced read-only from the
  already-scored outcome columns (46 of 49 decisions were scored by prior
  scheduled evaluator runs).
- The overlay remains in **`log_only`** mode (`overlay/cron.py` default). It had
  **zero** effect on the live SP500 plan during the validation window — which is
  exactly the premise this gate is meant to validate.
- Working tree was not modified except for creating this report. Pre-existing
  dirty changes (incl. `overlay/engine.py`, `tests/*`, `_attic/*` deletions)
  were preserved and untouched.

---

## 1. Evaluation Window & Data Sources

| Item | Value |
|------|-------|
| Decision source table | `overlay_decisions` in `data/atlas.db` |
| Realized-PnL bridge | `overlay_shadow_log` in `data/atlas.db` |
| Decision date range | **2026-04-02 → 2026-05-29** (~58 calendar days) |
| Total decisions logged | **49** (one per trading day, a few same-day dupes early) |
| Evaluated (scored) | **46** |
| Unevaluated (too recent at last run) | **3** (ids 63–65, all `no_change`, May 27–29) |
| Shadow-log realized-PnL events | **2** (ids 1–2, Apr 28 & May 1) |

### Requested 14-day window vs. reality

The gate asks for a **2-week (14-day) net-positive** validation. The data does
**not** support a clean 14-day read of the overlay's core mechanism:

- **Last 14 calendar days (since 2026-05-18):** 10 decisions, **all `no_change`,
  ZERO `tighten`**. The overlay's only lever (tighten-only) was never pulled.
  7 evaluated correct, 3 pending — but this window says **nothing** about
  tightening quality.
- **Last 14 decision-records (ids 52–65):** 4 `tighten` + 10 `no_change`. The 4
  tightens were all scored "correct" (3 neutral, 1 genuine downside save).

Because the most recent fortnight contains **no tightening actions**, the
review necessarily falls back to the **full ~58-day validation window** to say
anything meaningful about the overlay's behaviour. Per the task's instruction to
quantify available evidence and apply a conservative default when sparse, this
window-mismatch is itself a primary driver of the INSUFFICIENT_DATA verdict.

---

## 2. Headline Accuracy (reproduces evaluator Pass-2 logic, read-only)

| Window | Evaluated | Correct | Accuracy | Evaluator net_value |
|--------|-----------|---------|----------|---------------------|
| All evaluated (full window) | 46 | 42 | **91.3%** | positive (≥55%) |
| `tighten` only | 17 | 13 | **76.5%** | — |
| `no_change` only | 29 | 29 | **100%** | — |

On the surface this is a strong pass. **It is not, once decomposed.** See §3.

---

## 3. The Critical Decomposition — Why the Headline Is Inflated

The evaluator (`overlay/evaluator.py::_score_decision`) uses an **asymmetric,
SPY-3-day-proxy** scoring rule:

- `tighten` is **correct** if SPY falls >1% (protected) **OR is flat ±1%
  (counted "not costly")**; only **incorrect** if SPY rises >1% (missed upside).
- `no_change` is **correct** unless SPY drops >2% in 3 days.

This means a **calm or rising market mechanically inflates both rates.** The
validation window (Apr–May 2026) was exactly that: `transition_uncertain` →
`recovery_early` → `bull_risk_on`, a predominantly rising tape.

### Tighten decisions — genuine value breakdown (n=17)

| Outcome category | Count | Counts as "correct"? | Economic reality |
|------------------|-------|----------------------|------------------|
| **protected_downside** (market actually fell) | **1** | yes | the only real save (id 53, May 13, −1.31%) |
| neutral_not_costly (flat ±1%) | 12 | yes (by convention) | no harm, **no proven benefit** |
| missed_upside (market rose >1%) | 4 | **no** | actively gave up gains (ids 2, 40, 44, 51) |

**Only 1 of 17 tightens (5.9%) caught a genuine decline. 4 of 17 (23.5%)
demonstrably gave up upside.** The 76.5% "correct" rate is carried almost
entirely by 12 flat-market calls counted as correct-by-convention.

### no_change decisions — breakdown (n=29 evaluated)

| Outcome category | Count |
|------------------|-------|
| appropriate (no >2% drop followed) | 29 |
| should_have_tightened (missed opportunity) | **0** |

Zero missed-tightening flags — but only because **no >2% 3-day drop occurred
after any `no_change`** during a benign window. This reflects the market, not
validated skill.

### Data-availability bias check

- Decisions scored "correct" only because SPY data was unavailable: **0**
  (no inflation from the `assumed neutral` fallback path). Good.

---

## 4. Realized-PnL Evidence (the proper net-value bridge)

`overlay_shadow_log` is the only table that ties a hypothetical overlay sizing
change to an **actual closed-trade PnL**. It contains **only 2 rows**:

| id | date | ticker | mult | would_be_$_diff | actual trade PnL | hypothetical overlay effect |
|----|------|--------|------|-----------------|------------------|-----------------------------|
| 1 | 2026-04-28 | MU (sp500) | 0.8× | −$209.82 | −$18.04 (loss) | trim → ~+$3.6 (smaller loss) |
| 2 | 2026-05-01 | XLE (sector_etfs) | 0.8× | −$95.44 | +$2.28 (gain) | trim → ~−$0.46 (smaller gain) |

**Net realized shadow impact ≈ +$3.1 across 2 events — statistically
meaningless.** There is effectively **no realized-PnL evidence** that the
overlay adds net dollar value. The shadow wiring captured only 2 events in 58
days because plan generation rarely intersected a tighten decision under
log-only operation — a **measurement-coverage gap**, not a code bug.

---

## 5. Answers to the #215 Criteria

**(a) Were tightening calls correct more often than not?**
*Technically yes* (13/17 = 76.5% by the evaluator's convention), **but this is a
weak, inflated positive.** Genuine downside protection occurred only **1/17**
times; **4/17** tightens actively missed upside; the remaining 12 were "correct"
only because the market was flat. Net: **not clearly harmful, not clearly
skillful.**

**(b) Were there missed tightening opportunities?**
**None flagged** (0/29 `no_change` decisions were followed by a >2% 3-day drop).
However, this is **inconclusive**: the window was a calm bull market, so the
overlay was never stress-tested against a real drawdown where a missed tighten
would be costly.

**(c) Did the overlay provide information beyond the regime model?**
**Yes — substantively.** Every decision logged 7 incremental sources
(news, charts, sector_rotation, sentiment, etf_flows, macro_surprise, alt_data)
as available, and the `reasoning` text demonstrates genuine signal not present in
the quant regime model: **RSI overbought levels (SPY/QQQ 71–83), low-volume
conviction ratios, broad mega-cap insider selling (AAPL/GOOGL/AMZN/NVDA execs),
DXY/dollar weakness, geopolitics.** This is real incremental information. The
problem is not the information — it is that the information consistently produced
tightening that, in this rising regime, mostly cost upside.

**(d) Net value positive / negative / insufficient?**
**INSUFFICIENT to confirm positive.** Directionally, for *this specific bull
window*, a tighten-only overlay most likely netted **neutral-to-slightly-negative**
(it trims winners; 4 missed-upside vs 1 genuine save). But the only realized-PnL
evidence is 2 shadow events. The overlay has **not** been validated through a
real drawdown — the one scenario where tighten-only is supposed to earn its keep.

---

## 6. Go / No-Go Recommendation

### **INSUFFICIENT_DATA — gate NOT passed — #215 remains BLOCKED**

Rationale (conservative default applied, per task instruction):

- **Not PASS:** PASS requires demonstrated *net-positive value*. The 91.3%/76.5%
  accuracy is structurally inflated (neutral-counts-correct + calm market). Only
  1/17 tightens caught a real decline; realized-PnL coverage is 2 events. No
  proof of net-positive dollar value, and the requested 14-day window contains
  zero tightening actions.
- **Not FAIL:** FAIL requires demonstrated *net-negative value*. No single call
  was costly at scale (max realized shadow impact ≈ $3); the overlay does provide
  genuine incremental information (criterion c). Evidence of harm is too thin to
  condemn it.
- **INSUFFICIENT_DATA is correct and conservative:** the gate "net positive over
  2 weeks" cannot be affirmed on the evidence available.

### Recommended path to a future decision (no action taken now)

1. **Extend log-only validation** until the tighten mechanism is exercised in a
   genuine drawdown (the missing stress test), OR until shadow-log realized-PnL
   coverage reaches a meaningful sample (suggest **≥20 evaluated shadow events**).
2. **Upgrade the net-value metric** from the coarse SPY-3-day proxy to actual
   **portfolio-PnL attribution** (the shadow log is the right vehicle; it is just
   under-populated). This is a measurement-coverage improvement, not a bug fix.
3. **Re-run this review** once (1) or (2) is satisfied. Do not enable active
   overlay mode, change approval, or alter capital until then (consistent with
   the standing board red lines).

---

## 7. Commands Run (all read-only)

```bash
# Schema + counts
sqlite3 data/atlas.db ".schema overlay_decisions"
sqlite3 data/atlas.db "SELECT COUNT(*) FROM overlay_decisions;"

# Date range + action/outcome breakdown
sqlite3 -header -column data/atlas.db \
  "SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM overlay_decisions;"
sqlite3 -header -column data/atlas.db \
  "SELECT action, COUNT(*), SUM(outcome_evaluated), \
   SUM(CASE WHEN outcome_correct=1 THEN 1 ELSE 0 END) \
   FROM overlay_decisions GROUP BY action;"

# Full decision log
sqlite3 -header -column data/atlas.db \
  "SELECT id, substr(timestamp,1,10), regime_state, action, sizing_override, \
   outcome_evaluated, outcome_correct, confidence FROM overlay_decisions ORDER BY timestamp;"

# Tighten genuine-value categorisation (protected/neutral/missed)
sqlite3 -header -column data/atlas.db \
  "SELECT id, outcome_correct, CASE WHEN outcome_notes LIKE '%protected downside%' \
   THEN 'protected_downside' WHEN outcome_notes LIKE '%not costly (neutral)%' \
   THEN 'neutral_not_costly' WHEN outcome_notes LIKE '%missed upside%' \
   THEN 'missed_upside' ELSE 'other' END FROM overlay_decisions WHERE action='tighten';"

# Shadow log (realized-PnL bridge)
sqlite3 data/atlas.db ".schema overlay_shadow_log"
sqlite3 -header -line data/atlas.db \
  "SELECT id, created_at, ticker, market_id, original_size, overlay_size, \
   sizing_multiplier, would_be_dollar_diff, overlay_action, actual_outcome_pnl \
   FROM overlay_shadow_log;"

# Data sources (criterion c) + reasoning samples
sqlite3 -header -line data/atlas.db \
  "SELECT id, action, data_sources FROM overlay_decisions WHERE data_sources IS NOT NULL ORDER BY id DESC LIMIT 12;"
sqlite3 -header -line data/atlas.db \
  "SELECT id, action, confidence, substr(reasoning,1,400) FROM overlay_decisions WHERE action='tighten' ORDER BY id DESC LIMIT 5;"

# Regime distribution + 14-day window
sqlite3 -header -column data/atlas.db \
  "SELECT regime_state, COUNT(*), SUM(CASE WHEN action='tighten' THEN 1 ELSE 0 END) \
   FROM overlay_decisions GROUP BY regime_state;"
sqlite3 -header -column data/atlas.db \
  "SELECT action, COUNT(*), SUM(outcome_evaluated) FROM overlay_decisions \
   WHERE timestamp >= '2026-05-18' GROUP BY action;"
```

**Files inspected (read-only):** `overlay/cron.py`, `overlay/evaluator.py`,
`db/overlay.py`, `data/atlas.db`.

**Mutations performed:** none (this report file only).
