# Task #387 — Volatility-aware / Fractional-Kelly Sizing Analysis (SP500 momentum_breakout)

**Date:** 2026-06-01
**Mode:** Paper / backtest analysis only. No live config change, no broker action, no live trade.
**Config under test:** SP500 `v3.2.4` (active), single enabled strategy `momentum_breakout`, `approval=false`.
**Author tooling:** `scripts/analyze_fractional_kelly_sizing.py` (+ unit tests).
**Recommendation:** **DO NOT PROMOTE any sizing variant.** See §6.

> The active config (`config/active/sp500.json`) was loaded **read-only** and never modified. All
> arms are in-memory deep-copies with only the sizing blocks mutated.

---

## 1. What was tested and why it is a clean comparison

In the Atlas backtest engine, **signal generation is independent of sizing**: `momentum_breakout`
emits entry/stop/target/confidence, then the engine sizes the position. Sizing only changes share
counts (and a few downstream value-based filters). So holding strategy params + regime/macro scaling
constant and varying **only** the sizing overlay isolates the sizing effect on the *identical raw
signal stream* — satisfying the "compare sizing overlays on the same signal set" requirement, with a
true fixed-sizing control.

The live config already ships **two** sizing overlays enabled:
`vol_scaling` (portfolio vol targeting, target 12%) and `dynamic_sizing`
(ATR-vol scaling + graduated drawdown tiers). So the genuine fixed-sizing baseline required
**disabling both** in a copy.

### Arms

| Arm | Sizing | Role |
|-----|--------|------|
| `baseline_fixed` | flat 0.50%/trade, both overlays OFF | **control** |
| `live_as_configured` | vol_scaling ON + dynamic_sizing ON (ATR-vol + DD tiers) | what is live today |
| `vol_target_only` | portfolio vol targeting only | vol-aware treatment |
| `dd_scaling_only` | graduated drawdown de-risk only | drawdown treatment |
| `risk_mult_1.5x` | flat 0.75%/trade | sub-cap risk-multiplier sensitivity |
| `risk_mult_2.0x` | flat 1.00%/trade | sub-cap risk-multiplier sensitivity |
| `frac_kelly_0.25x` | capped fractional Kelly | requested Kelly variant |
| `frac_kelly_0.5x` | capped fractional Kelly | requested Kelly variant |

### Empirical Kelly fraction

Estimated from the **baseline arm's** realized R-multiples:

```
W (win rate)        ≈ 0.366
avg winner          ≈ +4.50 R
avg loser           ≈ -1.79 R     (losers overshoot the nominal 1R — gap/next-open fills)
b = 4.50 / 1.79     ≈ 2.51
full-Kelly  f* = W - (1-W)/b ≈ 0.115   → ~11.5% risk per trade
```

Full-Kelly ≈ **11.5%/trade** is extreme. Consequently **0.25x (≈2.9%) and 0.5x (≈5.7%) both exceed
the 2.0% hard safety cap** and collapse to a flat 2.0%/trade. This is itself a finding: under any
sane safety cap, "fractional Kelly" for this strategy = "max-cap flat risk."

---

## 2. Results — funded equity ($25,000), full 198-ticker universe

> Primary regime for inference: drawdowns are realistic, position floors/rounding do not distort.

| Arm | risk% | CAGR | Sharpe | Sortino | maxDD | PF | trades | DD'24–25 |
|-----|------:|-----:|-------:|--------:|------:|---:|-------:|---------:|
| baseline_fixed | 0.500% | 10.23% | **0.624** | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| live_as_configured | 0.500% | 9.89% | 0.586 | 0.884 | 10.86% | 1.60 | 427 | 7.96% |
| vol_target_only | 0.500% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| dd_scaling_only | 0.500% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| risk_mult_1.5x | 0.750% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| risk_mult_2.0x | 1.000% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| frac_kelly_0.25x | 2.000% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |
| frac_kelly_0.5x | 2.000% | 10.23% | 0.624 | 0.959 | 10.57% | 1.62 | 427 | 7.80% |

**Every flat-risk arm is byte-identical** across a 4× spread in risk-per-trade (0.5% → 2.0%). Only
`live_as_configured` differs — and it is **slightly worse** (Sharpe 0.586 vs 0.624).

### Why: the `max_order_value = $5,000` safety cap binds

The engine caps every position at `trading.live_safety.max_order_value` (= **$5,000** in v3.2.4):

```python
# backtest/engine.py L879-881
if self.max_position_value > 0 and position_value > self.max_position_value:
    shares = int(self.max_position_value / fill_price)   # cap to $5,000
```

