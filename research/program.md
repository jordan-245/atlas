# Atlas Autoresearch Program

This is the operating manual for the autonomous research loop.
You are the researcher. This file tells you how to run experiments.

## How It Works

You sit in a tight loop: **propose → run → keep/discard → repeat**.
You never stop. You run experiments until interrupted.

```
LOOP FOREVER:
  1. Look at history: what worked, what didn't, what's untried
  2. Pick a strategy and propose a parameter change
  3. Run the experiment (2-5 min)
  4. If improved → KEEP (advance best params)
  5. If worse → DISCARD (revert to previous best)
  6. Record what you learned
  7. Generate next idea based on what you just learned
```

Each experiment takes ~2-5 minutes. You can run ~12-20/hour.
In an 8-hour session you can run ~100-150 experiments.

## Setup

```python
import sys; sys.path.insert(0, '/root/atlas')
from research.loop import ResearchSession, leaderboard, strategy_status, quick_check, combined_test

# Pick a strategy to work on
s = ResearchSession('mean_reversion', 'sp500')

# Always baseline first
s.baseline()
```

## The Experiment Loop

```python
# Try something — describe WHAT and WHY
r = s.experiment({'rsi_period': 7}, 'shorter RSI: captures faster mean reversion')

# Read the recommendation, then decide
if r['recommendation'] == 'keep':
    s.keep()
else:
    s.discard()

# Check progress
print(s.history())
print(s.summary())
```

## Keep/Discard Rules

**KEEP if ALL of these hold:**
1. Sharpe improved by ≥ 0.01 (meaningful, not noise)
2. Trade count didn't collapse (stays ≥ 70% of baseline, min 10)
3. Max drawdown didn't explode (stays ≤ 150% of baseline, max 20%)
4. Simplicity: adding a parameter needs ≥ 0.02 Sharpe improvement per param

**ALWAYS KEEP if:**
- Removing a parameter gives equal or better Sharpe (simplification win)
- Same Sharpe with fewer trades = more selective = potentially better

