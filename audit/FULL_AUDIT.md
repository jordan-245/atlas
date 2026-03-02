# Atlas Codebase Audit — Full Report

**Date:** 2026-03-02  
**Scope:** All 90 Python files across 15 modules  
**Auditor:** Claude (comprehensive read of every module)

---

## Executive Summary

The Atlas codebase is well-architected for a trading system: clean broker abstractions, proper walk-forward backtesting, sensible risk gates, and good operational tooling. However, there are **several critical and high-severity issues** that could cause money loss, data corruption, or silent failures in production.

| Severity | Count |
|----------|-------|
| 🔴 CRITICAL | 5 |
| 🟠 HIGH | 12 |
| 🟡 MEDIUM | 15 |
| 🔵 LOW | 10 |

---

## 🔴 CRITICAL Issues (Money-at-Risk / Data Corruption)

### C1. Backtest Engine: Look-Ahead Bias in `_simulate_day` Exit Logic
**File:** `backtest/engine.py`, lines ~200-260  
**Description:** When the exit check is performed using `exit_data` (data up to yesterday), the fill price used is `today_df.loc[today, "open"]`. However, the MAE/MFE update block runs *after* exits but *before* entries, using today's high/low data. For positions that were NOT exited, this means MAE/MFE reflects today's full range. This is correct. But the **trailing stop** and **max_loss_cap** checks (lines ~330-430) use `today_df.loc[today, "close"]` — meaning they use the *closing* price to decide exits that should be acted on at *open* of the next day. This gives the strategy knowledge of the entire day's price action before deciding to exit, which is **look-ahead bias** for those exit types.

**Impact:** Backtest results for trailing stops and max-loss caps are overly optimistic — the system "sees" intraday close before deciding to exit.

**Fix:** Trailing stop and max_loss_cap exits detected on day T should execute at day T+1 open (like regular signal exits), OR use the prior day's close for the trigger decision.

---

### C2. EOD Settlement: Stops Checked Against Wrong Price Source
**File:** `scripts/eod_settlement.py`, lines ~91-120  
**Description:** `check_stop_losses()` uses `intraday_low` from yfinance data to check stops, but exits at `pos.stop_price` (simulating a stop-market fill at the exact stop level). However, for **live broker positions** (LivePortfolio), this creates a dual-execution risk:
1. The broker may have an exchange stop order placed (via `stop_order_id`)
2. EOD settlement checks `stop_order_id` and skips those — ✅ correct
3. But for positions WITHOUT exchange stops, the paper stop check uses **yesterday's** yfinance low (data may be delayed or incomplete), not real-time data.

**Impact:** On days where yfinance data is stale/delayed, a stop that was actually hit intraday may not fire in EOD settlement, leaving a losing position open overnight.

**Fix:** For live mode, ensure stops are ALWAYS placed as exchange orders (not paper-checked). Alternatively, add a freshness check on yfinance data timestamps.

---

### C3. LivePortfolio: `update_positions()` Method Missing
**File:** `brokers/live_portfolio.py`  
**Description:** `LivePortfolio` is used as a drop-in for `PaperPortfolio`, and `eod_settlement.py` calls `portfolio.update_positions(prices)` (line ~380). However, `LivePortfolio` does NOT implement `update_positions()` — it inherits nothing from `PaperPortfolio`. The `Position` objects in `LivePortfolio.positions` DO have `update_excursions()`, but there's no `update_positions()` wrapper method.

**Impact:** `AttributeError` crash during EOD settlement when using LivePortfolio. Since this is the live production path, it will crash every day.

**Fix:** Add `update_positions()` method to `LivePortfolio`:
```python
def update_positions(self, prices: dict[str, float]):
    for pos in self.positions:
        if pos.ticker in prices:
            pos.update_excursions(prices[pos.ticker])
```

---

### C4. Reconcile Stops: `get_today_deals()` Not on Base Broker
**File:** `brokers/live_executor.py`, line ~520  
**Description:** `reconcile_stops()` calls `self._broker.get_today_deals()`, but `get_today_deals()` is only defined on `MomooBroker` — it's NOT in the `BrokerAdapter` ABC or in `IBKRBroker`. When running with IBKR, this will raise `AttributeError`.

