## Vote: DEFER

---

### Top Recommendation

Do not start this project now — not even research/paper mode. The preconditions for safe exploration don't exist yet. Revisit after Atlas achieves `approval=true` and closes its three current blockers.

---

### Reasoning (Risk Lens)

**This is the highest-risk proposal the board has seen.** Leveraged intraday crypto combines every amplifier simultaneously: leverage multiplies losses, crypto volatility is 3–5× equities, 24/7 uptime means a single unhandled edge case liquidates while no one watches, funding fees erode edge in both directions, and short holding periods demand near-zero latency execution. The expected-loss tail is not "lose 20%", it's "lose 100% of deployed capital in one bad session."

Prior board guidance was already conditional: SP500 30+ closed trades, AUM >$25K, BTC/ETH spot only, 10% cap. **None of those gates are met.** The Q2 memo explicitly said stop thinking about Midas/crypto. We are not re-litigating that decision without new evidence of edge.

The current Atlas state makes this worse, not better:
- `approval=false` — Atlas itself is not trusted in live mode yet
- #215 overlay: blocked/insufficient data
- #399 sizing/capacity: architecture blocker
- #400 knowledge relevance: 25/25 not-found failures

We haven't validated our *equity* system. Betting leveraged crypto capital before Atlas is proven is a category error.

---

### Mandatory Gates Before Even Research/Paper Phase

| Gate | Requirement |
|------|-------------|
| Atlas baseline | `approval=true` + 30+ closed SP500 trades |
| AUM | Live portfolio >$25K (current: ~$1.4K) |
| Blockers | #215, #399, #400 all resolved |
| Edge proof | Documented backtest Sharpe >1.0 after fees/funding/slippage on OOS data |
| Engineering bandwidth | Atlas roadmap at Phase 5 or later |

---

### Mandatory Risk Limits If Ever Live

1. **Max leverage**: 2× hard ceiling, no exceptions, config-enforced
2. **Capital allocation**: ≤5% of total portfolio (not 10% — leverage doubles the effective exposure)
3. **Liquidation distance**: Stop-loss mandatory at ≥30% above liquidation price — hard-coded, not advisory
4. **Daily loss kill switch**: Auto-halt if daily PnL < −2% of deployed capital
5. **Position reconciliation**: Every 5 minutes, halt on mismatch
6. **Max hold time**: Hard close at 4 hours regardless of position state
7. **Exchange outage handler**: Auto-close all positions if API fails >60 seconds
8. **Separate isolated margin only**: No cross-margin mode permitted
9. **Audit log**: Every order, fill, funding charge, reconciliation event written to SQLite before execution confirmed
10. **Paper gate**: Minimum 90 days paper trading, >200 completed trades, Sharpe >0.8 OOS before any live capital

---

### Preferred Technical Approach

If eventually approved: **Midas/Freqtrade base**, not custom. Custom exchange adapters, order management, and risk engines take months to harden. Freqtrade has liquidation guards, dry-run mode, and Bybit integration already tested. Custom infrastructure for leveraged 24/7 trading is a high-probability failure mode — a bug in a custom order router at 3am liquidates the account.

Risk condition: Midas must be audited for current compatibility (Bybit API v5, current Freqtrade version) before reactivation. Treat it as untrusted until verified.

---

### Priority Relative to Atlas Blockers

```
#215 (overlay evidence) > #399 (sizing architecture) > #400 (knowledge relevance) >> this project
```

This project is **not on the priority stack** until Atlas blockers are closed. Engineering attention split here actively degrades Atlas timelines and increases the risk that Atlas never reaches `approval=true`.

---

**Bottom line**: The asymmetry is wrong. Downside is liquidation of real capital in a system we haven't proven, in a market we don't trade, using leverage we haven't modeled. Upside is faster feedback on an unvalidated edge. Defer until Atlas proves itself.
