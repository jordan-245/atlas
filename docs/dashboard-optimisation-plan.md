# Atlas Dashboard Data Feed Optimisation Plan

**Date:** 2026-03-03  
**Goal:** Eliminate dashboard downtime and data feed breakages  
**Companion:** [Audit Report](dashboard-audit-report.md)

---

## Priority Matrix

| # | Recommendation | Impact | Effort | Priority |
|---|---------------|--------|--------|----------|
| Q1 | Fix `get_portfolio()` undefined `legacy` var | 🔴 High | 5 min | **Immediate** |
| Q2 | Separate IBKR clientIds per consumer | 🔴 High | 15 min | **Immediate** |
| Q3 | Update hardcoded FX fallback | 🟡 Medium | 5 min | **Immediate** |
| Q4 | Add dashboard freshness timestamp to output | 🟡 Medium | 15 min | **Immediate** |
| Q5 | Add per-broker timeout to `generate()` | 🔴 High | 30 min | **Quick win** |
| M1 | Last-known-good cache layer | 🔴 High | 2-3 hrs | **Medium-term** |
| M2 | Cron hardening + alerting | 🔴 High | 1-2 hrs | **Medium-term** |
| M3 | Per-source freshness indicators | 🟡 Medium | 2 hrs | **Medium-term** |
| M4 | Exchange rate caching | 🟡 Medium | 30 min | **Medium-term** |
| M5 | IBKR gateway health check before connect | 🟡 Medium | 1 hr | **Medium-term** |
| A1 | Modular `generate_data.py` decomposition | 🟡 Medium | 1 day | **Architecture** |
| A2 | Cross-broker position reconciliation engine | 🟡 Medium | 4 hrs | **Architecture** |
| A3 | Historical FX rate storage for equity curves | 🟢 Low | 2 hrs | **Architecture** |

---

## Quick Wins (< 1 hour each)

### Q1. Fix `get_portfolio()` Undefined Variable ⏱️ 5 min

**File:** `dashboard/generate_data.py` lines 43-57  
**Bug:** `legacy` variable is never defined. Will crash with `NameError` when `per_market` file doesn't exist.

```python
# Current (broken):
def get_portfolio(config):
    market_id = config.get("market", "asx")
    per_market = PROJECT_ROOT / "brokers" / "state" / f"{market_id}.json"
    state = None
    if per_market.exists():
        state = safe_json(per_market, None)
    if state is None:
        state = safe_json(legacy, None)  # ← NameError!

# Fix:
def get_portfolio(config):
    market_id = config.get("market", "asx")
    per_market = PROJECT_ROOT / "brokers" / "state" / f"{market_id}.json"
    # Legacy path for backward compat (pre-market-routing state files)
    legacy = PROJECT_ROOT / "brokers" / "state" / "live_state.json"
    state = None
    if per_market.exists():
        state = safe_json(per_market, None)
    if state is None and legacy.exists():
        state = safe_json(legacy, None)
```

### Q2. Separate IBKR ClientIds ⏱️ 15 min

**Problem:** ClientId=10 is shared by the dashboard, telegram bot executor, and approval flow. IBKR allows only one socket per clientId. Concurrent connections cause failures.

**Files to change:**
- `config/active/asx.json` → keep `client_id: 10` for trade execution
- `dashboard/generate_data.py` → use clientId=20 for dashboard reads
- `services/telegram_bot.py` → keep clientId=10 for order execution

**Implementation:** Add a `dashboard_client_id` to the IBKR config or override in `get_live_broker_data()`:

```python
# In get_live_broker_data(), before broker.connect():
if broker_name == "ibkr" and hasattr(broker, '_client_id'):
    broker._client_id = config.get("ibkr", {}).get("dashboard_client_id", 20)
```

Or cleaner — add to each market's config:
```json
"ibkr": {
    "client_id": 10,
    "dashboard_client_id": 20
}
```

### Q3. Update Hardcoded FX Fallback ⏱️ 5 min

**File:** `dashboard/generate_data.py` in `generate()` (~line 1603)

```python
# Current:
exchange_rates = {"AUDUSD": 0.63, "USDAUD": 1.587}  # fallback

# Fix — use a more reasonable fallback and log a warning:
exchange_rates = {"AUDUSD": 0.70, "USDAUD": 1.43}  # fallback (updated 2026-03)
```

Better: read the last successful rate from the dashboard JSON before generating new data:
```python
prev_data = safe_json(OUTPUT, {})
prev_rates = prev_data.get("exchange_rates", {"AUDUSD": 0.70, "USDAUD": 1.43})
exchange_rates = prev_rates  # default to last known
```

