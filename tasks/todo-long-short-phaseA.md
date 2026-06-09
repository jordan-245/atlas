# Phase A: cross_sectional_long_short proxy — build + run

Board: ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding
Pre-reg: research/brain/hypotheses/equity_long_short.md
Spec: research/strategies/cross_sectional_long_short_SPEC.md

## Plan
- [x] 1. Study `research/proxy/pairs_stat_arb.py` — returns-based market-neutral template, scores via adapter.assemble_bundle+evaluate_tiers
- [x] 2. Scorer API mapped: assemble_bundle(returns, trades, grid, forward_net) -> {bundle,diagnostics}; evaluate_tiers -> {tier PROMOTE/SCREEN/FAIL}
- [x] 3. Baseline located: cached long-only csm net Sharpe +0.905, 85 trades (2020-2026) in _ensemble_cache.pkl. Data via vo.load_data; panels via build_panels; regime via regime_series.
- [x] 4. Built `research/proxy/cross_sectional_long_short_proxy.py`
- [x] 5. Apples-to-apples = short leg ON vs OFF, identical construction (cleaner than reproducing engine csm)
- [x] 6. Ran Phase A: borrow sweep {0,25,50}bps + 5bps/side slippage, scored via battery
- [x] 7. Verdict written to hypothesis doc; TSV appended
- [x] 8. Queue entry -> rejected; CEO reported

## REVIEW (2026-06-05) — KILL (honest null)
Long-short net Sharpe **+0.01 @0bps / -0.01 @50bps** vs long-only **+1.67** under identical
construction. Short leg adds NO incremental net OOS Sharpe (incremental -1.66); battery tier FAIL
at every borrow level (per_regime_ok False). All pre-registered KILL conditions fired. The factor's
return lives entirely in the LONG leg; neutralizing removes the beta/tilt and costs finish it
(consistent with #422 pairs/stat-arb net-of-cost null). **Do not tune to rescue.** Keep long-only
`cross_sectional_momentum`; Phase B engine plumbing for shorts stays unbuilt (never needed).
Next: news-sentiment overlay (board fast-follow #2).

## Gates (from pre-reg — do not move)
PASS: battery median Sharpe >=0.5 net AND >= long-only net OOS Sharpe; >=50 trades; +ve per-regime exp in >=2 regimes; +ve effective-N DSR.
KILL: net<0.3, OR short leg adds no incremental OOS Sharpe, OR negative regime exp, OR <50 trades, OR edge only at $0 cost. Do not tune to rescue.
