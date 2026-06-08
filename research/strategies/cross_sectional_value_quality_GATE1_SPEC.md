# Gate-1 Pre-Registration — Cross-Sectional Value+Quality Factor on `shm`

**Status:** PRE-REGISTERED (locked 2026-06-08, BEFORE fundamentals data in hand)
**Predecessor:** Gate-0 PASS (conditional on data) — `research/brain/hypotheses/fundamentals_value_quality_gate0.md`
**Discipline note:** Construction, weights, rebalance, costs, and kill criteria below are FROZEN now. No tuning to fit the data. The legitimate-lever rule applies: any deviation must be pre-declared with rationale, never post-hoc.

## Hypothesis
On the inefficient survivorship-correct mid/small-cap universe (`shm`, 609 names), a **cross-sectional value+quality composite** built from point-in-time fundamentals (Sharadar SF1) carries deployable predictive power that the 22 tested price/technical strategies do not — because fundamentals encode information orthogonal to the price series, and the value/quality premium is strongest where coverage/attention is thinnest (small-caps).

## Data
- **Source:** Sharadar SF1, dimension **ARQ** (as-reported quarterly).
- **Point-in-time alignment:** a fundamental row becomes usable only on/after its `datekey` (filing-known date), then lagged **+1 trading day**. NEVER use `reportperiod`/`calendardate` for availability. This is the integrity rail for fundamentals.
- **Universe/dates:** the 609 cached `shm` names, 2016–2026, sector tags from existing `data/processed/sector_map_shm.json` (REQUIRED — deployment-sanity rail collapses an untagged book).

## Factor construction (FROZEN, equal weights, no optimization)
Cross-sectionally each rebalance, z-score each component (winsorize at 1st/99th pct), then average:
- **Value composite** = mean z of: earnings yield (`netinc`/`marketcap`, i.e. 1/pe), book-to-price (`bvps`/`price`, i.e. 1/pb), FCF yield (`fcf`/`marketcap`). Higher = cheaper.
- **Quality composite** = mean z of: `roe`, `roa`, `grossmargin`, low-leverage (−`de`). Higher = better quality.
- **Combined score** = 0.5·value_z + 0.5·quality_z. (Equal weight pre-registered; no weight search.)

## Portfolio construction (FROZEN)
- **Rebalance:** monthly (first trading day).
- **Selection:** long **top quintile** by combined score (≈top 20% of names with valid fundamentals that month).
- **Sector tags:** carried on every signal (deployment rail).
- **Costs:** small-cap slippage 0.0015 + volume-aware (identical to `config/active/shm.json`). Long-only.
- **Caps:** max_positions 35 for search run; re-confirm at live max_positions 10 (portfolio-confirm step, as done for csm).

### AMENDMENT 2026-06-08 (sizing plumbing — pre-result, no alpha DoF)
Original spec said "equal-weight, no hard stops." On first execution the Atlas backtest engine
produced **0 trades** for every config: the engine sizes positions by **risk-per-trade**
(`shares = risk_budget / (fill − stop)`) and ignores `signal.position_size`; with `stop=0`,
`risk_per_share = full price` → 0 shares → no entries (and no equity to compound). There is no
notional/equal-weight path in this engine. csm and all 22 comparison strategies use the house
**ATR-stop risk-sizing** model and trade normally on the same `$971` config (csm peak_concurrent
14, deployment PASS). **Amendment:** value/quality now uses the identical house sizing — ATR stop
(`atr_stop_mult` default 3.0) + `max_risk_per_trade_pct` 0.005, primary exit still monthly rank
(leaves top quintile), ATR stop as catastrophic backstop. **Why this preserves discipline:** (1)
no performance/PnL was observed before the amendment — the 0-trades run was a pure plumbing bug,
artifact never written, holdout never evaluated; (2) it makes the test a *cleaner* controlled
comparison — only the RANKING signal (fundamentals vs momentum) differs from csm, sizing held
identical; (3) the alpha hypothesis (value+quality composite → top quintile) is unchanged. PASS/KILL
criteria below are unchanged. Grid swaps `min_price` for `atr_stop_mult` (the live sizing DoF).

