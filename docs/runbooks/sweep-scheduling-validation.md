# Sweep Scheduling Validation

**Runbook:** Research Window Sweep Schedule Validation  
**Created:** 2026-05-14  
**Status:** ✅ Validated — 2 gaps identified (see below)

---

## Overview

The Atlas research sweep runs nightly per-universe via **templated systemd units**:

- **Service template:** `systemd/atlas-research-window@.service`
- **Per-universe timers:** `systemd/atlas-research-window@<universe>.timer`
- **Execution script:** `scripts/research_window_universe.sh <universe>`

The service template was added in commit `790988bf` (chore: mirror autoresearch unit files into repo). Prior to this, the sweeps ran via a single `pi-cron.sh` entry (line 665) that was manually disabled (commented out).

---

## Schedule Table

| Universe | Timer File | OnCalendar (UTC implied) | Systemd Enabled? | Last Run | Params (hours/workers/llm) |
|---|---|---|---|---|---|
| `sp500` | `atlas-research-window@sp500.timer` | `*-*-* 23:00:00` | ✅ **enabled** | 2026-05-13 23:00 AEST | 1.0h / 3w / LLM=yes |
| `commodity_etfs` | `atlas-research-window@commodity_etfs.timer` | `*-*-* 00:00:00` | ❌ **disabled** | (none active) | 0.5h / 2w / LLM=yes |
| `sector_etfs` | `atlas-research-window@sector_etfs.timer` | `*-*-* 01:00:00` | ❌ **disabled** | (none active) | 0.25h / 1w / LLM=no |
| `gold_etfs` | `atlas-research-window@gold_etfs.timer` | `*-*-* 02:00:00` | ✅ **enabled** | 2026-05-14 02:00 AEST | 0.25h / 1w / LLM=no |
| `treasury_etfs` | `atlas-research-window@treasury_etfs.timer` | `*-*-* 03:00:00` | ✅ **enabled** | 2026-05-14 03:00 AEST | 0.25h / 1w / LLM=no |
| `defensive_etfs` | `atlas-research-window@defensive_etfs.timer` | `*-*-* 04:00:00` | ✅ **enabled** | 2026-05-14 04:00 AEST | 0.25h / 1w / LLM=no |
| `crypto` | `atlas-research-window@crypto.timer` | `*-*-* 05:00:00` | ✅ **enabled** | 2026-05-14 05:00 AEST | 0.25h / 1w / LLM=no |
| `asx` | (no timer file) | — | ❌ **missing** | — | N/A |

> **Note on timezones:** Systemd `OnCalendar` times without a timezone are local system time. The system timezone is `Australia/Brisbane` (AEST, UTC+10, no DST). So `23:00:00` local = 13:00 UTC.

---

## Gaps Identified

### ⚠️ GAP 1: `commodity_etfs` timer is DISABLED

**File:** `systemd/atlas-research-window@commodity_etfs.timer`  
**Status:** Timer file exists but `systemctl is-enabled` returns `disabled`.  
**Impact:** commodity_etfs sweep has never run via systemd. No research_best data is refreshed for this universe on the nightly cycle.

**Verification:**
```bash
systemctl is-enabled atlas-research-window@commodity_etfs.timer
# → disabled

systemctl list-timers | grep commodity_etfs
# → (no output)
```

**Fix (operator action required):**
```bash
systemctl enable --now atlas-research-window@commodity_etfs.timer
systemctl status atlas-research-window@commodity_etfs.timer
```

---

### ⚠️ GAP 2: `sector_etfs` timer is DISABLED

**File:** `systemd/atlas-research-window@sector_etfs.timer`  
**Status:** Timer file exists but `systemctl is-enabled` returns `disabled`.  
**Impact:** sector_etfs sweep has never run via systemd. No research_best data is refreshed nightly.

**Fix (operator action required):**
```bash
systemctl enable --now atlas-research-window@sector_etfs.timer
systemctl status atlas-research-window@sector_etfs.timer
```

---

### ℹ️ INFO: `asx` has no timer

No `atlas-research-window@asx.timer` file exists. ASX research may not be in-scope for nightly sweeps, or may be handled separately. Confirm with Engineering Lead if ASX sweeps are intended.

---

## Service Template Parameters

**File:** `systemd/atlas-research-window@.service`

Key settings:
- `ExecStart`: calls `scripts/research_window_universe.sh %i` (universe passed via template `%i`)
- `TimeoutStartSec=9000` (150 min) — covers worst-case sp500 (4200s sweep + 1500s LLM + buffer)
- `KillMode=control-group` — kills all child processes on timeout
- `Nice=10` — reduced priority to avoid starving other services

**Per-universe resource allocation** (from `research_window_universe.sh`):

| Universe | Sweep timeout | Hours | Workers | LLM loop |
|---|---|---|---|---|
| sp500 | 4200s (70 min) | 1.0h | 3 | ✅ yes (25 min) |
| commodity_etfs | 2400s (40 min) | 0.5h | 2 | ✅ yes (25 min) |
| sector_etfs, gold_etfs, treasury_etfs, defensive_etfs, crypto | 1200s (20 min) | 0.25h | 1 | ❌ no |

---

## Previous Schedule (pi-cron.sh)

The old `pi-cron.sh` approach (line 665) ran the `autoresearch_nightly.py` directly:

```bash
# 0 9 * * 1-5  python3 /root/atlas/research/autoresearch_nightly.py \
#     --hours 8 --workers 5 --notify > /root/atlas/logs/autoresearch_nightly_$(date +\%Y\%m\%d).log 2>&1
```

This was a **single weekday-only job** covering all universes in one 8-hour session. The templated systemd approach replaces this with **per-universe nightly timers** running every day, providing:
- Better isolation (one universe failure doesn't block others)
- Per-universe resource tuning
- Independent scheduling (staggered 23:00–05:00 local)

---

## Validation Commands

```bash
# Check all atlas-research-window timers
systemctl list-timers | grep atlas-research-window

# Check enabled status for each
for u in sp500 commodity_etfs sector_etfs gold_etfs treasury_etfs defensive_etfs crypto; do
    echo -n "atlas-research-window@${u}.timer: "
    systemctl is-enabled atlas-research-window@${u}.timer 2>/dev/null || echo "unknown"
done

# View recent sweep logs
ls -lt /root/atlas/logs/research_window_*.log | head -10
```

---

## Action Items

| Priority | Action | Owner |
|---|---|---|
| 🔴 HIGH | Enable `atlas-research-window@commodity_etfs.timer` | Operator |
| 🔴 HIGH | Enable `atlas-research-window@sector_etfs.timer` | Operator |
| 🟡 MEDIUM | Confirm if `asx` sweep is in-scope (create timer if needed) | Engineering Lead |
| 🟢 LOW | Remove the commented-out `pi-cron.sh` line 665 to avoid confusion | Backend Dev |
