# SP500 Backtest Optimization Plan

**Created:** 2026-02-27  
**Goal:** Build the best-performing swing trading system for S&P 500 stocks  
**Data:** 285 tickers × 753 days (2023-02-27 → 2026-02-26)  
**Hardware:** 8 CPU cores  
**Baseline:** CAGR 1.0%, Sharpe -0.20, PF 1.04 (ASX params on US data)  
**Benchmark:** SPY buy-and-hold: CAGR 8.5%, Sharpe 0.40  
**Target:** CAGR 5-10%, Sharpe 0.3-0.8, PF > 1.3, Max DD < 12%

---

## Phase 0: Code Changes (Pre-Optimization)
**Time estimate: 1-2 hours | Task #7**

Strategy code modifications required before any optimization can run. These add the new parameters as config-driven toggles so the optimizer can sweep them.

### 0A. Mean Reversion — Add SMA-200 trend filter
**File:** `strategies/mean_reversion.py`

```python
# In __init__:
self.sma200_filter = strat_cfg.get("sma200_filter", False)

# In generate_signals, after min_rows check:
if self.sma200_filter:
    sma200 = close.rolling(200).mean()
    if pd.isna(sma200.iloc[-1]) or close.iloc[-1] < sma200.iloc[-1]:
        continue
```

**Why:** Fidelity data shows S&P 500 returns +11.9% above 200 SMA vs -4.3% below. This is the single highest-impact filter for US mean reversion. Prevents buying stocks in structural downtrends.

### 0B. Mean Reversion — Add IBS confirmation filter
**File:** `strategies/mean_reversion.py`

```python
# In __init__:
self.ibs_max = strat_cfg.get("ibs_max", 1.0)  # 1.0 = disabled

# In generate_signals, after RSI/z-score checks:
if self.ibs_max < 1.0:
    ibs = calc_ibs(high, low, close)
    if pd.isna(ibs.iloc[-1]) or ibs.iloc[-1] > self.ibs_max:
        continue
```

**Why:** Alvarez Quant data shows IBS < 0.25 increases mean reversion avg P/L by 21% on S&P 500 stocks. `calc_ibs()` already exists in helpers.py.

### 0C. Opening Gap — Add SMA-200 trend filter
**File:** `strategies/opening_gap.py`

Same pattern as 0A. Only buy gap-down stocks that are still above their 200-day SMA (in a long-term uptrend, the gap is more likely a temporary dip).

### 0D. Opening Gap — Add earnings blackout
**File:** `strategies/opening_gap.py`

Copy the earnings blackout block from `mean_reversion.py`. Post-earnings gaps on US stocks have different dynamics (new information, may NOT mean revert).

### 0E. Add Williams VIX Fix to helpers.py
**File:** `utils/helpers.py`

```python
def calc_wvf(close: pd.Series, low: pd.Series, period: int = 22) -> pd.Series:
    """Williams VIX Fix — synthetic fear gauge for individual stocks."""
    highest_close = close.rolling(period).max()
    wvf = ((highest_close - low) / highest_close) * 100
    return wvf
```

**Why:** WVF spike = panic selling = high-probability mean reversion entry. PF 1.78 on S&P 500 (QuantifiedStrategies). Add as optional confidence booster in MR.

### 0F. Make reoptimize_parallel.py market-aware (Task #2)
**File:** `scripts/reoptimize_parallel.py`

Changes:
1. Add `--market` CLI arg (default `asx`)
2. `load_full_universe()` reads from `data/cache/{market_id}/`
3. Handle US tickers (no `.AX` suffix, skip `SPY` benchmark)
4. Load config via `get_active_config(market_id)`
5. Output to `backtest/results/reopt_{market_id}_*.json`

---

## Phase 1: Establish Baseline
**Time estimate: ~10-15 minutes | Task #1**

Run a single combined walk-forward backtest with the current untuned SP500 config to establish the "before" benchmark.

