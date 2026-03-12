# wave2_tom_filter

> **Strategy:** None | **Status:** discarded | **2026-03-04T14:31:03.655446+00:00**

## Change
- **Parameter:** The Turn of Month effect (last 5 + first 3 trading days) shows stocks generate virtually all monthly returns in this window (Lakonishok & Smidt 1988, confirmed 2024). Boosting signal confidence during TOM window (or suppressing signals mid-month) should improve trade quality. This is a calendar-based filter, completely uncorrelated with our price-based signals.
- **Sharpe Δ:** n/a (migrated)

## Metrics
| Metric | Value |
|--------|-------|
| Sharpe | -0.6446 |
| CAGR | 2.9% |
| Profit Factor | 1.40 |
| Max Drawdown | 2.6% |
| Total Trades | 115 |
