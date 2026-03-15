# Atlas Evolution Plan — From Trading System to Living Ecosystem

**Generated:** 2026-03-15
**Scope:** 5 tiers, 17 work packages, ~45 discrete tasks
**Target:** Fully adaptive, self-monitoring algorithmic trading ecosystem

## Execution Order

| Phase | Weeks | Work Packages | Theme |
|-------|-------|--------------|-------|
| 1 | 1-2 | WP-1.3, WP-1.4, WP-1.1 | Quick wins |
| 2 | 3-4 | WP-1.2, WP-2.1, WP-4.4 | Feedback infrastructure |
| 3 | 5-8 | WP-3.1, WP-3.3, WP-2.2, WP-3.2 | Adaptive intelligence |
| 4 | 9-12 | WP-4.1, WP-3.4, WP-4.2, WP-4.3 | Scale |
| 5 | Ongoing | WP-5.1, WP-5.2, WP-5.3 | Polish |

## Work Package Reference

Full specs in conversation/plan document. Key files per WP:

### Tier 1: Close the Feedback Loop (~2,500 LOC)
- **WP-1.1** Confidence Score Calibration → research/calibration.py
- **WP-1.2** Live Performance Tracker → monitor/strategy_health.py
- **WP-1.3** Enable Dynamic Position Sizing → config/active/sp500.json
- **WP-1.4** Slippage Feedback Loop → scripts/slippage_calibration.py

### Tier 2: Data Robustness (~2,000 LOC)
- **WP-2.1** Alpaca as Primary Data Source → data/ingest.py, brokers/alpaca/market_data.py
- **WP-2.2** Point-in-Time Universe → data/sp500_history.py, universe/builder.py

### Tier 3: Adaptive Intelligence (~3,500 LOC)
- **WP-3.1** Correlation-Aware Portfolio Weights → research/portfolio_optimizer.py
- **WP-3.2** Strategy Lifecycle Automation → monitor/lifecycle.py (depends: WP-1.2)
- **WP-3.3** Event Calendar Integration → data/events.py
- **WP-3.4** Short Selling Capability → strategies/base.py, backtest/engine.py

### Tier 4: Scale and Maturity (~3,000 LOC)
- **WP-4.1** Backtest Engine Refactoring → backtest/pipeline.py, backtest/filters.py
- **WP-4.2** Sub-Daily Entry Timing → data/intraday.py (depends: WP-2.1)
- **WP-4.3** Disaster Recovery → scripts/reconcile.py
- **WP-4.4** Comprehensive Test Suite → tests/

### Tier 5: Operational Polish (ongoing)
- **WP-5.1** Rejected Signal Analysis → research/rejected_signal_analysis.py
- **WP-5.2** Config Schema Validation → config/schema.py
- **WP-5.3** Dashboard Modernization → dashboard/
