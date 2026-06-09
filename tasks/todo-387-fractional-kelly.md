# Task #387 — Volatility-aware / Fractional-Kelly sizing analysis (SP500 momentum_breakout)

Paper/backtest analysis ONLY. No live config changes. No broker/systemd. Do not edit config/active/sp500.json.

## Plan
- [x] 1. Added `scripts/analyze_fractional_kelly_sizing.py` (pure helpers + parallel arm runner). Loads active config read-only, deep-copies, mutates only sizing blocks.
- [x] 2. Arms (same signal set; only sizing differs): baseline_fixed (control), live_as_configured, vol_target_only, dd_scaling_only, risk_mult_1.5x, risk_mult_2.0x, frac_kelly_0.25x, frac_kelly_0.5x.
- [x] 3. Estimated full-Kelly f* from baseline R-multiples: f*=0.115 (W=0.366, b=2.51). 0.25x/0.5x both exceed 2% cap.
- [x] 4. Ran full-history walk-forward per arm (parallel, 6 workers). Derived 2024-2025 max DD from equity-curve slice.
- [x] 5. Metrics captured: CAGR, Sharpe, Sortino, PF, max DD (full + 2024-25), trades, calmar, expectancy_r.
- [x] 6. Gate evaluated at $25k (funded) + $971 (live slice). No variant passes cleanly. promote=NO.
- [x] 7. Tests `tests/test_fractional_kelly_sizing_analysis.py` — 25 passed.
- [x] 8. Report `docs/project-notes/fractional-kelly-sizing-analysis-2026-06-01.md` written with commands/paths.

## Review (2026-06-01)
- **Outcome: NO promotion.** Sizing question answered NO — neither vol-targeting nor capped fractional-Kelly improves risk-adjusted return for live SP500 momentum_breakout.
- **Structural blocker found:** `trading.live_safety.max_order_value=$5,000` cap binds for every position at funded equity ($25k) -> all flat-risk arms byte-identical (Sharpe 0.624) -> risk_pct + overlays inert. At live $971 slice sizing expresses but min_position_value floor + integer rounding + concentration distort (33-62% DD). No equity band where risk-based sizing both expresses and is undistorted.
- **Live overlays net-negative on Sharpe** in both regimes (0.586/0.844 vs 0.624/0.910). Fractional Kelly collapses to 2% cap (full-Kelly ~11.5%) -> 62% maxDD at $971.
- **Active config untouched.** No broker/systemd. Recommend new follow-up task to reconcile position-sizing architecture (scale/replace $5k cap) before any re-test, and a separate reopt/OOS candidate to consider disabling the net-negative live overlays.
- #387 can be marked COMPLETE (analysis delivered with explicit NO recommendation + follow-up).

## Notes
- Single full backtest ~124s; parallelize across cores.
- Run at representative equity ($25k) primary + live $971 sensitivity (low-equity floor distortion per lessons).
- regime/macro scaling left identical across arms (constant control factor).
