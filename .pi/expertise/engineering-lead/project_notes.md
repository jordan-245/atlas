# Engineering Lead — Project Notes


## Per-market equity attribution (post-FIX-PMEQ-001, 2026-05-01)

### Components
- **Snapshot writer**: `scripts/eod_settlement.py` calls `portfolio/market_equity_attribution.py::attribute_equity_pro_rata()` at EOD to write pos_mv, cash_attributed, broker_equity, snapshot_time to `market_equity_history` table. Runs ~22:01 UTC daily.
- **Live reader**: `brokers/live_portfolio.py::_get_per_market_equity()` reads the latest snapshot, then calls `portfolio/per_market_cash_flow.py::compute_realized_cash_flow_since()` to get actual FILL+DIV cash flows since the snapshot_time. `live_cash = snap_cash + cash_flows[market]`.
- **Degraded mode**: if the Alpaca activities API call fails, `compute_realized_cash_flow_since()` returns `(zeros, True)`. In degraded mode: `live_cash = snap_cash` (frozen), `self._per_market_equity_degraded = True`, kill switch suppressed for dd < 20% in `check_daily_drawdown`.
- **Cache**: `per_market_cash_flow._CACHE` — 30s in-process TTL to prevent hammering Alpaca activities endpoint on every drawdown check.
- **HWM reset tool**: `scripts/reset_per_market_hwm.py --apply` — connects to broker, computes per-market equity, writes new HWM to JSON state files + market_state SQLite.

### Edge cases
- **GLD ambiguity**: GLD is in BOTH `commodity_etfs` and `gold_etfs` static definitions. `derive_universe('GLD')` returns 'commodity_etfs' (alphabetically first from the set). Since only sp500/sector_etfs/commodity_etfs are tracked by `_ALL_TRADED_MARKETS`, GLD correctly maps to commodity_etfs.
- **No broker**: `_broker is None` → degraded mode, `live_cash = snap_cash`, kill switch still suppressed. In production, broker is always connected before drawdown check; this path is test-only.
- **Missing snapshot_time**: if column is NULL (old rows), degraded=True, live_cash=snap_cash.
- **Legacy rows** (pos_mv=0, cash_attributed=0): proportional scaling `snap_alloc * cash_scale` (backwards-compat, no activities fetch).

### Pre-fix phantom-drawdown root cause (Apr 30 2026)
Formula was `per_market_eq = current_pos_mv + snap_cash * cash_scale`. When XLY exited, its \$1,170 proceeds moved from position_mv → broker cash but snap_cash stayed at \$220.40 → estimated equity fell \$1,170 → 13.6% phantom drawdown on sector_etfs.

Fix: `live_cash = snap_cash + realized_fills_since_snapshot` so exit proceeds are correctly accounted for.