At $25k equity, `risk_budget = equity × leverage × risk_pct = 25000 × 2 × 0.005 = $250`, which for a
normal-volatility name implies a position far above $5,000 → **capped**. Raising `risk_pct` to 2%
only pushes the implied position higher → **still capped to $5,000**. So the per-trade risk fraction
(and every overlay that scales it: vol-target, DD-scaling, Kelly, risk multipliers) is **completely
inert** at funded equity. The only overlay that can still act is `live_as_configured`, because its
multipliers occasionally pull a position *below* the cap — and that net effect is a small Sharpe
**loss**.

**Conclusion at funded equity:** no sizing overlay can improve risk-adjusted return — there is no
sizing degree of freedom left once the $5k order cap binds.

---

## 3. Results — live equity slice ($971), full 198-ticker universe

> The $971 figure is the v3.2.4 `risk.starting_equity` baseline (current live slice ≈ $1,373). At
> this equity the $5k cap rarely binds, so sizing **does** express — but results are distorted by the
> `min_position_value = $100` floor, integer-share rounding, and extreme concentration (an account
> this small holds very few names). Per project lessons, **low live equity distorts solo results**, so
> these numbers are directional, not promotion-grade.

| Arm | risk% | CAGR | Sharpe | Sortino | maxDD | PF | trades | DD'24–25 | Gate vs baseline |
|-----|------:|-----:|-------:|--------:|------:|---:|-------:|---------:|:----------------:|
| baseline_fixed | 0.500% | 31.06% | 0.910 | 1.312 | 33.21% | 1.53 | 425 | 33.21% | — (control) |
| live_as_configured | 0.500% | 21.31% | 0.844 | 1.128 | 22.02% | 1.51 | 427 | 20.85% | FAIL (ΔSharpe −0.066) |
| vol_target_only | 0.500% | 24.49% | 0.772 | 1.064 | 31.95% | 1.41 | 427 | 31.95% | FAIL (ΔSharpe −0.138) |
| dd_scaling_only | 0.500% | 21.38% | 0.809 | 1.139 | 22.64% | 1.47 | 422 | 21.58% | FAIL (ΔSharpe −0.101) |
| risk_mult_1.5x | 0.750% | 44.51% | 1.019 | 1.525 | 37.28% | 1.65 | 425 | 37.17% | **PASS hard** (ΔSharpe +0.109, ΔDD +4.07pts → breaches soft 3pt) |
| risk_mult_2.0x | 1.000% | 45.43% | 0.958 | 1.446 | 44.95% | 1.66 | 424 | 40.96% | FAIL (ΔDD +11.75pts) |
| frac_kelly_0.25x | 2.000% | 53.45% | 1.019 | 1.564 | 62.39% | 1.68 | 397 | 41.53% | FAIL (ΔDD +29.18pts) |
| frac_kelly_0.5x | 2.000% | 53.45% | 1.019 | 1.564 | 62.39% | 1.68 | 397 | 41.53% | FAIL (ΔDD +29.18pts) |

Observations at the live slice:

- **The live overlays hurt risk-adjusted return.** `live_as_configured`, `vol_target_only`, and
  `dd_scaling_only` all post **lower Sharpe than the plain fixed baseline**. They cut drawdown
  (22–32% vs 33%) but at a Sharpe and CAGR cost — they de-risk into a return drag.
- **More risk → more Sharpe, but drawdown scales faster.** Only `risk_mult_1.5x` (0.75%) clears the
  board's *hard* 5pt DD band (+4.07pts) while improving Sharpe (+0.109) — but it **breaches the soft
  3pt band**, increases drawdown to 37%, and is the *opposite* of the "vol-aware de-risking" intent.
- **Fractional Kelly is disqualified outright:** collapses to the 2% cap → **62% max drawdown
  (+29pts)**. Unacceptable under any reading of the gate.

---

## 4. Drawdown-period check (2024–2025)

The `DD'24–25` column is the max drawdown computed strictly within the 2024-01-01…2025-12-31 slice of
each arm's equity curve (full-history warm-up preserved; no truncated re-run). It tracks the
full-period maxDD ranking in both equity regimes:

- **$25k:** baseline 7.80% vs live 7.96% — the live overlay does **not** improve the 2024–2025 DD.
- **$971:** baseline 33.2%, overlays 21–32% (lower DD but lower Sharpe), Kelly 41.5% (worse).

No variant improves risk-adjusted return in 2024–2025 without either (a) being inert (funded equity)
or (b) increasing drawdown / dragging Sharpe (live slice).

---

## 5. Board gate evaluation

