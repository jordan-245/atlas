---
id: 2026-06-01-atlas-performance-growth-or-new-project
title: "Should We Extend Atlas for Higher Returns or Start a New Project?"
created: 2026-06-01T03:29:54.840Z
profile: standard

---

# Should We Extend Atlas for Higher Returns or Start a New Project?

## Situation

The user is asking how to increase performance/returns, or whether a new project should be started instead. Atlas is currently active but capital is small (~$1.37K SP500 equity, realized PnL +$283 as of May 29) and live approval remains false. Current live active config is SP500 v3.2.4 with one strategy, momentum_breakout, which recent nightly research showed is exhausted/fragile: repeated runs produced 38 screened -> 1 combined -> 0 kept. We have just repaired key research trust issues (#392-#396): backlog diagnostics, baseline accounting, schema compatibility, knowledge ingest, source->claim backfill, and LLM paper metric extraction. Recent board guidance already warned not to increase exposure first; focus should be diagnosing the 0/32 SP500 promotion signal, overlay log-only review (#215), regression harness (#219), stale SP500 tests (#354), sizing analysis (#387), and one additive OOS-validated SP500 strategy (#388). The question now is whether to keep investing in Atlas extensions, pursue adjacent return engines, or start a separate project.

## Stakes

Decision affects engineering time, research direction, and potential capital risk. A wrong choice could waste weeks on low-signal strategy churn or push unsafe trading changes; a good choice could create a more scalable return engine or a parallel income/edge project. Atlas currently has low capital but high infrastructure leverage. The biggest opportunity cost is spending another month tuning an exhausted strategy instead of building validated sizing/strategy additions or a new project with faster feedback and lower risk. Financial exposure should remain capped until research validation, overlay review, and approval gates pass.

## Constraints

No live trading/config changes without explicit approval. Do not promote Atlas configs or increase exposure first. Preserve risk gates: approval=false, tight config promotion guards, OOS validation, no threshold softening. Engineering bandwidth is limited; current Atlas open critical/high tasks include #215 overlay log-only review, #219 regression harness, #267 SQLite sole-writer cutover, #276 reconcile-script retirement, #354 stale SP500 tests, plus research direction #387/#388 and #397 knowledge extraction review. New projects should avoid regulatory or operational complexity unless expected edge justifies it.

## Key Questions

1. 1. What is the highest expected-return path inside Atlas over the next 2-4 weeks: sizing, additive SP500 strategy, universe expansion, overlay, or infrastructure cleanup?
2. 2. What Atlas work should be explicitly stopped or deprioritized because it is low-signal or exhausted?
3. 3. Should we start a new project in parallel? If yes, which category offers the best risk-adjusted upside: prediction-market research, credibility/alpha intelligence, sports/NRL forecasting, SaaS/dashboard product, or another data edge?
4. 4. How should engineering time be allocated between Atlas hardening, Atlas return expansion, and any new project?
5. 5. What concrete next 7-day plan would maximize upside while preserving safety gates?
