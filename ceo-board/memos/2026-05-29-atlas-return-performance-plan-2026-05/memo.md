# Decision Memo: Atlas Return & Performance Improvement Plan

**Date**: 2026-05-29  
**Decision**: CONDITIONAL ACCEPT  
**Confidence**: High  
**Vote**: 5 for conditional, 0 against, 0 abstain

## Executive Summary

Atlas should pursue higher returns, but not by loosening risk gates or immediately expanding universes. The board unanimously recommends a staged plan: diagnose the 0/32 research-promotion signal, complete the overlay log-only review, stabilize the data/research foundations, then add return levers one at a time: volatility-aware sizing, one additive SP500 strategy, and only later paper-only universe expansion.

## Board Positions

### Revenue — CONDITIONAL
The largest revenue leak is not missing markets; it is a built-but-idle overlay and a research loop that promoted zero variants. Fix the promotion pipeline first, activate the overlay if log-only proves positive, then scale.

### Risk — CONDITIONAL
Do not let the pressure for returns weaken gates. The recent cross-universe leakage and active SQLite transition mean Atlas must close foundational safety items before new live complexity.

### Technical — CONDITIONAL
The 0/32 promotion rate is a diagnostic. Without the regression harness, Atlas cannot tell whether it rejected weak variants correctly or silently discarded good candidates. Technical foundations gate live promotion.

### Moonshot — CONDITIONAL
Atlas should learn faster while capital is small. The AI overlay and volatility-aware sizing are the highest-upside levers, but log-only cannot become a permanent limbo state.

### Operations — CONDITIONAL
Do not change multiple axes at once. Sequence research diagnostics, overlay review, sizing analysis, SP500 strategy validation, and any eventual universe expansion so attribution remains clear.

## Decision Rationale

The board agrees on five principles:

1. **Do not increase exposure first.** Increase edge quality and measurement confidence first.
2. **0/32 promotions must be explained.** It may mean gates are working, but it may also mean search space, thresholds, or pipeline behavior is wrong.
3. **Overlay is the nearest high-ROI lever.** It is already built, but activation requires a formal #215 log-only verdict.
4. **Position sizing is underused.** Volatility-scaled or fractional-Kelly sizing can improve returns without adding new strategy classes, if drawdown remains bounded.
5. **Universe expansion is deferred.** SP500 has enough opportunity; cross-universe leakage was too recent to reopen live multi-universe scope.

## Mandatory Gates

Before any new live strategy/config promotion:

- #219 regression harness merged and green.
- #354 stale SP500 phase2 tests fixed before momentum parameter changes.
- OOS/walk-forward validation: minimum 6-month hold-out, no optimization on hold-out.
- Candidate thresholds: OOS Sharpe ≥ 0.6, profit factor ≥ 1.2–1.3, max drawdown ≤ 15% or not worse than baseline by >3–5 pts.
- Human approval for any live config change; re-enable approval before material capital scaling.
- No simultaneous changes to strategy + overlay + sizing; one axis at a time.

Before overlay activation:

- #215 closed with documented log-only results: signal quality, false positives, avoided losses, missed winners, and net PnL effect.
- Initial live mode must be tighten-only, with kill switch and conservative caps.

Before universe expansion:

- SQLite cutover/shadow validation complete (#267/#276).
- Cross-universe isolation covered by regression tests.
- Paper-only validation first; no direct live expansion.

## 30 / 60 / 90 Day Roadmap

### Days 0–30 — Diagnose and unlock safe levers

1. Diagnose the latest 0/32 promotion cycle: thresholds, search space, data quality, and audit trail.
2. Complete #219 regression harness and #354 stale SP500 tests.
3. Complete #215 overlay log-only review with a go/no-go memo.
4. Run volatility-scaled/fractional-Kelly sizing analysis on current `momentum_breakout`; no live sizing change until drawdown gate passes.
5. No new live strategies, no universe expansion, no promotion-threshold softening.

### Days 30–60 — Activate proven improvements

1. If #215 passes, activate overlay in tighten-only mode with conservative caps and kill switch.
2. If sizing analysis passes, promote bounded volatility-aware sizing through config gate.
3. Run clean SP500 candidate sweeps under #219; identify 1–2 genuinely additive strategies/variants.
4. Continue SQLite cutover/shadow validation; avoid live strategy complexity during unstable data-layer windows.

### Days 60–90 — Add diversification carefully

1. Promote at most one additional SP500 strategy if OOS and portfolio-combination gates pass.
2. Paper-test second strategy for 30 real-time trading days before live if attribution remains uncertain.
3. Queue non-SP500 universes for paper-only research after SP500 multi-strategy stability is proven.
4. Review whether capital can be scaled only after drawdown, approval, and live-vs-backtest drift gates pass.

## Explicit No-Go Items

- No loosening OOS thresholds to force promotions.
- No live universe expansion until isolation and SQLite gates pass.
- No overlay activation before #215 is closed.
- No config promotion during an active SQLite migration/shadow-failure window.
- No disabling fail-closed regime behavior.
- No simultaneous strategy + overlay + sizing changes.
- No material capital scaling while `approval: false` remains live policy.

## Next Actions

1. Create/track the zero-promotion diagnostic task.
2. Create/track the volatility-aware sizing analysis task.
3. Keep #215, #219, #267/#276, and #354 as gating priorities.
4. Revisit this decision after #215 and #219 close, or by 2026-06-29, whichever comes first.
