# Survivorship-Free Data Vendors — Detailed Comparison (2026-06-06)

> For the board's paid-data decision (`ceo-board/memos/2026-06-06-survivorship-free-data-decision`).
> Our need: **daily** OHLCV, **survivorship-free (delisted included)**, **point-in-time index
> membership** (esp. **mid/small-cap S&P 400/600**), **Python/API**, cheap. Intraday/options not
> needed for the search (that was the deferred SIP). All names still pass our Alpaca `is_tradable`
> filter for live eligibility.

## Comparison table

| Vendor | Survivorship-free (delisted)? | Point-in-time index membership? | Depth | API | Price (approx) | Verdict for us |
|---|---|---|---|---|---|---|
| **Norgate Data (Platinum, US)** | ✅ 25,222 delisted since 1950 | ✅ S&P 500/**400/600**, Russell 3000 (PIT) | 1950/1990+ daily EOD | `norgatedata` Python | **~$630/yr ≈ $52.50/mo** (Gold/current-only $360/yr) | **BEST FIT** — exactly our need, cheapest credible survivorship-free. Daily-only (fine). Annual/6-mo billing (less flexible for a short trial). |
| **Sharadar (Nasdaq Data Link, core bundle)** | ✅ 21k+ active+delisted to 1998 | ⚠️ via tickers metadata; PIT S&P 400/600 weaker than Norgate | 1998+ daily | `nasdaqdatalink` Python | **$99/mo — DOWNLOADABLE then CANCEL** (≈ $99 one-time for full history) | **BEST FOR THE TIME-BOX** — pay $99 once, bulk-download the entire survivorship-free dataset as ZIP, cancel, run the 8-wk search locally. Reused-symbol handling slightly weaker. |
| **EODHD** | ✅ delisted API | ✅ historical constituents S&P 500/**400/600**/100 | global, deep | Python SDK | Fundamentals €59.99/mo (~$65); All-in-One €99.99/mo (~$108; €83/mo annual); EOD-only €19.99 (no constituents) | **Good #3** — global, has PIT mid/small constituents. EUR pricing; need the Fundamentals tier (~$65/mo) for constituents+delisted. |
| **FMP (Financial Modeling Prep)** | ⚠️ delisted-companies endpoint exists | ⚠️ historical S&P 500 constituents (400/600 less clear) | deep | REST/Python | ~$22–70/mo tiered (Starter/Premium/Ultimate) | **Cheapest API, quality risk** — data accuracy/PIT rigor inconsistently reported; usable but verify before trusting. |
| **Polygon.io (Massive)** | ❌ delisted "spotty at best" | ❌ | 20yr | Python | $29–329/mo | **Rule out for survivorship** (great for live/intraday, not this). |
| **Tiingo** (we already use) | ⚠️ some delisted, not rigorous | ❌ no PIT index membership | deep | Python | ~$10–50/mo | Keep for `price_arbiter`; **insufficient** for survivorship-correct universe. |
| **Intrinio** | ✅ | ✅ | deep | API | **~$3,000/yr+** | **Too expensive** for our scale. |
| **CRSP (via WRDS)** | ✅ gold standard | ✅ | 1925+ | bulk | **institutional $$$$** (university WRDS) | Gold standard but only realistic with academic affiliation. Context only. |
| **QuantConnect (Lean data)** | ✅ delisted + map files | ⚠️ via their universe data | 1998+ | platform/download | data subscription varies (~$/mo) + platform | Option if we adopt their data pipeline; off-platform licensing nuance. Not needed (we have our own engine). |
| **yfinance (free)** | ❌ **proven biased** | ❌ | 2018+ practical | Python | $0 | What we have — **insufficient** (the mirage). ~32% delisted recovery only. |

## Cost corrections to the board memo

- **Norgate Platinum ≈ $52.50/mo** (annual $630), NOT the $117/mo I wrote. Cheaper → strengthens the buy.
- **Sharadar $99/mo is DOWNLOAD-AND-CANCEL friendly** → the entire survivorship-free dataset (prices + fundamentals + tickers, active+delisted to 1998) for **~$99 one-time**, run locally.

## Recommended plan (revised, cheaper than the memo)

**For the 8-week decisive trial: use Sharadar's $99 one-time bulk download — not a recurring sub.**
1. Subscribe Sharadar core bundle ($99), **bulk-download the full ZIP** (SEP prices + SF1 fundamentals + TICKERS metadata, active+delisted to 1998), **cancel**.
2. Build the local survivorship-free markets from the dump (large/mid/small-cap, delisted included) — point-in-time via the TICKERS table.
3. Run the rail-equipped loop for 8 weeks on the clean inefficient universe.
4. **If PASS** (a strategy clears battery PROMOTE + write-once holdout + starts forward paper) → THEN subscribe **Norgate Platinum (~$52.50/mo)** for ongoing fresh survivorship-free daily updates + cleaner PIT mid/small-cap membership.
5. **If MISS** → we spent **~$99 total** (not $234) to definitively answer "does Atlas have an edge?" → declare "no edge at this scale," reallocate.

**Net:** the decisive test now costs **~$99 one-time** instead of ~$234 recurring. Norgate becomes the *ongoing* feed only if the trial proves an edge. SIP ($99/mo) stays deferred (it was for intraday/options, not this).

## Caveats / due diligence before buying
- Verify Sharadar's reused-symbol handling won't corrupt the PIT mapping for our window (2019–2026 drops matter most).
- Confirm the TICKERS table gives usable PIT S&P 400/600 membership (else Norgate is the cleaner mid/small-cap source).
- Whatever vendor: keep the Alpaca `is_tradable` live-eligibility filter; delisted names are backtest-only.
- Data-quality gate on ingest (same as today's sp500hist) regardless of vendor.
