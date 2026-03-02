# Atlas Dashboard Data Feed Audit Report

**Date:** 2026-03-03  
**Trigger:** IBKR exclusion bug + telegram bot stale-code incident  
**Scope:** All data feeds into `dashboard/generate_data.py` (1,748 lines)

---

## Summary Table

| # | Data Feed | Severity | Status | Key Issue |
|---|-----------|----------|--------|-----------|
| 1 | IBKR Broker | 🔴 Critical | Fixed (today) | Was excluded from dashboard; `reqExecutionsAsync` timeout hangs gateway |
| 2 | Moomoo Broker | 🟢 OK | Working | Garbage detection in place; cross-broker sharing works |
| 3 | yfinance Prices | 🟡 Warning | Fragile | No retry, no caching, rate-limited; failure blocks price display |
| 4 | Config Files | 🟢 OK | Working | Schema varies across markets but `safe_json` prevents crashes |
| 5 | Broker State Files | 🟡 Warning | Fragile | No staleness detection; missing file returns empty defaults |
| 6 | Plan Files | 🟢 OK | Working | `get_latest_plan()` handles per-market + legacy naming |
| 7 | Trade Ledger | 🟡 Warning | Fragile | Single shared file; no per-market separation; can grow unbounded |
| 8 | Research Data | 🟢 OK | Working | All reads via `safe_json`; missing files gracefully skipped |
| 9 | Equity Curves | 🟡 Warning | Correctness risk | Only updated when `data_source == "broker"`; timezone mixing |
| 10 | Benchmark Curves | 🟡 Warning | Fragile | yfinance fallback for recent days; forward-fill masks gaps |
| 11 | Dashboard Cron | 🔴 Critical | No error recovery | `set -e` aborts on first error; no alerting; IBKR timeout blocks all markets |
| 12 | Cross-Broker Reconciliation | 🟡 Warning | Dedup risk | Moomoo can hold .AX stocks; filtering by ticker suffix only |
| 13 | Exchange Rates | 🟡 Warning | Fragile | Single yfinance call, hardcoded fallback (0.63), no caching |

---

## 1. IBKR Broker Feed 🔴 Critical

**What it provides:** Live positions, account equity/cash, open orders for ASX and HK markets.

**How it connects:** `get_live_broker_data()` → `brokers.registry.get_live_broker()` → `IBKRBroker.connect()` → `ib_insync.IB.connect()` (socket to port 4001, clientId=10, timeout=20s).

**Error handling:**
- `broker.connect()` returns False on failure → `get_live_broker_data()` returns `(None, [], False, [])` → market falls back to offline mode.
- Outer `try/except Exception` catches all errors.
- $0 equity/$0 cash detection as garbage data.

