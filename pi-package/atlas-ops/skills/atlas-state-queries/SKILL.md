---
name: atlas-state-queries
description: "How to check every piece of Atlas system state — services, broker, positions, equity, config, research, dashboard, logs, and data freshness. Use when you need to inspect system status, diagnose issues, or verify health before operations."
---

# Atlas State Queries

Quick reference for checking any piece of Atlas system state.

---

## Quick Lookup

| I want to check... | Do this |
|---------------------|---------|
| Are all services up? | `systemctl is-active atlas-dashboard atlas-dashboard-refresh atlas-telegram-bot atlas-director atlas-research-runner atlas-research-window` |
| What's my equity? | Read `logs/equity_curve_sp500.json` — last entry has `equity`, `pnl`, `date` |
| Open positions? | `python3 scripts/cli.py -m sp500 status` |
| Broker connected? | `python3 scripts/cli.py -m sp500 broker` |
| Config version? | `python3 -c "import json; print(json.load(open('config/active/sp500.json'))['version'])"` |
| Data freshness? | `ls -lt data/cache/sp500/ \| head -5` (check mtimes) |
| Recent errors? | `journalctl -u atlas-<service> --no-pager -n 30 --since '1 hour ago'` |
| Research progress? | `ls research/results/*.tsv \| wc -l` and check research queue |
| Dashboard working? | `curl -s http://localhost:8501/ \| head -5` |
| Last trade plan? | `ls -lt plans/plan_sp500_*.json \| head -3` |
| Job status? | Use `atlas_jobs_list_runs` tool or check `.pi/atlas-runs/*.json` |
| Stored state? | Use `atlas_state_list` tool with appropriate scope |

---

## Service Health

### Check all services at once

```bash
systemctl is-active atlas-dashboard atlas-dashboard-refresh atlas-telegram-bot atlas-director atlas-research-runner atlas-research-window
```

Output: one line per service, `active` or `inactive`/`failed`.

### Detailed service status

```bash
systemctl status atlas-<name> --no-pager
```

### Recent service logs

```bash
# Last 50 lines
journalctl -u atlas-<name> --no-pager -n 50

# Last hour
journalctl -u atlas-<name> --no-pager --since "1 hour ago"

# Follow live
journalctl -u atlas-<name> -f
```

### Restart a service

```bash
systemctl restart atlas-<name>
# Verify after restart:
sleep 3 && systemctl is-active atlas-<name>
```

### Service → code mapping

| Service | Entry point | Config |
|---------|------------|--------|
| atlas-dashboard | `services/dashboard_server.py` | `/etc/systemd/system/atlas-dashboard.service` |
| atlas-dashboard-refresh | `scripts/dashboard_loop.sh` | `/etc/systemd/system/atlas-dashboard-refresh.service` |
| atlas-telegram-bot | `services/telegram_bot.py` | `/etc/systemd/system/atlas-telegram-bot.service` |
| atlas-director | `scripts/director_cron.py` | `/etc/systemd/system/atlas-director.service` (timer-activated) |
| atlas-research-runner | `scripts/autoresearch.py` | `/etc/systemd/system/atlas-research-runner.service` |
| atlas-research-window | (sweep script) | `/etc/systemd/system/atlas-research-window.service` (timer-activated) |

---

## Broker & Account

### Connection check

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 broker
```

Shows: broker type, mode (live/paper), base URL, connection status, equity, cash, positions.

Key output lines:
- `AlpacaBroker connected: paper=False feed=iex equity=$3519.69 status=ACTIVE` → healthy
- Connection error → broker offline

### Portfolio status (full)

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 status
```

Shows: config version, equity, cash, open positions with entry prices, unrealized PnL, exposure.

### Open orders

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 orders
```

### Trade history

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 ledger
cd /root/atlas && python3 scripts/cli.py -m sp500 history  # with actual fees
```

### Market state

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 market-check
```

Shows: market open/closed, trading calendar, next open/close times.

---

## Equity & Performance

### Equity curve

```bash
# Read latest entry
python3 -c "
import json
curve = json.load(open('logs/equity_curve_sp500.json'))
latest = curve[-1]
print(f'Date: {latest[\"date\"]}')
print(f'Equity: \${latest[\"equity\"]:.2f}')
print(f'PnL: \${latest[\"pnl\"]:.2f}')
print(f'Entries: {len(curve)}')
"
```

Each entry: `{ "date": "YYYY-MM-DD", "equity": float, "pnl": float, "fx_rate": float, "estimated": bool }`

### Performance metrics from backtest

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 backtest --days 252
```

Or use `atlas_jobs_run` tool with job `cli_backtest`.

---

## Config

### Read active config

```bash
python3 -c "
import json
cfg = json.load(open('config/active/sp500.json'))
print(f'Version: {cfg[\"version\"]}')
print(f'Mode: {cfg[\"trading\"][\"mode\"]}')
print(f'Approval: {cfg[\"trading\"][\"approval_required\"]}')
strats = [k for k,v in cfg['strategies'].items() if v.get('enabled')]
print(f'Strategies ({len(strats)}): {strats}')
print(f'Max positions: {cfg[\"risk\"][\"max_open_positions\"]}')
"
```

### Compare candidate vs active

Use the `atlas_risk_check_config_promotion` tool:
```
Tool: atlas_risk_check_config_promotion
Params: { "candidatePath": "config/candidates/<file>.json" }
```

