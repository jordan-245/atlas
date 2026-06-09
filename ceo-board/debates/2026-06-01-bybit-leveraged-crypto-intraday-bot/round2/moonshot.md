## MOONSHOT — Final Position

**Vote: CONDITIONAL_ACCEPT_RESEARCH_ONLY**

---

### Final Scope (2–3 Weeks, Hard Bounded)

Week 1: Midas/Freqtrade archaeology only — dependency audit, Bybit API v5 compatibility check, backtest harness validation against historical BTC/ETH perp data. **Output**: go/no-go on Midas as viable base. No testnet connection yet.

Week 2–3: If week 1 passes — run one strategy on Bybit testnet (paper mode, zero real keys). Measure edge signal: minimum 50 simulated trades, Sharpe ≥ 0.8 after 0.06% taker + funding model + 0.1% slippage. **Output**: edge validation report.

Total: one person, part-time, zero capital, zero Atlas interference.

---

### Non-Negotiable Live Gates

1. SP500 ≥ 30 closed trades + AUM > $25K (prior conditional stands — no waiver)
2. Atlas `approval=true` before any crypto live capital
3. #215 overlay evidence collected + #399 sizing unblocked
4. Sharpe ≥ 0.8 OOS (not in-sample) after full fee/funding model
5. Kill switch, liquidation monitor, circuit breaker all independently tested before live keys touch anything

---

### Kill Criteria

Kill the spike immediately if:
- Week 1 Midas audit reveals >2 weeks of remediation work to reach testnet-ready
- Any Atlas blocker (#399, #400, #215) demands engineering attention from the same person running the spike
- 50+ paper trades produce Sharpe < 0.6 after fees

No zombie projects. If it's killed, the file closes — not paused.

---

### Concession to Risk's DEFER Position

Risk is right that none of the prior conditional gates are met. I'm not pretending otherwise. The concession: **if week 1 Midas audit shows the codebase is significantly degraded** (API breaking changes, major dependency rot, scope underestimated), we immediately adopt Risk's DEFER position and revisit only after Atlas hits `approval=true`. The research spike is only justified if the infrastructure cost is genuinely low. If it isn't, Risk wins.

---

### Why Not DEFER Outright

The 3-week spike costs almost nothing if staffed correctly. The edge opportunity in crypto perpetuals is real and uncorrelated to Atlas equity returns. Deferring until Atlas is fully proven could mean deferring 6–12 months, during which crypto market structure changes. The asymmetry favors a cheap look. But I accept that "cheap look" is the operative condition — not a blank check to build.

**Prove the edge. Then build. Not the other way around.**
