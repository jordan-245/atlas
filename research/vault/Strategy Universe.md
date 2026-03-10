---
tags: [meta, strategy-universe]
updated: 2026-03-10
---

# Strategy Universe

Master registry of all trading strategies — existing and planned.

## Summary

- **Total strategies**: 31
- **Active**: 3
- **Not built**: 18
- **Dead end**: 0

## Registry

| Strategy | Type | Tier | Status | Reference |
|----------|------|------|--------|-----------|
| [[Adx Trend Pullback]] | trend_following | 1 | ⬜ not_built | Welles Wilder 'New Concepts in Technical Trading'  |
| [[Bb Squeeze]] | volatility | 0 | 🟡 dormant | Bollinger Band Squeeze |
| [[Connors Rsi2]] | mean_reversion | 0 | 🟡 dormant | Connors 2008 |
| [[Consecutive Down Days]] | mean_reversion | 0 | 🔵 screening | Quantified Strategies |
| [[Demark Sequential]] | mean_reversion | 1 | ⬜ not_built | Tom DeMark 'The New Science of Technical Analysis' |
| [[Dividend Capture]] | event | 0 | ⬜ untested | Dividend event |
| [[Donchian Breakout]] | trend_following | 1 | ⬜ not_built | Richard Donchian, Turtle Traders (1983) |
| [[Gap And Go]] | momentum | 1 | ⬜ not_built | Quantified Strategies gap research, related to Ope |
| [[Heikin Ashi Reversal]] | mean_reversion | 1 | ⬜ not_built | Japanese candlestick patterns, quantified by Quant |
| [[Inside Bar Nr7]] | volatility_breakout | 1 | ⬜ not_built | Toby Crabel 'Day Trading with Short Term Price Pat |
| [[Keltner Reversion]] | mean_reversion | 1 | ⬜ not_built | Chester Keltner (1960), modernized by Linda Bradfo |
| [[Lower Band Reversion]] | mean_reversion | 0 | 🟡 dormant | Bollinger lower band |
| [[Macd Divergence]] | mean_reversion | 1 | ⬜ not_built | Gerald Appel (1979), MACD divergence patterns |
| [[Mean Reversion]] | mean_reversion | 0 | 🟢 active | Atlas original |
| [[Momentum Breakout]] | momentum | 0 | 🟡 dormant | Atlas original |
| [[Monthly Rotation]] | rotation | 1 | ⬜ not_built | Faber 'A Quantitative Approach to TAA' (2007), Ant |
| [[Mtf Momentum]] | momentum | 0 | 🟡 dormant | Multi-timeframe |
| [[Opening Gap]] | mean_reversion | 0 | 🟢 active | Atlas original |
| [[Overnight Return]] | mean_reversion | 1 | ⬜ not_built | Cliff et al. 'Overnight Return' (2019), Quantpedia |
| [[Pead Earnings Drift]] | event | 1 | ⬜ not_built | Ball & Brown (1968), Bernard & Thomas (1989) |
| [[Put Call Vix Proxy]] | sentiment | 1 | ⬜ not_built | VIX fear gauge research, CBOE put/call ratio studi |
| [[Relative Strength Pullback]] | momentum | 1 | ⬜ not_built | O'Neil CANSLIM (1988), Minervini 'Trade Like a Sto |
| [[Rsi Divergence]] | mean_reversion | 1 | ⬜ not_built | Andrew Cardwell RSI divergence methodology |
| [[Sector Rotation]] | rotation | 0 | 🔴 broken | 0 trades in backtest |
| [[Short Term Mr]] | mean_reversion | 0 | 🟡 dormant | Atlas original |
| [[Stochastic Oversold]] | mean_reversion | 1 | ⬜ not_built | George Lane (1950s), quantified by Connors |
| [[Trend Following]] | trend_following | 0 | 🟢 active | Atlas original |
| [[Triple Rsi]] | mean_reversion | 0 | 🟡 dormant | Multi-period RSI |
| [[Volume Climax]] | mean_reversion | 1 | ⬜ not_built | Quantified Strategies volume research, Wyckoff met |
| [[Vwap Reversion]] | mean_reversion | 1 | ⬜ not_built | Institutional VWAP trading, Quantified Strategies |
| [[Williams Percent R]] | mean_reversion | 1 | ⬜ not_built | Larry Williams 'How I Made $1M' (1979) |