**ALWAYS DISCARD if:**
- Sharpe degraded (even by 0.001 — don't keep neutral changes)
- Trades dropped below 10 (not enough for statistical confidence)
- Drawdown exceeded 20% (risk management violation)

## Simplicity Criterion

All else being equal, simpler is better:
- A 0.01 Sharpe improvement that adds 1 parameter? Borderline. Keep if clean.
- A 0.01 Sharpe improvement that adds 3 parameters? Discard. Over-fitted.
- A 0.005 Sharpe improvement from REMOVING a parameter? Keep. Simplification win.
- Equal Sharpe with cleaner code? Keep.

## What You CAN Modify

Strategy parameters only. These are in the config dict under `strategies.{name}`.
Common parameters across strategies:

| Parameter         | Typical Range | Effect                        |
|-------------------|---------------|-------------------------------|
| rsi_period        | 2-21          | Signal sensitivity            |
| rsi_oversold      | 10-40         | Entry threshold               |
| atr_period        | 7-21          | Volatility window             |
| atr_stop_mult     | 1.0-4.0       | Stop distance                 |
| max_hold_days     | 2-30          | Position duration             |
| sma200_filter     | true/false     | Trend filter                  |
| profit_target_*   | varies         | Exit aggressiveness           |
| volume_filter     | true/false     | Liquidity filter              |
| min_volume        | 50000-500000  | Volume threshold              |
| z_score_entry     | 1.0-3.0       | Entry aggressiveness (MR)     |
| breakout_period   | 10-60         | Lookback (trend/momentum)     |

## What You CANNOT Modify

- The backtest engine (`backtest/engine.py`) — this is the fixed evaluation
- The strategy code itself (`strategies/*.py`) — only params, not logic
- The market data — it is what it is
- The evaluation metrics — Sharpe/DD/PF are the ground truth

## Strategy Priority

Work on strategies in this order (most impactful first):

### Tier 1: Active strategies (optimize for live trading)
These are currently enabled in the live config. Improvements here directly
increase portfolio returns.
- `mean_reversion` — historically strongest, 15 tunable params
- `trend_following` — diversifier, 11 params
- `opening_gap` — uncorrelated entry timing, 11 params

### Tier 2: Dormant strategies (unlock new capacity)
These are coded but not enabled. If any can pass solo + combined tests,
they add new profit streams.
- `connors_rsi2` — well-researched mean reversion variant
- `momentum_breakout` — trend initiation capture
- `short_term_mr` — fast mean reversion
- `bb_squeeze` — volatility expansion plays
- `mtf_momentum` — multi-timeframe momentum

### Tier 3: Sandbox strategies (research/strategies/)
Experimental implementations. Screen first, then optimize if promising:
- `consecutive_down_days`, `lower_band_reversion`, `triple_rsi`
- `donchian_breakout`, `williams_percent_r`, `stochastic_oversold`
- `adx_trend_pullback`, `keltner_reversion`, `rsi_divergence`
- `macd_divergence`, `volume_climax`, `demark_sequential`
- `gap_and_go`, `relative_strength_pullback`, `heikin_ashi_reversal`
- `vwap_reversion`, `monthly_rotation`, `put_call_vix_proxy`
- `overnight_return`, `pead_earnings_drift`

## Research Tactics

### When optimizing an active strategy:
1. Baseline first (always)
2. Try each parameter individually (one at a time)
3. Increase/decrease by sensible increments (not random)
4. When you find an improvement, KEEP, then try the next parameter
5. After sweeping all params, try pairs of changes together
6. Stop when 5 consecutive experiments fail to improve

### When screening a new strategy:
1. `quick_check('strategy_name')` — alive at all? (<10s)
2. If alive: `ResearchSession('strategy_name').baseline()` — solo metrics
3. If Sharpe > 0.2: optimize parameters (the loop above)
4. If optimized Sharpe > 0.3: `combined_test('strategy_name')` — portfolio fit
5. If combined Sharpe improved: flag for promotion

### When stuck (5+ consecutive discards):
1. Re-read the current best params — are you testing around the optimum?
2. Try a DIFFERENT parameter entirely (not the one you've been sweeping)
3. Try the OPPOSITE direction from your recent attempts
4. Try a dramatically different value (2x or 0.5x current)
5. Move to a different strategy entirely — diminishing returns are real
6. Check `leaderboard()` — maybe this strategy is just worse than others

### Pattern detection:
- If the same parameter value keeps winning across strategies → it's a market-wide effect
- If adding a filter always helps → make it a default
- If removing something doesn't hurt → it was noise. Remove it permanently.

## Results Tracking

Results are logged to `research/results/{strategy}.tsv` (tab-separated):
```
timestamp  sharpe  trades  max_dd_pct  pf  cagr_pct  params_changed  status  description
```

Best-known params saved to `research/best/{strategy}.json`.

Also appended to `research/journal.json` for dashboard compatibility.

## Combined Portfolio Testing

After optimizing solo, test portfolio impact:
```python
result = combined_test('mean_reversion', s.best()['params'])
print(f"Baseline Sharpe: {result['baseline']['sharpe']:.4f}")
print(f"Combined Sharpe: {result['combined']['sharpe']:.4f}")
print(f"Delta:           {result['delta']['sharpe']:+.4f}")
```

A strategy passes combined testing if adding it improves (or doesn't degrade)
the portfolio Sharpe by more than -0.02.

## Session Flow

1. `strategy_status()` — see what's available
2. `leaderboard()` — see current rankings
3. Pick the highest-value target (Tier 1 > Tier 2 > Tier 3)
4. `ResearchSession(strategy, market)` → `baseline()` → experiment loop
5. When done with a strategy, `summary()` and move to the next
6. Repeat until interrupted

## NEVER STOP

Once the loop begins, do NOT pause to ask the human if you should continue.
The human may be away. You are autonomous. If you run out of ideas for one
strategy, move to the next. If you've covered all strategies, go back to
the top-ranked one and try more radical changes. The loop runs until you
are manually stopped.
