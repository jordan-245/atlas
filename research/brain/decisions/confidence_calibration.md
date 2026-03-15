# Decision: Confidence Score Calibration

**Date:** 2026-03-15
**Status:** CALIBRATED — threshold lowered from 0.75 to 0.65
**Config:** `config/active/sp500.json` — `risk.min_confidence = 0.65`

## Context

The 0.75 confidence threshold rejected 50+ signals on March 13, 2026. Confidence scores are hand-crafted heuristics (e.g., `0.6 + 0.2 * rsi_bonus + 0.2 * zscore_bonus` in MR). No prior empirical calibration existed.

## Method

Ran a full walk-forward backtest with `min_confidence=0` to capture ALL signals (489 trades), then analyzed the confidence-outcome relationship.

## Overall Calibration Curve

| Bucket | Count | Win Rate | Avg PnL | Expected Value | Total PnL |
|--------|-------|----------|---------|----------------|-----------|
| 0.4-0.5 | 2 | 50.0% | $18.96 | +$18.96 | $37.92 |
| 0.5-0.6 | 2 | 50.0% | $13.23 | +$13.23 | $26.46 |
| **0.6-0.7** | **103** | **48.5%** | **$3.92** | **+$3.92** | **$403.50** |
| 0.7-0.8 | 123 | 54.5% | $9.41 | +$9.41 | $1,157.94 |
| 0.8-0.9 | 171 | 49.7% | $27.98 | +$27.98 | $4,784.69 |
| 0.9-1.0 | 88 | 47.7% | $3.98 | +$3.98 | $350.12 |

**Key finding:** ALL confidence buckets have positive expected value. The 0.75 threshold was rejecting 103 profitable signals in the 0.6-0.7 range ($403 total P&L left on the table).

## Calibration Quality

- **Overall Brier score: 0.350** — poor (random = 0.25, perfect = 0.0)
- **Confidence-return correlation: very low** — confidence doesn't strongly predict return magnitude
- **Win rate is flat across buckets** (47-55%) — confidence doesn't predict win probability well either
- **EV varies by avg win/loss size**, not by win rate — the 0.8-0.9 bucket wins because its winners are $72 avg vs $16 avg losers, not because it wins more often

**Conclusion:** The confidence formula is a poor predictor of trade outcomes. It needs fundamental redesign (not just threshold adjustment). However, lowering the threshold still captures profitable signals that were being rejected.

## Per-Strategy Findings

| Strategy | Trades | Brier | Corr | Quality | Recommended |
|----------|--------|-------|------|---------|-------------|
| momentum_breakout | 269 | 0.306 | +0.10 | Poor | 0.00 (all buckets positive) |
| sector_rotation | 177 | 0.420 | +0.06 | Poor | 0.00 (mixed, mostly positive) |
| mean_reversion | 26 | 0.371 | +0.10 | Poor | 0.90 (only highest bucket profitable) |
| trend_following | 13 | 0.291 | -0.24 | Poor | 0.00 (all positive) |
| connors_rsi2 | 1 | 0.029 | 0.00 | N/A | N/A (1 trade) |
| short_term_mr | 1 | 0.090 | 0.00 | N/A | N/A (1 trade) |
| opening_gap | 2 | 0.491 | 0.00 | N/A | N/A (2 trades) |

**Notable:** Mean reversion is the only strategy where higher confidence actually predicts outcomes — only the 0.9+ bucket is profitable. All other strategies are profitable across the confidence range.

## Decision

### Threshold Change
- **Lower `min_confidence` from 0.75 to 0.65**
- This captures the 0.6-0.7 bucket (103 trades, $403 total PnL)
- Conservative choice — could go lower, but sample sizes below 0.6 are too small (4 trades)
- The 0.65 threshold still filters noise while capturing the bulk of profitable signals

### NOT changing per-strategy thresholds (yet)
- MR data suggests raising to 0.90, but only 26 MR trades in the sample — insufficient for per-strategy tuning
- Will revisit after accumulating 100+ trades per strategy

### Research Priority Flagged
- **Confidence formula needs redesign** — Brier 0.35 is worse than random guessing
- Win rate is flat across confidence levels — the formula doesn't capture edge
- The 0.8-0.9 bucket's high EV comes from win *size*, not win *probability*
- Possible improvement: incorporate regime, relative strength, and event proximity into confidence

## Files

- **Report:** `research/reports/calibration_sp500_20260315.json`
- **Module:** `research/calibration.py`
- **CLI:** `atlas calibrate` command added

## Future Work

- Re-run calibration quarterly as trades accumulate
- Per-strategy min_confidence once sample size > 100/strategy
- Redesign confidence formula to correlate with actual outcomes
- Test whether removing confidence entirely improves or hurts (pure signal + allocation pool limiting may be sufficient)
