# Gate-0 Feasibility — Point-in-Time Fundamentals (Sharadar SF1) → Small-Cap Value/Quality Factors

**Status:** OPEN (Gate-0 feasibility spike)
**Opened:** 2026-06-08
**Owner:** CEO/research
**Board context:** Extends 2026-06-06 reallocation memo. The only "better data" lever not refuted by the 22 price/technical nulls on clean `shm` data. Hermes pitcher-K remains the human-attention priority; this runs as a low-cost autonomous spike.

## Thesis
Every one of the 22 strategies tested on the survivorship-correct mid/small-cap universe (`shm`) is **price/technical** (momentum, mean-reversion, breakout, oscillators). All FAIL the 3 rails. New data can only help if it carries information **not already in the price series**. Point-in-time fundamentals (Sharadar SF1) → cross-sectional **value/quality** factors on inefficient small-caps is the single untested substrate with strong academic priors (the small-cap value/quality premium is the most robust anomaly in the literature).

## Gate-0 = FEASIBILITY ONLY (no performance claim). HARD KILL if ANY criterion fails.
Pre-registered BEFORE pulling any data. Time-box ≤ 1 day.

1. **Accessibility / cost.** SF1 pullable with the existing `NASDAQ_DATA_LINK_API_KEY` at $0 incremental, OR at ≤ the SEP $69/mo we already pay (download-and-own then cancel). **KILL** if SF1 requires a materially more expensive subscription (> $150/mo) with no download-and-own option.
2. **Point-in-time integrity.** SF1 must expose `datekey` (date the filing became publicly known) distinct from `reportperiod`, so every factor can be lagged to its known-date. **KILL** if only `reportperiod` is available (look-ahead unavoidable → any backtest is a mirage, same class of bug the rails exist to catch).
3. **Coverage.** ≥ 60% of the 609 cached `shm` names have SF1 fundamentals with ≥ 12 quarterly observations spanning 2016–2026. **KILL** if < 60% coverage OR median history < 12 quarters (cross-sectional ranks too sparse to populate each rebalance).
4. **Feature availability.** Core value + quality fields present to build composites: at least one value field (book value / P/B, earnings / P/E, or FCF yield) AND at least one quality field (ROE/ROA, gross margin, or accruals). **KILL** if fundamentals too sparse to build either a value OR a quality composite.

## Decision rule
- **ALL 4 pass →** proceed to **Gate-1**: one pre-registered cross-sectional value/quality factor backtest on `shm` under all 3 rails (holdout quarantine + FDR-aware promote bar + deployment-sanity), with explicit OOS kill criteria defined before the run. Hard kill ≤ 2 weeks.
- **ANY fail →** CLOSE "better data / fundamentals" with documented evidence. Atlas autonomous price-strategy trial continues untouched to its 2026-08-01 verdict. Reallocate attention back to Hermes.

## Results (2026-06-08 feasibility probe)

**Criterion 2 — Point-in-time integrity: ✅ PASS.** SF1 schema (112 cols) exposes both `datekey` (filing-known date) and `reportperiod`, distinct, confirmed on real sample rows (e.g. datekey 2022-05-31 vs reportperiod). Sharadar is genuinely point-in-time → factors can be lagged to known-date, no unavoidable look-ahead. This is the criterion that matters most for backtest integrity, and it passes cleanly.

**Criterion 4 — Feature availability: ✅ PASS.** Full value + quality field set present: value = `pb`, `pe`, `pe1`, `ps`, `ps1`, `bvps`, `fcf`, `fcfps`, `evebit`, `evebitda`, `divyield`; quality = `roe`, `roa`, `roic`, `grossmargin`, `netmargin`, `ebitdamargin`, `de`, `currentratio`, `assetturnover`, accrual inputs. More than enough to build both a value AND a quality composite.

**Criterion 1 — Accessibility / cost: ❌ NOT on current key.** SF1 is a separate paid subscription, NOT included in our SEP ($69/mo) entitlement. Confirmed three ways: (a) filtered API returns 0 rows for all 3 `shm` small-caps tested (ACAD/ACHC/ACIA); (b) mega-caps return only 2 `MRY` rows (sample); (c) bulk export yields a 20 KB **sample** file — 30 Dow-30 mega-caps, `MRY` only, 2022–2024. To obtain SF1 for our 609 small-caps we must subscribe to SF1 (download-and-own one month, then cancel — same model as SEP). **Gate hinges on the SF1 monthly price vs the ≤$150/mo + download-and-own threshold.**

**Criterion 3 — Coverage: ✅ PASS (resolved 2026-06-08 after subscribe).** Full SF1 entitled (650 MB bulk export vs 20 KB sample). Ingested ARQ rows for the shm universe: **609/609 names have data; 579/609 = 95.1% have ≥12 quarters** (bar 60%); median **42 quarters/name**; datekey range 1994→2026 (full PIT). 32,163 rows → `data/cache/shm_fundamentals.parquet`. Decisive PASS.

**→ ALL 4 GATE-0 CRITERIA PASS. Proceeding to Gate-1 (pre-reg: `research/strategies/cross_sectional_value_quality_GATE1_SPEC.md`).**

### Pricing (2026-06-08, web-verified)
- SF1 ("Core US Fundamentals") is a **standalone, individually-licensable** product (data.nasdaq.com/databases/SF1) — NOT bundle-only, so the ">$150/mo bundle-only" KILL condition does **not** trigger.
- Individual-user tier is in the **$50–99/mo** range (ProQuest historical floor $50; our own comparable SEP product is $69/mo; SF1 individual commonly ~$99). Comfortably under the $150 threshold.
- **Download-and-own model works** (proven on SEP: one month → full 25-yr point-in-time history bulk export → cancel). So real cost ≈ **one month (~$69–99)**, reversible.
- **Free alternative exists:** SEC EDGAR `companyfacts` XBRL API is $0 and genuinely point-in-time (filing dates known), but requires a real parsing build (XBRL → standardized quarterly factors, restatement handling, CIK↔ticker map) and small-cap XBRL coverage is messier. Sharadar's value is turnkey-clean point-in-time. For a *feasibility* Gate-1, Sharadar $69 is the cheapest-**time** path; EDGAR is the cheapest-**dollar** path.

### Gate-0 verdict: **PASS (conditional on a ~$69–99 one-month, reversible spend)**
- Criterion 1 (cost): PASS — standalone product, ≤$150/mo, download-and-own available.
- Criterion 2 (point-in-time): PASS.
- Criterion 4 (features): PASS.
- Criterion 3 (coverage): only remaining unknown — measurable the moment we have the data.

→ **Decision needed (human, spend gate):** authorize one month of SF1 (download-and-own) — OR take the $0 EDGAR path — to unblock the coverage check and proceed to **Gate-1** (one pre-registered cross-sectional value/quality factor backtest on `shm` under all 3 rails, OOS kill criteria pre-set, ≤2 weeks). Hermes pitcher-K remains the human-attention priority; Gate-1 runs mostly autonomous.