Gate (from #387 brief): **improved risk-adjusted return (Sharpe) AND max drawdown not worse than
baseline by > 3–5 percentage points**, with **no live promotion regardless**.

| Variant | Funded ($25k) | Live slice ($971) | Verdict |
|---------|---------------|-------------------|---------|
| Portfolio vol targeting | inert (cap-bound) | Sharpe −0.138 | **FAIL** |
| Drawdown de-risk | inert (cap-bound) | Sharpe −0.101 | **FAIL** |
| Live combined (deployed) | Sharpe −0.038 | Sharpe −0.066 | **FAIL** (net negative both regimes) |
| Fractional Kelly (0.25x/0.5x→2% cap) | inert (cap-bound) | maxDD +29pts | **FAIL** |
| Risk × 1.5 (0.75%) | inert (cap-bound) | Sharpe +0.109 but DD +4pts (soft breach), unreliable | **NO** |
| Risk × 2.0 (1.0%) | inert (cap-bound) | DD +11.75pts | **FAIL** |

**No variant cleanly and reliably passes.** The only arm that passes the *hard* band (`risk_mult_1.5x`)
does so only at the distorted $971 equity, breaches the soft band, raises drawdown, and is a
risk-*increasing* change — not the vol-aware de-risking the task set out to validate.

---

## 6. Recommendation

**Do NOT promote any sizing variant. #387 sizing question is answered: NO — under the current
framework, neither volatility-targeting nor capped fractional-Kelly improves risk-adjusted return for
live SP500 `momentum_breakout`.** Specifically:

1. **No live config change.** Leave `config/active/sp500.json` (`v3.2.4`, approval=false) untouched.
   Promotion would also require OOS validation + approval gates, which were not run and are not
   warranted given the negative result.
2. The currently-deployed overlays (`vol_scaling` + `dynamic_sizing`) are **net-negative on Sharpe**
   in both equity regimes (marginally at funded equity, clearly at the live slice). They are not
   actively harmful enough to warrant an emergency change, but **a future clean-up candidate should
   consider disabling them** — to be validated through the normal reopt/OOS/approval pipeline, not
   here.
3. **Fractional Kelly is unsuitable** for this strategy: its empirical edge implies a full-Kelly of
   ~11.5%/trade, so any defensible cap collapses the fractional variants to a single aggressive flat
   risk that produces 50–62% drawdowns at the live slice.

### Structural blocker (root cause)

Per-trade risk-based sizing **cannot be evaluated cleanly** in the current SP500 framework:

- **At funded equity** the `live_safety.max_order_value = $5,000` cap binds for essentially every
  position, so `risk_pct` and all overlays acting on it are inert.
- **At the live equity slice** the cap rarely binds, but `min_position_value`, integer-share rounding,
  and severe concentration distort the results (33–62% drawdowns), so inference is unreliable.

There is no equity band in the current configuration where risk-based sizing both *expresses* and is
*undistorted*. This is the same "low live equity distorts solo results" pattern flagged in lessons.

### Follow-up recommendation (new task)

To answer the sizing question rigorously, a future task should reconcile the **position-sizing
architecture** before re-testing overlays: e.g. evaluate with `max_order_value` scaled to equity (or
replaced by a true equity-fraction cap), at the realistic funded equity the strategy will actually run
at, with the `min_position_value` floor controlled — then re-run this harness. Until then, sizing
overlays should be treated as having **no demonstrated edge**.

---

## 7. Reproduction — commands & artifact paths

```bash
# Unit tests for the pure helpers (Kelly math, gate, sub-window DD, arm config)
python3 -m pytest tests/test_fractional_kelly_sizing_analysis.py -q
#   -> 25 passed

# Funded-equity run (primary inference regime)
python3 scripts/analyze_fractional_kelly_sizing.py --equity 25000 --workers 6 \
    --out backtest/results/fractional_kelly_sizing_25k_full.json

# Live-equity-slice run (sizing expresses, but low-equity distortion)
python3 scripts/analyze_fractional_kelly_sizing.py --equity 971 --workers 6 \
    --out backtest/results/fractional_kelly_sizing_971_full.json

# Fast 40-ticker smoke (sanity only)
python3 scripts/analyze_fractional_kelly_sizing.py --quick --equity 25000
```

**Artifacts:**
- `backtest/results/fractional_kelly_sizing_25k_full.json` — funded-equity full run
- `backtest/results/fractional_kelly_sizing_971_full.json` — live-slice full run
- `scripts/analyze_fractional_kelly_sizing.py` — analysis harness (read-only on active config)
- `tests/test_fractional_kelly_sizing_analysis.py` — 25 unit tests for pure helpers

**Scope guarantees:** no edits to `config/active/*`, no broker/systemd interaction, no live trade. All
sizing arms are in-memory deep-copies of the active config with only sizing blocks mutated.