### List config backups

Use the `atlas_risk_list_config_backups` tool, or:
```bash
ls -lt config/versions/active_config_pre_reopt_*.json | head -10
```

### Diff two configs

```bash
diff <(python3 -m json.tool config/active/sp500.json) <(python3 -m json.tool config/candidates/<candidate>.json) | head -60
```

---

## Research State

### Research queue

```bash
ls research/queue/ 2>/dev/null
cat research/queue/*.json 2>/dev/null | python3 -m json.tool | head -40
```

### Experiment results

```bash
# List all results
ls -lt research/results/ | head -20

# Read a TSV result
head -5 research/results/<experiment>.tsv
```

### Brain knowledge base

```bash
cat memory/SUMMARY.md
```

### Check what's been tested

```bash
# Research results by strategy/experiment
ls research/results/*.tsv | sed 's|.*/||;s|\.tsv||'
```

---

## Data Freshness

### Cache file age

```bash
# SP500 cache — check most recent files
ls -lt data/cache/sp500/ 2>/dev/null | head -5

# Count cached tickers
ls data/cache/sp500/*.parquet 2>/dev/null | wc -l

# Check if cache is stale (>24h)
find data/cache/sp500/ -name "*.parquet" -mmin +1440 | wc -l
```

### Refresh data

```bash
cd /root/atlas && python3 scripts/cli.py -m sp500 ingest
```

Or use `atlas_jobs_run` tool with job `cli_ingest`.

### Universe file

```bash
python3 -c "
import json
u = json.load(open('data/universe_sp500.json', 'r') if __import__('os').path.exists('data/universe_sp500.json') else open('universe/sp500.json', 'r'))
print(f'Tickers: {len(u.get(\"tickers\", u if isinstance(u, list) else []))}')
" 2>/dev/null || echo "Universe file not found at expected path"
```

---

## Trade Plans

### Latest plan

```bash
ls -lt plans/plan_sp500_*.json | head -3
```

### Read plan summary

```bash
python3 -c "
import json
from pathlib import Path
plans = sorted(Path('plans').glob('plan_sp500_*.json'), reverse=True)
if plans:
    p = json.load(open(plans[0]))
    print(f'Date: {p.get(\"trade_date\")}')
    print(f'Status: {p.get(\"status\")}')
    print(f'Entries: {len(p.get(\"proposed_entries\", []))}')
    print(f'Exits: {len(p.get(\"proposed_exits\", []))}')
    print(f'Rejections: {len(p.get(\"rejected_entries\", []))}')
"
```

Or use `atlas_artifacts_summarize` tool with the plan path.

### Check plan gate

Use `atlas_risk_check_plan_gate` tool:
```
Tool: atlas_risk_check_plan_gate
Params: { "date": "2026-03-14", "action": "evaluate" }
```

---

## Logs

### Log file locations

| Log | Path | What |
|-----|------|------|
| Health check | `logs/healthz-autofix.log` | Healthz cron output |
| Intraday monitor | `logs/intraday_sp500.log` | Position monitoring during market hours |
| Protective orders | `logs/sync_protective.log` | Stop-loss/take-profit sync |
| Maintenance | `logs/maintenance.log` | Weekly cleanup |
| Ceasefire monitor | `logs/ceasefire-cron.log` | Geopolitical monitor |
| Iran monitor | `logs/iran-monitor-cron.log` | Iran situation tracker |
| Dashboard refresh | (systemd journal) | `journalctl -u atlas-dashboard-refresh` |

### Tail recent logs

```bash
# Application logs
tail -50 logs/healthz-autofix.log
tail -50 logs/intraday_sp500.log

# Service logs
journalctl -u atlas-telegram-bot --no-pager -n 30
journalctl -u atlas-research-runner --no-pager -n 30
```

### Search for errors

```bash
grep -i "error\|exception\|traceback\|failed" logs/*.log | tail -20
```

---

## Dashboard

### Check dashboard is running

```bash
systemctl is-active atlas-dashboard atlas-dashboard-refresh
curl -s -o /dev/null -w '%{http_code}' http://localhost:8501/
```

### Refresh dashboard data manually

```bash
cd /root/atlas && python3 dashboard/generate_data.py
```

Or use `atlas_jobs_run` tool with job `dashboard_generate_data`.

### Dashboard data freshness

```bash
ls -lt dashboard/data/*.json 2>/dev/null | head -5
```

---

## Pi Extension State

### Job runs

```bash
# Use the tool:
atlas_jobs_list_runs  # params: { "limit": 10 }

# Or directly:
ls -lt .pi/atlas-runs/*.json 2>/dev/null | head -10
```

### Key-value state store

```bash
# Use the tool:
atlas_state_list  # params: { "scope": "default" }

# Or directly:
ls .pi/atlas-state/kv/default/ 2>/dev/null
```

### Locks

```bash
# Use the tool:
atlas_state_lock_status  # params: { "name": "heavy-backtest" }

# Or directly:
ls .pi/atlas-state/locks/ 2>/dev/null
cat .pi/atlas-state/locks/*.json 2>/dev/null
```

---

## Disk Usage

```bash
# Project size
du -sh /root/atlas

# Largest directories
du -sh /root/atlas/*/ 2>/dev/null | sort -rh | head -10

# Large log files
find /root/atlas/logs -name "*.log" -size +1M -exec ls -lh {} \;

# Free disk space
df -h /
```
