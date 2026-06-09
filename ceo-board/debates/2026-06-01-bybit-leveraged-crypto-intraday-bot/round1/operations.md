## Vote: RESEARCH_TESTNET_ONLY

**Top Recommendation:** Authorize a time-boxed, zero-capital research spike using Midas/Freqtrade as the base — 2 weeks max, testnet only, no engineering diversion from Atlas critical path. If the spike produces no edge signal, close the file.

---

### Reasoning (Operations Lens)

**The 24/7 operational burden is the hidden killer here.** Intraday leveraged crypto isn't just a trading system — it's an ops problem that runs while everyone's asleep:

- Liquidation events don't wait for business hours
- Exchange outages (Bybit has history) need automated circuit breakers, not a human waking up
- Funding fees accrue every 8 hours regardless of position health
- Position reconciliation after an API timeout at 3am is a real failure mode

We have zero current infrastructure for 24/7 ops monitoring on any system. Atlas runs equities with market-hours constraints and still has #215 (overlay evidence) blocked. Adding a crypto system with continuous exposure and leverage before we've got Atlas's own gates closed is operationally reckless.

**Midas reactivation cost is unknown and likely non-trivial.** "70% complete, dormant" means:
- Dependencies have drifted (Python packages, Bybit API changes since March)
- Config/secrets are stale
- Test coverage is unknown
- Nobody knows what the remaining 30% is until we look

This is a 1–3 day archaeology task before we know if Freqtrade/Midas is actually a viable base or a write-off.

**Bandwidth is not there for parallel tracks.** #399 (sizing/capacity architecture) is a structural blocker for Atlas returns. #215 (overlay) needs 2 more weeks of live data. #400 (knowledge relevance) affects research quality. None of these are soft dependencies — they gate Atlas's ability to generate returns from its primary vehicle. Diverting engineering attention now means those blockers linger longer.

---

### Mandatory Gates Before Live Capital

1. **Edge validation**: Minimum 90-day backtest on BTC/ETH perps with realistic fees (taker 0.055%), funding (±0.01%/8hr), and 0.1–0.3% slippage model. Sharpe ≥ 0.8, max DD ≤ 20%.
2. **Testnet paper trading**: 4 weeks minimum, ≥50 completed round-trips, P&L within 15% of backtest expectation.
3. **Atlas conditions met**: SP500 ≥ 30 closed trades, AUM > $25K, #215 overlay gate resolved.
4. **Ops infrastructure complete**: Kill switch tested, liquidation-distance monitor live, daily loss limit enforced in code (not config), exchange outage handler validated.
5. **Explicit human approval** on each gate before advancing.

---

### Mandatory Risk Limits If Ever Live

| Parameter | Limit |
|-----------|-------|
| Leverage | ≤ 3× initial; ≤ 5× hard cap |
| Capital allocation | ≤ 5% of total AUM |
| Daily loss limit | 2% of allocated capital → auto-halt |
| Liquidation distance | Position closed if < 15% margin buffer |
| Max concurrent positions | 2 (BTC + ETH only initially) |
| Kill switch | Human-triggerable in < 30 seconds |
| Max holding period | 4 hours; forcible close at market open Asia |

---

### Technical Approach

**Use Midas/Freqtrade as base, but audit first.** Don't assume it works. Spend 1 day running a Midas health check: dependency audit, Bybit API connectivity test, strategy backtests against current data. If the codebase passes, use it. If it's a mess, the custom build time estimate will clarify whether this is worth pursuing at all.

---

### Priority vs Atlas Blockers

**Hard ordering: #399 → #215 → #400 → this project.**

No crypto spike should consume Atlas engineering cycles. This is a solo research effort (1 person, part-time, 2-week timebox) or it doesn't happen. If we can't ring-fence it from Atlas bandwidth, defer until at least one Atlas blocker (#399 or #215) resolves.