### Q4. Add Per-Source Freshness Timestamps ⏱️ 15 min

**File:** `dashboard/generate_data.py` in `generate_market()` and `generate()`

Add a `data_freshness` section to the output:

```python
# In generate_market(), add to result dict:
"data_freshness": {
    "broker_connected": broker_ok,
    "broker_timestamp": datetime.now(BRISBANE).isoformat() if broker_ok else None,
    "data_source": data_source,
    "equity_curve_last_date": eq_curve[-1]["date"] if eq_curve else None,
    "plan_date": plan.get("trade_date") if plan else None,
    "state_file_mtime": _file_mtime(per_market) if per_market.exists() else None,
}
```

The dashboard HTML can then show "Last broker update: 2 min ago" or "⚠️ Stale: 4 hours ago".

### Q5. Per-Broker Timeout Wrapper ⏱️ 30 min

**Problem:** IBKR `connect()` can hang for 20-60s (reqExecutionsAsync timeout). This blocks the entire `generate()` loop including Moomoo.

**File:** `dashboard/generate_data.py` in `generate()` broker loop

```python
import signal

def _timeout_handler(signum, frame):
    raise TimeoutError("Broker connection timed out")

for mid in markets:
    cfg = get_config(mid)
    trading = cfg.get("trading", {})
    broker_name = trading.get("broker", "ibkr")
    if trading.get("mode") == "live" and trading.get("live_enabled", False):
        # Per-broker timeout — don't let one broker block others
        broker_timeout = 25 if broker_name == "ibkr" else 15
        try:
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(broker_timeout)
            acct, positions, ok, orders = get_live_broker_data(cfg)
            signal.alarm(0)  # cancel alarm
            if ok:
                broker_caches[mid] = {"acct": acct, "positions": positions, "ok": True, "orders": orders}
                print(f"  {mid}: broker connected ({broker_name}), {len(positions)} positions")
            else:
                print(f"  {mid}: broker connect FAILED ({broker_name})")
        except TimeoutError:
            signal.alarm(0)
            print(f"  {mid}: broker TIMEOUT after {broker_timeout}s ({broker_name})")
```

This ensures Moomoo still runs even if IBKR hangs.

---

## Medium-Term Improvements (1 day each)

### M1. Last-Known-Good Cache Layer ⏱️ 2-3 hours

**Problem:** When a broker is temporarily down, the dashboard shows 0 positions. It should show the last known state with a staleness warning.

**Design:**
1. After each successful broker fetch, save the result to `dashboard/cache/broker_{market_id}.json` with a timestamp.
2. When broker fails, load from cache and flag `data_source: "cached"` with the cache age.
3. Dashboard HTML shows a yellow banner: "⚠️ ASX data from 15 min ago (broker offline)".

**Files:**
- `dashboard/generate_data.py` — save/load cache in `get_live_broker_data()` or `generate_market()`
- `dashboard/templates/index.html` — conditional staleness banner

```python
CACHE_DIR = PROJECT_ROOT / "dashboard" / "cache"

def _save_broker_cache(market_id, acct, positions, orders):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "timestamp": datetime.now().isoformat(),
        "acct": acct, "positions": positions, "orders": orders
    }
    with open(CACHE_DIR / f"broker_{market_id}.json", "w") as f:
        json.dump(data, f, default=str)

def _load_broker_cache(market_id, max_age_minutes=60):
    path = CACHE_DIR / f"broker_{market_id}.json"
    if not path.exists():
        return None
    data = safe_json(path, None)
    if not data:
        return None
    ts = datetime.fromisoformat(data["timestamp"])
    age = (datetime.now() - ts).total_seconds() / 60
    if age > max_age_minutes:
        return None  # too old
    data["cache_age_minutes"] = round(age, 1)
    return data
```

### M2. Cron Hardening + Alerting ⏱️ 1-2 hours

**Problem:** `refresh_dashboard.sh` has no error handling, no alerting, and no log rotation.

