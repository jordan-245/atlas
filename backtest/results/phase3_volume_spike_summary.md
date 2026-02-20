# Phase 3: Volume Spike Confirmation - Results

**Date:** 2026-02-20  
**Test:** A/B comparison - volume confidence modifier ON vs OFF  
**Baseline:** CAGR=8.34%, Sharpe=0.522, Trades=199  

## Results

| Metric        | Arm A (vol OFF) | Arm B (vol ON) | Delta   |
|---------------|-----------------|----------------|---------|
| Total Trades  | 199             | 214            | +15     |
| CAGR          | 8.34%           | 8.15%          | -0.19%  |
| Sharpe        | 0.522           | 0.497          | -0.025  |
| Profit Factor | 1.638           | 1.568          | -0.071  |
| Win Rate      | 54.3%           | 54.2%          | -0.1%   |
| Max Drawdown  | 7.46%           | 8.05%          | +0.59%  |

## Verdict: DISABLED (0/4 metrics improved)

## Analysis
- Volume boost (surge_boost=0.05) added 15 lower-quality trades (+7.5%)
- These extra trades diluted all key performance metrics
- ASX small-cap mean reversion: volume spikes are NOT reliable confirmation signals
- Confirms Phase 7A finding: volume has no predictive power in this strategy
- The existing quality filters (RS momentum, market breadth, min_confidence) are sufficient

## Config Change
- `strategies.mean_reversion.volume.surge_boost` reset to 0.0
- `strategies.mean_reversion.volume.dry_penalty` remains 0.0
- Volume data retained as informational feature for future ML use

## Phase 3 Scorecard
1. Fee-aware signal filter: ❌ DISABLED
2. Regime filter (IOZ MA + breadth): ❌ DISABLED  
3. Volume spike confirmation: ❌ DISABLED
