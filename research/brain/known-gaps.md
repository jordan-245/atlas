# Atlas — Known Data / Feature Gaps

Living document. Updated whenever a data source goes down, a feature is disabled,
or a gap is found. Remove entries when resolved.

Last audit: 2026-04-22

## Current Gaps

- **2026-06-05 — Engine is hard-coded long-only.** `strategies/base.py::Signal.__post_init__`
  raises unless `direction == "long"` (stops/TPs assume long); no short-side handling exists in
  `engine/` or `risk/`. Alpaca broker *can* short ETB names ($0 borrow, needs $2k equity) but the
  internal signal -> plan -> risk -> sizing pipeline cannot represent a short.
  Impact: blocks any live long-short / market-neutral / short-MR strategy (board #388 lever).
  Fix: Phase B engine plumbing (Signal short semantics, short sizing/risk, broker short orders,
  daily ETB re-check) — BLOCKED until the long-short edge passes Phase A research
  (`research/strategies/cross_sectional_long_short_SPEC.md`) + AUM gates.
  Monitor: pre-registration `research/brain/hypotheses/equity_long_short.md`; board review 2026-06-19.

## Recently Resolved

- **2026-04-22 — FRED regime features.**
  User flagged potential "credit/dollar/yield curve NULL" degradation (~25% regime
  degradation estimate). Verified healthy: key present in `~/.atlas-secrets.json`
  under `fred_api_key`, `data/fred.py::FREDClient` loads correctly,
  `regime_history` has fresh rows through 2026-04-22 with `credit +1.00` and
  `yield curve normal (+0.43)` populated. Added `scripts/check_fred_health.py` +
  weekly `atlas-fred-health.timer` to prevent silent regression.

## How to Detect a New Gap

1. **`scripts/check_fred_health.py`** — FRED API key present, each series
   non-empty, latest data point within acceptable lag (5d for daily, 35d for
   monthly). Sends Telegram alert on failure; exits 1. Runs weekly via
   `atlas-fred-health.timer` (Mon 08:00 AEST).
2. **`scripts/check_regime_features_staleness.py`** — Parses `regime_history.reasoning`
   for credit / yield curve / trend / risk features; alerts if any feature is
   absent for ≥7 consecutive days.
3. **`scripts/data_integrity_monitor.py`** — Research DB identical-metric canary
   (P1.1). Detects ETF cross-universe identical metrics bug class. Runs every 6h.
4. **`scripts/regime_performance_report.py`** — Includes a "Data Quality" section
   (top of report) with FRED health, regime_history row count, and per-feature
   coverage for last 7 days.
5. **`scripts/post_sweep_canary_check.py`** — Automated post-sweep verification
   for research quality.

## Adding a New Known Gap

Add an entry under `## Current Gaps` with:
- **Date discovered**
- **What's missing/broken** (table, column, API, config, feature name)
- **Impact** (quantified if possible — e.g., "regime model degrades ~25%")
- **Fix** (one-line action to take — e.g., "add fred_api_key to ~/.atlas-secrets.json")
- **Monitor** (how we'll know when fixed — link to health script or DB query)

Example entry format:
```
- **2026-XX-XX — <name>.** <short description>.
  Impact: <estimated impact>.
  Fix: `<command or action>`.
  Monitor: `scripts/check_fred_health.py --json` or query `regime_history`.
```