**Impact:** Stop reconciliation crashes for IBKR broker, meaning positions closed by exchange stops will never be synced to paper state.

**Fix:** Add `get_today_deals()` to `BrokerAdapter` base class (with empty default), and implement it in `IBKRBroker`.

---

### C5. Config: IBKR Account ID Hardcoded in Config File
**File:** `config/active/asx.json`, `ibkr.account_id` field  
**Description:** The IBKR account ID `U24658976` is hardcoded in the JSON config file, which is committed to git. While account IDs aren't passwords, they're sensitive identifiers that can be used for social engineering or targeted attacks.

**Impact:** Security exposure — account identifier in version control.

**Fix:** Move `account_id` to `~/.atlas-secrets.json` (which already has `IBKR_ACCOUNT_ID` in `SECRET_KEYS`), and read it from secrets in the broker init, not from config.

---

## 🟠 HIGH Issues (Reliability / Correctness)

### H1. Backtest Engine: Sector Concentration Not Enforced
**File:** `backtest/engine.py`, `_simulate_day()`, entry signal processing  
**Description:** The engine checks `max_positions` but never checks `max_sector_concentration` during signal processing. The `self.max_sector` attribute is loaded from config but never used in `_simulate_day()`. Sector concentration is only checked in `PaperPortfolio.check_risk_limits()` (paper engine), not in the backtest.

**Impact:** Backtest results don't reflect the sector concentration limit that is enforced in live trading, making backtests overly optimistic.

**Fix:** Add sector concentration check in the entry signal loop in `_simulate_day()`.

---

### H2. Backtest Engine: `new_test_dates` Indexing Bug
**File:** `backtest/engine.py`, lines ~1160-1170  
**Description:** When simulating days in a walk-forward window, `_simulate_day` is called with `day_idx=i` and `trading_dates=new_test_dates`. But `_simulate_day` uses `day_idx > 0` to check "is this not the first day" and accesses `trading_dates[day_idx - 1]` as "yesterday". The problem: `new_test_dates` is a *filtered* subset of `test_dates` (excluding already-simulated dates). So `new_test_dates[0]` might not be the first day of the test window — it could be any date. When `i=0`, the engine skips entry signal generation entirely. When `i=1`, it uses `new_test_dates[0]` as "yesterday", which is correct only if those dates are consecutive.

**Impact:** In overlapping walk-forward windows (step < test), the first new date in each window will never generate entry signals. This slightly understates backtest performance.

**Fix:** Use the full `test_dates` array for indexing and only skip equity recording for already-simulated dates, OR pass a proper "yesterday" date explicitly.

---

### H3. MTF Momentum Strategy: Trailing Stop Never Triggers
**File:** `strategies/mtf_momentum.py`, `check_exits()`, lines ~240-255  
**Description:** The trailing stop check computes `trail_stop = today_close - self.trailing_stop_atr_mult * current_atr` and then checks `if today_close <= trail_stop`. Since `trail_stop = today_close - X` (where X > 0), this condition `today_close <= today_close - X` is ALWAYS FALSE.

**Impact:** The trailing stop exit in MTF Momentum will never fire. Only stop_loss, take_profit, and time_exit work.

**Fix:** The trailing stop should track the highest price since entry and trail from that peak, not from today's close. Use `pos.get("highest_price", entry_price)` and trail from that.

---

### H4. PaperPortfolio: Commission Model Inconsistency
**File:** `paper_engine/engine.py`, `_calc_commission()`  
**Description:** `PaperPortfolio._calc_commission()` uses `max(flat, pct)` always, while `BacktestEngine._calc_commission()` uses a smart model: pct-only below `flat_fee_threshold`, `max(flat, pct)` above. This means the paper engine charges higher commissions on small positions than the backtest, causing P&L tracking divergence.

**Impact:** Paper portfolio P&L will be worse than backtest P&L for the same trades on small accounts, making live results look worse than expected.

**Fix:** Apply the same `flat_fee_threshold` logic in `PaperPortfolio._calc_commission()`.

---

