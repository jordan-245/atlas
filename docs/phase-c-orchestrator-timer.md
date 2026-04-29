# Phase C.3 — Single Orchestrator Timer

**Status**: PLANNED — implementation deferred until Phase B crontab audit and B.2 cutover complete.  
**Estimated effort**: 1–2 weeks  
**Pre-requisites**: B.2 reconcile consolidation; B.4 cron idempotency tests green.

---

## 1. Motivation

### 1a. The scheduling chaos problem

Atlas currently has **49 cron entries** in `pi-cron.sh` and **17 systemd timers**.
At any given 15-minute interval, up to 8 separate cron jobs may fire simultaneously
for the same market:

```
*/15   sync_protective_orders --market sp500
*/15   sync_protective_orders --market commodity_etfs
*/15   sync_protective_orders --market sector_etfs
:05    reconcile_positions --market sp500
:02    reconcile_positions --market commodity_etfs
:32    intraday_monitor (sp500)
:20    execute_approved.py (sp500)
0 4    sync_broker_orders
```

These jobs are sequenced by *calendar time*, not by *data dependency*. The
correct order for safe protective-order management is:

```
sync_broker_orders → reconcile_fills → sync_protective_orders → emit healthz
```

Running them in arbitrary cron order means `sync_protective_orders` sometimes fires
before `reconcile_fills` has updated the DB, leading to the RCA #2A/2B race
(atomic bracket missed → stop-only OTO → TP placed separately → fill race).

### 1b. The hotspot this fixes

**Hotspot #9** (from validation report): *Timing chaos from 49 cron entries + 17
systemd timers — no DAG, no dependency tracking, no overlap prevention.*

RCA #2A root cause: `_execute_entry` placed stop-only OTO because entry signal had
`take_profit=None`. The TP was added by `sync_protective_orders` in the next cron
cycle — a separate network call with a TOCTOU window.

A single orchestrator that sequences `reconcile → sync_broker_orders → sync_protective`
atomically (within one Python process) eliminates this window.

---

## 2. Proposal: Single Per-Market Supervisor Process

Replace the 49 cron entries with a **single Python supervisor** that owns the
15-minute cycle for all markets. The supervisor:

1. Wakes every 15 minutes (driven by `systemd` `OnCalendar=*:0/15`).
2. For each active market (sp500, commodity_etfs, sector_etfs) — in parallel:
   - **Step A**: `sync_broker_orders(market)` — refresh fill-price oracle.
   - **Step B** (after A): `reconcile_fills(market)` — update DB from broker fills.
   - **Step C** (after B): `sync_protective_orders(market)` — ensure stop+TP present.
   - **Step D** (after C): `emit_healthz(market)` — write heartbeat to DB.
3. Logs the full cycle time and any per-step errors to `logs/orchestrator.log`.
4. Sends a single consolidated Telegram summary (not 3 separate messages).

### 2a. 15-minute cycle sequence (DAG)

```
┌──────────────────────────────────────────────────────┐
│ orchestrator.py — every 15 min                        │
│                                                       │
│  for market in [sp500, commodity_etfs, sector_etfs]:  │
│    ┌─────────────────────┐                            │
│    │ A: sync_broker_orders│                           │
│    └──────────┬──────────┘                            │
│               ▼                                       │
│    ┌─────────────────────┐                            │
│    │ B: reconcile_fills  │                            │
│    └──────────┬──────────┘                            │
│               ▼                                       │
│    ┌─────────────────────┐                            │
│    │ C: sync_protective  │                            │
│    └──────────┬──────────┘                            │
│               ▼                                       │
│    ┌─────────────────────┐                            │
│    │ D: emit_healthz     │                            │
│    └─────────────────────┘                            │
└──────────────────────────────────────────────────────┘
```

Markets run **concurrently** (ThreadPoolExecutor with 3 workers) so the 15-minute
window is not consumed by sequential market processing.

---

## 3. Architecture Sketch

### 3a. New file: `core/orchestrator.py`

```python
"""Atlas per-market orchestration cycle (15 min).

Called by atlas-orchestrator.service every 15 minutes. Sequences:
  sync_broker_orders → reconcile_fills → sync_protective → healthz

Usage:
  python3 core/orchestrator.py [--once] [--market sp500]
"""

from concurrent.futures import ThreadPoolExecutor
from core.reconcile import reconcile_fills
from scripts.sync_broker_orders import sync_broker_orders
from scripts.sync_protective_orders import sync_market as sync_protective

_ACTIVE_MARKETS = ["sp500", "commodity_etfs", "sector_etfs"]

def run_cycle(markets=_ACTIVE_MARKETS) -> dict:
    def _run_market(market_id: str) -> dict:
        result = {}
        result["broker_orders"] = sync_broker_orders(days=1, dry_run=False)
        result["reconcile"]     = reconcile_fills(market=market_id, dry_run=False)
        result["protective"]    = sync_protective(market_id, today(), dry_run=False)
        _write_heartbeat(market_id)
        return result

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(_run_market, m): m for m in markets}
        return {m: f.result() for f, m in zip(futures.keys(), futures.values())}
```

