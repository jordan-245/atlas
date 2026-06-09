# Atlas Live-Edge Diagnostic (SP500) — 2026-06-03

**Question:** Is the ~3-month flat equity curve *expected variance* or a *broken edge*?
**Answer:** Neither cleanly — it's a **thin, real gross edge being eroded to flat by (a) a quantified execution leak and (b) a sharp May regime break**, with (c) the strategy mix tilted toward the *weaker* strategy. Partly fixable, but the live edge is currently thin/unproven.

## Data (data/atlas.db, closed trades 2026-03-18 → 06-02)
- **Whole account flat:** ~$5,134 → ~$5,208. Total realized across all sleeves **+$101** (~+2% / 2.7mo).
- **SP500:** 54 closed, **+$222**, win **43%**. The other sleeves (commodity/sector ETFs) are **net ~−$120** (dragging the account to flat).
- **≥30 SP500 trades gate: MET (54).** Binding gates are approval (human) + AUM>$25K (capital, not returns).

## The three things going on

### 1. Sharp regime break in May — not normal variance
| month | n | win% | PF | exp/trade |
|---|---:|---:|---:|---:|
| Mar | 13 | 69% | 2.56 | +$7.19 |
| Apr | 25 | 52% | 2.09 | +$8.10 |
| **May** | 15 | **7%** | **0.26** | **−$4.90** |
| Jun | 1 | 0% | — | −$0.90 |
A 52–69% → **7%** win-rate collapse over 54 trades is far too sharp for a stationary 50%+ process — it's a **regime break / non-stationarity**, not variance. momentum_breakout (the headline strategy) needs trend; May chop killed it. (Direct parallel to the crypto illiquidity finding: breakout/momentum edges die in chop.)

### 2. A quantified EXECUTION leak (the most fixable part)
- **Same-bar round trips: 9 trades, −$66.8** (17% of SP500 trades) — entry + stop fill on the same bar (opening-bar volatility blowing through tight ATR stops). This is the known, deferred #316 (entry-delay) anti-pattern. **−$67 ≈ ~30% of gross winner contribution and roughly the entire net realized PnL.**
- **trailing_stop exits: −$37.2** (60% "win" but net negative — giving profits back).
- By contrast, **signal-driven exits are strongly positive** (signal +$72.8, signal_exit +$52.6, take_profit +$49.2, all ~100% win). **The SIGNAL has real predictive value when trades reach their intended exits; the STOP/execution layer is where the edge leaks.**

### 3. The strategy mix is tilted toward the weaker strategy
| sp500 strategy | n | win% | exp/trade | PF |
|---|---:|---:|---:|---:|
| **momentum_breakout** (current headline) | 29 | 28% | +$2.67 | 1.38 |
| **connors_rsi2** (mean-reversion) | 9 | 56% | **+$7.80** | **2.55** |
| mean_reversion (disabled) | 4 | 100% | +$32.3 | inf |
| trend_following | 3 | 33% | −$11.5 | 0.12 |
| sector_rotation | 4 | 50% | −$5.1 | 0.49 |
The **mean-reversion family is outperforming the breakout strategy the config leans on** (small samples, but directionally clear — and consistent with May being choppy). Several other strategies are net-negative drag.

