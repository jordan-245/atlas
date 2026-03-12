# wave3_ibs_sweep

> **Strategy:** mean_reversion | **Status:** discarded | **2026-03-06T00:05:42.192931+00:00**

## Change
- **Parameter:** Requiring low IBS (close near day's low) for MR entries improves signal quality. Alvarez research shows IBS < 25 gives 58% avg gain improvement on RSI(2) strategy. Our MR has ibs_max=1.0 (disabled). Testing restrictive thresholds should filter out weak MR signals.
- **Sharpe Δ:** n/a (migrated)

## Metrics
| Metric | Value |
|--------|-------|
| Sharpe | 0.6101 |
| CAGR | 0.0% |
| Profit Factor | 0.00 |
| Max Drawdown | 0.0% |
| Total Trades | 0 |
