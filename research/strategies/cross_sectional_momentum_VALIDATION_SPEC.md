# Clean Validation Run Spec: cross_sectional_momentum (csm)

> **PRE-REGISTRATION 2026-06-05.** Fix every choice below BEFORE running. The board's standing
> task (#388 / 2026-06-01) is "validate ONE additive OOS strategy, then human approval before live."
> After two killed sleeves (long-short, news-sentiment), csm is the **only** additive sleeve that
> survives net-of-cost OOS — this run gives it a definitive, reproducible tier.
> Refs: pre-reg `research/brain/hypotheses/equity_long_short.md` (kill), board memo
> `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding`.

## Current state (evidence — this is NOT a rescue)

Most recent real battery run (`backtest/results/battery_csm_newgate_20260604_082630.json`,
post regime-gate recalibration, `--select default`, grid_size 12, max_positions 35) = **SCREEN**.
It passes **every gate except one**:

| Gate | Value | PROMOTE bar | Status |
|---|---|---|---|
| median CPCV Sharpe | 0.720 | ≥ 0.50 | ✅ |
| frac paths positive | 0.867 | ≥ 0.55 | ✅ |
| **PBO** | **0.129** | ≤ 0.50 | ✅ (not overfit) |
| **DSR (search-deflated)** | **0.772** | **≥ 0.90** | ❌ **only failing gate** |
| regime concentration | 1.18 | ≤ 2.0 | ✅ |
| per-regime expectancy | True | True | ✅ |
| min regime Sharpe | 0.88 | ≥ −0.5 | ✅ |
| top_group_frac | 0.094 | < 0.5 | ✅ (not single-name) |
| loo group ok | True | True | ✅ |
| oos cagr degradation ok | True | True | ✅ |
| forward_net | +59.25 | > 0 | ✅ |

**The entire PROMOTE-vs-SCREEN question is DSR**: search-history-deflated confidence that the true
Sharpe > 0. csm is at 77%; PROMOTE wants 90%. Everything else is clean.

## What honestly moves DSR (and what would be GAMING — forbidden)

DSR rises with (a) higher observed Sharpe of the **pre-registered default** config, (b) **more
observations N**, and falls with (c) a broader/again-correlated search (higher expected max Sharpe
across trials).

- ✅ **Allowed:** run on the **full available history** (more obs → tighter DSR honestly).
- ✅ **Allowed:** keep the committed, economically-motivated grid + default fixed.
- ❌ **FORBIDDEN (gaming):** shrinking the grid or shortening the window to inflate DSR;
  post-hoc selecting the best-DSR config (that is exactly what DSR exists to catch — use
  `--select default`, never `best_cpcv`/`best_oos`); widening PARAM_GRID ranges to chase a higher
  default Sharpe. If anything, a LARGER grid_size only makes the bar HARDER (more conservative) and
  is acceptable; a SMALLER one is not.

## Pre-registered run configuration (FROZEN before run)

| Knob | Value | Rationale |
|---|---|---|
| strategy | `cross_sectional_momentum` (sandbox) | the additive sleeve |
| market | `sp500` | live universe |
| **default / primary config** | mom_lookback=126, mom_skip=21, vol_lookback=126, sma_period=200, atr_stop_mult=3.0, top_n=30, exit_rank=60, max_hold_days=90, w_mom=1.0, w_qual=0.5, trend_filter=true | the economically-motivated prior (6-1 momentum + low-vol/quality, Jegadeesh-Titman / Frazzini-Pedersen). **Unbiased verdict config.** |
| `--select` | **default** | NO selection bias; grid only informs PBO/DSR |
| PARAM_GRID | the committed grid in `research/strategies/cross_sectional_momentum.py` (mom_lookback[126,189,252], vol_lookback[63,126], atr_stop_mult[2.5,3,3.5], top_n[10,15,20,30], exit_rank[30,40,60], max_hold_days[60,90,120], w_qual[0,0.5,1.0]) | principled axes only; pre-registered, not iterated after seeing DSR |
| `--grid-size` | **12** (seed=42, reproducible) | matches prior; do not reduce. May raise (more conservative) but not lower |
| `--max-positions` | **35** | breadth for a ~150-name factor book; matches prior |
| window | **full `vo.load_data` history** (~7yr) | maximize N for an honest DSR — the one legitimate lever |

## Run command

```bash
python3 scripts/run_strategy_battery.py \
    --strategy cross_sectional_momentum --market sp500 \
    --grid-size 12 --max-positions 35 --select default \
    --output-path backtest/results/battery_csm_clean_validation_$(date +%Y%m%d).json
```
Headless via systemd if runtime > a few min (CPU-bounded, nice'd; ~3 workers default).

## Pre-registered decision tree (accept the outcome — do not re-roll)

1. **PROMOTE** (DSR ≥ 0.90 AND all other gates pass):
   - csm becomes the **first additive strategy eligible for staged promotion**.
   - Write a candidate config to `config/candidates/` (NOT live). **Human approval required.**
   - Then **paper-forward** via the rapid-pipeline forward clock (#420) before any live weight.
   - Live only at material AUM (~$25K) per risk doc + board; never auto-promote.
2. **SCREEN** (0.70 ≤ DSR < 0.90, all other gates pass) — *the current state*:
   - csm is a **validated research edge but NOT promote-confidence**. Do **not** add to the live
     config. Register it on the **forward paper clock** (#420) and re-evaluate after ≥ 3 months of
     forward evidence (forward CLV/PnL + a re-run battery with the longer window).
   - This is a legitimate, board-actionable outcome — not a failure.
3. **FAIL** (any non-DSR gate fails): diagnose regression (gate change? data?) before any conclusion.

## Integrity guards (pre-registered)

- Everything above is fixed before the run; the seed (42) makes the grid reproducible.
- **Gate-still-bites regression check:** in the same session, confirm the battery still returns
  FAIL/low-DSR for a known-null (e.g., re-run a degenerate/random config or the killed long-short
  proxy returns) — proves the gate isn't silently passing everything.
- Commit the run artifact + this spec; record the verdict in
  `research/brain/hypotheses/cross_sectional_momentum_validation.md` + `research/results/`.
- No config/systemd/live mutation as part of the validation run itself.

## What this adds over the 2026-06-04 SCREEN run
Formalizes the grid/seed/default/window/max_positions as a **pre-registration**, runs on the **full
history** to give DSR its best *honest* shot (more obs), ties the outcome to a **pre-registered
promotion/paper decision**, and adds a **gate-integrity regression check** — so the verdict is
reproducible and board-actionable, whichever tier it lands.
