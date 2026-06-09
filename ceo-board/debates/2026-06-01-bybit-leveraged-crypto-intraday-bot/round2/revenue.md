## REVENUE — Final Position

**Vote: CONDITIONAL_ACCEPT_RESEARCH_ONLY**

---

### Allowed Scope (Next 3 Weeks, Hard Ceiling)

**Week 1 only**: Midas/Freqtrade archaeology audit. One engineer, read-only. Deliverable: written assessment of Bybit API v5 compatibility, dependency state, and estimated hours to testnet-ready. If audit returns >8 days of work: **auto-kill, defer to Q3**.

**Weeks 2–3 (if audit passes)**: Freqtrade paper mode against BTC/ETH perpetuals only. Backtest one strategy with realistic fee model (0.055% taker + 0.01%/8hr funding + 0.1% slippage). Target: ≥100 simulated round-trips. No live Bybit keys, no leverage, no capital. Single engineer, zero Atlas engineering cycles consumed.

**Not allowed in this window**: Custom infrastructure, live keys, leverage configuration, altcoin markets, anything touching Atlas codebases.

---

### Non-Negotiable Live Gates (All Must Pass Before Any Capital)

1. **Atlas gate**: `approval=true` + ≥30 closed SP500 live trades + AUM >$25K
2. **Edge gate**: Backtest Sharpe ≥0.8 after full fee model, both long AND short sides, OOS data
3. **Paper gate**: ≥30 live testnet trades, P&L within 20% of backtest expectation
4. **Atlas blockers closed**: #215 resolved, #399 unblocked
5. **Explicit human approval** at each gate — no autonomous advancement

---

### Kill Criteria (Stop Immediately If Any Triggered)

- Audit returns >8 days of work to reach testnet
- Backtest Sharpe <0.6 after fees on either long or short side
- Any Atlas task slips due to bandwidth bleed from this project
- Freqtrade/Bybit API incompatibility requiring custom connector build

---

### Concession to Risk

Risk is correct that we're pattern-matching to distraction. The Q2 "stop thinking about Midas" memo exists for a reason. I'm voting CONDITIONAL_ACCEPT only because the audit-first gate makes the spike self-terminating if the foundation is rotten — and a 1-week archaeology task costs almost nothing while producing definitive evidence either way. If the audit comes back clean, we've paid $0 in capital risk for a real answer. If it comes back messy, we defer with data instead of assumption.

**Revenue bottom line**: The edge opportunity is real but unproven. The only revenue argument for proceeding is faster feedback loops — and that argument evaporates entirely if we build before validating the edge. Three weeks, no capital, one person, binary outcome. That's a bet worth taking.
