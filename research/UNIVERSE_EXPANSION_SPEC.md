# Universe Expansion — Spec (small/mid-cap, where edges actually live)

> Board memo `ceo-board/memos/2026-06-05-atlas-research-strategy-free-compute`: the 3 rigorous nulls
> (long-short, news-sentiment, csm-properly-deployed) were ALL on the most-efficient slice of the
> market — **liquid large-cap, daily**. Free unlimited compute is wasted searching where edges are
> already arbitraged. This spec points the (now rail-equipped) search at **less-efficient corners**.
>
> Crucial: **the data is FREE.** `data/ingest.py` pulls OHLCV via yfinance for arbitrary US tickers,
> ~7yr daily. So the SEARCH needs no paid feed (the deferred $99/mo SIP is a LIVE/intraday concern,
> not this). This is a data-engineering task, not a purchase.

## RESOLUTION (2026-06-05) — survivorship decision + first list

**Decision: free fixed-set + delisted-inclusion.** Assembled the first Alpaca-tradable,
survivorship-correct candidate universe: `data/universes/sp500_hist_survivorship.json`
(source: fja05680/sp500 historical components 1996-2019, free).
- **496 live-eligible** (currently `is_tradable()`) + **588 delisted/dropped** (backtest-only,
  the survivorship bias-killers) = **1,097-name survivorship-correct backtest universe**.
- vs our current **biased 203 survivors** -> adds ~588 delisted/dropped names. Live signals fire ONLY
  on the 496 tradable; delisted names exist in the backtest purely to defeat survivorship bias.

**Honest limitation — the free/midcap tradeoff.** Clean FREE survivorship-correct historical
constituents exist for the **S&P 500 (large-cap)**, NOT pure mid/small-cap (S&P 400/600, Russell). So:
- free + survivorship-correct -> **large/fallen-cap** (this list; fixes a REAL bias in our existing
  research — the 3 nulls were on the biased 203 — and is broader/includes fallen mid-caps). Ready now.
- free + mid-cap -> **survivor-biased** (current Alpaca mid-band, no delisted) — the exact landmine; avoid.
- mid-cap + survivorship-correct -> needs **paid** point-in-time data (a board-level call, distinct from
  the SIP feed; the one place paid data may be justified for the SEARCH).

**Recommendation:** ingest + research the free survivorship-correct broad universe FIRST (makes our
existing large-cap research valid; clean template), and treat pure survivorship-correct mid-cap as a
separate paid-data decision once the free universe is exhausted. Re-running the 3 nulls on the
survivorship-correct universe is itself a high-value check (did survivor bias flatter them?).

## A "market" in Atlas (what we're adding)

`vo.load_data(market=X)` reads `data/cache/X/*.parquet` (one OHLCV file per ticker). To add a market:
1. Ticker list (universe definition)  2. Ingest parquet into `data/cache/<market>/`
3. Sector map `data/processed/sector_map_<market>.json`  4. `config/active/<market>.json`
5. `data/processed/<market>/universe.json`. Then the battery/runner target `--market <market>` and
**all three rails apply unchanged** (holdout `config/holdout.json` start is global; quarantine works).

## HARD FILTER #0 — Alpaca-tradable only (we trade ONLY on Alpaca)

The founding constraint of this whole research line: **only research what Alpaca can execute.** A
yfinance ticker list will contain thousands of names Alpaca cannot trade (foreign, OTC pink-sheet,
structured products, delisted). Researching an edge on untradable names is worthless. So Alpaca
tradability is a HARD GATE at universe-definition time, not a live afterthought.

- Mechanism EXISTS: `brokers/alpaca/tradable_assets.py::is_tradable(symbol)` / `get_tradable_set()`
  (verified 2026-06-05: **13,028** active tradable US equities; AAPL/FIVE/DECK/BLDR/CROX/SMCI -> True,
  delisted PXD -> False, all 203 sp500 -> True). Alpaca trades essentially the whole liquid US equity
  market, so mid-caps are well covered. `asset_class=US_EQUITY, status=ACTIVE, a.tradable==True`.
