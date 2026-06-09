## Vote: RESEARCH_TESTNET_ONLY

### Top Recommendation
Authorize a **time-boxed 3-week technical spike** on testnet only: resurrect the Midas/Freqtrade base, validate it runs cleanly against Bybit testnet, and define the minimum edge-validation harness. No live keys, no leverage, no capital. Gate any further work on both a working testnet stack AND Atlas #399/#215 clearing.

---

### Reasoning (Technical Lens)

**Leveraged intraday crypto is architecturally harder than Atlas.** Atlas runs ~once per day. A leveraged intraday bot requires:
- Sub-second position reconciliation loops
- Real-time liquidation-distance monitoring (not batch)
- Funding fee P&L accounting on every 8h interval
- Kill-switch that fires reliably under network partition or exchange API degradation
- 24/7 uptime with graceful restart (not cron-friendly)

Each of these is a distinct engineering surface area. Getting any one wrong with leverage is capital-destructive, not just performance-degrading.

**The Midas/Freqtrade base is the right starting point — but it needs audit.** "~70% complete" from March means ~70% of some prior scope. Freqtrade handles order management, backtesting, and basic risk — that's real value. But:
- Freqtrade's default risk model assumes spot; leveraged perpetuals require custom stop/liquidation logic layered on top
- Bybit API v5 has breaking changes from v3; connector compatibility needs verification
- The Midas codebase has been dormant ~3 months — dependency rot, config drift, and stale exchange credentials are likely

Estimated effort to resurrect to testnet-ready: **5–8 days** for a focused engineer. This is non-trivial but tractable.

**Mandatory infrastructure for live leverage trading (not optional):**
| Component | Complexity | Notes |
|---|---|---|
| Kill switch (hard close all) | Medium | Must work even if main process is dead — systemd oneshot or separate watchdog |
| Liquidation distance monitor | High | Poll position margin ratio every ~30s; alert + reduce at threshold |
| Exchange outage handler | High | Bybit has planned maintenance windows + unplanned outages; need circuit breaker |
| Position reconciliation | Medium | Compare local state vs exchange state on every boot and every N minutes |
| Funding fee tracker | Low | Bybit pushes these; need to model in P&L |
| Daily loss circuit breaker | Medium | Hard stop if realized+unrealized loss > X% of account in 24h |
| Audit log (immutable) | Low | Append-only CSV/SQLite; must not be writable by trading process |

Building this from scratch is 4–6 weeks of engineering. Using Freqtrade as base cuts it to 2–3 weeks — still significant.

---

### Mandatory Gates Before Live Capital

1. **Edge validation**: Backtest on 6+ months of tick/OHLCV data with realistic slippage (0.05–0.1% per side), funding fees, and borrow costs showing Sharpe ≥ 0.8 and max DD ≤ 20% on OOS data
2. **Testnet paper run**: 2+ weeks of live testnet execution with zero crashes, zero missed stops, zero reconciliation errors
3. **Risk controls audit**: Independent review of kill switch, liquidation monitor, and circuit breaker — verify they fire correctly under simulated failure conditions
4. **Atlas gate**: #215 overlay evidence collected AND #399 sizing/capacity unblocked (demonstrates our ability to operationalize risk systems before adding another live system)

---

### Mandatory Risk Limits (If Ever Live)

- Max leverage: **3x** (Bybit allows 100x — hard-cap in config, not just parameter)
- Max capital: **$500** initial, no increase until 30+ closed trades with positive expectancy
- Max position size: 2% of account per trade
- Daily loss limit: 5% of account → hard stop, no new trades until next day
- Liquidation distance: never open if margin ratio < 30%; reduce if drops below 20%
- Markets: BTC/ETH perpetuals only; no altcoin leverage

---

### Preferred Technical Approach

**Freqtrade/Midas base with surgical additions.** Don't rebuild order management — it's solved. Add the leverage-specific safety layer (liquidation monitor, funding tracker, circuit breaker) as an independent watchdog process, not embedded in the trading loop. This isolates safety from strategy logic.

---

### Priority vs Atlas Blockers

**Atlas #399, #400, #215 come first.** This is not a scheduling preference — it's an engineering capacity constraint. We have one primary engineer. Starting a new leveraged system while Atlas has unresolved sizing architecture (#399) and insufficient overlay data (#215) means two concurrent live risk systems with unresolved issues. That's how you get surprised.

**Rank**: Atlas #215 → Atlas #399 → Atlas #400 → *then* crypto testnet spike.
