---
id: 2026-06-01-bybit-leveraged-crypto-intraday-bot
title: "Should We Build a Bybit Leveraged Intraday Crypto Trading Bot?"
created: 2026-06-01T05:06:11.146Z
profile: standard

---

# Should We Build a Bybit Leveraged Intraday Crypto Trading Bot?

## Situation

The user wants to create a crypto trading bot using Bybit leveraged trading, targeting directional long/short trades held for a few hours at most. This would be a new high-risk trading system or reactivation of dormant Midas crypto work. Current portfolio context: Atlas is the active primary project but still has live approval=false and recent board work concluded not to increase exposure or expand universes until research gates are trustworthy. Atlas task #219 is now complete, #354 fixed, #387 sizing and #388 additive strategy both returned NO-PROMOTE due architecture/portfolio construction blockers, and #215 overlay remains blocked/insufficient data. Prior CEO journal notes show an earlier crypto expansion decision was conditional: crypto only after SP500 had 30+ closed trades, AUM > $25K, start BTC/ETH only, 10% allocation cap. Midas crypto/Bybit/Freqtrade was noted as dormant and ~70% complete in March, but later Q2 memo explicitly said stop thinking about Midas/crypto revival and focus. The proposed bot is more aggressive than spot crypto: leveraged, intraday, long/short, short holding periods.

## Stakes

This decision can create material financial loss because leverage, crypto volatility, liquidation risk, exchange/API failure, slippage, funding fees, and 24/7 operations compound quickly. Even small capital can be lost if leverage/risk controls are weak. It also competes for engineering attention with Atlas blockers (#399 sizing/capacity, #400 knowledge relevance, #215 overlay evidence). Upside is faster feedback than equities and potentially more frequent opportunities, but only if a robust edge exists after fees/funding/slippage. Building without edge validation could waste weeks and introduce a system that trades continuously with high operational risk.

## Constraints

No live trading or real capital deployment without explicit human approval and a separate risk gate. Start research/paper only if approved. Use existing Midas/Freqtrade/Bybit work if it reduces build risk, but do not connect live keys or enable leverage until paper gates pass. Initial markets should be restricted to highly liquid majors (BTC/ETH perpetuals) if pursued. Max initial capital should be tiny and capped; leverage should be low despite Bybit availability. Must include kill switch, liquidation-distance checks, stop-loss enforcement, exchange outage handling, position reconciliation, fees/funding/slippage modeling, and audit logs. Engineering bandwidth is limited and Atlas remains primary.

## Key Questions

1. 1. Should we implement this now, defer it, or only run a research/paper spike?
2. 2. What minimum evidence of edge is required before any live Bybit leverage trading?
3. 3. If pursued, should we build custom infrastructure or use Freqtrade/Midas as the base?
4. 4. What risk limits are mandatory for leverage, capital allocation, stops, liquidation distance, daily loss, and kill switches?
5. 5. What phased implementation plan and go/no-go gates should govern the project?
6. 6. How should this rank against current Atlas blockers (#399, #400, #215)?
