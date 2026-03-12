# Experiments Index

> Last 78 experiments (newest first). Older experiments pruned.

| ID | Strategy | Parameter | Result | Sharpe Δ |
|----|----------|-----------|--------|----------|
| wave5_tf_trail_sweep | ? | TF trailing_stop_atr_mult 3.5 slightly better than 3.0 | discarded | n/a (migrated) |
| wave5_pool_toggle | ? | Allocation pools should not degrade current 3-strategy portfolio | discarded | n/a (migrated) |
| wave5_og_gap_sweep | ? | OG gap_threshold sweep — current -0.02 may be suboptimal | discarded | n/a (migrated) |
| wave5_mr_profit_sweep | ? | MR profit_target_atr_mult sweep should find better exit level | discarded | n/a (migrated) |
| wave5_full_reopt | ? | Post-SMA200 reoptimization should find better params since all were tuned without SMA200 | kept | n/a (migrated) |
| wave5_cdd_solo | ? | CDD captures short-term reversal on large-cap stocks | discarded | n/a (migrated) |
| wave4_mr_strength_exit | ? | The LBR published exit rule (sell when close > yesterday high) captures the first sign of strength recovery. Testing this on existing MR strategy as an alternative to the current profit-target + mean-reversion exit. Expected: faster exits, higher win rate, possibly lower avg profit per trade. | discarded | n/a (migrated) |
| wave4_mr_hold5_oos | ? | Wave 3 found max_hold=5 beats max_hold=10 (Sharpe +0.035, CAGR +3.1pp, PF 4.55 vs 3.64). OOS validation needed before promotion. MR trades resolve quickly — 5-day hold captures most reversion, longer holds add noise. | discarded | n/a (migrated) |
| wave4_lbr_solo_relaxed | ? | Relaxing IBS threshold from 0.3 to 0.5 generates more trades on individual stocks (which have wider IBS distributions than SPY). Tests if the band signal alone carries enough edge without strict IBS filtering. | discarded | n/a (migrated) |
| wave4_lbr_solo | ? | The Quantitativo IBS lower-band strategy (Sharpe 2.11 on SPY) can generate profitable signals when adapted to individual SP500 stocks. Published params: range_lookback=25, high_lookback=10, band_mult=2.5, IBS<0.3, exit on close>prev_high. | discarded | n/a (migrated) |
| wave4_lbr_no_sma200 | ? | SMA-200 filter was a clear win for existing MR (+0.28 Sharpe). But LBR specifically targets extreme dips — which often occur in downtrends. Testing if removing SMA-200 captures more deep-dip opportunities that still revert quickly. Published strategy on SPY used SMA-300 as improvement. | kept | n/a (migrated) |
| wave4_lbr_ibs_sweep | ? | IBS threshold controls signal quality vs quantity. Published used 0.3 for SPY. Individual stocks have different IBS distributions. Testing 0.1-0.6 to find optimal threshold that maximizes risk-adjusted returns. | discarded | n/a (migrated) |
| wave4_lbr_band_sweep | ? | Band multiplier controls selectivity: wider band = fewer but deeper dips. Published used 2.5x. Testing 1.5-4.0x to find optimal trade-off between trade frequency and signal quality on individual stocks. | discarded | n/a (migrated) |
| wave3_vol_sweep | ? | Higher volume threshold for MR entries improves trade quality. Wave 1 proved 1.5x volume on MR solo: Sharpe -0.02→0.38, PF 1.30→1.62. Wave 2 combined test FAILED due to infrastructure bug (nested params). This experiment uses full volume dict sweep to bypass the nested param issue. Expect 1.5x to be optimal in combined mode too. | discarded | n/a (migrated) |
| wave3_trsi_solo | ? | Triple RSI (RSI(5) declining 3 days, below 30, with lookback check) generates rare but high-conviction mean reversion signals on individual SP500 stocks. Published edge on SPY: 90% WR, PF 4.0. Adapted for individual stocks with SMA-200 filter and volume confirmation. Expects fewer but higher-quality trades than existing MR strategy. | discarded | n/a (migrated) |
| wave3_rsi_period | ? | Web research (Triple RSI, Connors, Alvarez) consistently shows RSI(2-5) outperforming RSI(14) for mean reversion signals. Our MR uses RSI(14). Shorter RSI periods may improve entry timing. Combined-mode sweep gives realistic portfolio-level impact unlike unreliable solo sweeps (lesson #30). | discarded | n/a (migrated) |
| wave3_ibs_sweep | ? | Requiring low IBS (close near day's low) for MR entries improves signal quality. Alvarez research shows IBS < 25 gives 58% avg gain improvement on RSI(2) strategy. Our MR has ibs_max=1.0 (disabled). Testing restrictive thresholds should filter out weak MR signals. | discarded | n/a (migrated) |
| wave3_hold_combined | ? | Wave 2 tested max_hold_days in SOLO mode (all negative Sharpe due to fee drag at $4K). Relative ranking showed 10 > 15 > 7 > 5 > 3. Combined-mode sweep gives realistic absolute Sharpe. Short holds (3-5d) may reduce time risk; longer holds (10-15d) capture more reversion. | discarded | n/a (migrated) |
| wave2_vol_combined | ? | Wave 1 proved 1.5x volume filter on MR solo: Sharpe -0.02→0.38, PF 1.30→1.62. Applying to the full combined portfolio should similarly improve signal quality by filtering out low-conviction entries across all strategies. | discarded | n/a (migrated) |
| wave2_tom_filter | ? | The Turn of Month effect (last 5 + first 3 trading days) shows stocks generate virtually all monthly returns in this window (Lakonishok & Smidt 1988, confirmed 2024). Boosting signal confidence during TOM window (or suppressing signals mid-month) should improve trade quality. This is a calendar-based filter, completely uncorrelated with our price-based signals. | discarded | n/a (migrated) |
| wave2_rsi2_solo | ? | Connors RSI(2) with SMA-200 filter generates profitable mean reversion signals | discarded | n/a (migrated) |
| wave2_exit_og | ? | Shorter hold periods better match gap-fill resolution timeline | discarded | n/a (migrated) |
| wave2_exit_mr | ? | Shorter hold periods improve MR risk-adjusted returns | discarded | n/a (migrated) |
| wave2_chandelier_tf | ? | Wider trailing stop ATR multiplier captures more trend profit | discarded | n/a (migrated) |
| ar-20260312_124147 | ? | rsi_period: 10→14 | kept | n/a (migrated) |
| ar-20260312_110553 | ? | sma200_filter: None→False | kept | n/a (migrated) |
| ar-20260312_104004 | ? | atr_stop_mult: None→1.5 | kept | n/a (migrated) |
| ar-20260312_101555 | ? | adx_period: None→7 | kept | n/a (migrated) |
| ar-20260312_094057 | ? | rsi_period: 7→10 | kept | n/a (migrated) |
| ar-20260312_091855 | ? | rsi_period: 14→7 | kept | n/a (migrated) |
| ar-20260312_005338 | ? | atr_stop_mult: 1.2→2.0 | kept | n/a (migrated) |
| ar-20260312_003716 | ? | bb_std: 2.5→1.5 | kept | n/a (migrated) |
| ar-20260312_001727 | ? | max_hold_days: 15→10 | kept | n/a (migrated) |
| ar-20260312_000417 | ? | atr_stop_mult: 2.0→1.5 | kept | n/a (migrated) |
| ar-20260311_234534 | ? | ibs_max: 1.0→0.7 | kept | n/a (migrated) |
| ar-20260311_233403 | ? | profit_target_atr_mult: 1.5→2.5 | kept | n/a (migrated) |
| ar-20260311_233005 | ? | ibs_threshold: None→1.0 | kept | n/a (migrated) |
| ar-20260311_232235 | ? | zscore_lookback: 20→30 | kept | n/a (migrated) |
| ar-20260311_231130 | ? | min_down_days: None→2 | kept | n/a (migrated) |
| ar-20260311_231123 | ? | rsi_period: 10→14 | kept | n/a (migrated) |
| ar-20260311_213036 | ? | zscore_lookback: 30→20 | kept | n/a (migrated) |
| ar-20260311_212959 | ? | rsi_period: 14→10 | kept | n/a (migrated) |
| ar-20260311_183152 | ? | max_hold_days: 10→20 | kept | n/a (migrated) |
| ar-20260311_182518 | ? | profit_target_atr_mult: 3.0→1.5 | kept | n/a (migrated) |
| ar-20260311_181209 | ? | atr_period: 14→20 | kept | n/a (migrated) |
| ar-20260311_180204 | ? | zscore_lookback: 20→30 | kept | n/a (migrated) |
| ar-20260311_175423 | ? | rsi_period: 8→14 | kept | n/a (migrated) |
| ar-20260311_174215 | ? | atr_stop_mult: 1.9→1.5 | kept | n/a (migrated) |
| ar-20260311_174025 | ? | atr_period: 14→10 | kept | n/a (migrated) |
| ar-20260311_173914 | ? | pullback_pct: 0.05→0.06 | kept | n/a (migrated) |
| ar-20260311_173736 | ? | slow_ma: 175→20 | kept | n/a (migrated) |
| ar-20260311_173539 | ? | fast_ma: 25→50 | kept | n/a (migrated) |
| ar-20260311_172526 | ? | zscore lookback 10→7: even faster | kept | n/a (migrated) |
| ar-20260311_153100 | ? | RSI period 8 — between grid points | kept | n/a (migrated) |
| ar-20260311_152720 | ? | trail=2.8 (between 2.5 and 3.0) | kept | n/a (migrated) |
| ar-20260311_152317 | ? | atr_period: 14→20 | kept | n/a (migrated) |
| ar-20260311_152116 | ? | Asymmetric risk: 1.5x stop, 3x target | kept | n/a (migrated) |
| ar-20260311_151649 | ? | fast_ma: 25→50 | kept | n/a (migrated) |
| ar-20260311_150729 | ? | atr_stop=1.9 (just below 2.0) | kept | n/a (migrated) |
| ar-20260311_150658 | ? | max_hold_days: 10→20 | kept | n/a (migrated) |
| ar-20260311_145916 | ? | atr_stop_mult: 2.5→1.5 | kept | n/a (migrated) |
| ar-20260311_145350 | ? | atr_stop=1.8 (between 1.5 and 2.0) | kept | n/a (migrated) |
| ar-20260311_145315 | ? | zscore_entry: -2.0→-1.0 | kept | n/a (migrated) |
| ar-20260311_144909 | ? | zscore_lookback: 20→10 | kept | n/a (migrated) |
| ar-20260311_144117 | ? | Disable SMA200 filter — trade in all conditions | kept | n/a (migrated) |
| 20260310_185804_9ff181 | ? | Test if connors_rsi2 is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185804_7a6d6d | ? | Test if bb_squeeze is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185804_730b2e | ? | Test if momentum_breakout is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185804_461735 | ? | Test if short_term_mr is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185801_dd443f | ? | Test if momentum_breakout is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185801_754a55 | ? | Test if short_term_mr is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_185801_5ee089 | ? | Test if bb_squeeze is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_183205_f86e93 | ? | Test if short_term_mr is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_183205_1eac79 | ? | Test if momentum_breakout is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_181024_f7508b | ? | Test if bb_squeeze is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_181024_d2d7b3 | ? | Test if mtf_momentum is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_181024_d14b81 | ? | Test if short_term_mr is viable as standalone strategy | discarded | n/a (migrated) |
| 20260310_181024_9b3400 | ? | Test if momentum_breakout is viable as standalone strategy | discarded | n/a (migrated) |
