# Sharpe Improvement Roadmap → Research Engine Implementation Plan

> Source: `research/brain/hypotheses/sharpe_improvement_roadmap.md`
> Created: 2026-03-12
> Goal: Systematic path from 0.69 → 1.0+ portfolio Sharpe

---

## Executive Summary

The Sharpe improvement roadmap identifies 7 enhancement layers with academic backing. Cross-referencing with the current research engine state reveals that **most of these enhancements require new infrastructure** — the engine can't test them today. This plan converts each roadmap recommendation into concrete engineering work + queued experiments.

### The Three Moves That Matter Most (from roadmap)

| # | Enhancement | Expected Impact | Engine Status | Task |
|---|-------------|----------------|---------------|------|
| 1 | Multi-strategy combination | 0.69 → 0.85-0.95 | ⚠️ No correlation/weighting tools | #123 |
| 2 | Conditional volatility scaling | +0.10-0.20 Sharpe | ❌ Not in engine | #124 |
| 3 | 200-day MA market filter | +0.10-0.20 Sharpe | ✅ Already PROMOTED | — |

### Full Priority Stack

| Priority | Task | What | Blocked By |
|----------|------|------|-----------|
| P0 | #129 | Fix 8 infrastructure blockers | Nothing |
| P1 | #86 → #123 | Correlation matrix → optimal weighting | #129 (clean data needed) |
| P2 | #124 | Volatility scaling module | Nothing (engine change) |
| P3 | #125 | Risk-adjusted momentum signal | Nothing (strategy change) |
| P4 | #127 | Deflated Sharpe + CPCV | Nothing (evaluator change) |
| P5 | #126 | Stop-loss experiments | Nothing (queue experiments) |
| P6 | #128 | Sector-neutral + volume filters | Nothing (strategy change) |
| P7 | #90 | Regime-conditional activation | #86 (need correlation data) |
| P8 | #130 | Earnings momentum (SUE) | Earnings data source |

---

## Phase 1: Fix Infrastructure (prerequisite — tasks #129)

**Why first**: 15+ experiments have false failures. New experiments on bad infrastructure produce garbage.

```
[ ] mtf_momentum confidence 0.50 → set to 1.0 or remove min_confidence filter
[ ] filter_test: wire TOM params to strategy code (or remove filter_test method)
[ ] volume variants: fix test harness to actually vary the parameter
[ ] max_open_positions contention: increase to 20+ for combined tests
[ ] ConsecutiveDownDays: add check_exits() method
[ ] MTFMomentum: fix generate_signals() signature
[ ] SectorRotation: add rebalance support or mark as dead-end
[ ] validate_oos.py: generalize for sp500 (remove ASX hardcoding)
```

After fixes → requeue all affected experiments → re-evaluate.

---

## Phase 2: Correlation Matrix & Optimal Weighting (tasks #86, #123)

**Why next**: Biggest single Sharpe lift. The math is clear — combining 10 strategies with low correlation yields portfolio Sharpe 0.83-0.96 *even without any individual strategy improvement*.

### Step 2a: Build correlation analysis tool
```python
# New: research/portfolio_optimizer.py
def compute_correlation_matrix(strategies: list[str]) -> pd.DataFrame:
    """Extract daily P&L from backtests, compute NxN correlation matrix."""
    # 1. Run backtest for each strategy solo → extract daily returns series
    # 2. Align dates across all strategies
    # 3. Compute Pearson correlation matrix
    # 4. Apply Ledoit-Wolf shrinkage to covariance matrix
    # 5. Return correlation matrix + covariance matrix

def compute_optimal_weights(corr_matrix, sharpe_ratios, constraints) -> dict:
    """Sharpe-ratio-tilted inverse-volatility weights."""
    # w_i ∝ SR_i / σ_i
    # Cap: max 20%, min 5%
    # Exclude: SR < 0.25
    # Tier: 60% Tier 1 (SR>0.55), 35% Tier 2 (0.40-0.55), 5% Tier 3 (0.25-0.40)

def backtest_weighted_portfolio(weights, strategies) -> dict:
    """Run combined backtest with strategy-level capital allocation."""
```

### Step 2b: Validate the correlation hypothesis
```
Experiments to queue:
- corr_matrix_baseline: compute full matrix for all 10+ positive-Sharpe strategies
- verify_mr_momentum_hedge: confirm cross-group correlation < 0.20
- optimal_weights_v1: inverse-vol + Sharpe tilt vs equal weight
- monthly_rebalance: test monthly rebalancing with 3-month trailing Sharpe gate
```

### Step 2c: Integrate into research pipeline
- Add correlation matrix to weekly report (data_scientist.py)
- Write to vault: Portfolio/Correlation Matrix.md
- Add to promotion criteria: new strategy must not be >0.7 correlated with existing

---

## Phase 3: Volatility Scaling (task #124)

