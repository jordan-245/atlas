# Atlas Improvement Report

**Date:** 2026-02-26  
**Based on:** Full codebase audit of 16,023 lines across 50+ files

---

## Current State Summary

| Dimension | Status |
|-----------|--------|
| **Markets** | ASX 200 (live, Moomoo), S&P 500 (paper, unoptimized v1.0) |
| **Active Strategies** | 3 of 10: Mean Reversion, Trend Following, Opening Gap |
| **Backtest (ASX)** | CAGR 11.9%, Sharpe 0.79, PF 1.54, MaxDD 6.8%, 408 trades |
| **Live Capital** | $5,000 AUD, 10 positions open, $3,621 cash |
| **Automation** | 2x daily cron (premarket/postclose), Telegram approval bot |
| **Dashboard** | Static HTML at atlas.getflowtide.com via Cloudflare Tunnel |
| **Data** | yfinance OHLCV, parquet cache, ~500 tickers across both markets |
| **Optimization** | Parallel coordinate descent, walk-forward validation, perturbation stability |

### Known Weaknesses (from optimization guide)
- Parameter sensitivity: "sharp peak" — perturbation collapses in 2/10 trials
- S&P 500 config is copy-paste of ASX defaults (never optimized for US)
- 6 disabled strategies need work before they're viable
- Dynamic sizing, regime filter, fee-aware filter all tested & disabled
- OOS time-split validation structurally broken (data too short)
- Only long-only — no shorting capability

---

## 1. New Market Universes

### 1A. FTSE 100 / LSE (🔴 High Impact, Medium Effort)

**Why:** Different timezone (UTC), low correlation with ASX/US sessions, GBP diversification. Mean reversion strategies often work well on UK mid-caps.

