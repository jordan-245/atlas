# Portfolio Allocation Analysis

> Generated: 2026-03-16 20:38

## Portfolio Metrics

- **Analytic Sharpe**: -2.3033
- **Simulated Sharpe**: 0.5284
- **Strategies active**: 31
- **Average correlation**: 0.2061
- **Annual return**: 1.1%
- **Annual volatility**: 2.1%
- **Max drawdown**: -2.8%

## Optimal Weights (Sharpe-tilted inverse-vol)

| Strategy | Weight | Sharpe | Trades | CAGR% | Group |
|----------|--------|--------|--------|-------|-------|
| connors_rsi2 | 3.2% | -0.380 | 729 | 2.0% | mean_reversion |
| demark_sequential | 3.2% | -0.685 | 188 | 1.8% | mean_reversion |
| bb_squeeze | 3.2% | -0.662 | 179 | 1.9% | mean_reversion |
| donchian_breakout | 3.2% | -0.812 | 112 | 1.1% | momentum |
| gap_and_go | 3.2% | -1.732 | 204 | -0.0% | momentum |
| consecutive_down_days | 3.2% | -1.387 | 629 | -0.5% | mean_reversion |
| adx_trend_pullback | 3.2% | -0.242 | 233 | 3.6% | momentum |
| inside_bar_nr7 | 3.2% | -1.232 | 248 | 0.5% | other |
| mean_reversion | 3.2% | -0.399 | 237 | 2.2% | mean_reversion |
| lower_band_reversion | 3.2% | -1.578 | 369 | -0.1% | mean_reversion |
| macd_divergence | 3.2% | -0.632 | 171 | 2.6% | momentum |
| keltner_reversion | 3.2% | -0.769 | 199 | 1.5% | mean_reversion |
| monthly_rotation | 3.2% | -1.902 | 51 | 0.3% | other |
| momentum_breakout | 3.2% | -0.565 | 294 | 0.9% | momentum |
| opening_gap | 3.2% | -0.475 | 466 | 1.4% | other |
| pead_earnings_drift | 3.2% | -0.561 | 87 | 2.8% | other |
| dividend_capture | 3.2% | -1.229 | 110 | 1.3% | other |
| put_call_vix_proxy | 3.2% | -4.129 | 19 | 0.5% | other |
| overnight_return | 3.2% | -1.483 | 436 | -0.1% | other |
| relative_strength_pullback | 3.2% | -0.976 | 219 | 0.1% | momentum |
| sector_rotation | 3.2% | -0.914 | 284 | -0.5% | other |
| trend_following | 3.2% | -0.217 | 231 | 3.5% | momentum |
| short_term_mr | 3.2% | -1.227 | 783 | -2.0% | mean_reversion |
| rsi_divergence | 3.2% | -2.571 | 125 | -2.3% | mean_reversion |
| stochastic_oversold | 3.2% | -0.794 | 292 | 2.2% | mean_reversion |
| volume_climax | 3.2% | -1.062 | 483 | 0.2% | mean_reversion |
| williams_percent_r | 3.2% | -0.505 | 233 | 2.4% | mean_reversion |
| triple_rsi | 3.2% | -1.409 | 197 | 0.1% | other |
| vwap_reversion | 3.2% | -1.573 | 208 | -0.3% | other |
| mtf_momentum | 3.2% | -2.179 | 46 | -0.5% | other |
| heikin_ashi_reversal | 3.2% | -0.092 | 225 | 4.5% | momentum |

## Excluded Strategies (weight = 0)


## Method

Weights computed as w_i ∝ SR_i / σ_i (Sharpe-ratio-tilted inverse-volatility),
with Ledoit-Wolf shrinkage on the covariance matrix.
Constraints: max 25% per strategy, min 3%.

References: Bailey & López de Prado (2013), Treynor-Black theorem