### H5. IBKRBroker: Missing `get_history_deals()` Implementation
**File:** `brokers/ibkr/broker.py`, line ~470  
**Description:** `get_history_deals()` returns an empty list `[]`. This means:
- `LiveExecutor.get_fee_analysis()` won't work for IBKR
- `LiveExecutor.get_slippage_analysis()` won't work for IBKR  
- Stop reconciliation (C4 above) can't find deal fills

**Impact:** No post-trade analytics or stop reconciliation for IBKR broker.

**Fix:** Implement using `self._ib.fills()` or `self._ib.executions()`.

---

### H6. IBKRBroker: `_get_last_price()` Blocks for 2 Seconds Per Ticker
**File:** `brokers/ibkr/broker.py`, `_get_last_price()`, line ~260  
**Description:** Each call does `self._ib.sleep(2)` waiting for market data. In `get_positions()`, this is called for EVERY position (line ~195). With 7 positions, that's 14 seconds of blocking. In `get_prices()`, it's called per ticker.

**Impact:** Portfolio refresh takes 14+ seconds, causing UI freezes and potential timeout in Telegram bot callbacks.

**Fix:** Use `reqMktData` in streaming mode (not snapshot), or batch requests, or use a shorter timeout with fallback to `avgCost`.

---

### H7. Dynamic Sizer: Drawdown Scale Can Return 0
**File:** `utils/dynamic_sizing.py`, `_get_drawdown_scale()`  
**Description:** If graduated tiers contain a tier with `"scale": 0.0`, the position size becomes 0 and NO trades will be taken. The config doesn't validate that scale values are > 0.

**Impact:** A misconfigured tier silently stops all trading.

**Fix:** Clamp scale to `max(scale, 0.1)` or validate config on load.

---

### H8. Data Ingestion: No Lock on Cache Writes
**File:** `data/ingest.py`, `_save_cache()`  
**Description:** Multiple processes (EOD settlement, research runner, CLI) can write to the same parquet cache file simultaneously. Parquet writes are NOT atomic — a concurrent read during write will get a corrupted file.

**Impact:** Occasional `ArrowInvalid` errors when reading cache during concurrent operations (e.g., EOD running while research backtest also fetches data).

**Fix:** Use a write-to-temp-then-rename pattern (atomic on most filesystems):
```python
tmp = path.with_suffix('.tmp')
df.to_parquet(tmp)
tmp.rename(path)
```

---

### H9. LiveExecutor: Exit Uses Current Price as Limit
**File:** `brokers/live_executor.py`, `_execute_exit()`, line ~310  
**Description:** The exit limit price is set to `pos.current_price` from the broker. If the price moves down between querying positions and placing the order, the LIMIT SELL won't fill (price below limit). This is especially problematic for stop-loss exits where you WANT to exit immediately.

**Impact:** Stop-loss exits may fail to fill, leaving losing positions open.

**Fix:** Use MARKET orders for stop-loss exits, or set limit price with a buffer (e.g., `price * 0.99`).

---

### H10. Moomoo Broker: Trade Unlock Failure Not Fatal
**File:** `brokers/moomoo/broker.py`, `connect()`, line ~165  
**Description:** If `unlock_trade()` fails, it logs a warning but returns `True` (connected). Subsequent `place_order()` calls will fail with "trade not unlocked" errors.

**Impact:** Silent connection "success" followed by every order failing.

**Fix:** Make unlock failure return `False` from `connect()`, or at minimum set a flag that prevents order placement.

---

### H11. Market Weekend Detection: SP500 Uses Brisbane Timezone
**File:** `scripts/eod_settlement.py`, line ~340  
**Description:** When the market profile can't be loaded (`except` block), the fallback timezone is `BRISBANE` (AEST). For SP500 market, this means weekend detection uses Australian time, which can be off by a day relative to US Eastern time.

**Impact:** SP500 EOD settlement might run on Sunday (thinking it's Monday in Brisbane) or skip Friday (thinking it's Saturday).

**Fix:** Use `America/New_York` as fallback for non-ASX markets.

---

### H12. Config: `market` Field Mismatch in ASX Config
**File:** `config/active/asx.json`  
**Description:** The ASX config has `"market": "asx"` at the bottom of the file, but the config is clearly for ASX via IBKR. The description says "IBKR live, ASX" and broker is set to "ibkr". However, the `moomoo` config section is still present with full credentials config. This is confusing and could lead to wrong broker initialization if code reads `moomoo` section.

