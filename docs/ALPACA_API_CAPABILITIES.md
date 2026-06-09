# Alpaca API — Asset & Data Capabilities (research for strategy design)

> Compiled 2026-06-05 from docs.alpaca.markets + alpaca.markets. Purpose: map what
> Alpaca can actually *trade* and what *data* it gives us, so we only research
> strategies the broker can execute. Atlas already runs Alpaca live (SP500 + ETFs,
> IEX feed, paper→live). This doc covers the full surface area, including what we
> are **not** yet using.

---

## 1. Tradable asset classes

| Asset class | Tradable? | Notes / constraints |
|---|---|---|
| **US equities & ETFs** | ✅ (live now) | 11k+ US-listed names. Long + short. Fractional on 2,000+ names. |
| **US-listed equity options** | ✅ | American-style, **Level 1–3** (covered calls/CSPs → long calls/puts → spreads & multi-leg). Single + **multi-leg (`mleg`)** orders. Exercise / DNE via API. |
| **Crypto (spot)** | ✅ (we have routing) | 20+ coins, **spot only — no leverage, no perps, no margin, no shorting**. 24/7. Executed on Alpaca's own crypto venue. |
| **Fixed income / bonds** | ⚠️ Broker-API only | Mentioned for Broker-API partners, not the individual Trading API. Ignore for Atlas. |
| **Tokenized equities** | 🔬 Emerging | 24/7 tokenized-equity product announced 2025/26. Not relevant yet. |
| **Futures** | ❌ | Not offered. (If we ever want futures-style exposure → different broker.) |

### Equity specifics
- **Short selling:** only **ETB (easy-to-borrow)** names; HTB not yet shortable. **$0 borrow fee on ETB**. Requires **≥ $2,000 account equity** + margin enabled. `easy_to_borrow` & `marginable` flags live on the Assets endpoint (refreshed each morning — must re-check daily; names flip ETB↔HTB).
- **Buying power:** up to **4× intraday**, **2× overnight** (margin account ≥ $2k). PDT rules apply < $25k.
- **Fractional:** market orders only; no notional shorts; 2,000+ names.
- **IPOs:** limit-only until first print, then market allowed (`ipo` flag on Assets).

### Options specifics
- Universe: US-listed **equity** options (incl. ETFs). No index/futures options.
- Levels: **L1** covered call / cash-secured put; **L2** long calls/puts, long straddles; **L3** spreads, iron condors/butterflies, all multi-leg via `order_class:"mleg"` + `legs[]` with `position_intent`.
- Order types for options: market, limit, stop, stop_limit (**stop/stop_limit single-leg only**).
- **No extended-hours, no fractional options, no LCT.**
- **Commission-free.**

### Crypto specifics
- Coins (spot): AAVE, AVAX, BAT, BCH, BTC, CRV, DOGE, DOT, ETH, GRT, LINK, LTC, MKR, SHIB, SUSHI, UNI, USDC, USDT, XTZ, YFI (20+, list grows).
- Quote currencies: **USD, USDT, USDC, BTC** (e.g. BTC/USD, ETH/BTC, ETH/USD).
- 24/7. Spot only. No margin/short → **long-only or pair rotation** strategies.

---

## 2. Market data

### Equities data feeds
| | Basic (free) | Algo Trader Plus ($99/mo) |
|---|---|---|
| Real-time coverage | **IEX only** (~2–3% of volume) | **Full SIP** (all US exchanges, 100% volume) |
| Websocket symbols | 30 | Unlimited |
| Historical depth | since **2016** | since **2016** |
| Recent-data limit | last 15 min **withheld** | none |
| REST rate | 200 calls/min | 10,000 calls/min |

Data types: **bars** (min/hour/day), **quotes (NBBO)**, **trades**, **snapshots**, latest quote/trade. Sources: CTA (NYSE) + UTP (Nasdaq).

> ⚠️ **Atlas lesson already on file:** the free **IEX feed is structurally stale** for NYSE/Nasdaq mega-caps (NFLX/AAPL price gaps of 7–11% observed). `config/price_arbiter.json` already routes authority IEX→Tiingo on mismatch. **Any serious intraday or options strategy needs SIP (Algo Trader Plus), or a third-party feed.** This is the #1 data constraint for new-strategy research.

### Options data
- Feeds: **Indicative** (free) vs **OPRA** (full, included in Algo Trader Plus).
- Types: bars, trades, quotes; option chain / contracts endpoint; greeks/IV where available.
- **Historical option data only since Feb 2024** → **~2 years of backtest depth max.** This is the binding constraint for options-strategy validation (vs our 7-yr equity history).