**Why**: The most robust finding in the momentum literature — scaling down during high-vol periods.

### Engine changes needed:
```python
# In backtest/engine.py — add volatility scaling to position sizing
class VolatilityScaler:
    def __init__(self, config):
        self.enabled = config.get('vol_scaling', {}).get('enabled', False)
        self.lookback = config.get('vol_scaling', {}).get('lookback', 60)
        self.half_life = config.get('vol_scaling', {}).get('half_life', 20)
        self.target_vol = config.get('vol_scaling', {}).get('target_vol', 0.12)
        self.conditional = config.get('vol_scaling', {}).get('conditional', True)
        self.percentile_threshold = config.get('vol_scaling', {}).get('percentile_threshold', 80)
    
    def scale_factor(self, portfolio_returns: pd.Series, date) -> float:
        """Returns 0.0-1.0 scale factor. Capped at 1.0 (no leverage)."""
        trailing = portfolio_returns.iloc[-self.lookback:]
        realized_vol = trailing.std() * np.sqrt(252)
        
        if self.conditional:
            # Only scale down if vol is in top quintile
            vol_history = portfolio_returns.rolling(self.lookback).std() * np.sqrt(252)
            threshold = np.percentile(vol_history.dropna(), self.percentile_threshold)
            if realized_vol < threshold:
                return 1.0
        
        return min(1.0, self.target_vol / realized_vol)
```

### Experiments to queue:
```
- vol_scale_lookback_sweep: [20, 40, 60, 126] day lookback
- vol_scale_target_sweep: [10%, 12%, 15%] target vol
- vol_scale_conditional_vs_always: conditional (80th pct) vs always-scale
- vol_scale_half_life: [10, 20, 40] day exponential decay
```

---

## Phase 4: Risk-Adjusted Momentum (task #125)

### Strategy changes needed:
```python
# In strategies/momentum_breakout.py — add signal_mode parameter
def generate_signals(self, data, ...):
    signal_mode = self.config.get('signal_mode', 'raw')
    
    if signal_mode == 'risk_adjusted':
        # Rank by return / volatility ratio
        returns_12m = data.pct_change(252).iloc[-1]
        vol_12m = data.pct_change().rolling(252).std().iloc[-1]
        signal = returns_12m / vol_12m
    
    elif signal_mode == 'idiosyncratic':
        # Regress on SPY, rank on cumulative residuals / residual vol
        spy_returns = data['SPY'].pct_change()
        for ticker in universe:
            stock_returns = data[ticker].pct_change()
            beta = stock_returns.cov(spy_returns) / spy_returns.var()
            residuals = stock_returns - beta * spy_returns
            signal[ticker] = residuals.cumsum()[-1] / residuals.std()
    
    else:  # 'raw'
        signal = data.pct_change(252).iloc[-1]  # standard 12-month return
```

### Experiments to queue:
```
- momentum_raw_vs_riskadjusted: compare Sharpe with raw vs return/vol ranking
- momentum_idiosyncratic: residual momentum (Blitz et al.)
- momentum_lookback_sweep: 12-2, 12-7, 6-2 lookback windows
- momentum_skip_month: 12-1 with vs without 1-month skip
```

---

## Phase 5: Statistical Rigor (task #127)

### Evaluator changes needed:
```python
# In research/evaluator.py — add statistical correction methods
class ExperimentEvaluator:
    def deflated_sharpe_ratio(self, observed_sr, n_strategies, T_months, skew, kurt):
        """Bailey & López de Prado (2014) Deflated Sharpe Ratio.
        
        Returns p-value: probability of observing this SR by chance
        given n_strategies tested.
        """
        # E[max SR under null] = (1 - γ) * Φ^{-1}(1 - 1/N) + γ * Φ^{-1}(1 - 1/(N*e))
        # where γ ≈ 0.5772 (Euler-Mascheroni), N = n_strategies
        # Then: DSR = Φ((SR - E[max SR]) * sqrt(T) / sqrt(1 - skew*SR + (kurt-1)/4 * SR^2))
        ...
    
    def cpcv_validate(self, strategy, data, n_folds=6, k_test=2):
        """Combinatorial Purged Cross-Validation (López de Prado, 2018).
        
        Returns: PBO (Probability of Backtest Overfitting)
        Flag if PBO > 0.50.
        """
        # Use skfolio library or implement manually
        ...
    
    def parameter_stability_test(self, strategy, base_params, variation_pct=0.15):
        """Vary each param ±15%. Fail if Sharpe changes by >50%."""
        ...
```

### Integration:
- Add DSR to all experiment verdicts (informational, not gating until validated)
- Add CPCV to OOS validation stage (gating: PBO must be < 0.50)
- Add parameter stability to promotion criteria
- Log multiple-testing-corrected significance in vault experiment notes

---

