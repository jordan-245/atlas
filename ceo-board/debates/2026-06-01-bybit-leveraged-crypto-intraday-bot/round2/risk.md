## RISK — Final Position

**Vote: CONDITIONAL_ACCEPT_RESEARCH_ONLY** *(narrow concession — conditions are strict)*

---

### What Changed My Vote

The four RESEARCH_TESTNET_ONLY positions are disciplined. They aren't proposing to trade — they're proposing to *audit* dormant code and run testnet with no capital at risk. I was conflating "approve the project" with "approve the spike." Those are different risk events. A contained 2-week Midas audit + testnet run with zero live keys has a bounded, recoverable downside. I can accept that — barely.

---

### Exact Allowed Scope (Next 2–3 Weeks)

**Week 1 — Audit only (read-only, no trading):**
- Dependency audit: Python packages, Bybit API v5 compatibility, Freqtrade version delta
- Config/secrets hygiene: verify NO live Bybit keys are present or accessible
- Scope the remaining 30% of Midas build
- Deliverable: written assessment — is Midas viable or write-off?

**Weeks 2–3 — Testnet paper only (if Week 1 passes):**
- Freqtrade dry-run mode against Bybit testnet feed
- BTC/ETH perpetuals only
- Zero leverage (dry-run mode)
- No live API keys connected at any point

**Hard stops on scope:** No live key generation. No margin account activation. No leverage configuration. No Atlas engineering time diverted.

---

### Non-Negotiable Live Gates (All Must Clear Before Any Live Capital)

1. Atlas `approval=true` with ≥30 closed SP500 trades
2. AUM >$25K in live portfolio
3. #215, #399, #400 all resolved
4. Backtest Sharpe ≥1.0 after taker fees + funding + 0.1% slippage on 2022–2025 OOS data, both long AND short
5. ≥90 days testnet paper trading, ≥200 completed round-trips, reconciliation errors = 0
6. Kill switch, liquidation monitor, and circuit breaker independently reviewed and verified under simulated failure

---

### Kill Criteria (Spike Ends Immediately If)

- Any live Bybit key is created or connected
- Midas audit reveals API incompatibility requiring >1 week of infrastructure work
- Atlas critical path (any of #215, #399, #400) is measurably delayed
- Week 1 audit concludes Midas is not viable and rebuild estimate >2 weeks

---

### Concession to My Round 1 Position

I was right that the prior conditions (SP500 30 trades, AUM >$25K) haven't been met and should not be waived. I maintain those as hard live-capital gates — they are not softened by this vote. The concession is narrow: **research and audit without capital risk is not the same as trading**. A zero-capital testnet spike with Atlas remaining primary and hard kill criteria is an acceptable information-gathering exercise.

**Bottom line:** Approved for audit + testnet only. The moment any live key is touched, this vote reverts to DEFER.
