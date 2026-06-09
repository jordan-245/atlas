## TECHNICAL — Final Position

**Vote: CONDITIONAL_ACCEPT_RESEARCH_ONLY**

---

### Allowed Scope (Next 2–3 Weeks)

**Week 1 — Midas Audit Only.** One engineer, read-only archaeology:
- Bybit API v5 compatibility check (v3 vs v5 is a breaking change)
- Freqtrade version delta and dependency health
- Identify what the remaining ~30% of Midas actually is

Deliverable: a written verdict — "resurrect in N days" or "write-off." If resurrection cost exceeds 5 days, kill the spike immediately. No code changes in week 1.

**Weeks 2–3 — Testnet Only (conditional on audit passing).** Freqtrade paper mode against Bybit testnet:
- One strategy, BTC/ETH perpetuals only
- No live keys connected at any point
- Kill switch and liquidation-distance monitor must exist before testnet trading begins — not after

---

### Non-Negotiable Live Gates

These are engineering hard-stops, not soft guidelines:

1. **Bybit API v5 connector verified clean** — not assumed, tested
2. **Kill switch operates independently of main trading process** (systemd watchdog or equivalent) — verified under simulated main-process crash
3. **Liquidation-distance monitor polling ≤30s** — alerts and auto-reduces before margin ratio hits 15%
4. **Position reconciliation on boot and every 5 minutes** — halt on mismatch
5. **Daily loss circuit breaker in code, not config** — 5% of allocated capital, hard halt
6. **Atlas #215 overlay gate resolved** before any live capital discussion

---

### Kill Criteria

Stop immediately if any of these occur:

- Midas audit returns >5 days resurrection cost
- Testnet run has >1 missed stop-loss or >1 reconciliation error in the 2-week window
- Backtest Sharpe <0.8 after fees/funding/slippage on OOS data
- Atlas #399 or #215 shows signs of slipping — all bandwidth returns to Atlas

No exceptions. "Almost there" is not a pass.

---

### Concession to Risk

Risk's DEFER position is technically correct that the preconditions aren't met. The concession I'll make: **the audit in week 1 is a go/no-go gate, not a commitment to proceed.** If Midas is a mess — stale deps, broken connector, unclear scope — we defer the entire project and don't touch it again until Atlas reaches `approval=true`. The audit itself is low-risk (read-only, zero capital), but it earns the right to continue only if the result is clean. Risk should accept this as a structured DEFER-with-early-exit rather than an open-ended research project.

---

**Bottom line**: Three weeks of read-only archaeology plus testnet-only paper trading costs us almost nothing if the Midas audit comes back clean. If it doesn't, we've answered the question cheaply. Atlas #399/#215/#400 remain primary. No live keys, no leverage, no capital. The moment any Atlas blocker shows signs of slipping, this stops.
