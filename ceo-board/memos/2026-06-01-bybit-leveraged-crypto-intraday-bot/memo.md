# Decision Memo: Should We Build a Bybit Leveraged Intraday Crypto Trading Bot?

**Date**: 2026-06-01  
**Decision**: CONDITIONAL ACCEPT — research/testnet only  
**Confidence**: High for rejecting live implementation; Medium for value of the spike  
**Vote**: 5 for, 0 against, 0 abstain

## Executive Summary

The board unanimously rejects implementing a live leveraged Bybit crypto bot now. It conditionally approves only a tightly bounded Midas/Freqtrade audit and, if that audit passes, a zero-capital BTC/ETH testnet research run. No live keys, no real capital, no leverage, no margin activation, no Atlas code changes, and no custom exchange infrastructure are approved.

## Context

The proposal is to create a Bybit leveraged intraday crypto bot making directional long/short trades held for a few hours. This is materially riskier than Atlas equity trading because it combines leverage, crypto volatility, 24/7 operation, funding costs, liquidation risk, exchange/API outages, and short holding periods. Prior board guidance allowed crypto only conditionally after SP500 proof, AUM growth, and BTC/ETH-only caps; later Q2 guidance explicitly deprioritized Midas/crypto while Atlas was unfinished.

Atlas remains the primary system. Recent Atlas work closed #219, #354, #387, #388, and #397, but #215 remains blocked and #399/#400 are now known blockers. Therefore this decision cannot authorize live crypto risk.

## Board Positions

### Revenue — Conditional Accept, Research Only
Revenue sees real opportunity in crypto perpetuals because liquidity and volatility create faster feedback loops. But revenue upside exists only if there is measurable edge after taker fees, funding, and slippage. Revenue supports a 3-week maximum research/testnet spike and rejects production build before edge validation.

### Risk — Conditional Accept, Narrow Concession
Risk initially voted defer, citing the proposal as the highest-risk trading idea so far. Risk changed to conditional accept only because the approved scope is audit/testnet with zero live keys or capital. Risk keeps prior live gates intact: Atlas approval, 30+ closed SP500 trades, AUM > $25K, and #215/#399/#400 resolved before live capital.

### Technical — Conditional Accept, Research Only
Technical argues leveraged intraday crypto is architecturally harder than Atlas: real-time reconciliation, liquidation-distance monitoring, funding accounting, outage handling, kill switches, and 24/7 uptime. The only acceptable approach is to audit Midas/Freqtrade first and use existing battle-tested infrastructure if viable. Custom exchange/order infrastructure is rejected.

### Moonshot — Conditional Accept, Research Only
Moonshot supports a cheap look because crypto perps may offer faster learning and uncorrelated return potential. However, Moonshot also rejects building first: prove the edge, then build. If the Midas audit shows infrastructure rot, defer.

### Operations — Conditional Accept, Research Only
Operations highlights the 24/7 burden as the hidden killer. The approved scope must be ring-fenced from Atlas, one person only, and hard-killed if it requires more than a short Midas resurrection. Any Atlas slippage caused by this work kills the project.

## Decision Rationale

The board distinguishes three separate decisions:

1. **Live leveraged bot now** — rejected.
2. **Production build** — rejected.
3. **Read-only audit plus zero-capital testnet research** — conditionally accepted.

The core logic is information value. A one-week Midas audit can determine whether the dormant Freqtrade/Bybit base is viable or rotten. If viable, a short BTC/ETH testnet experiment can test edge without capital risk. If not viable, the project is killed cheaply.

The decision does not relax any prior crypto live gates. It explicitly preserves the prior conservative stance: no live crypto until Atlas is substantially more proven and the specific strategy demonstrates edge after fees/funding/slippage.

## Approved Scope

### Week 1 — Midas/Freqtrade Audit Only

Allowed:
- Inspect `/root/midas` read-only.
- Check dependency health and Freqtrade version drift.
- Check Bybit API v5 compatibility.
- Check whether Freqtrade supports Bybit futures/perps, shorts, isolated margin, and testnet cleanly.
- Verify no live Bybit keys are present or used.
- Inventory existing strategies, data, configs, tests, and backtest capability.
- Estimate effort to testnet-ready.
- Produce written audit report.

