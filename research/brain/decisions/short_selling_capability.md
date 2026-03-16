# Short Selling Capability — CLOSED

**Date:** 2026-03-14 (updated 2026-03-16)
**Status:** CLOSED — Research line terminated. Shorting structurally unprofitable on SP500.

## Summary

Built full short-selling infrastructure (engine, executor, protective orders, signals). Then tested it. It doesn't work.

## Evidence

### Test 1: Mean Reversion Shorts (Task #169)
- 103 short trades, 38.8% win rate, **-$550.70 total P&L**
- Avg short trade: -$5.35
- 2022 bear market: 69% WR, +$189 (only profitable year)
- 2024 bull market: 29% WR, -$324
- Stop hits: 46/103 trades → -$1,316 in stop losses alone
- **Verdict:** Negative. Sharpe destroyed from 0.338 → -0.419

### Test 2: Trend Following Shorts (Task #170)
- 70 short trades, 40% win rate, **-$316 total P&L**
- Avg short trade: -$4.52
- 2022 bear market: 59% WR, but only +$28.75 (barely positive)
- 2021 bull: 0% WR (complete wipeout)
- **Verdict:** Negative. Even in ideal conditions, edge is negligible.

### Test 3: Inverse ETF Hedge (Task #173)
- Conditional SH hedge (SPY<SMA200, VIX>25, 20d ret<-5%)
- Every hedge ratio (10-50%) **degraded** portfolio Sharpe
- 0/6 sensitivity variants improved Sharpe
- Root cause: lagging indicators miss fast crashes, catch bear bounces
- **Verdict:** Rejected. MaxDD reduction 0pp (needed ≥5pp).

## Why Shorting Fails on SP500

1. **Positive drift.** SP500 stocks are the 500 largest companies with institutional support, buybacks, and sector rotation. Recovery bias is structural, not cyclical.

2. **Signal lag.** Both MR overbought signals and TF downtrend signals activate too late — after most of the decline has already happened, right as recovery begins.

3. **Asymmetric payoff.** Short stop-losses fire more often than take-profits because stocks tend to bounce (V-recovery). 46/103 MR shorts hit stops vs 15/103 hitting TP.

4. **Fast crashes can't be caught.** The 2020 crash went ATH→-34% in 23 trading days. SPY was above SMA200 until mid-March when the worst was over. No lagging trigger catches this.

5. **Bear market rallies.** Even during 2022, SPY rallied +2.84% during the days the hedge signal was active. Counter-trend bounces within bears are violent enough to stop out shorts.

## What Remains

The infrastructure (direction-aware engine, executor, protective orders) is still in place and functional. The strategy-level short signal code was removed from the active code paths — `generate_signals()` only produces long signals. The dead `_generate_short_signals()` method in `mean_reversion.py` remains but is never called.

If a fundamentally different short approach is ever tested (e.g., options-based protection, sector-rotation shorts on different markets, or VIX-based volatility strategies), the infrastructure is ready.

## Decision

**Do not revisit shorting on SP500 individual stocks or inverse ETFs.** Three independent tests all reached the same conclusion. The portfolio's existing protections (VIX gate, breadth filters, position sizing, max exposure limits) are the appropriate risk management tools for a long-only SP500 portfolio.
