## Revenue & Growth Strategist — Initial Position

**Vote: RESEARCH_TESTNET_ONLY**

---

### Top Recommendation

Run a time-boxed 3-week paper/testnet spike to validate whether a statistically significant edge exists *after* fees, funding, and slippage. If edge gates pass, revisit with live capital. Do not build production infrastructure until then.

---

### Reasoning (Revenue Lens)

The TAM is real: crypto perpetuals trade ~$50B+ daily on Bybit, 24/7, with high volatility = frequent setups. Intraday leveraged long/short is one of the highest-frequency revenue opportunities available to retail. I'm not dismissing it.

But the revenue math only works with edge. Here's why I can't vote IMPLEMENT_NOW:

- **Funding fees alone destroy unedged strategies.** On 5x leverage, 8-hour funding at 0.01–0.05% = 0.05–0.25% per round-trip. You need a strategy that overcomes this *before* slippage.
- **Without 30+ closed Atlas trades, we have no baseline for comparing system performance.** We can't evaluate whether crypto adds revenue or just noise to a portfolio that hasn't proven itself in equities yet.
- **Unit economics are unknown.** Win rate, average R, holding period PnL — none of this is quantified. Building infrastructure before knowing the edge inverts the correct order of operations.
- **Leverage is a multiplier on both sides.** If the edge assumption is wrong by 20%, capital doesn't get dinged 20% — it gets wiped. That's not growth, that's a lottery ticket.

The *revenue opportunity* is significant. The *revenue certainty* is near zero without evidence. Data-driven means I demand the evidence first.

---

### Mandatory Gates Before Live Capital

1. **Edge validation**: Minimum 200 paper/backtest trades with Sharpe ≥ 0.8 *after* realistic fees (0.04% taker + funding + 0.1% slippage model). Not before.
2. **Atlas gate cleared**: SP500 must have ≥30 closed live trades with approval=true. Atlas is the primary revenue engine; don't dilute bandwidth on a speculative second system while the first is still blocked.
3. **Paper testnet run**: Minimum 2 weeks of real-time paper trading (not just backtest) with consistent performance before any Bybit live keys are connected.
4. **Kill switch verified**: Must demonstrate kill switch works in staging *before* any live capital, not as an afterthought.

---

### Mandatory Risk Limits (If Ever Live)

- **Max leverage**: 3x hard cap for first 90 days. Bybit offers 100x — that's irrelevant; we cap at 3x.
- **Max capital**: $500 initial, capped until 60-day live Sharpe ≥ 0.7.
- **Daily loss limit**: -5% of allocated capital triggers auto-halt, requires manual restart.
- **Max position size**: 25% of allocated capital per trade.
- **Liquidation distance**: Stop-loss must be placed such that liquidation distance is ≥ 3× the stop.
- **Max drawdown**: -15% from peak triggers system pause + board review.
- **Funding cost model**: System must account for funding fees in position sizing — not optional.

---

### Preferred Technical Approach

**Midas/Freqtrade base, not custom.** The 70%-complete Midas work represents ~2–3 weeks of saved build time. Custom infrastructure from scratch adds 4–6 weeks of operational risk (exchange connectivity, reconciliation, order management) that Freqtrade already handles. Use Freqtrade's paper trading mode for the testnet spike — zero live key risk, real market data.

The spike deliverable is: *one strategy* running on Freqtrade paper mode against BTC/ETH perpetuals for 3 weeks, with a performance report at the end. That's it. No new infrastructure, no live keys.

---

### Priority vs Atlas Blockers (#399, #400, #215)

**This ranks below all three Atlas blockers.** Here's the stack rank:

1. #399 (sizing/capacity architecture) — blocks Atlas revenue scaling
2. #215 (overlay evidence) — blocks Phase 4 completion
3. #400 (knowledge relevance) — blocks research quality
4. **Crypto paper spike** — can run in parallel *only* if bandwidth allows, zero Atlas interference
5. Live crypto implementation — deferred until Atlas gates clear

The revenue opportunity here is real but speculative. Atlas is generating live PnL today. You don't abandon a working (if constrained) revenue engine to chase an unvalidated one.

**Bottom line**: 3-week paper spike approved from a revenue perspective. Everything else is premature until we have edge evidence.