**Implementation:**
- Create `markets/ftse.py` (yfinance suffix `.L`, benchmark `ISF.L`)
- Add `config/active/ftse.json` — UK-specific fees (IBKR: ~£1/trade)
- Trading hours: 08:00–16:30 GMT — no overlap with ASX, partial overlap with US
- Universe: ~100 FTSE 100 + top 50 FTSE 250 by liquidity
- Risk: 0.5% per trade, £5,000 starting capital
- Broker: IBKR adapter (Moomoo doesn't cover UK)

**Effort:** ~2 days (market profile + config + universe + broker adapter skeleton)

### 1B. Nikkei 225 / TSE (🟡 Medium Impact, Medium Effort)

**Why:** Highly liquid, JPY diversification, strong momentum/MR effects documented in academic literature, timezone fills gap between AU close and EU open.

**Implementation:**
- Create `markets/nikkei.py` (suffix `.T`, benchmark `1321.T`)
- Trading hours: 09:00–15:00 JST
- Universe: Top 100 liquid Nikkei components
- Challenge: yfinance JPY data quality can be spotty for smaller names

**Effort:** ~2 days

### 1C. ASX Small Ordinaries / Micro-Caps (🟡 Medium Impact, Low Effort)

**Why:** Uses existing ASX infrastructure. Small caps have stronger mean-reversion effects. Higher volatility = bigger ATR-based stops = more room for strategies to work with small capital.

**Implementation:**
- Create `markets/asx_small.py` — 200 tickers from S&P/ASX Small Ordinaries
- Lower `min_market_cap` to $100M, raise `min_median_daily_value` to $500K
- Wider stops (higher ATR), smaller position sizes
- Same Moomoo broker, same fees

**Effort:** ~0.5 days (just a new market profile + config, everything else reuses)

### 1D. Crypto (BTC/ETH majors via yfinance) (🟢 Low Impact, Low Effort)

**Why:** 24/7 market, uncorrelated returns, yfinance supports `BTC-USD` etc. Good for testing strategies on a completely different asset class.

**Implementation:**
- `markets/crypto.py` — 20-30 top coins by market cap
- No suffix, no broker (paper only initially)
- Modify trading hours to support 24/7 (or treat as "always open")
- Adjust risk parameters for higher volatility (ATR multipliers 2-3x wider)

**Effort:** ~1 day

### Recommendation: Start with **1C (ASX Small Ords)** for fastest value, then **1A (FTSE)** for true diversification.

---

## 2. Strategy Improvements

### 2A. Activate & Optimize Disabled Strategies (🔴 High Impact, High Effort)

You have **6 fully-implemented but disabled strategies**. This is low-hanging fruit — the code exists, it just needs market-specific optimization.

| Strategy | Status | Potential | Priority |
|----------|--------|-----------|----------|
| **BB Squeeze** | Disabled (v9.2 showed score jump -2.45→+8.34) | High — volatility compression works well on ASX | **P1** |
| **Short-Term MR** | Disabled | High — RSI(2) + IBS is proven academic edge | **P1** |
| **MTF Momentum** | Disabled | Medium — weekly/daily alignment is sound | **P2** |
| **Momentum Breakout** | Disabled | Medium — needs tighter universe filter | **P2** |
| **Sector Rotation** | Disabled (min_conf=0.80) | Medium — top-down approach diversifies signal source | **P3** |
| **Dividend Capture** | **Not implemented** (stub only) | Low — franking credit edge is real but small | **P3** |

**Action Plan:**
1. Run parallel backtest grid for BB Squeeze + Short-Term MR on current ASX data
2. Use the v9.4 robust scoring function (min 15 trades, PF cap, trade ramp)
3. Walk-forward validate each independently before combining
4. Enable one at a time, measure incremental impact

### 2B. Short Selling Capability (🔴 High Impact, High Effort)

**Why:** Atlas is long-only. In bear markets (e.g., Jun-Sep 2025 noted in optimization guide), you sit idle. Short selling during downtrends doubles the opportunity set.

**Implementation:**
1. Extend `Signal.direction` to allow `'short'` (currently raises ValueError)
2. Add short-specific exit logic (cover on bounce, short squeeze protection)
3. Modify backtest engine: short positions need inverted P&L, margin tracking
4. Modify paper engine: short tracking, borrow cost simulation
5. Broker: Moomoo supports short selling for AU stocks (with borrow check)
6. New strategy: **Trend Following Short** — mirror of existing TF but for breakdowns

**Effort:** ~5 days (touches Signal, BaseStrategy, BacktestEngine, PaperPortfolio, broker)  
**Risk:** Moomoo short availability for ASX mid-caps may be limited

### 2C. Pairs Trading / Statistical Arbitrage (🟡 Medium Impact, High Effort)

**Why:** Market-neutral, lower drawdown, works in any regime. You already have a `cointegration_filter` module that was tested but disabled.

**Implementation:**
1. Reuse `utils/cointegration_filter` infrastructure
2. New strategy `strategies/pairs_trading.py`:
   - Identify cointegrated pairs (Engle-Granger or Johansen)
   - Trade z-score of spread: long underperformer + short outperformer
   - Built-in hedging removes market beta
3. Requires short selling capability (2B above)

**Effort:** ~4 days on top of 2B

### 2D. Overnight / Pre-Market Strategy (🟡 Medium Impact, Low Effort)

**Why:** Opening Gap is already the closest thing to this. But a dedicated overnight strategy that factors in US session moves for next-day ASX could capture the cross-market overnight premium.

**Implementation:**
- Use US close data (available by 07:00 AEST) as input to ASX open predictions
- New strategy: `strategies/overnight_momentum.py`
  - If S&P 500 was up >1% overnight → long ASX beta-sensitive names at open
  - If VIX spiked >5% → defensive positioning / avoid entries
- Leverage the multi-market data that Atlas already downloads

**Effort:** ~2 days

### 2E. Machine Learning Signal Scoring (🟡 Medium Impact, High Effort)

**Why:** Replace hand-tuned confidence scoring with learned models. The regime filter notes already mention "injected as INFO features for future ML use (Phase 5)."

**Implementation:**
1. Feature engineering: Extract 50+ features per signal (RSI, volume ratios, breadth, RS, ATR%, sector momentum, etc.) — many already computed
2. Train a gradient-boosted classifier (LightGBM) on historical trades: win/loss label
3. Replace confidence score with model probability
4. Walk-forward retrain (train on windows, predict on next window)
5. Start with signal filtering (reject <30% predicted win rate), not signal generation

**Effort:** ~5 days  
**Risk:** Overfitting — must be walk-forward validated rigorously

---

## 3. Operations & Infrastructure Upgrades

### 3A. S&P 500 Optimization (🔴 High Impact, Medium Effort)

**Why:** SP500 config is v1.0 — literally the ASX defaults copy-pasted with no optimization. This is the single biggest quick win.

**Current state (sp500.json):**
- Mean Reversion: RSI oversold=35 (ASX optimized to 40), profit target 1.5x ATR (ASX: 3.0x), max hold 7 days (ASX: 20)
- Trend Following: slow_ma=50 (ASX optimized to 30), stop=3.5x ATR (ASX: 2.0x)
- Opening Gap: gap threshold -0.01 (ASX: -0.025), IBS confirm 0.35 (ASX: 0.50)
- **None of these have been optimized for US market dynamics**

**Action Plan:**
1. Run `reoptimize_parallel.py --market sp500` with the robust scoring function
2. Walk-forward validate on 3 years of US data
3. Separate parameter set reflecting US market microstructure (tighter spreads, higher liquidity, different volatility patterns)

**Effort:** ~4 hours compute + 1 hour review

### 3B. Bayesian Optimization (🟡 Medium Impact, Medium Effort)

**Why:** Coordinate descent (current method) gets stuck in local optima. The "sharp peak" problem documented in v9.2/v9.4 suggests the parameter landscape has narrow ridges.

**Implementation:**
1. Replace coordinate descent with Optuna or Bayesian optimization (GP-based)
2. Use the same robust scoring function as objective
3. Run 200-500 trials per strategy (vs. current ~50-100 grid points)
4. Add parameter smoothness regularization: penalize distance from defaults

**Effort:** ~3 days  
**Expected improvement:** More robust parameter selection, wider plateau around optimum

### 3C. Dynamic Position Sizing Activation (🟡 Medium Impact, Low Effort)

**Why:** The module is **fully implemented** (`utils/dynamic_sizing.py`) but disabled. It has:
- Confidence scaling (higher confidence → bigger position)
- Volatility scaling (higher vol → smaller position)  
- Graduated drawdown tiers (deeper DD → smaller position)

**Action Plan:**
1. Backtest with dynamic sizing enabled vs. fixed sizing
2. Focus on equity curve drawdown scaling first (most defensive)
3. If positive: enable in config, monitor for 2 weeks of live trading

**Effort:** ~2 hours (just config change + backtest comparison)

### 3D. Regime-Aware Strategy Selection (🟡 Medium Impact, Medium Effort)

**Why:** The regime filter was tested and disabled because it was too aggressive (removed 46% of trades). But a softer approach — strategy weighting, not filtering — could help.

**Implementation:**
1. Bull regime: weight Trend Following higher, Mean Reversion lower
2. Bear regime: weight Mean Reversion higher (if shorts added: weight shorts)
3. High-vol regime: reduce all position sizes, tighten stops
4. Use IOZ/SPY relative to 50/200 MA + VIX proxy (market breadth % above 50MA already computed)

**Effort:** ~2 days

### 3E. Real-Time Monitoring & Alerting (🟡 Medium Impact, Medium Effort)

**Why:** Currently only 2 alerts/day (premarket + postclose). No intraday stop-loss monitoring, no position-level alerts.

**Implementation:**
1. Add mid-day health check cron (12:30 AEST): check stops, check if any position hit take profit
2. Telegram alerts for: position hits stop, position hits TP, portfolio DD exceeds threshold
3. Watchdog for Moomoo OpenD connection drops
4. Weekly performance digest (Sharpe, drawdown, vs benchmark)

**Effort:** ~2 days

### 3F. Multi-Market Cron Orchestration (🟡 Medium Impact, Low Effort)

**Why:** Currently cron only runs for ASX. SP500 needs its own schedule aligned to US hours.

**Implementation:**
```
# ASX (AEST)
30 8  * * 1-5  ATLAS_MARKET=asx  /root/atlas/scripts/pi-cron.sh premarket
00 17 * * 1-5  ATLAS_MARKET=asx  /root/atlas/scripts/pi-cron.sh postclose

# SP500 (EST via TZ override)  
00 9  * * 1-5  ATLAS_MARKET=sp500 TZ=America/New_York /root/atlas/scripts/pi-cron.sh premarket
30 16 * * 1-5  ATLAS_MARKET=sp500 TZ=America/New_York /root/atlas/scripts/pi-cron.sh postclose
```

**Effort:** ~1 hour (cron entries + verify pi-cron.sh respects ATLAS_MARKET)

### 3G. Portfolio-Level Risk Management (🟡 Medium Impact, Medium Effort)

**Why:** Current risk management is per-position. No cross-market correlation check, no portfolio-level VaR, no max total drawdown kill switch.

**Implementation:**
1. Portfolio heat tracking: sum of (position_value × beta) across all markets
2. Cross-market correlation: don't enter ASX mining + SP500 materials simultaneously
3. Global max drawdown halt: if total portfolio DD > X%, stop all new entries for N days
4. Daily VaR estimate (historical simulation, simple)

**Effort:** ~3 days

---

## 4. Dashboard & Analytics Upgrades

### 4A. Strategy Attribution Dashboard (🟡 Medium Impact, Low Effort)

**Why:** Can't currently see which strategy is making/losing money in the live dashboard.

**Add:**
- Per-strategy P&L breakdown (MR vs TF vs Gap)
- Strategy hit rate over rolling 30 days
- Signal quality tracking (confidence vs actual outcome)

### 4B. Multi-Market Dashboard (🟡 Medium Impact, Medium Effort)

**Why:** Dashboard currently only shows ASX. When SP500 goes live, need combined view.

**Add:**
- Market selector tabs
- Combined equity curve (AUD-normalized)
- Cross-market correlation display

### 4C. Backtest Comparison Tool (🟢 Low Impact, Low Effort)

**Why:** Currently have to dig through JSON files to compare backtest runs.

**Add:**
- Dashboard page showing last 5 backtest results side-by-side
- Highlight parameter changes between versions
- Walk-forward window heatmap

---

## 5. Code Quality & Robustness

### 5A. Test Suite (🔴 High Impact, Medium Effort)

**Why:** Zero automated tests. For a live-trading system with real money, this is a major risk.

**Priority tests:**
1. Position sizing: verify risk per trade never exceeds limit
2. Signal validation: stop < entry, confidence [0,1], position_size > 0
3. Fee calculations: round-trip cost matches Moomoo actual
4. Paper engine: entry/exit/settlement state transitions
5. Backtest engine: no look-ahead bias (signal on T, fill on T+1)

**Effort:** ~3 days for critical path tests

### 5B. Error Recovery & Idempotency (🟡 Medium Impact, Medium Effort)

**Why:** If cron fails mid-execution, there's no retry logic. If Moomoo OpenD drops during order placement, partial state.

**Add:**
- Idempotent plan generation (re-running for same date = same result)
- Order state reconciliation on startup (already have `sync` command)
- Automatic retry for transient failures (network, API rate limits)
- State backup before every mutation

### 5C. Configuration Validation (🟢 Low Impact, Low Effort)

**Why:** No schema validation on config files. A typo in `asx.json` could crash at runtime.

**Add:**
- JSON schema or Pydantic model for config
- Validate on load, fail fast with clear error message
- Type checking for all numeric parameters

---

## Prioritized Roadmap

### Phase 1: Quick Wins (1-2 weeks)
| # | Item | Effort | Impact |
|---|------|--------|--------|
| 1 | **SP500 optimization** (3A) | 4 hrs | 🔴 High |
| 2 | **Dynamic sizing backtest** (3C) | 2 hrs | 🟡 Medium |
| 3 | **Multi-market cron** (3F) | 1 hr | 🟡 Medium |
| 4 | **ASX Small Ords universe** (1C) | 0.5 days | 🟡 Medium |
| 5 | **BB Squeeze + Short-Term MR optimization** (2A) | 2 days | 🔴 High |

### Phase 2: Strategy Expansion (2-4 weeks)
| # | Item | Effort | Impact |
|---|------|--------|--------|
| 6 | **Bayesian optimization** (3B) | 3 days | 🟡 Medium |
| 7 | **Regime-aware selection** (3D) | 2 days | 🟡 Medium |
| 8 | **Overnight momentum strategy** (2D) | 2 days | 🟡 Medium |
| 9 | **Real-time monitoring** (3E) | 2 days | 🟡 Medium |
| 10 | **Strategy attribution dashboard** (4A) | 1 day | 🟡 Medium |

### Phase 3: Major Capabilities (1-2 months)
| # | Item | Effort | Impact |
|---|------|--------|--------|
| 11 | **Short selling** (2B) | 5 days | 🔴 High |
| 12 | **FTSE 100 market** (1A) | 2 days | 🔴 High |
| 13 | **ML signal scoring** (2E) | 5 days | 🟡 Medium |
| 14 | **Portfolio-level risk mgmt** (3G) | 3 days | 🟡 Medium |
| 15 | **Test suite** (5A) | 3 days | 🔴 High |

### Phase 4: Polish & Scale (2-3 months)
| # | Item | Effort | Impact |
|---|------|--------|--------|
| 16 | **Pairs trading** (2C) | 4 days | 🟡 Medium |
| 17 | **Multi-market dashboard** (4B) | 2 days | 🟡 Medium |
| 18 | **Nikkei 225** (1B) | 2 days | 🟡 Medium |
| 19 | **Config validation** (5C) | 1 day | 🟢 Low |
| 20 | **Error recovery** (5B) | 2 days | 🟡 Medium |

---

## Expected Impact Summary

If Phase 1-2 executed well:

| Metric | Current | Target | Notes |
|--------|---------|--------|-------|
| **Markets** | 2 (1 live) | 3-4 (2 live) | ASX, SP500, ASX Small Ords |
| **Active Strategies** | 3 | 5-6 | +BB Squeeze, +Short-Term MR, +Overnight |
| **CAGR (ASX backtest)** | 11.9% | 14-18% | More strategies = more trades = more compounding |
| **Sharpe** | 0.79 | 0.9-1.2 | Regime awareness + diversified strategies |
| **Max Drawdown** | 6.8% | 5-8% | Dynamic sizing should limit |
| **Parameter Stability** | 2/10 collapses | 0-1/10 | Bayesian opt + regularization |
| **Trade Frequency** | 408/yr (ASX) | 600-800/yr | More strategies + more markets |
| **Automation** | 2x daily ASX | 4x daily multi-market | Full coverage of both sessions |

---

## Key Architectural Strengths to Preserve

1. **MarketProfile abstraction** — adding markets is genuinely easy (1 file + 1 config)
2. **BaseStrategy ABC** — clean interface, strategies are self-contained
3. **Walk-forward backtesting** — no look-ahead bias, rigorous validation
4. **Human-in-the-loop** — Telegram approval prevents runaway execution
5. **Config versioning** — audit trail for parameter changes
6. **Self-annealing loop** — automatic degradation detection

These patterns should be preserved and extended, not replaced.