- **Live/current universe** = `{t for t in candidates if is_tradable(t)}`. No exceptions.
- **Reconcile with survivorship (see #1 below):** `is_tradable` returns False for DELISTED names
  (PXD). For a survivorship-correct BACKTEST we still INCLUDE names that *were* tradable during the
  period (delisted-to-zero), because Alpaca traded ~all active US equities historically too. So:
  - Backtest universe = US-equity names with data + liquidity in-period, INCLUDING now-delisted.
  - Each name tagged `currently_tradable = is_tradable(t)`; **live signals fire ONLY on
    currently-tradable names**; delisted names exist in the backtest purely to kill survivorship bias.
- **Enhance the cache (small build):** `tradable_assets.py` currently stores only symbols. Fetch the
  full asset objects and cache per-asset attributes — `tradable, status, exchange, shortable,
  easy_to_borrow, fractionable` — so the universe filter can also (a) require a MAJOR exchange
  (NYSE/NASDAQ; exclude thin OTC), and (b) expose `shortable`+`easy_to_borrow` if any future strategy
  shorts (Atlas is long-only today, so `tradable` is the gate; ETB matters only for live shorts +
  >=$2k equity). Crypto/other asset classes are out of scope (equity-only expansion).
- Refresh the tradable set as part of the universe rebuild (cron already exists; cache was 2026-06-03).

## ⚠️ #1 RISK — survivorship bias (this makes or breaks small-cap research)

Small/mid-cap universes have **far more delistings, bankruptcies, and M&A** than the S&P 500. A naive
universe = "today's constituents" only shows the **survivors** → backtests systematically overstate
edge (the losers that went to zero are invisible). **Most published small-cap edges evaporate once
survivorship is corrected.** This is the dominant methodological risk and is non-negotiable:

- **Preferred:** point-in-time constituents (membership as-of each date) + **delisted tickers included**
  with their final returns (delist-to-zero or last price). Sources: a historical constituents dataset,
  or reconstruct from index add/drop history.
- **Minimum acceptable:** ingest a BROAD fixed ticker set defined years ago (e.g., the universe as of
  2018) and carry every name forward including those that later delisted (yfinance often retains
  delisted history) — so the dead names are in-sample, not filtered out.
- **Forbidden:** "current S&P 400 members, 7yr history" — pure survivorship, will produce false edges
  that the rails CANNOT catch (the bias is in the data, upstream of every gate).
- Pre-register a **survivorship audit**: count names that delisted in-sample; if ~0, the universe is
  survivor-only and any result is invalid.

## Recommended first universe: liquid mid-cap (S&P MidCap 400-class)

Not Russell-2000 micro-caps (untradable, data-poor, gappy). Start with **~400-600 liquid mid-caps**:
- Less efficient than mega-cap (real research target), but liquid enough to trade and mostly
  **Alpaca-tradable** (many also ETB-shortable — relevant only for any future live).
- Define by liquidity, not just index membership: min median daily $-volume, min price, min market cap
  (the existing `universe` config block already supports `min_median_daily_value`, `min_price`,
  `min_market_cap`, `top_n`). Tighter liquidity floor than sp500 (small-caps need it).

## Data ingestion (free, headless, parallel)

- `data/ingest.py` via yfinance for the ticker list → `data/cache/midcap/`. ~400-600 names × 7yr ≈
  minutes–hours (yfinance rate-limited). Run **headless via systemd**, parallel, checkpointed
  (mirror the Benzinga ingest pattern from 2026-06-05).
- **Data-quality gate:** small-cap yfinance data has gaps/split glitches. Validate each ticker
  (min rows, no absurd jumps, adj-close present); drop or flag bad ones. Log coverage.
- Keep `price_arbiter` discipline: yfinance/daily is fine for SEARCH; live would re-check via the
  arbiter (and small-caps may need a better live feed — a separate live concern).

## Sector map + config

- Build `sector_map_<market>.json` from yfinance `.info` sector for the new tickers (same shape as
  `sector_map_sp500.json`). REQUIRED — without it, `max_sector_concentration` collapses the book to 2
  positions (the exact csm bug from 2026-06-05; deployment-sanity would auto-FAIL it, but tag sectors
  properly so strategies actually deploy).
- `config/active/midcap.json`: `trading.mode=paper`, `live_enabled=false`; **realistic costs** —
  small/mid-cap slippage is materially higher, so set `slippage_model=volume_aware` with a higher
  impact and a conservative `min_position_value`; risk params mirror sp500. Universe is already
  Alpaca-tradable-filtered (#0); live additionally restricts to the `currently_tradable` subset (+ ETB
  + >=$2k equity for any short). No live until material AUM.

## Liquidity & cost realism (small-cap eats naive edges)

- Wider spreads + market impact: the volume-aware slippage must be conservative or backtests overstate
  edge. Pre-register the slippage assumption; stress it (run net at 1×/2× the impact estimate).
- `volume_participation_limit` (already in backtest config) matters more — cap position size vs ADV so
  the strategy can't "trade" more than a realistic fraction of a thin name's volume.
- Deployment-sanity (Rail 3) + the cost model are the guards that a "small-cap edge" isn't a liquidity
  mirage.

## Integration with the rails + loop (no new gate work)

- Battery/runner already accept `--market`; the 3 rails apply unchanged. Each new universe family is a
  Rail-2 registry family. The write-once holdout (2025-01-01) quarantines the new market too.
- Director/discovery generation can target the new market once it exists (the universe is the
  data-infra prerequisite the loop spec flagged).

## Phased plan

1. **Universe definition + survivorship sourcing (the hard part).** Decide point-in-time vs fixed-2018
   set; assemble the ticker list INCLUDING delisted names. **Intersect candidates with Alpaca-tradable
   US equities** (`is_tradable`; keep delisted-but-historically-tradable for the backtest, tag
   `currently_tradable`). Enhance `tradable_assets.py` to cache per-asset attributes (exchange/shortable/
   ETB/fractionable) and apply a MAJOR-exchange filter (NYSE/NASDAQ). Pre-register the survivorship audit.
2. **Ingest** (yfinance, headless) → `data/cache/midcap/`; run the data-quality gate; log coverage +
   delisted count.
3. **Sector map + `config/active/midcap.json`** (paper, conservative costs).
4. **Baseline + survivorship audit:** run the battery on `cross_sectional_momentum` (our reference)
   on `midcap` under full rails; confirm (a) delisted names are in-sample, (b) deployment-sanity passes
   (sectors tagged), (c) the result is plausible (not a survivorship moonshot).
5. **Open the search:** point generation at `midcap`; let the rail-equipped loop screen families there.

## Acceptance criteria

- Survivorship audit shows a realistic delisting count in-sample (NOT ~0).
- Ingest coverage logged; bad tickers dropped; sector map covers ≥95% of names.
- The battery runs end-to-end on `midcap` with all rails (quarantine, deployment-sanity, FDR bar).
- Costs are conservative; a candidate edge survives a 2× slippage stress.
- **Every backtested name was an Alpaca-tradable US equity in-period** (major exchange); **every
  live-eligible name passes `is_tradable()` now**. Delisted names appear in the backtest only (tagged
  `currently_tradable=False`) to defeat survivorship bias — they never generate live signals.
- No live exposure (paper only) until material AUM + the `currently_tradable`/ETB subset is locked.

## Open decisions for the human

1. **Survivorship approach:** pay/find a point-in-time constituents dataset (best, may cost), OR the
   free "fixed 2018 broad set incl. delisted" approximation (good enough to start, zero cost)?
2. **Universe size/floor:** ~400 liquid mid-caps vs a broader ~1000 small+mid (more breadth, more
   data, more survivorship work)?
3. **Scope now:** ingest + baseline-audit first (prove the data is clean + unbiased) before opening the
   full search, or go straight to opening the loop on it?