### Crypto data
- Bars, trades, quotes, **latest orderbook** (L2). Single consolidated feed (Alpaca's own venue) since they internalised crypto. No 15-min withholding, no per-symbol cap.

### Overnight / 24-5 data
- **24/5 equity trading live since Feb 2026** (Sun 8pm ET → Fri 8pm ET).
- **BOATS feed** (`feed=boats`, Blue Ocean ATS) for historical **overnight** bars/quotes/trades — requires Algo Trader Plus.
- Full extended hours: overnight 8pm–4am, pre-market 4am–9:30am, after-hours 4pm–8pm ET.

### News & fundamentals-ish data
- **News API** (Benzinga): real-time websocket + **historical since 2015**, ~130 articles/day, stock + crypto, symbol-tagged. Free tier available.
- **Corporate actions API**: splits, dividends, etc. by symbol/date (no SLA on creation time).
- **Screener API**: `most_actives` (by volume/trade count), **top movers** (gainers/losers) — **SIP-based, so needs ATP for real value**.
- No deep fundamentals (earnings/financials) — would need an external source (we already use Tiingo; FMP/others possible).

### Order types (for strategy mechanics)
- `market`, `limit`, `stop`, `stop_limit`, `trailing_stop`.
- Order classes: **bracket** (entry + TP + SL), **OCO**, **OTO** — Atlas already uses broker-side OCO/trailing stops.
- TIF: day, gtc, ioc, fok, opg (market-on-open), cls.
- Extended-hours orders: **limit + day/gtc only**.

### Rate limits (trading)
- Trading API: ~200 req/min default. Data REST per table above. Streaming for order updates (recommended for state).

---

## 3. Strategy space Alpaca can actually execute

Ranked by fit with Atlas (data depth, execution support, our existing infra):

### A. Equity strategies — ✅ best supported (what we already do)
- Daily cross-sectional momentum / mean-reversion / sector rotation (current Atlas book).
- **NEW levers we're not using:**
  - **Short / long-short** on ETB names ($0 borrow, 2× overnight BP) → market-neutral, pairs, short-side mean reversion. Biggest untapped equity lever.
  - **Overnight / 24-5 gap strategies** (earnings drift, overnight news reaction) now executable since Feb 2026. Needs BOATS data (ATP) to backtest honestly.
  - **Extended-hours fills** for opening-gap entries (already have an `opening_gap` strategy — could route limit fills pre-market).
- Data: **7 yr history, full SIP at $99/mo.** Strong backtest depth. **Best risk-adjusted bet for new research.**

### B. Crypto (spot) — ✅ supported, modest
- Long-only / rotation across 20+ coins, 24/7. Momentum & vol-targeting, BTC/ETH trend, alt rotation, calendar/weekend effects.
- **Constraints:** no shorting, no leverage → can't do market-neutral or short-vol; capacity & spread/fees matter on alts. We already have `CryptoHistoricalDataClient` wired.
- Good as an *uncorrelated sleeve*, not a high-Sharpe alpha source given long-only spot.

### C. Options — ✅ supported, ⚠️ data-limited
- Executable: covered calls / CSPs (income), verticals, straddles/strangles, **iron condors/butterflies** (multi-leg `mleg`).
- Natural fits: **systematic premium selling** (CSP/covered-call overlay on the equity book), defined-risk vol strategies, earnings straddles (pair with News API).
- **Hard constraint:** historical option data **only since Feb 2024** → can't walk-forward validate across regimes (no 2018/2020/2022 stress). Treat as **paper-first, forward-validated**, exactly like Hermes — *do not* trust a 2-yr in-sample options backtest.
- OPRA + greeks need Algo Trader Plus.

### D. News / event-driven — ✅ data is unusually good
- Benzinga news **back to 2015** + corporate-actions API → event studies (earnings, M&A, guidance), news-sentiment overlays on the equity book, halt/gap reactions in the 24-5 session.
- Cheapest high-value research: a **news-sentiment overlay** feeding the existing Atlas signal stack (we already have an `overlay/` module + `news` data source stub).

### E. Multi-asset portfolio — ✅
- One account holds equities + options + crypto → unified Kelly/risk sizing across sleeves (mirrors Hermes portfolio-construction philosophy).

### What Alpaca **cannot** do (rule these out)
- ❌ Futures / index options / commodities futures (our `commodity_etfs` market already uses **ETF proxies** — correct call).
- ❌ Crypto leverage / perps / shorting (Bybit-crypto board idea would *not* run on Alpaca).
- ❌ FX spot, fixed income (Trading API).

---

## 4. Recommendations for the research queue

1. **Highest ROI, lowest new cost — equity short/long-short.** We already have 7-yr SIP-quality history and live execution; adding ETB short-side unlocks market-neutral + short mean-reversion with $0 borrow. No new data purchase to start (daily). → candidate experiments for the Atlas queue.
2. **News-sentiment overlay** on the existing book — Benzinga history to 2015 is free-ish and deep; plugs into `overlay/`. Cheap event-study research.
3. **Options premium-selling overlay** (covered calls / CSPs on existing equity holdings) — income on inventory, defined risk. **But gate it like Hermes:** paper-first, forward CLV/PnL, because option history is only ~2 yrs.
4. **Subscribe to Algo Trader Plus ($99/mo)** *before* any intraday, options, overnight, or screener research — the free IEX feed is too stale (already a documented Atlas failure) and OPRA/SIP/BOATS all gate behind it. This is a board-level call (recurring cost) but small.
5. **Crypto spot rotation** as an uncorrelated sleeve — nice-to-have, lower priority (long-only spot caps the edge).

### Key gating constraints to remember
- **Data quality, not execution, is the binding constraint.** Free IEX is stale (proven); budget $99/mo SIP for anything beyond daily SP500.
- **Options backtest depth = ~2 yrs** → forward-validate, don't trust in-sample.
- **Crypto = long-only spot** → no neutral/short structures.
- Short selling needs ≥ $2k equity + ETB + daily borrow-status re-check.