```bash
cd /root/atlas
python3 scripts/cli.py -m sp500 backtest
```

Record per-strategy breakdown:
- Mean Reversion: trades, win rate, PF, CAGR, avg hold
- Trend Following: trades, win rate, PF, CAGR, avg hold
- Opening Gap: trades, win rate, PF, CAGR, avg hold
- Combined: all metrics + equity curve

**Expected:** Poor results — CAGR ~1%, Sharpe negative, PF ~1.0.

---

## Phase 2: Individual Strategy Isolation Tests
**Time estimate: ~30-45 minutes (3 runs, ~10-15 min each)**

Run each strategy ALONE to understand its independent performance before optimization. This tells us which strategies are worth optimizing vs. disabling.

```bash
# Mean Reversion only
python3 -c "
import json; cfg = json.load(open('config/active/sp500.json'))
cfg['strategies']['trend_following']['enabled'] = False
cfg['strategies']['opening_gap']['enabled'] = False
json.dump(cfg, open('/tmp/sp500_mr_only.json', 'w'), indent=2)
"
python3 scripts/cli.py -m sp500 backtest --config /tmp/sp500_mr_only.json

# Trend Following only
# Opening Gap only
# (same pattern, toggle enabled flags)
```

**Decision gate:** If any strategy has PF < 0.8, consider disabling it for the combined system. A strategy actively losing money drags down the whole portfolio.

---

## Phase 3: Research-Driven Parameter Sweep
**Time estimate: ~4-8 hours | Task #3 (depends on #7)**

This is the main optimization phase. Uses coordinate descent with parallel evaluation. The sweep is organized in **three rounds** to manage the search space.

### Round 1: Structural Filters (highest expected impact)
Test the new binary/threshold filters first, as they fundamentally change signal quality.

**Mean Reversion Structural Sweep:**
| Parameter | Values | Rationale |
|-----------|--------|-----------|
| `sma200_filter` | [true, false] | #1 research finding |
| `ibs_max` | [0.20, 0.25, 0.30, 1.0] | IBS confirmation (1.0=off) |
| `rsi_period` | [2, 3, 5, 14] | RSI(2) is proven best for US |

For each RSI period, the `rsi_oversold` threshold must be adapted:
- RSI(2): test [5, 10, 15]
- RSI(3): test [10, 15, 20]
- RSI(5): test [15, 20, 25, 30]
- RSI(14): test [25, 30, 35, 40]

**Opening Gap Structural Sweep:**
| Parameter | Values | Rationale |
|-----------|--------|-----------|
| `sma200_filter` | [true, false] | Uptrend filter |
| `earnings_blackout.enabled` | [true, false] | Post-earnings gap protection |
| `sma_exit_period` | [3, 5] | Faster exit for US |

**Approach:** Create a custom script `scripts/sweep_structural.py` that:
1. Tests each structural filter combination (small search space, ~48 combos for MR)
2. Runs each combo as a single-strategy backtest
3. Ranks by the existing `score()` function
4. Locks in the best structural config for Round 2

### Round 2: Core Parameter Optimization (coordinate descent)
With structural filters locked, sweep the continuous parameters.

**Mean Reversion:**
| Parameter | Values |
|-----------|--------|
| `zscore_entry` | [-3.0, -2.5, -2.0, -1.5, -1.0] |
| `atr_stop_mult` | [2.5, 3.0, 3.5, 4.0] |
| `profit_target_atr_mult` | [1.0, 1.5, 2.0, 2.5] |
| `max_hold_days` | [3, 5, 7, 10] |

**Trend Following:**
| Parameter | Values |
|-----------|--------|
| `fast_ma` | [5, 10, 15, 20] |
| `slow_ma` | [20, 30, 40, 50, 60] |
| `pullback_pct` | [0.01, 0.02, 0.03, 0.04, 0.05] |
| `atr_stop_mult` | [2.0, 2.5, 3.0, 3.5] |
| `trailing_stop_atr_mult` | [2.5, 3.0, 3.5, 4.0] |
| `max_hold_days` | [10, 15, 20, 25] |