Not allowed:
- No live keys.
- No real capital.
- No margin account activation.
- No leverage configuration.
- No Atlas changes.
- No custom connector build.
- No production services/systemd changes.

### Weeks 2–3 — Conditional Testnet Research Only

Only if Week 1 passes:
- BTCUSDT and ETHUSDT perpetuals only.
- Freqtrade dry-run/testnet only.
- One strategy family only.
- Full fee/funding/slippage accounting.
- Produce edge validation report.

Minimum useful sample:
- 30–50 completed testnet/paper round trips, or enough historical backtest trades to reject quickly.

## Live Capital Gates

No live Bybit capital unless **all** are satisfied:

1. Atlas `approval=true`.
2. At least 30 closed SP500 live trades.
3. Live AUM > $25K.
4. #215 overlay gate resolved.
5. #399 sizing/capacity blocker resolved or explicitly superseded.
6. #400 knowledge/source relevance blocker resolved or not relevant to crypto decision.
7. Backtest OOS Sharpe >= 0.8 minimum, >=1.0 preferred, after taker fees, funding, and slippage.
8. Both long and short sides evaluated separately.
9. >=90 days paper/testnet or >=200 completed paper round trips before real capital, unless the board explicitly revises this.
10. Reconciliation errors = 0 unresolved.
11. Stop-loss misses = 0 tolerated.
12. Independent kill switch/liquidation monitor/circuit breaker review passed.
13. Explicit human approval at the live gate.

## Mandatory Risk Limits If Ever Live

Initial live pilot, if gates pass:

| Risk Control | Limit |
|---|---:|
| Initial capital | $100–$500 max |
| Markets | BTC/ETH perpetuals only |
| Margin mode | Isolated only |
| Leverage | 1x–2x preferred; 3x hard initial cap |
| Max concurrent positions | 1–2 max |
| Daily loss kill | 2%–5% of allocated capital |
| Weekly/peak drawdown halt | 10%–15% of allocated capital |
| Stop-loss | Exchange-native, placed immediately |
| Liquidation distance | Liquidation must be materially beyond stop; halt/reduce if margin buffer deteriorates |
| Averaging down | Forbidden |
| Martingale/grid recovery | Forbidden |
| Altcoin leverage | Forbidden at pilot stage |

## Kill Criteria

Kill or defer immediately if:

- Week 1 audit estimates >5–10 days to testnet-ready.
- Bybit support requires custom connector work.
- Live keys are created, connected, or discovered in use.
- Atlas #215/#399/#400 work slips because of this project.
- Freqtrade/Bybit compatibility is not clean.
- Backtest Sharpe <0.6 after fees/funding/slippage.
- Testnet has missed stop-losses or unresolved reconciliation errors.
- The project begins to require custom infrastructure before edge is proven.

## Phased Implementation Plan

### Phase A — Audit
Deliverable: `midas-bybit-audit-2026-06.md`

Checklist:
1. Project/dependency health.
2. Freqtrade version and Bybit futures support.
3. Bybit API v5/testnet compatibility.
4. Existing config/secrets hygiene.
5. Strategy/data/backtest inventory.
6. Risk control inventory.
7. Effort estimate to testnet-ready.
8. Go/no-go verdict.

### Phase B — Historical Edge Screen
If audit passes:
1. BTC/ETH only.
2. 5m/15m candles.
3. Directional long/short strategy family.
4. Fees: realistic taker assumptions.
5. Funding modeled.
6. Slippage modeled.
7. OOS split across bull/bear/chop.

### Phase C — Testnet Paper
If historical screen passes:
1. Bybit testnet only.
2. No live keys.
3. Freqtrade dry-run.
4. Reconciliation and stop-loss simulations.
5. Report live-testnet vs backtest drift.

### Phase D — New Board Review
Before any live deployment, reconvene the board with audit, backtest, testnet, risk-control, and Atlas-state evidence.

## Priority vs Atlas

Atlas remains primary. This project is below:

1. #215 overlay evidence / log-only gate.
2. #399 sizing/capacity architecture.
3. #400 knowledge relevance/spec repair.

The only reason to run the audit is that it is bounded, zero-capital, and can cheaply answer whether Midas remains a viable base. If it competes with Atlas engineering capacity, it stops.

## Review Date

After Week 1 audit, or immediately if any kill criterion trips.
