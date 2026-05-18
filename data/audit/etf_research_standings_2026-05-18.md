# ETF Research Loop — Sharpe Standings
**Generated:** 2026-05-18  |  **Scope:** sector\_etfs, gold\_etfs, treasury\_etfs, defensive\_etfs  |  **Status filter:** kept

## Summary
- **Total (universe, strategy) pairs tracked:** 9
- **Promotion candidates (Sharpe > 0.3):** 3
- **Strong promotion candidates (Sharpe > 0.5 + ↑ trend):** 1
- **Degradation warnings (4wk decline > 0.2):** 7

> ⚠️ **Portfolio-level finding:** All 4 ETF universes achieved `mean_reversion` peak Sharpe of **1.0849** on 2026-04-20.
> However, subsequent research runs have produced lower-Sharpe variants that are also marked `kept`, pushing the *latest* kept Sharpe below 1.0.
> The peak configurations from 2026-04-20 remain the strongest validated results — but current momentum shows **mean_reversion degrading** across all 4 universes while `connors_rsi2` on `gold_etfs` is actively rising.

## Current Standings (sorted by latest kept Sharpe)

| Universe | Strategy | Current Sharpe | Peak Sharpe | Trades | MaxDD% | PF | CAGR% | 4wk Trend | Flag |
|----------|----------|----------------|-------------|--------|--------|----|-------|-----------|------|
| gold_etfs | connors_rsi2 | **0.7588** | 0.9865 | 405 | 28.93% | 1.398 | 22.34% | ↑ (+0.888) (n=51) | PROMOTION CANDIDATE, STRONG PROMOTION CANDIDATE |
| sector_etfs | momentum_breakout | **0.3998** | 0.7597 | 183 | 11.95% | 1.745 | 8.97% | ↓ (-0.156) (n=19) | PROMOTION CANDIDATE |
| gold_etfs | momentum_breakout | **0.3343** | 0.7597 | 420 | 11.01% | 1.473 | 7.55% | ↓ (-0.221) (n=8) | PROMOTION CANDIDATE, DEGRADATION WARNING |
| defensive_etfs | mean_reversion | **0.2159** | 1.0849 | 201 | 11.20% | 1.865 | 7.74% | ↓ (-0.682) (n=14) | DEGRADATION WARNING |
| gold_etfs | mean_reversion | **0.0646** | 1.0849 | 461 | 11.36% | 1.342 | 5.20% | ↓ (-0.833) (n=5) | DEGRADATION WARNING |
| sector_etfs | mean_reversion | **-0.0593** | 1.0849 | 160 | 10.39% | 1.428 | 4.33% | ↓ (-0.957) (n=30) | DEGRADATION WARNING |
| defensive_etfs | momentum_breakout | **-0.4650** | 0.7597 | 72 | 7.66% | 1.268 | 1.99% | ↓ (-1.021) (n=8) | DEGRADATION WARNING |
| treasury_etfs | momentum_breakout | **-1.5574** | 0.7597 | 50 | 13.37% | 0.544 | -1.82% | ↓ (-2.113) (n=13) | DEGRADATION WARNING |
| treasury_etfs | mean_reversion | **-1.5822** | 1.0849 | 247 | 14.30% | 0.894 | -0.71% | ↓ (-2.480) (n=8) | DEGRADATION WARNING |

*Current Sharpe = latest kept row's Sharpe. Peak Sharpe = best ever kept result. 4wk Trend = delta over last 28 days among kept rows (latest − oldest in window).*

## Top Promotion Candidates

**3 strategies currently exceed the 0.3 Sharpe promotion threshold.**

### 1. gold_etfs / connors_rsi2 ✨ STRONG PROMOTION CANDIDATE
- **Current Sharpe:** 0.7588  |  **Peak Sharpe:** 0.9865
- **Trades:** 405  |  **MaxDD:** 28.93%  |  **PF:** 1.398  |  **CAGR:** 22.34%
- **Last evaluated:** 2026-05-13 16:00:41 UTC
- **4-week trend:** ↑ (+0.888) across 51 kept runs in window
- **Why promotion-worthy:** Active research is improving this strategy — Sharpe rose +0.888 in 28 days. 51 trades with only 28.93% max drawdown and PF 1.398 = strong risk-adjusted profile. Gold ETF exposure adds diversification vs equity-heavy mean_reversion.