## Verdict
**Not a dead flagship, but not a proven one either.** The signal layer works; the net result is flat because ~$67 leaks to same-bar stops, ~$37 to trailing givebacks, the breakout strategy is regime-fragile (dies in chop), and the better mean-reversion strategies are under-weighted while losing sleeves drag. This is **thin-and-leaky, not broken-and-hopeless** — but it confirms the live edge is currently **unproven**, so gating crypto behind "Atlas compounds to $25K via returns" is unrealistic (that's a capital decision).

## Recommended interventions (highest ROI first)
1. **Plug the execution leak** — ship the deferred #316 entry-delay / same-bar mitigation and review trailing-stop logic. Concrete, ~$67+$37 reclaimable; turns flat into modestly positive on the *same* signal.
2. **Rebalance the strategy mix to what's working live** — up-weight connors_rsi2 / mean-reversion, regime-gate or shrink momentum_breakout in chop (same regime-filter lesson as crypto), prune the net-negative sleeves.
3. **Formal variance-vs-broken test** — re-run walk-forward/OOS on momentum_breakout to confirm whether the live decay matches its backtest distribution (no clean stored baseline existed to compare against — a gap worth closing).

## Strategic implication
Atlas's flatness is **partly fixable** (execution + strategy mix), so it's not a reason to abandon it — but it is **not** currently a viable *compounding path* to the $25K gate. The AUM gate is a **capital/funding decision**, decoupled from engineering. Allocation between Atlas and the (better-validated) crypto edge should be re-weighed on **merit + operational readiness**, not incumbency.

---

## RESOLUTION (2026-06-03) — rigorous evidence corrected 2 of 3 planned actions

Checking the team's prior rigorous work (config decommission notes + knowledge layer) before acting changed the plan — the right discipline:

- **#3 (variance vs broken): NOT BROKEN.** Knowledge layer (research_best, last measured 2026-05-28): momentum_breakout sp500 backtest **Sharpe ~1.0** (bull_risk_on 1.20 / recovery 1.12 / transition_uncertain 0.74) — a *regime-dependent* edge. Live made money Mar–Apr (favourable) and gave it back in May (transition/chop → matches the 0.74 weak-regime). The flat 54-trade window is **within expectation for a regime-dependent Sharpe-1.0 strategy**, not a broken edge. (Fresh cli_backtest launched to confirm.)
- **#2 (rebalance to mean-reversion): REJECTED — disciplined.** connors_rsi2 was already rigorously decommissioned (#340: clean solo Sharpe −0.51, p=0.63, *no edge* on sp500). Its +$7.80/56% in the live data is a **9-trade small-sample artifact**. Re-enabling it would be performance-chasing against a rigorous prior decision — NOT done.
- **#1 (execution leak): EXECUTED the root-cause fix.** The live `atr_stop_mult` was **0.61 — roughly half the research-validated optimum (~1.2; brain kept 1.19–1.26)** and had no research support. That anomalously tight stop is the prime cause of the same-bar round-trips (−$67/9 trades). Promoted **0.61 → 1.2** via the risk gate (verdict=allow) + timestamped backup. **Risk-NEUTRAL** (risk-based sizing keeps $/trade constant — fewer shares, wider stop). Reversible. The intraday #316 entry-delay remains a separate future item (needs 5-min data).

**Net:** Atlas wasn't broken — it was a regime-dependent Sharpe-1.0 strategy hobbled by a misconfigured (too-tight) stop leaking to same-bar exits, in an unfavourable May regime. The stop fix (live next cycle) should reduce the leak. But momentum_breakout is still only ~1.0 Sharpe and regime-dependent, so Atlas remains a modest single-strategy book — reinforcing that crypto-vs-equities allocation should be decided on merit, and that the live equities book lacks a *strong* edge.

## CONFIRMATORY BACKTEST (2026-06-03, fresh walk-forward, 447 trades, 198 tickers)
- **Sharpe 1.00, CAGR +25.9%, MaxDD 26.1%, PF 1.52, win 39.1%, expectancy +0.60R, edge p=0.0009 (significant)**; beats buy & hold (Sharpe 0.77, CAGR 15.4%); alpha 19.9%.
- Live win rate (43%) ≈ backtest (39%) — the win rate TRANSFERS; the dollar edge does not.
- **Backtest +0.60R vs live −0.53R (momentum_breakout):** the ~1.1R gap = the same-bar execution leak (full-risk losses on opening-bar stop-outs) + the unfavourable May regime. This confirms (a) #3 the edge is real & significant — NOT broken — and (b) the #1 stop-fix rationale (reduce same-bar exits → close the gap toward +0.60R).

## MONITORING (post-fix, autonomous)
Baseline (pre-fix): same-bar 18% all-time (10/55, −$67.7); **50% last-30d (8/16, −$38.4)**; momentum_breakout live avgR −0.53.
Fix effective: v3.2.5 promoted 2026-06-03 08:43 UTC → applies to trades entered next cycle onward.
Success criteria: post-fix same-bar rate materially <18% (ideally <10%); live avgR (entry_date≥2026-06-03 cohort) trends toward backtest +0.60R (>0); per-trade $ risk unchanged (risk-based sizing → neutral).
Watcher: scripts/monitor_same_bar_stops.py (daily Tue-Sat 08:30 AEST, escalation-gated). Checkpoint state: atlas_state sp500-stop-fix-monitor/checkpoint-2026-06-03.
Review: 2026-06-17 or after ≥15 post-fix closed trades. Rollback: restore active_config_backup_20260603084310.json.
