## OPERATIONS — Final Position

**Vote: CONDITIONAL_ACCEPT_RESEARCH_ONLY**

Risk's DEFER argument is operationally sound but strategically too conservative. A clean-scoped audit costs almost nothing if we do it right. The question is whether we can ring-fence it.

---

### Exact Allowed Scope (Next 2–3 Weeks)

**Week 1 only: Midas archaeology audit** — one person, read-only, no code changes, no live keys.
Deliverable: a written health report covering:
- Bybit API v5 compatibility status
- Freqtrade version and dependency drift
- What "30% incomplete" actually means
- Estimated effort to testnet-ready

**Decision point at end of Week 1**: if audit shows >10 days to testnet-ready, **kill the project immediately** — not defer, kill. The opportunity cost isn't worth rebuilding a crumbling foundation.

**Weeks 2–3 only if audit passes**: Freqtrade paper mode on testnet, BTC/ETH perps only, zero live keys, zero leverage configured. Deliverable: 20+ completed paper round-trips with P&L accounting including synthetic funding fees and slippage.

**Hard constraint**: This work must not consume any Atlas engineering cycles. If the same engineer is touching #399 or #215, this stops immediately.

---

### Non-Negotiable Live Gates

Before any Bybit live keys are connected, ALL of the following must be true — no exceptions, no partial credit:

1. Atlas `approval=true` with ≥30 closed SP500 trades
2. AUM >$25K live
3. #215 overlay gate resolved
4. #399 sizing architecture unblocked
5. ≥50 paper testnet round-trips with Sharpe ≥0.8 after fees/funding/slippage
6. Kill switch tested and proven under simulated API failure
7. Explicit human approval on each gate before advancing

---

### Kill Criteria

Stop the project immediately if any of:
- Week 1 audit shows >10 days to testnet-ready
- Atlas engineer gets pulled onto this
- Week 2–3 paper run shows Sharpe <0.5 after fees
- Bybit API v5 connector requires custom build (not Freqtrade-native)
- Any live key is connected before all live gates pass

---

### Concession to Risk

Risk is correct that we've never operationalized a 24/7 system. Atlas runs during market hours — that's a fundamentally different ops profile. The liquidation-at-3am failure mode is real and we have zero current infrastructure for it. If the Week 1 audit shows the Midas ops infrastructure is incomplete (no watchdog, no exchange outage handler, no reconciliation loop), I would upgrade my position to DEFER. The audit has to explicitly assess those components, not just the trading logic.

**Bottom line**: 1-week audit, hard kill criteria, zero Atlas bandwidth consumed. If the audit clears, 2-week testnet paper run. Everything else is gated behind Atlas proving itself first.