**New `scripts/refresh_dashboard.sh`:**
```bash
#!/bin/bash
# Refresh dashboard with error handling and alerting.
cd /root/atlas

# Rotate log (keep last 1000 lines)
LOG="/root/atlas/logs/dashboard-refresh.log"
if [ -f "$LOG" ] && [ $(wc -l < "$LOG") -gt 5000 ]; then
    tail -1000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi

# Run with timeout — don't let IBKR hang the entire cron
timeout 120 python3 dashboard/generate_data.py 2>&1
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo "[$(date)] Dashboard refresh FAILED (exit $EXIT_CODE)"
    # Alert via Telegram (only if it's been >30 min since last alert)
    ALERT_FLAG="/tmp/atlas-dashboard-alert"
    if [ ! -f "$ALERT_FLAG" ] || [ $(($(date +%s) - $(stat -c %Y "$ALERT_FLAG"))) -gt 1800 ]; then
        python3 -c "
from utils.telegram import send_message
send_message('⚠️ Dashboard refresh failed (exit $EXIT_CODE). Check logs/dashboard-refresh.log')
" 2>/dev/null
        touch "$ALERT_FLAG"
    fi
else
    rm -f /tmp/atlas-dashboard-alert
fi

# Always copy template (even on partial failure, old data is better than broken page)
cp -f dashboard/templates/index.html dashboard/data/index.html 2>/dev/null
```

### M3. Per-Source Freshness Indicators in Dashboard UI ⏱️ 2 hours

**File:** `dashboard/templates/index.html`

Add a status bar at the top of the dashboard showing each data source:
```
🟢 SP500 (Moomoo): live, 2 min ago | 🟡 ASX (IBKR): cached, 18 min ago | 🟢 FX: 0.7108 (2 min ago)
```

Implementation:
- Read `data_freshness` from each market's data (added in Q4)
- Color-code: green (<5 min), yellow (5-30 min), red (>30 min or offline)
- Show "OFFLINE" when broker never connected this session

### M4. Exchange Rate Caching ⏱️ 30 min

**File:** `dashboard/generate_data.py` in `generate()`

Cache the FX rate to a file and only refresh hourly:
```python
FX_CACHE = PROJECT_ROOT / "dashboard" / "cache" / "fx_rates.json"

def _get_exchange_rates():
    # Check cache (refresh hourly)
    cached = safe_json(FX_CACHE, None)
    if cached:
        ts = datetime.fromisoformat(cached.get("timestamp", "2000-01-01"))
        if (datetime.now() - ts).total_seconds() < 3600:
            return cached["rates"]

    # Fetch fresh
    try:
        import yfinance as yf
        audusd = float(yf.Ticker("AUDUSD=X").history(period="1d")["Close"].iloc[-1])
        rates = {"AUDUSD": round(audusd, 5), "USDAUD": round(1/audusd, 5)}
        # Save cache
        FX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with open(FX_CACHE, "w") as f:
            json.dump({"timestamp": datetime.now().isoformat(), "rates": rates}, f)
        return rates
    except Exception:
        # Fallback: last cached, then hardcoded
        if cached:
            return cached["rates"]
        return {"AUDUSD": 0.70, "USDAUD": 1.43}
```

### M5. IBKR Gateway Health Check Before Connect ⏱️ 1 hour

**Problem:** `IBKRBroker.connect()` can hang when the gateway is unhealthy. Pre-flight check avoids the hang.

**File:** `brokers/ibkr/broker.py` in `connect()`

```python
def connect(self) -> bool:
    # Pre-flight: check if gateway port is accepting connections
    import socket
    try:
        sock = socket.create_connection((self._host, self._port), timeout=3)
        sock.close()
    except (ConnectionRefusedError, TimeoutError, OSError):
        logger.warning("IBKRBroker: gateway not reachable at %s:%d", self._host, self._port)
        return False

    # Also check Docker health if available
    try:
        import subprocess
        result = subprocess.run(
            ["docker", "inspect", "atlas-ibgateway", "--format={{.State.Health.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip() == "unhealthy":
            logger.warning("IBKRBroker: gateway container is unhealthy — skipping connect")
            return False
    except Exception:
        pass  # Docker not available or check failed — proceed anyway

    # ... existing connect logic ...
```

---

## Architecture Recommendations (multi-day)

### A1. Modular Decomposition of `generate_data.py` ⏱️ 1 day

**Problem:** `generate_data.py` is 1,748 lines handling 12+ data feeds, 7 insight miners, 3 price sources, cross-broker reconciliation, and multi-market merging. Any change risks breaking unrelated feeds.