## Tier 1 Descriptions

### Adx Trend Pullback
**Reference:** Welles Wilder 'New Concepts in Technical Trading' (1978)
**Description:** ADX > 25 (strong trend) + pullback to 20-EMA. Enter on bounce from EMA. Exit: trailing ATR stop.

### Demark Sequential
**Reference:** Tom DeMark 'The New Science of Technical Analysis' (1994)
**Description:** TD Sequential buy setup: 9 consecutive closes below close 4 bars earlier. Enter on bar 9. Exit on TD sell setup or time.

### Donchian Breakout
**Reference:** Richard Donchian, Turtle Traders (1983)
**Description:** Buy on 20-day high breakout, sell on 10-day low. Classic trend following. ATR position sizing.

### Gap And Go
**Reference:** Quantified Strategies gap research, related to Opening Gap
**Description:** Buy stocks that gap UP > 2% at open with volume confirmation. Ride momentum. Exit: intraday trailing stop or close.

### Heikin Ashi Reversal
**Reference:** Japanese candlestick patterns, quantified by Quantified Strategies
**Description:** 3+ red Heikin-Ashi candles followed by green doji/reversal in uptrend. Enter long. Exit on 2 red HA candles.

### Inside Bar Nr7
**Reference:** Toby Crabel 'Day Trading with Short Term Price Patterns' (1990)
**Description:** NR7 (narrowest range of 7 days) or inside bar → breakout entry. Enter on break of NR7 high/low. Exit: trailing stop or time-based (3-5 days).

### Keltner Reversion
**Reference:** Chester Keltner (1960), modernized by Linda Bradford Raschke
**Description:** Price touches lower Keltner Channel (EMA ± ATR mult) → buy. Exit at middle band (EMA). Uptrend filter.

### Macd Divergence
**Reference:** Gerald Appel (1979), MACD divergence patterns
**Description:** Price makes new low but MACD histogram makes higher low. Enter long. Exit on MACD crossover or time stop.

### Monthly Rotation
**Reference:** Faber 'A Quantitative Approach to TAA' (2007), Antonacci dual momentum
**Description:** Monthly rebalance: rank sectors/stocks by 6-month momentum. Hold top N. Rotate monthly. Cash filter: below SMA-200 → cash.

### Overnight Return
**Reference:** Cliff et al. 'Overnight Return' (2019), Quantpedia #53
**Description:** Buy at close, sell at open. Captures overnight premium. Filter: strong recent performers only.

### Pead Earnings Drift
**Reference:** Ball & Brown (1968), Bernard & Thomas (1989)
**Description:** Post-Earnings Announcement Drift. Buy after positive earnings surprise, hold 20-60 days. Needs earnings data.

### Put Call Vix Proxy
**Reference:** VIX fear gauge research, CBOE put/call ratio studies
**Description:** VIX > 30 or VIX spike > 20% in 1 day → buy SPY/broad market. Exit when VIX drops below 20. Contrarian sentiment play.

### Relative Strength Pullback
**Reference:** O'Neil CANSLIM (1988), Minervini 'Trade Like a Stock Market Wizard'
**Description:** Stocks with relative strength rank > 80th percentile that pull back to 10-EMA. Enter on bounce. Exit: trailing stop.

### Rsi Divergence
**Reference:** Andrew Cardwell RSI divergence methodology
**Description:** Price makes new low but RSI makes higher low (bullish divergence). Enter long. Exit on RSI > 60 or time.

### Stochastic Oversold
**Reference:** George Lane (1950s), quantified by Connors
**Description:** Stochastic %K < 20 and %D < 20 in uptrend (>SMA200). Exit on %K > 80 or time stop.

### Volume Climax
**Reference:** Quantified Strategies volume research, Wyckoff method
**Description:** Extreme volume spike (>3x avg) on a down day in uptrend = capitulation selling. Buy reversal. Exit: time or strength.

### Vwap Reversion
**Reference:** Institutional VWAP trading, Quantified Strategies
**Description:** Price > 2 std below daily VWAP in uptrending stock. Enter long. Exit at VWAP or above. Needs intraday-proxy via daily estimate.

### Williams Percent R
**Reference:** Larry Williams 'How I Made $1M' (1979)
**Description:** Williams %R oversold (<-80) with trend filter. Exit on %R > -20 or time stop.
