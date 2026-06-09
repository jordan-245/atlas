# Risk Expertise

## 2026-06-01 — Leveraged crypto bot risk pattern
Leveraged intraday crypto is a distinct risk class from spot crypto or daily equity strategies: leverage + 24/7 uptime + exchange/API failure + funding + liquidation risk + short holding periods. Board-approved posture: live implementation is rejected until Atlas is proven and explicit live gates pass. A zero-capital audit/testnet spike is acceptable only if no live keys/capital/leverage are touched and hard kill criteria stop scope creep.

Mandatory future gates for Bybit/perp systems: isolated margin, 1–2x preferred/3x initial cap, BTC/ETH only, exchange-native stops, independent kill switch, liquidation-distance monitor, 5-min reconciliation, outage circuit breaker, daily loss halt, OOS edge after taker fees/funding/slippage, clean testnet/paper, explicit human approval.