**Proposed structure:**
```
dashboard/
├── generate_data.py          # Orchestrator only (~200 lines)
├── feeds/
│   ├── __init__.py
│   ├── broker.py             # get_live_broker_data(), sync_broker_fills()
│   ├── prices.py             # get_live_prices(), get_cache_prices(), get_prices()
│   ├── exchange_rates.py     # FX fetching + caching
│   ├── portfolio.py          # get_portfolio(), equity curve management
│   ├── plans.py              # get_latest_plan(), _load_plan_metadata()
│   ├── research.py           # generate_research_data()
│   └── benchmarks.py         # _get_benchmark_curve(), _merge_benchmark_curves()
├── insights/
│   ├── __init__.py           # generate_daily_insight() dispatcher
│   ├── opt_lift.py
│   ├── param_scatter.py
│   ├── strategy_compare.py
│   ├── trade_anatomy.py
│   ├── vix_regime.py
│   ├── fee_impact.py
│   └── monthly_season.py
├── cache/                    # Cached broker data, FX rates
│   ├── broker_asx.json
│   ├── broker_sp500.json
│   └── fx_rates.json
├── data/
│   ├── dashboard-data.json   # Output
│   └── index.html            # Deployed template
└── templates/
    └── index.html            # Source template
```

**Benefits:**
- Each feed can be tested independently
- Failures in one module don't cascade
- Easier to add new markets or data sources
- Insight miners can be added/removed without touching the main pipeline

### A2. Cross-Broker Position Reconciliation Engine ⏱️ 4 hours

**Problem:** When a user holds .AX stocks on both Moomoo and IBKR, positions could be double-counted.

**Design:** Add a reconciliation step after all broker connections:

```python
def reconcile_positions(broker_caches: dict, markets: list) -> dict:
    """Deduplicate positions across brokers.

    Rules:
    1. IBKR is source of truth for ASX/HK markets (it's the configured broker)
    2. Moomoo is source of truth for SP500
    3. Cross-broker positions (e.g. .AX on Moomoo when IBKR is primary)
       are shown as "manual" holdings on the non-primary market
    4. Same ticker on both brokers → use the primary broker's data
    """
    seen_tickers = {}  # {ticker: (market_id, source_broker)}
    # First pass: mark primary broker positions
    for mid in markets:
        primary_broker = get_config(mid).get("trading", {}).get("broker", "ibkr")
        cache = broker_caches.get(mid, {})
        for pos in cache.get("positions", []):
            ticker = pos.get("ticker", "")
            if ticker:
                seen_tickers[ticker] = (mid, primary_broker)
    # Second pass: flag duplicates
    for mid in markets:
        cache = broker_caches.get(mid, {})
        positions = cache.get("positions", [])
        for pos in positions:
            ticker = pos.get("ticker", "")
            if ticker in seen_tickers and seen_tickers[ticker][0] != mid:
                pos["_duplicate"] = True  # flag for exclusion
    return broker_caches
```

### A3. Historical FX Rate Storage ⏱️ 2 hours

**Problem:** Equity curves store values in native currency but display uses current FX rate, distorting historical data.

**Design:** Record the FX rate alongside each equity curve point:

```python
# When updating equity curve:
eq_curve.append({
    "date": today_str,
    "equity": round(equity, 2),
    "fx_rate": exchange_rates.get("AUDUSD") if currency == "USD" else 1.0
})
```

Then `_merge_equity_curves()` uses the historical rate per point instead of today's rate.

---

## Implementation Order

### Phase 1: Immediate (today, < 1 hour total)
1. ✅ Q1 — Fix `legacy` NameError
2. ✅ Q2 — Separate clientIds (dashboard=20, executor=10)
3. ✅ Q3 — Update FX fallback to 0.70
4. ✅ Q4 — Add freshness timestamps

### Phase 2: This week (2-4 hours total)
5. Q5 — Per-broker timeout wrapper
6. M2 — Cron hardening + alerting
7. M4 — FX rate caching
8. M5 — IBKR gateway health pre-check

### Phase 3: Next week (1 day total)
9. M1 — Last-known-good cache layer
10. M3 — Dashboard UI freshness indicators

### Phase 4: Future sprint
11. A1 — Modular decomposition
12. A2 — Cross-broker reconciliation engine
13. A3 — Historical FX rate storage

---

## Key Metrics to Track

After implementing these changes, monitor:

| Metric | Target | Current |
|--------|--------|---------|
| Dashboard uptime (data < 30 min old) | > 99% | ~90% (estimated, IBKR outages) |
| Broker connect success rate | > 95% per market | ~80% ASX (IBKR timeouts) |
| Average refresh time | < 30s | ~45s (IBKR hangs) |
| Stale data incidents per week | 0 | ~2-3 (broker restarts) |
| ClientId conflict errors | 0 | Unknown (not logged) |