**Failure modes found:**
1. **`reqExecutionsAsync` timeout (TODAY'S OUTAGE):** After placing 14 orders (7 entries + 7 trailing stops), the IB Gateway became overwhelmed. Subsequent `connect()` calls timed out during the executions sync phase. The gateway went `unhealthy` and required `docker restart`. No automatic recovery exists.
2. **`Australia/NSW` timezone:** `ib_insync` tries to parse execution timestamps with `ZoneInfo("Australia/NSW")` which requires the `tzdata` package. Missing package caused non-fatal `ZoneInfoNotFoundError` in the decoder (executions silently dropped). Fixed today by installing `tzdata`.
3. **ClientId=10 shared everywhere:** The same clientId (10) is used by the dashboard, the telegram bot's executor, and the approval flow. IBKR allows only one connection per clientId. If the dashboard refresh runs while the bot is executing trades, the second connection will fail.
4. **Connection refused during gateway restart:** When IB Gateway restarts (as happened today), `connect()` gets `ConnectionRefusedError`. The cron refresh at 09:45 hit this — ASX showed 0 positions and fell back to Moomoo cross-broker data (also 0 .AX positions on Moomoo).
5. **No retry on timeout:** A single 20s timeout failure kills IBKR data for the entire refresh cycle. No exponential backoff or retry.

**Current state:** Fixed the `broker_name != "ibkr"` exclusion filter. IBKR now connects for ASX/HK. Gateway restarted and healthy.

---

## 2. Moomoo Broker Feed 🟢 OK

**What it provides:** Live positions, account equity/cash, open orders for SP500. Also cross-broker data for any .AX/.HK stocks held on Moomoo.

**How it connects:** `get_live_broker_data()` → `MomooBroker.connect()` → OpenD gateway at `127.0.0.1:11111`. Connection is shared across markets via `moomoo_data` cache in `generate()`.

**Error handling:**
- Connection failure returns `(None, [], False, [])`.
- $0 equity/$0 cash garbage detection (OpenD up but Futu backend unreachable).
- Outer `try/except` catches all errors.
- Positions filtered by ticker suffix (`.AX` for ASX, `.HK` for HK, remainder for SP500).

**Issues:**
- Position filtering by suffix could misclassify tickers that don't follow the convention (edge case).
- When Moomoo is down, ALL markets lose broker data since `moomoo_data` is the shared cache.

**Status:** Stable. OpenD has been reliable; the garbage detection is a good safety net.

---

## 3. yfinance Price Feeds 🟡 Warning

**What it provides:** Live intraday prices (15-min), exchange rates (AUDUSD), and benchmark curves (SPY, IOZ.AX).

**How it connects:** `yf.download()` in `get_live_prices()`, `yf.Ticker("AUDUSD=X").history()` for FX, `yf.download()` in `_get_benchmark_curve()`.

**Error handling:**
- Each call wrapped in `try/except Exception` with fallback.
- Live prices fall back to parquet cache (`get_cache_prices()`).
- Exchange rate has hardcoded fallback `{"AUDUSD": 0.63, "USDAUD": 1.587}`.
- Benchmark falls back to forward-fill of last known value.

**Failure modes:**
1. **Rate limiting:** yfinance uses Yahoo Finance scraping. Heavy use triggers HTTP 429s. No rate limit awareness or throttling. The dashboard calls yfinance up to 3 times per refresh (prices, FX, benchmark extension).
2. **No caching between refreshes:** Exchange rates fetched fresh every 15 minutes. Could cache for 1 hour (FX doesn't move that fast).
3. **Hardcoded FX fallback is stale:** `0.63` is the fallback AUDUSD rate. Current rate is ~0.71. A 12% error in AUD normalisation when yfinance fails.
4. **`yf.download()` can hang:** No explicit timeout on the HTTP call. If Yahoo is slow, the entire dashboard refresh stalls.
5. **Multi-index column bug potential:** `yf.download()` with multiple tickers returns MultiIndex columns. The code handles this in `_get_benchmark_curve()` with `close_s.iloc[:, 0]` but `get_live_prices()` uses `data["Close"][t]` which is fragile.

---

## 4. Config Files 🟢 OK

**What it provides:** Market-specific configuration (trading mode, broker, risk params, fees, universe).

**How it loads:** `get_config(market_id)` → `safe_json(config/active/{market_id}.json)`.

**Error handling:** `safe_json` returns `{}` on any failure — prevents crashes but gives no warning.

**Issues:**
- Schema varies across markets: ASX has `ibkr` section, SP500 has `moomoo` section, HK has both. No schema validation.
- HK has `live_enabled: false` — excluded from live broker connections but still generates a dashboard section with empty data.
- No version check or migration path when config format changes.

---

## 5. Broker State Files 🟡 Warning

**What it provides:** Persistent portfolio state (positions, closed trades, equity history, halt status). Used as fallback when broker is offline.

**Location:** `brokers/state/{market_id}.json` and `brokers/state/live_{market_id}.json`.

**Error handling:** `safe_json` returns `None` on failure; `get_portfolio()` returns empty defaults.

**Issues:**
1. **No staleness indicator:** State file could be days old with no warning. Dashboard shows stale data as if current.
2. **Two state file patterns:** `{market_id}.json` (via `get_portfolio`) and `live_{market_id}.json` (for closed trades). Different code paths access each. Potential for inconsistency.
3. **`get_portfolio()` references a `legacy` variable** that's undefined (line ~48). If `per_market` doesn't exist, it falls through to `state = safe_json(legacy, None)` which would raise `NameError`. This is a latent bug — it works only because `per_market.exists()` has always been true so far.

---

## 6. Plan Files 🟢 OK

**What it provides:** Today's trade plan (proposed entries, exits, risk summary).

**How it loads:** `get_latest_plan(market_id)` scans `plans/plan_{market}_{date}.json` (per-market) then falls back to legacy `plan_{date}.json`.

**Error handling:** `safe_json` on each file; returns `None` if nothing found.

**Issues:**
- `_load_plan_metadata()` scans last 30 plan files for position enrichment. This could slow down as plan history grows, though 30 files is bounded.
- Archive directory (`plans/archive/`) is not scanned — correctly excluded.

---

## 7. Trade Ledger 🟡 Warning

**What it provides:** Historical closed trade records for P&L calculation and win rate.

**Location:** `journal/trade_ledger.json` — single shared file.

**Issues:**
1. **Not per-market:** Single ledger for all markets. No market_id filtering in the load path.
2. **Fallback chain is confusing:** Closed trades come from `live_{market_id}.json` first, then `get_portfolio()` state, then ledger. The first non-empty list wins. This could mix markets.
3. **No rotation/archival:** File grows unbounded with every closed trade.

---

## 8. Research Data 🟢 OK

**What it provides:** Research queue status, recent experiment results, strategy coverage matrix, daily insights.

**How it loads:** `generate_research_data()` reads `research/queue.json`, `research/journal.json`, and `research/experiments/*.json` via `safe_json`.

**Error handling:** All reads via `safe_json`. Missing files return empty defaults. Each insight miner in `generate_daily_insight()` is individually wrapped in `try/except`.

**Issues:**
- `generate_daily_insight()` is 424 lines with 7 complex miners. Any unhandled exception in a miner would propagate (though each is wrapped).
- No timeout on insight generation — a pathological dataset could cause slow calculations.

---

## 9. Equity Curves 🟡 Warning

**What it provides:** Per-market equity history over time, merged into a combined AUD-normalised curve.

**How it loads:** `logs/equity_curve_{market_id}.json` — append-only JSON array. Updated only when `data_source == "broker"`.

**Error handling:** `safe_json` for loading. Curve is NOT written when broker is offline (correct — prevents stale data).

**Issues:**
1. **Gaps when broker is down:** If IBKR is unreachable for a day, no equity point is recorded. The gap is invisible in the curve — it just jumps from the last known date.
2. **AUD normalisation uses current exchange rate:** Historical equity points in USD are converted using TODAY's exchange rate, not the rate on the date they were recorded. This distorts historical comparisons.
3. **`_merge_equity_curves()` uses `series["_last"]`** — mutates the input dict by adding a `_last` key. Cosmetic but could cause unexpected side effects if the dict is reused.

---

## 10. Benchmark Curves 🟡 Warning

**What it provides:** SPY (for SP500), IOZ.AX (for ASX) — scaled benchmark for performance comparison.

**How it loads:** `_get_benchmark_curve()` reads from parquet cache, extends via yfinance for recent days.

**Error handling:** yfinance failure → forward-fill last known value. Cache miss → empty curve.

**Issues:**
1. **Forward-fill masks real gaps:** If yfinance fails, the benchmark flatlines. This makes the portfolio look like it's outperforming when the benchmark is actually unknown.
2. **Parquet cache can lag 1-2 days:** The ingest pipeline updates cache. Between ingests, the benchmark curve is stale until yfinance extends it.
3. **Benchmark for HK not configured:** HK config likely uses a generic benchmark that may not exist in the cache.

---

## 11. Dashboard Cron 🔴 Critical

**Schedule:** `*/15 1-18 * * 1-6` — every 15 minutes, hours 01:00-18:00, Monday-Saturday.

**Script:** `scripts/refresh_dashboard.sh`
```bash
set -e
cd /root/atlas
python3 dashboard/generate_data.py 2>&1
cp -f dashboard/templates/index.html dashboard/data/index.html
```

**Failure modes:**
1. **`set -e` kills the entire script on first error.** If IBKR connect fails with a non-zero exit (it doesn't — Python catches it), the template copy also won't run. However, `generate_data.py` catches all broker errors internally and exits 0, so `set -e` is mostly harmless but misleading.
2. **No alerting on failure.** If `generate_data.py` crashes, the dashboard silently serves stale data. No Telegram notification, no error log beyond the cron redirect.
3. **IBKR timeout blocks all markets.** `generate()` connects brokers sequentially. An IBKR timeout (up to 20s + socket timeout) delays the entire refresh, including SP500 data. Today's outage: IBKR hung for ~30s, then SP500 connected fine, but the entire cycle was delayed.
4. **No partial output.** If the script crashes mid-generation, no `dashboard-data.json` is written. Dashboard serves the previous (possibly hours-old) data with no staleness indicator.
5. **Log file grows unbounded:** `>> /root/atlas/logs/dashboard-refresh.log` with no rotation.

---

## 12. Cross-Broker Reconciliation 🟡 Warning

**What it provides:** `sync_broker_fills()` detects broker positions not yet in live state and records them as new fills.

**How it works:** Compares broker position tickers against live state tickers. Any broker position in the approved plan but missing from live state is auto-synced.

**Issues:**
1. **Position filtering by ticker suffix only:** `.AX` → ASX, `.HK` → HK, remainder → SP500. ETFs or ADRs that don't follow this convention would be misclassified.
2. **Moomoo can hold .AX stocks:** If a user manually buys ASX stocks through Moomoo (not IBKR), they appear in the shared Moomoo data AND could appear in IBKR data. The current code handles this via `cross_broker_positions` only when the market is NOT in live mode. If both brokers report the same ticker, it could be double-counted.
3. **`sync_broker_fills()` connects to IBKR inside the dashboard refresh** — a separate connection with the same clientId=10. This is a third concurrent connection risk.
4. **sync_broker_fills() has a dead-code line:** Line 251 `live_tickers = live_tickers` — this was likely meant to be a disconnect or is a copy-paste artifact. The `portfolio.disconnect()` on line 250 runs, then this line is a no-op.

---

## 13. Exchange Rates 🟡 Warning

**What it provides:** AUDUSD rate for normalising combined portfolio to AUD.

**How it loads:** `yf.Ticker("AUDUSD=X").history(period="1d")` in `generate()`.

**Error handling:** Hardcoded fallback `{"AUDUSD": 0.63, "USDAUD": 1.587}`.

**Issues:**
1. **Stale fallback:** Fallback rate is 0.63 but actual rate is ~0.71 (12% error). This would cause A$800+ error on the combined equity display if yfinance fails.
2. **No caching:** Rate is fetched fresh every 15-minute refresh cycle. Could be cached for 1-4 hours.
3. **Single point of failure:** If yfinance is down for the entire session, all refreshes use the stale hardcoded rate.

---

## Latent Bug Found

**`get_portfolio()` — undefined `legacy` variable (line ~48):**
```python
def get_portfolio(config):
    market_id = config.get("market", "asx")
    per_market = PROJECT_ROOT / "brokers" / "state" / f"{market_id}.json"
    state = None
    if per_market.exists():
        state = safe_json(per_market, None)
    if state is None:
        state = safe_json(legacy, None)  # ← 'legacy' is never defined!
```
This will raise `NameError` if the per-market state file doesn't exist. Currently masked because `asx.json` and `sp500.json` always exist in `brokers/state/`.

---

## Conclusion

The dashboard has **2 critical issues** (IBKR integration fragility + cron resilience) and **6 warnings** (yfinance reliability, state staleness, equity curve gaps, cross-broker dedup, exchange rate fallback, trade ledger growth). The research data feed and plan file handling are solid. The biggest systemic risk is that a single broker failure or gateway restart can cascade into stale/missing data across all markets with no alerting.