## Phase 6: Stop-Loss & Exit Optimization (task #126)

### Strategy changes needed:
```python
# Add ATR trailing stop mode to strategy exit logic
class ATRTrailingStop:
    """Trailing stop based on ATR from highest close."""
    def __init__(self, atr_mult=2.0, activation_mult=1.0):
        self.atr_mult = atr_mult      # stop distance in ATR units
        self.activation_mult = activation_mult  # activate after N×ATR profit
    
    def check_exit(self, position, current_bar):
        if not position.trailing_active:
            profit_atr = (current_bar.close - position.entry_price) / position.atr_at_entry
            if profit_atr >= self.activation_mult:
                position.trailing_active = True
                position.trailing_stop = current_bar.close - self.atr_mult * current_bar.atr14
        else:
            new_stop = current_bar.close - self.atr_mult * current_bar.atr14
            position.trailing_stop = max(position.trailing_stop, new_stop)
            if current_bar.close < position.trailing_stop:
                return True  # EXIT
        return False

# Scale-out mode
class ScaleOutExit:
    """Take 50% at target, trail remainder."""
    def __init__(self, target_rr=3.0, trail_atr=2.0):
        ...
```

### Experiments to queue:
```
FOR MOMENTUM: momentum_breakout, trend_following
- stop_atr_sweep: [1.5, 2.0, 2.5, 3.0] × ATR(14) initial stop
- trail_atr_sweep: [2.0, 3.0] × ATR(14) trailing stop
- scale_out_test: 50% at 3:1 RR, trail rest at 2× ATR
- time_stop_sweep: [15, 20, 25, 30] trading days
- close_vs_intraday: close-based vs intraday stop triggers

FOR MEAN REVERSION: mean_reversion, connors_rsi2, short_term_mr
- mr_no_stop_confirm: no stop vs 3× ATR vs 4× ATR (expect no-stop wins)
```

---

## Phase 7: Universe & Signal Filters (task #128)

### Experiments to queue:
```
- sector_neutral_momentum: equal picks per GICS sector
- volume_confirm_breakout: require breakout volume > 1.5× 20-day avg
- atr_expansion_filter: ATR(14) > MA(50) of ATR
- universe_cap_floor: $2B+ market cap filter
- universe_price_floor: $5+ share price
- profitability_screen: positive trailing 12M EPS
- trend_prefilter: stock above 100-day SMA
```

---

## Expected Cumulative Impact (from roadmap, non-additive)

| Enhancement Layer | Cumulative Sharpe | Phase |
|---|---|---|
| Baseline (best individual) | 0.69 | — |
| + Multi-strategy combination | 0.85-0.95 | Phase 2 |
| + Volatility scaling | 0.90-1.00 | Phase 3 |
| + Risk-adjusted momentum | 0.95-1.05 | Phase 4 |
| + Stop-loss overlay | 1.00-1.10 | Phase 6 |
| + Universe/sector optimization | 1.05-1.15 | Phase 7 |

**Realistic target: 0.95-1.10 portfolio Sharpe** — at which point further optimization enters diminishing returns territory.

---

## Dependencies Graph

```
#129 (fix infra blockers)
  └─→ #86 (correlation matrix in reports)
       └─→ #123 (optimal weighting pipeline)
            └─→ #90 (regime-conditional activation)

#124 (vol scaling) ─── independent ───→ queue experiments
#125 (risk-adj momentum) ─── independent ───→ queue experiments  
#127 (DSR + CPCV) ─── independent ───→ integrate into evaluator
#126 (stop-loss experiments) ─── depends on strategy exit changes
#128 (sector/volume filters) ─── independent ───→ queue experiments
#130 (earnings momentum) ─── depends on earnings data source
```

---

## Success Criteria

- [ ] Infrastructure blockers fixed, 15+ experiments requeued
- [ ] Full NxN correlation matrix computed for all positive-Sharpe strategies
- [ ] Portfolio-level Sharpe > 0.80 from optimal weighting alone
- [ ] Volatility scaling shows ≥0.05 Sharpe improvement in backtest
- [ ] Risk-adjusted momentum shows ≥0.10 Sharpe improvement
- [ ] DSR and CPCV integrated into evaluator
- [ ] All promoted strategies survive DSR correction at p<0.05
- [ ] Combined portfolio Sharpe reaches 0.95+ in backtest

---

## Risk: Overfitting at Scale

The roadmap warns that running more experiments increases multiple-testing burden. Every new experiment weakens the statistical significance of our best result. **Phase 5 (DSR + CPCV) should be implemented early** to act as a guard rail, not just a final check.

Practical mitigation:
- Track cumulative strategies tested in vault Meta/
- Report DSR-corrected Sharpe alongside raw Sharpe in all experiment notes
- Require CPCV PBO < 0.50 for any promotion
- Maintain data exposure log to flag overused test windows