**Impact:** Low operational risk, but confusing for maintenance. The unused `moomoo` section suggests incomplete config migration.

---

## 🟡 MEDIUM Issues

### M1. Strategy Import Duplication
**File:** `scripts/cli.py`, `scripts/anneal.py`, `scripts/reoptimize_parallel.py`, etc.  
**Description:** Every script that needs strategies manually imports each one and has its own `get_strategies()` function. There's no central strategy registry.

**Fix:** Create `strategies/registry.py` with `get_enabled_strategies(config)`.

---

### M2. `format_plan_text` Uses f-strings with Dict Keys Inside Quotes
**File:** `paper_engine/engine.py`, `format_plan_text()`, line ~495+  
**Description:** Uses `f"...{plan["trade_date"]}..."` with double quotes inside double-quoted f-strings. This works in Python 3.12+ (PEP 701) but fails on Python 3.11 and earlier.

**Impact:** Crashes on Python < 3.12.

**Fix:** Use single quotes for dict keys inside f-strings: `plan['trade_date']`.

---

### M3. No Input Validation on Config JSON
**File:** `utils/config.py`  
**Description:** `load_config()` loads JSON and returns it with no schema validation. Missing required fields (like `risk.starting_equity`) will cause `KeyError` crashes deep in the codebase.

**Fix:** Add a `validate_config()` function that checks required fields exist.

---

### M4. Equity Curve Double-Counts Unrealized P&L
**File:** `backtest/engine.py`, lines ~1175-1185  
**Description:** `mtm_value = equity + unrealized`. But `equity` already includes realized P&L from closed trades. The mark-to-market correctly adds unrealized on top. However, when a position is closed, `equity` jumps by `net_pnl` — but the equity record for that same day might have already been computed with the unrealized P&L of that now-closed position. The ordering is: simulate_day (which may close positions and update equity) → then compute mtm_value. This is actually correct but fragile — the comment should clarify.

---

### M5. Missing `__init__.py` Files
**File:** `scripts/` directory  
**Description:** The `scripts/` directory has no `__init__.py`, which means it can't be imported as a package. This is fine for standalone scripts, but `eod_settlement.py` does `from monitor.evaluator import evaluate_all` which works because `monitor/` has `__init__.py`.

---

### M6. Backtest Benchmark Calculation Uses `all_dates[self.train_window]`
**File:** `backtest/engine.py`, line ~1240  
**Description:** Benchmark start date is `all_dates[self.train_window]`, but `all_dates` is the union of ALL ticker dates. If some tickers have more history than others, this may not correspond to the actual first test date.

---

### M7. `PaperPortfolio.execute_exit()` Double Commission
**File:** `paper_engine/engine.py`, line ~367  
**Description:** `pnl = net_proceeds - pos.entry_value - self._calc_commission(pos.entry_value)`. This calculates entry commission again at exit time, rather than storing it from the entry. If commission rates change between entry and exit (unlikely but possible), this would be wrong.

---

### M8. No Timeout on yfinance Downloads
**File:** `data/ingest.py`, `download_ticker()`  
**Description:** `yf.download()` has no explicit timeout. A hung download blocks the entire process indefinitely.

**Fix:** Add `timeout=30` parameter or wrap in a threading timeout.

---

### M9. Dashboard Server: No CSRF Protection
**File:** `services/dashboard_server.py`  
**Description:** The dashboard has Basic Auth but no CSRF tokens. Any authenticated request can be replayed.

---

### M10. VIX Filter Uses `^VIX` Ticker Without Market ID Awareness
**File:** `backtest/engine.py`, line ~1100  
**Description:** `download_ticker('^VIX', use_cache=True, market_id='sp500')` — this hardcodes `sp500` market_id for VIX data. If the backtest is running for ASX, VIX data still downloads to the sp500 cache. Not incorrect, but confusing.

---

### M11. `calc_strategy_correlation`: P&L Spread Evenly is Inaccurate
**File:** `backtest/metrics.py`, `calc_strategy_correlation()`  
**Description:** Trade P&L is spread evenly across holding days (`daily_pnl = pnl / hold`). This doesn't reflect actual daily return attribution. A trade that loses everything on day 1 of 10 would show only 1/10th loss per day.

