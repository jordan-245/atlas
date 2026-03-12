# wave4_mr_strength_exit

> **Strategy:** mean_reversion | **Status:** discarded | **2026-03-08T00:17:47.461203+00:00**

## Change
- **Parameter:** The LBR published exit rule (sell when close > yesterday high) captures the first sign of strength recovery. Testing this on existing MR strategy as an alternative to the current profit-target + mean-reversion exit. Expected: faster exits, higher win rate, possibly lower avg profit per trade.
- **Sharpe Δ:** n/a (migrated)

## Metrics
| Metric | Value |
|--------|-------|
| Sharpe | -2.0995 |
| CAGR | 0.4% |
| Profit Factor | 1.03 |
| Max Drawdown | 1.4% |
| Total Trades | 68 |