**Opening Gap:**
| Parameter | Values |
|-----------|--------|
| `gap_threshold` | [-0.01, -0.015, -0.02, -0.025, -0.03] |
| `ibs_confirm` | [0.20, 0.25, 0.35, 0.50] |
| `rsi14_max` | [25, 30, 35, 40, 50] |
| `vol_surge_threshold` | [1.0, 1.2, 1.5] |
| `atr_stop_mult` | [1.5, 2.0, 2.5, 3.0] |
| `max_hold_days` | [2, 3, 5, 7] |

**Command:**
```bash
python3 scripts/reoptimize_parallel.py \
  --market sp500 \
  --workers 5 \
  --candidate-path config/sp500_candidate_round2.json \
  --results-path backtest/results/reopt_sp500_round2.json
```

### Round 3: Confidence & Interaction Tuning
Fine-tune the confidence modifiers and interaction effects.

| Parameter | Values |
|-----------|--------|
| MR `volume.surge_boost` | [0.0, 0.05, 0.10] |
| MR `volume.dry_penalty` | [0.0, 0.10, 0.15] |
| MR `breadth.low_boost` | [0.0, 0.015, 0.03] |
| MR `breadth.high_penalty` | [0.0, 0.015, 0.03] |
| Risk `min_confidence` | [0.70, 0.75, 0.80] |
| Risk `max_open_positions` | [8, 10, 12] |

---

## Phase 4: Combined Strategy Backtest
**Time estimate: ~15-20 minutes | Task #4**

Run all optimized strategies together to check interaction effects.

```bash
python3 scripts/cli.py -m sp500 backtest \
  --config config/sp500_candidate_round2.json
```

**Check for:**
- Strategy correlation: do MR and Gap fire on the same tickers? (bad)
- Position crowding: do signals cluster in time? (capacity issue)
- Sector concentration: are we overloaded in tech?
- Combined Sharpe vs. individual strategy Sharpes (should improve via diversification)

---

## Phase 5: Out-of-Sample Validation
**Time estimate: ~2-3 hours | Task #4**

Three-part validation using the existing `validate_oos.py` pattern:

### 5A. Time-Period Split
- **In-sample:** 2023-02-27 → 2025-06-01 (train the params on this window)
- **Out-of-sample:** 2025-03-01 → 2026-02-26 (3-month warmup overlap)
- **Pass criteria:**
  - OOS Sharpe ≥ 0.0 (at minimum not negative)
  - OOS CAGR > 0%
  - OOS/IS Sharpe ratio > 0.5 (not heavily overfit)
  - OOS PF > 1.0
  - ≥ 30 trades in OOS window

### 5B. Parameter Perturbation (Robustness)
- 10 trials with ±20% random perturbation of all params
- **Pass criteria:**
  - Mean CAGR across trials > 0%
  - Fewer than 3/10 trials with negative CAGR
  - Sharpe std < 50% of mean Sharpe

### 5C. Walk-Forward Window Consistency
- Analyze per-window equity returns from the full backtest
- **Pass criteria:**
  - ≥ 50% of walk-forward windows are profitable
  - No single window has > 5% drawdown
  - Consistent trade count across windows

```bash
python3 scripts/validate_oos.py \
  --config config/sp500_candidate_round2.json \
  --output-path backtest/results/sp500_oos_validation.json
```

**Decision gate:** If validation FAILS, go back to Phase 3 with tighter constraints (more conservative params, higher minimum trade count). Do NOT promote a config that fails OOS validation.

---

## Phase 6: Comparison Report & Config Promotion
**Time estimate: ~30 minutes | Task #5, #6**

### 6A. Side-by-Side Metrics
Generate a comparison table:

