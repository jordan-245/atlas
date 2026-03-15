# Decision: Enable Dynamic Position Sizing

**Date:** 2026-03-15  
**Status:** PROMOTED TO LIVE  
**Config:** `config/active/sp500.json` — `dynamic_sizing.enabled = true`

## Context

`utils/dynamic_sizing.py` (215 lines) implements graduated drawdown-based position sizing:
- Tracks all-time equity high
- When portfolio drawdown exceeds tier thresholds, position sizes are scaled down
- Three tiers with progressive scaling
- Fully implemented and unit-tested, but disabled in production since creation

The $3,500 Alpaca account needs capital protection — a single 10%+ drawdown at this account size is hard to recover from.

## Tiers Tested

### Aggressive (rejected)
| Drawdown | Scale | $3,500 trigger |
|----------|-------|-----------------|
| 2% | 0.75x | $70 — too sensitive, normal daily noise |
| 4% | 0.50x | $140 |
| 6% | 0.25x | $210 |

**Result:** MaxDD -4.70pp ✅ but CAGR -23.78pp ❌, Sharpe -0.249 ❌. Position sizes crushed during normal volatility, preventing recovery. Rejected.

### Conservative (promoted)
| Drawdown | Scale | $3,500 trigger |
|----------|-------|-----------------|
| 4% | 0.75x | $140 — meaningful drawdown, reasonable to reduce exposure |
| 7% | 0.50x | $245 — serious drawdown, halve position sizes |
| 10% | 0.25x | $350 — severe drawdown, minimal new exposure |

**Result:**

| Metric | Baseline | Dynamic Sizing | Delta |
|--------|----------|----------------|-------|
| **Max Drawdown** | 13.46% | **11.51%** | **-1.95pp ✅** |
| CAGR | 44.56% | 42.75% | -1.81pp (acceptable) |
| Sharpe | 0.788 | 0.766 | -0.022 (negligible) |
| Sortino | 7.785 | 7.755 | -0.030 (negligible) |
| **Calmar** | 3.311 | **3.715** | **+0.404 ✅** |
| Win Rate | 50.8% | 52.2% | +1.4pp ✅ |
| Profit Factor | 3.34 | 3.51 | +0.17 ✅ |
| Total Trades | 518 | 515 | -3 (negligible) |

Key trade-off: 1.81pp CAGR cost buys 1.95pp drawdown protection and improved risk-adjusted returns (Calmar +12%).

## OOS Validation

| Test | Verdict | Notes |
|------|---------|-------|
| Time-Period Split | FAIL* | *Also fails for baseline config — market regime issue (tariff wars, conflict), not dynamic sizing specific. Baseline OOS Sharpe: -0.36 |
| Perturbation (10 trials) | **PASS** | 0 collapses, all trials positive CAGR, robust: True |
| Walk-Forward Consistency | **PASS** | 55.1% profitable windows (38/69) |

## Design Choices

1. **Only equity_curve_scaling enabled.** Confidence and volatility scaling disabled — keep changes minimal. These can be tested independently later as separate experiments.

2. **Conservative thresholds.** 4% first tier (not 2%) because $3,500 × 2% = $70, which is normal single-trade volatility. First tier should trigger on actual drawdowns, not noise.

3. **0.25x floor at 10%.** At 10% drawdown ($350 loss), system enters near-cash mode. Prevents catastrophic drawdown spirals but doesn't fully stop trading.

4. **base_risk_pct matches existing config.** 0.0035 = `risk.max_risk_per_trade_pct`. Dynamic sizing only modifies behavior during drawdowns.

## Files

- **Candidate:** `config/candidates/sp500_dynamic_sizing.json`
- **A/B results:** `backtest/results/dynamic_sizing_comparison.json`
- **OOS results:** `backtest/results/dynamic_sizing_oos.json`
- **Comparison script:** `scripts/compare_dynamic_sizing.py`

## Future Work

- After 30+ live trades: calibrate whether tiers activate at appropriate points
- Test confidence_scaling as separate experiment (WP-1.1 calibration informs this)
- Test volatility_scaling as separate experiment
- Review tier thresholds after account grows past $10K (tiers may need widening)
