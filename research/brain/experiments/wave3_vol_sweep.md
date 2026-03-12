# wave3_vol_sweep

> **Strategy:** mean_reversion | **Status:** discarded | **2026-03-06T00:05:42.203102+00:00**

## Change
- **Parameter:** Higher volume threshold for MR entries improves trade quality. Wave 1 proved 1.5x volume on MR solo: Sharpe -0.02→0.38, PF 1.30→1.62. Wave 2 combined test FAILED due to infrastructure bug (nested params). This experiment uses full volume dict sweep to bypass the nested param issue. Expect 1.5x to be optimal in combined mode too.
- **Sharpe Δ:** n/a (migrated)

## Metrics
| Metric | Value |
|--------|-------|
| Sharpe | 0.6076 |
| CAGR | 0.0% |
| Profit Factor | 0.00 |
| Max Drawdown | 0.0% |
| Total Trades | 0 |