### 3b. systemd service: `atlas-orchestrator.service`

```ini
[Unit]
Description=Atlas 15-minute orchestration cycle
After=network.target atlas-dashboard.service

[Service]
Type=oneshot
WorkingDirectory=/root/atlas
ExecStart=/usr/bin/flock -n /tmp/atlas-orchestrator.lock \
          /usr/bin/python3 core/orchestrator.py --once
TimeoutStartSec=840
Environment=PYTHONPATH=/root/atlas
StandardOutput=append:/root/atlas/logs/orchestrator.log
StandardError=append:/root/atlas/logs/orchestrator.log

[Install]
WantedBy=multi-user.target
```

### 3c. systemd timer: `atlas-orchestrator.timer`

```ini
[Unit]
Description=Atlas 15-minute orchestrator trigger

[Timer]
OnCalendar=*:0/15
AccuracySec=30
Persistent=false

[Install]
WantedBy=timers.target
```

### 3d. Crontab reduction

After cutover, the 49 cron entries reduce to **8**:
- 1 premarket ingest (once/day, before market open)
- 1 EOD settlement (once/day, after market close)
- 1 research nightly (once/day, off-hours)
- 1 healthcheck (hourly — separate from trading cycle)
- 1 universe rebuild (weekly)
- 1 canary check (daily)
- 2 manual overrides (overlay, alt-data)

The orchestrator timer replaces all 41 remaining entries.

---

## 4. Migration Strategy

### Phase 1 — Shadow mode (1 week)

Run `atlas-orchestrator.service` alongside the existing cron entries.
Both paths execute independently. After each orchestrator cycle, a diff
is computed against what the old cron would have done (using the shadow-mode
pattern from B.2 reconcile).

Shadow diff alerts fire if:
- Orchestrator placed a protective order that cron did NOT
- Cron placed a protective order that orchestrator did NOT
- Cycle time exceeds 12 minutes (leaves < 3 min buffer in 15-min window)

### Phase 2 — Cutover (1 day)

1. Confirm 7 consecutive shadow days with zero diff alerts.
2. Comment out 41 cron entries from `pi-cron.sh` (keep for rollback).
3. `systemctl enable atlas-orchestrator.timer && systemctl start atlas-orchestrator.timer`.
4. Monitor for 48 hours.

### Phase 3 — Prune

Delete commented-out cron entries. Remove shadow-mode code from orchestrator.

---

## 5. Risks and Rollback

| Risk | Severity | Mitigation |
|------|----------|------------|
| Orchestrator cycle exceeds 15 min | High | Per-market timeout (10 min), flock guard prevents overlap |
| Concurrent broker calls (3 markets × 3 steps) rate-limit | Medium | Stagger market starts by 30s; broker SDK has built-in retry |
| Step C fires before Step B completes (if B raises) | High | Step C only runs if Step B returns without exception; market skipped on error |
| Cron entries not fully removed → duplicate execution | Medium | Shadow mode diff alerts catch this before cutover |
| orchestrator.py crashes at 23:30 AEST during premarket | Medium | Systemd `Restart=on-failure` with 60s backoff; flock prevents stuck instances |

**Rollback**: Re-enable old cron entries (they are commented, not deleted, during
the cutover validation window). Disable the timer: `systemctl stop atlas-orchestrator.timer`.

---

## 6. Estimated Effort

| Sub-task | Estimate |
|----------|----------|
| `core/orchestrator.py` skeleton | 2 days |
| systemd service + timer | 0.5 days |
| Shadow mode diff logging | 1 day |
| 7-day shadow monitoring (passive) | 7 days |
| Cutover + pruning | 0.5 days |
| Tests (cycle sequencing, timeout handling) | 2 days |
| **Total** | **~2 weeks** |

---

## 7. Action Items

- [ ] Read `core/reconcile.py` API (B.2 output) — ensure `reconcile_fills(market)` is callable
- [ ] Write `core/orchestrator.py` — sequential per-market DAG with concurrent market workers
- [ ] Write `systemd/atlas-orchestrator.{service,timer}`
- [ ] Shadow mode: log both orchestrator result and old-cron result; diff on cycle end
- [ ] 7-day shadow run with zero-diff validation
- [ ] Update `scripts/pi-cron.sh` — comment out 41 entries with `# ORCHESTRATOR` marker
- [ ] Cutover: enable timer, disable cron entries, 48h monitoring
- [ ] Pruning: delete commented entries, remove shadow code
- [ ] Add `test_orchestrator_cycle.py` (step sequencing, market isolation, timeout guard)
- [ ] Update `healthz_hourly.sh` to verify orchestrator timer is active

---

*Design doc authored: 2026-04-29. Review before implementation kickoff.*