---

### M12. IBKR Contract Resolution: No Caching
**File:** `brokers/ibkr/broker.py`, `_qualify_contract()`  
**Description:** Every order placement calls `qualifyContracts()` which is a network round-trip. For repeated orders on the same ticker, this wastes time and API rate limits.

**Fix:** Cache qualified contracts in a dict.

---

### M13. `LivePortfolio.equity()` Ignores `prices` Param Sometimes
**File:** `brokers/live_portfolio.py`, `equity()`  
**Description:** If `self._broker_equity > 0`, the `prices` argument is completely ignored, and broker-reported equity is used. This means `check_daily_drawdown(prices)` may use stale broker equity rather than freshly-fetched prices.

---

### M14. Backtest: Available Equity Check Uses Entry `position_value` Not Current
**File:** `backtest/engine.py`, entry signal loop  
**Description:** `invested = sum(p["position_value"] for p in open_positions)` uses the entry-time position value, not the current mark-to-market value. In a rising market, actual invested capital is higher than `position_value`, so the engine might over-allocate.

---

### M15. Race Condition in PaperPortfolio State Save  
**File:** `paper_engine/engine.py`, `save_state()`  
**Description:** `save_state()` writes to a JSON file without any file locking. If two processes (e.g., Telegram bot and EOD settlement) both call `save_state()` concurrently, one write will be lost.

**Fix:** Use file locking (`fcntl.flock`) or write-to-temp-then-rename.

---

## 🔵 LOW Issues

### L1. Unused `_fred_data` Attribute
**File:** `backtest/engine.py`, line ~155  
`self._fred_data = {}` is assigned but never used.

### L2. `.gitignore` Missing Common Entries
Should include: `*.pyc`, `__pycache__/`, `.env`, `*.parquet` (data/cache already covered?), `logs/`, `*.log`.

### L3. Dead Code: `ACTIVE_CONFIG_PATH` in `utils/config.py`
Line ~248: `ACTIVE_CONFIG_PATH = ACTIVE_DIR / f"{DEFAULT_MARKET}.json"` — module-level constant that isn't used anywhere.

### L4. Inconsistent Default Market
`scripts/cli.py` defaults to `sp500`, but most other code defaults to `asx`. This can cause confusion.

### L5. `asx.json.bak.20260228_222754` in config/active
A backup file is committed to git in the active config directory. Should be in `.gitignore` or removed.

### L6. Paper Engine State Path Confusion
`PaperPortfolio` has both per-market (`state/asx.json`) and legacy (`portfolio_state.json`) paths. The legacy fallback is only for ASX, which is correct but the `STATE_FILE` class attribute is misleading.

### L7. `brokers/moomoo/mapper.py` — Small File, No Error Handling
Only 81 lines. No validation that ticker format is correct before conversion.

### L8. `monitor/seed.py` — No Docstring
100 lines with no module-level documentation.

### L9. `research/models.py` — Large Experiment Model
387 lines of data models that could benefit from Pydantic for validation.

### L10. Strategy `check_exits()` Returns Dict Instead of Structured Type
All strategies return `List[Dict]` from `check_exits()`. Should use a dataclass like `ExitRecommendation` for type safety.

---

## Architectural Observations

1. **Excellent broker abstraction** — `BrokerAdapter` ABC is clean and well-designed. Adding new brokers is straightforward.

2. **Good safety layering** — Pre-flight checks, halt files, daily order limits, dry-run mode, journal logging. Defense in depth.

3. **Walk-forward backtest is solid** — Positions carry across windows, proper T+1 fill model, commission/slippage modeling. The sector concentration gap (H1) is the main deficiency.

4. **Missing: Automated test suite** — No `tests/` directory. For a system handling real money, unit tests for position sizing, commission calculation, and risk checks are essential.

5. **Missing: Position reconciliation on startup** — When the system restarts, paper state and broker state can diverge. There's `reconcile_stops()` but no general "reconcile everything on startup" flow.

6. **Good secret management** — `brokers/secrets.py` with permission checks and multi-source loading is well done.