| Metric | ASX v9.2 | SP500 Baseline | SP500 Optimized | SPY Buy&Hold |
|--------|----------|----------------|-----------------|--------------|
| CAGR | ? | 1.0% | target 5-10% | 8.5% |
| Sharpe | ? | -0.20 | target 0.3-0.8 | 0.40 |
| Max DD | ? | 10.9% | target <12% | 14.1% |
| Win Rate | ? | 53.7% | target 55-65% | — |
| PF | ? | 1.04 | target 1.3-1.8 | — |
| Trades | ? | 322 | 200-400 | — |
| Avg Hold | ? | 16.4d | target 5-10d | — |

### 6B. Promote Config
If validation passes:
```bash
# Version and snapshot the candidate
cp config/sp500_candidate_round2.json config/versions/sp500_v2.0_optimized.json

# Promote to active
cp config/sp500_candidate_round2.json config/active/sp500.json
```

Update sp500.json version string and description.

---

## Phase 7: Stretch Goals (Post-Optimization)

These are enhancements to test AFTER the core optimization is proven:

### 7A. Williams VIX Fix Integration
- Add WVF spike as MR confidence booster
- Test: WVF > 1.5 std above 20-day mean → +0.10 confidence

### 7B. Williams %R as Alternative Oversold Indicator  
- Test replacing RSI with Williams %R in MR strategy
- QuantifiedStrategies ranked it #1 for risk-adjusted swing trading returns

### 7C. Dynamic Position Sizing
- Already have code in `utils/dynamic_sizing.py`
- Test with: confidence scaling ON, volatility scaling ON
- Should improve risk-adjusted returns without changing signal quality

### 7D. Regime Filter (Market-Level)
- Use SPY > 200 SMA + breadth > 50% for bull regime
- Scale positions: bull=1.0x, neutral=0.75x, bear=0.5x
- Previously HURT ASX results — US may respond differently

### 7E. Sector-Specific Parameters
- US tech stocks may mean-revert differently than financials
- Test sector-adaptive parameters (different RSI thresholds per GICS sector)

---

## Execution Sequence (Critical Path)

```
Phase 0 (Code) ─────────────────┐
  ├── 0A: MR SMA-200 filter     │
  ├── 0B: MR IBS filter         │
  ├── 0C: Gap SMA-200 filter    │ ~1-2 hours
  ├── 0D: Gap earnings blackout │
  ├── 0E: WVF in helpers.py     │
  └── 0F: Market-aware reopt    │
                                 ▼
Phase 1 (Baseline) ────────────── ~15 min
                                 ▼
Phase 2 (Isolation) ──────────── ~45 min
                                 ▼
Phase 3 (Optimization) ──────── ~4-8 hours
  ├── Round 1: Structural (1-2h)
  ├── Round 2: Parameters (2-4h)
  └── Round 3: Tuning (1-2h)
                                 ▼
Phase 4 (Combined) ──────────── ~20 min
                                 ▼
Phase 5 (Validation) ─────────── ~2-3 hours
                                 ▼
Phase 6 (Promote) ───────────── ~30 min
```

**Total estimated wall time: ~10-16 hours**  
**Can be done in 2-3 sessions over 1-2 days**

---

## Risk Mitigation

1. **Overfitting risk**: Phase 5 validation catches this. Use min 15 trades per strategy, 2-pass coordinate descent, and OOS time split.

2. **Data snooping**: Walk-forward structure prevents look-ahead bias. OOS window is never used during optimization.

3. **Too few trades**: Score function already penalizes < 15 trades and ramps to full credit at 50+. We have 285 tickers × 753 days — should generate sufficient signals.

4. **Regime dependency**: Perturbation test catches fragile params. Walk-forward consistency checks for regime-sensitivity.

5. **Transaction cost drag**: US fees are 63% cheaper than ASX. Min position value already set at $100 (vs $500 ASX). Commission model is already validated against real Moomoo orders.