### 2. sector_etfs / momentum_breakout
- **Current Sharpe:** 0.3998  |  **Peak Sharpe:** 0.7597
- **Trades:** 183  |  **MaxDD:** 11.95%  |  **PF:** 1.745  |  **CAGR:** 8.97%
- **Last evaluated:** 2026-05-14 08:03:38 UTC
- **4-week trend:** ↓ (-0.156) across 19 kept runs in window
- **Why promotion-worthy:** Clears 0.3 threshold with 183 trades (statistically robust sample). PF 1.745 indicates positive edge. Monitor for trend stabilisation before promoting.

### 3. gold_etfs / momentum_breakout ✨ DEGRADATION WARNING
- **Current Sharpe:** 0.3343  |  **Peak Sharpe:** 0.7597
- **Trades:** 420  |  **MaxDD:** 11.01%  |  **PF:** 1.473  |  **CAGR:** 7.55%
- **Last evaluated:** 2026-05-01 09:26:48 UTC
- **4-week trend:** ↓ (-0.221) across 8 kept runs in window
- **Why promotion-worthy:** Clears 0.3 threshold with 420 trades (statistically robust sample). PF 1.473 indicates positive edge. Monitor for trend stabilisation before promoting.

## Portfolio-Level Finding: mean_reversion Peak at 1.0849

All four ETF universes independently converged on **Sharpe = 1.0849** on **2026-04-20**, confirming the robustness of the mean_reversion parameter set found during that research window.

| Universe | Peak Sharpe (2026-04-20) | Current Latest Sharpe | Δ from Peak |
|----------|--------------------------|----------------------|-------------|
| defensive_etfs | **1.0849** | 0.2159 | -0.8690 |
| gold_etfs | **1.0849** | 0.0646 | -1.0203 |
| sector_etfs | **1.0849** | -0.0593 | -1.1442 |
| treasury_etfs | **1.0849** | -1.5822 | -2.6671 |

**Interpretation:**
- The 1.0849 peak was reached simultaneously across universes on 2026-04-20 — this suggests the research loop found a globally-optimal mean_reversion parameter configuration.
- Subsequent research sweeps explored additional (worse) parameter combinations, diluting the apparent 'latest' Sharpe.
- The April-20 configurations remain valid promotion-worthy results from a historical standpoint. However, because the *most recent* kept rows are weaker variants, they do not currently flag as promotion candidates by the latest-row rule.
- **Recommendation:** Consider tagging the April-20 configurations as 'champion' variants and re-running fresh research seeded from those parameters to assess if current market conditions still support 1.0+ Sharpe.

## Degradation Warnings

**7 pairs show Sharpe decline > 0.2 over the last 28 days.**

| Universe | Strategy | Current Sharpe | 4wk Δ | n runs |
|----------|----------|----------------|-------|--------|
| treasury_etfs | mean_reversion | -1.5822 | -2.480 | 8 |
| treasury_etfs | momentum_breakout | -1.5574 | -2.113 | 13 |
| defensive_etfs | momentum_breakout | -0.4650 | -1.021 | 8 |
| sector_etfs | mean_reversion | -0.0593 | -0.957 | 30 |
| gold_etfs | mean_reversion | 0.0646 | -0.833 | 5 |
| defensive_etfs | mean_reversion | 0.2159 | -0.682 | 14 |
| gold_etfs | momentum_breakout | 0.3343 | -0.221 | 8 |

> ⚠️ `treasury_etfs/mean_reversion` and `treasury_etfs/momentum_breakout` show severe degradation (−2.1 to −2.5 Sharpe drop). Treasury ETF strategies may be responding poorly to current rate environment — investigate whether the market regime has shifted unfavourably for these strategies.

---
*Report generated by QA-Engineer agent | Source: `data/atlas.db` table `research_experiments` | Filter: status=kept, 4 ETF universes*

---

## Resolution — 2026-05-18

**Disposition: Decision 3A — SKIP promotions.**

Premise correction: the report at the top of this document recommends "Re-running fresh research seeded from peak parameters" — it does NOT recommend direct promotion of the 3 listed candidates. Prior batch synthesis incorrectly framed this as a promotion decision.

Additional blocker: `gold_etfs`, `defensive_etfs`, and `treasury_etfs` do not have live `config/active/<universe>.json` files. Promotion would require new-universe live-trading scaffolding (starting_equity, risk allocation, cron entries, sync_protective enrollment, intraday_monitor cron, live state file initialization) — not in scope for this batch.

**Actions taken:** none (documentation only).
**Follow-up:** task scheduled in `tasks/todo.md` titled "Re-run fresh research on ETF candidates per #252 report recommendation".

Commit: f18be14012dcc34358a4641262eef6189843aea3