## Validation (rail-equipped battery — the ONLY valid backtest)
```
python3 scripts/run_strategy_battery.py --strategy cross_sectional_value_quality --market shm \
  --grid-size 12 --max-positions 35 --select default --holdout-eval \
  --output-path backtest/results/search/battery_cross_sectional_value_quality_shm.json
```
All 3 rails: write-once HOLDOUT (`--holdout-eval`, single-use — the only incorruptible gate), FDR-aware promote bar (`research/hypothesis_registry.jsonl`), deployment-sanity auto-FAIL.

## PASS / KILL criteria (FROZEN, evaluated against the battery output)
**PASS** (→ stage paper candidate, forward-track, NO live money) requires ALL:
- Battery TIER = **PROMOTE** (clears FDR-aware bar), AND
- Write-once **HOLDOUT = PASS**, AND
- median_cpcv ≥ 0.5, AND DSR ≥ 0.90, AND
- IS→OOS Sharpe does NOT flip sign / no degradation blowup (the csm failure mode), AND
- deployment-sanity = PASS (peak concurrency ≥ 5, ≥ 8 sectors — genuine breadth, not a 1–2 name book).

**KILL** (→ CLOSE the fundamentals thesis, document honest null, Atlas price-strategy trial continues to 2026-08-01) if ANY PASS condition fails.

**Time-box:** ≤ 2 weeks from data-in-hand. Coverage precondition (Gate-0 criterion 3): ≥ 60% of 609 names with ≥ 12 quarterly obs — if coverage fails, KILL at ingest (data too sparse for cross-sectional ranks).

## VERDICT 2026-06-08: TIER = FAIL → KILL (honest null)
Ran the full rail-equipped battery (12 configs, holdout quarantine on 2025–2026, FDR bar, deployment-sanity). Artifact: `backtest/results/search/battery_cross_sectional_value_quality_shm.json`.

**Primary (pre-registered default: w_value 0.5, top_pct 0.20, atr_stop 3.0):**
- CPCV median **0.240** (bar ≥0.5 → ❌) | DSR effective-N(5 of 12) **0.532** (FDR bar 0.979, n_families=23 → ❌) | LOO-group robustness **fail** ❌
- frac+ paths 1.00 ✓ | PBO 0.320 ✓ | min_regime +1.24 ✓ | regime_conc 1.00 ✓ | per_regime ✓ | forward_net +69.45 ✓
- time-split: IS Sharpe −0.022 → OOS +0.279 (both ≈ noise on a ~0 base)
- **Deployment: PASS** — peak_concurrent 15, avg 8.86, **11 sectors**, single-name 10.5%, 73 trades, median hold 151d. ⇒ a GENUINE broad factor book, so the FAIL is a real alpha null, NOT a breadth/plumbing artifact.
- Best grid config (w_value 0.5, top_pct 0.10, atr_stop 2.5): CPCV 0.393 / PF 1.44 — selection-biased, still < 0.5.
- **Holdout: NOT evaluated (null) — write-once gate INTACT.** Failed the in-search tier before earning a holdout look; the rail correctly preserved the incorruptible gate.

**Per the FROZEN PASS/KILL: TIER=FAIL ⇒ KILL.** The value+quality fundamental factor, tested honestly on survivorship-correct mid/small-caps under the same rails that caught the momentum mirage, deploys as a real 11-sector book and STILL produces no edge clearing the bar. Same answer as the 22 price/technical strategies and csm momentum: real-but-weak signal, not OOS/FDR-robust. The "better data" lever (orthogonal fundamental information) did not change the verdict at this scale.

**Do NOT re-open** without a materially different fundamental thesis (e.g., fundamental MOMENTUM/revisions, not static value/quality levels) pre-registered fresh. Data (`data/cache/shm_fundamentals.parquet`) + ingester cached for that contingency only.

## Build steps once SF1 entitled (in order)
1. `python3 scripts/sharadar_download.py SF1` → bulk export full PIT history.
2. `python3 scripts/ingest_sharadar_fundamentals.py` → coverage report + `data/cache/shm_fundamentals.parquet`. **Stop if coverage < 60%.**
3. Implement `research/strategies/cross_sectional_value_quality.py` EXACTLY per this spec (BaseStrategy, sector-tagged signals).
4. Run the rail-equipped battery above.
5. Evaluate against FROZEN PASS/KILL. Record verdict to brain + registry. Either stage paper candidate OR document honest null.
