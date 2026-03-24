# Atlas Trading System — Disaster Recovery Runbook

**Version:** 1.0  
**Last Updated:** 2026-03-24  
**Owner:** Atlas Operations  
**Criticality:** P0 — Trading System Continuity  

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Critical Dependencies](#critical-dependencies)
3. [Backup & Restore Procedures](#backup--restore-procedures)
4. [Disaster Scenarios](#disaster-scenarios)
   - [Scenario 1: Server Dies During Market Hours](#scenario-1-server-dies-during-market-hours)
   - [Scenario 2: Corrupted Database](#scenario-2-corrupted-database)
   - [Scenario 3: Broker API Outage](#scenario-3-broker-api-outage)
   - [Scenario 4: Bad Config Deployed](#scenario-4-bad-config-deployed)
   - [Scenario 5: Strategy Gone Haywire](#scenario-5-strategy-gone-haywire)
5. [Emergency Contacts](#emergency-contacts)
6. [Post-Incident Checklist](#post-incident-checklist)

---

## System Overview

### Architecture Summary
- **Primary Market:** SP500 (live trading via Alpaca)
- **Secondary Market:** ASX (passive/disabled)
- **Deployment:** Single VPS (8 cores, root@vps)
- **Trading Mode:** Daily (market-on-open execution)
- **Current AUM:** ~$5,050 (SP500), ~$2,680 (ASX)

### Core Components
```
atlas/
├── config/active/          # Active trading configs (sp500.json, asx.json)
├── data/
│   ├── cache/             # Historical price data (regenerable)
│   ├── processed/         # Computed features (regenerable)
│   ├── snapshots/         # Periodic equity snapshots
│   └── position_monitor/  # Real-time position tracking ⚠️ CRITICAL
├── journal/
│   ├── trade_ledger.json  # All executed trades ⚠️ CRITICAL
│   └── decision_journal.json  # Plan approvals and decisions
├── brokers/state/         # Broker connection state
└── scripts/               # Automation and cron jobs
```

### Services (systemd)
- `atlas-dashboard.service` — Web UI (port 8000, auth-protected)
- `atlas-dashboard-refresh.service` — Real-time position updates (10s loop)
- `atlas-telegram-bot.service` — Alert notifications

### Scheduled Operations (cron)
```
19:00 Mon-Fri  → Premarket data refresh + plan generation
23:15 Mon-Fri  → Execute approved trades (market-on-open orders)
23:30 Mon-Fri  → Sync protective stop-loss orders
01:30-07:30    → Intraday position monitoring (30min intervals)
08:00 Tue-Sat  → Post-close settlement + equity snapshot
06:00 Sunday   → Weekly maintenance (log rotation, cache cleanup)
```

### Critical Data Files
| File | Criticality | Recovery Method |
|------|-------------|-----------------|
| `journal/trade_ledger.json` | **P0** | Backup + broker reconciliation |
| `data/position_monitor/positions.json` | **P0** | Regenerate from broker API |
| `config/active/sp500.json` | **P1** | Backup (version history in config/versions/) |
| `~/.atlas-secrets.json` | **P0** | Secure offline backup |
| `data/cache/*` | **P3** | Regenerate via `atlas ingest` |

---

## Critical Dependencies

### External Services
1. **Alpaca Markets API** (broker execution)
   - Live API: `https://api.alpaca.markets`
   - Paper API: `https://paper-api.alpaca.markets`
   - Credentials: `~/.atlas-secrets.json` (ALPACA_API_KEY, ALPACA_SECRET_KEY)
   - Fallback: None (single broker)

2. **IB Gateway** (historical data, backup broker)
   - Docker containers: `atlas-ibgateway` (port 4001), `cronus-ibgateway-paper` (port 4002)
   - Watchdog: `/root/scripts/ib-gateway-watchdog.sh` (auto-restart every 10min)

3. **Yahoo Finance** (market data)
   - Primary source for historical data
   - Fallback: Alpaca IEX feed

4. **Telegram Bot** (alerts)
   - Token: `~/.atlas-secrets.json` (TELEGRAM_BOT_TOKEN)
   - Chat ID: `~/.atlas-secrets.json` (TELEGRAM_CHAT_ID)

### Internal State
- **Trade Ledger:** Complete history of all trades (entry, exit, PnL)
- **Decision Journal:** Audit trail of plan approvals
- **Position Monitor:** Real-time broker position sync
- **Config Versions:** Historical config snapshots in `config/versions/`

---

## Backup & Restore Procedures

### Automated Backup System
**Schedule:** Daily at 04:00 AEST  
**Method:** `restic` incremental snapshots  
**Repository:** `/root/backups/restic-repo`  
**Retention:** 7 daily, 4 weekly, 3 monthly  

**What's Backed Up:**
- All configs (`config/active/`, `config/versions/`)
- Critical data (`journal/`, `data/position_monitor/`, `data/snapshots/`)
- Broker state (`brokers/state/`)
- Credentials (`~/.atlas-secrets.json`)
- Crontab snapshot
- Systemd service files (`/etc/systemd/system/atlas-*.service`)

**What's Excluded:**
- Large cache files (`data/cache/earnings/`, `data/cache/backtest/`)
- Regenerable data (`data/processed/`)
- Python bytecode (`__pycache__/`, `*.pyc`)

### Manual Backup (Pre-Major Change)
```bash
cd /root/atlas
timestamp=$(date +%Y%m%d_%H%M%S)

# Backup config
cp config/active/sp500.json config/versions/sp500_backup_${timestamp}.json

# Backup critical data
tar -czf /tmp/atlas_manual_backup_${timestamp}.tar.gz \
    config/active/ \
    journal/ \
    data/position_monitor/ \
    ~/.atlas-secrets.json

# Verify backup
tar -tzf /tmp/atlas_manual_backup_${timestamp}.tar.gz | head -20
echo "Backup saved: /tmp/atlas_manual_backup_${timestamp}.tar.gz"
```

### List Available Backups
```bash
export RESTIC_PASSWORD="$RESTIC_PASSWORD"
export RESTIC_REPOSITORY="/root/backups/restic-repo"

# List all snapshots
restic snapshots

# List files in latest snapshot
restic ls latest | grep -E "(config|journal|position_monitor)"

# Show snapshot details
restic snapshots --tag automated --last
```

### Restore from Backup
```bash
export RESTIC_PASSWORD="$RESTIC_PASSWORD"
export RESTIC_REPOSITORY="/root/backups/restic-repo"

# Restore specific snapshot to temp directory
SNAPSHOT_ID="abc123"  # Get from 'restic snapshots'
restic restore $SNAPSHOT_ID --target /tmp/restore_$(date +%s)

# Or restore latest
restic restore latest --target /tmp/restore_latest

# Verify restored files
ls -lah /tmp/restore_*/root/atlas/journal/
cat /tmp/restore_*/root/atlas/config/active/sp500.json | jq '.version'

# Copy critical files back (CAREFUL - verify first!)
cp /tmp/restore_*/root/atlas/journal/trade_ledger.json /root/atlas/journal/
cp /tmp/restore_*/root/.atlas-secrets.json ~/
chmod 600 ~/.atlas-secrets.json
```

### Offsite Backup (Recommended)
**Not currently automated.** Manual monthly export recommended:
```bash
cd /root/backups
tar -czf atlas_offsite_$(date +%Y%m).tar.gz restic-repo/
# Transfer to secure offsite storage (S3, external drive, etc.)
```

---

## Disaster Scenarios

---

## Scenario 1: Server Dies During Market Hours

### Symptoms
- VPS unresponsive / hardware failure
- Cannot SSH to server
- Dashboard offline
- Positions still open at broker

### Immediate Actions (Priority: 5-10 minutes)

#### Step 1: Verify Broker Positions (30 seconds)
```bash
# From ANY machine with internet:
# Option A: Alpaca web console
open https://app.alpaca.markets/paper/portfolio/positions

# Option B: Quick API check (if you have curl + credentials)
curl -X GET "https://api.alpaca.markets/v2/positions" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET" | jq '.[] | {symbol, qty, current_price, unrealized_pl}'
```

**Decision Point:**
- If unrealized loss > 2% of equity → **HALT TRADING** (see Step 2)
- If positions look normal → Continue to recovery

#### Step 2: Emergency Trading Halt (if needed)
```bash
# Close all positions via Alpaca web console OR API
curl -X DELETE "https://api.alpaca.markets/v2/positions" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET"

# Verify closure
curl -X GET "https://api.alpaca.markets/v2/positions" \
  -H "APCA-API-KEY-ID: YOUR_KEY" \
  -H "APCA-API-SECRET-KEY: YOUR_SECRET"
```

**⏱ Time Checkpoint: 2 minutes elapsed**

---

### Recovery Steps (Priority: Restore execution by market close)

#### Step 3: Provision New Server (5 minutes)
```bash
# Spin up new VPS (Ubuntu 22.04+, 4+ cores)
# Example: DigitalOcean, Vultr, AWS EC2
# Set timezone to AEST
sudo timedatectl set-timezone Australia/Brisbane

# Install dependencies
sudo apt update && sudo apt install -y \
    python3 python3-pip python3-venv \
    docker.io docker-compose \
    restic git curl jq

# Clone Atlas repo
cd /root
git clone <your-atlas-repo-url> atlas
cd atlas
```

#### Step 4: Restore Critical Files (3 minutes)
```bash
# Option A: From restic backup (requires backup repo access)
export RESTIC_REPOSITORY="s3:your-backup-bucket/atlas"  # Or your backup location
export RESTIC_PASSWORD="$RESTIC_PASSWORD"

restic restore latest --target /tmp/restore \
    --include '/root/atlas/config/active/*' \
    --include '/root/atlas/journal/*' \
    --include '/root/.atlas-secrets.json'

cp /tmp/restore/root/.atlas-secrets.json ~/.atlas-secrets.json
chmod 600 ~/.atlas-secrets.json
cp /tmp/restore/root/atlas/config/active/sp500.json /root/atlas/config/active/
cp /tmp/restore/root/atlas/journal/trade_ledger.json /root/atlas/journal/

# Option B: Manual (if no backup access)
# Copy files from secure offline storage or re-create credentials
# CRITICAL: trade_ledger.json can be reconstructed from broker API (see Step 6)
```

#### Step 5: Install Dependencies (2 minutes)
```bash
cd /root/atlas
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

#### Step 6: Reconcile Positions with Broker (2 minutes)
```bash
# Rebuild position monitor from live broker state
cd /root/atlas
python3 scripts/rebuild_positions_from_broker.py --market sp500

# Verify reconciliation
cat data/position_monitor/positions.json | jq '.positions | length'
python3 -c "
import json
ledger = json.load(open('journal/trade_ledger.json'))
open_trades = [t for t in ledger['trades'] if t['status'] == 'open']
print(f'Open trades in ledger: {len(open_trades)}')
"
```

**⏱ Time Checkpoint: 12 minutes elapsed**

#### Step 7: Start Critical Services (1 minute)
```bash
# Copy systemd service files
sudo cp /tmp/restore/etc/systemd/system/atlas-*.service /etc/systemd/system/
sudo systemctl daemon-reload

# Start services
sudo systemctl start atlas-telegram-bot
sudo systemctl start atlas-dashboard-refresh
sudo systemctl start atlas-dashboard

# Verify
sudo systemctl status atlas-telegram-bot atlas-dashboard
```

#### Step 8: Verify Data Integrity (2 minutes)
```bash
# Check config validity
cd /root/atlas
python3 -c "
import json, sys
cfg = json.load(open('config/active/sp500.json'))
assert cfg['version'] == 'v3.0', 'Wrong config version'
assert cfg['trading']['mode'] == 'live', 'Not in live mode'
print('✓ Config valid:', cfg['version'], cfg['market'])
"

# Verify broker connectivity
python3 -c "
from brokers.alpaca_adapter import AlpacaAdapter
broker = AlpacaAdapter(paper=False)
account = broker.get_account()
print(f'✓ Broker connected: equity=${account.equity}, buying_power=${account.buying_power}')
"

# Check trade ledger sanity
python3 -c "
import json
ledger = json.load(open('journal/trade_ledger.json'))
print(f'✓ Trade ledger loaded: {len(ledger[\"trades\"])} total trades')
"
```

**⏱ Time Checkpoint: 15 minutes elapsed**

---

### Verification Checklist

- [ ] Broker positions match `position_monitor/positions.json`
- [ ] Trade ledger matches broker closed trades (PnL reconciled)
- [ ] Config version matches expected (v3.0 for SP500)
- [ ] Telegram bot sending alerts
- [ ] Dashboard accessible at `http://<new-ip>:8000`
- [ ] Crontab restored (`crontab -l` shows scheduled jobs)
- [ ] IB Gateway containers running (if needed)
- [ ] Protective stop-loss orders active at broker

### Crontab Restoration
```bash
# Restore from backup
cp /tmp/restore/tmp/crontab-backup.txt /tmp/crontab.txt
crontab /tmp/crontab.txt

# Or manually re-create critical jobs:
crontab -e
# Add:
# 19:00 Mon-Fri → /root/atlas/scripts/pi-cron.sh premarket sp500
# 23:15 Mon-Fri → /root/atlas/scripts/execute_approved.py -m sp500
# 23:30 Mon-Fri → /root/atlas/scripts/sync_protective_orders.py --market sp500
```

### Estimated Recovery Time
- **Minimal viable (positions safe):** 2-5 minutes
- **Trading operational:** 15-20 minutes
- **Full system (dashboard, monitoring):** 20-30 minutes

### What Can Wait Until After Market Close
- Historical data cache regeneration (`atlas ingest`)
- Dashboard prettification
- Non-critical service restarts (research loops, director)
- Log file restoration

---

## Scenario 2: Corrupted Database

### Symptoms
- `trade_ledger.json` unreadable / malformed JSON
- `positions.json` inconsistent with broker
- Equity calculations wrong
- PnL mismatches

### Immediate Actions

#### Step 1: Assess Damage (30 seconds)
```bash
cd /root/atlas

# Check trade ledger validity
python3 -c "import json; json.load(open('journal/trade_ledger.json'))" 2>&1
# If error → corrupted

# Check positions validity
python3 -c "import json; json.load(open('data/position_monitor/positions.json'))" 2>&1

# Check for backup
ls -lah journal/trade_ledger_backup_*.json
ls -lah data/position_monitor/positions.json.bak
```

#### Step 2: Stop Trading Immediately
```bash
# Prevent new trades from executing
sudo systemctl stop atlas-dashboard-refresh

# Comment out execute_approved cron job
crontab -e
# Add '#' before: 15 23 * * 1-5 ... execute_approved.py

# Notify via Telegram (manual)
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="🚨 TRADING HALTED: Database corruption detected. Manual reconciliation required."
```

---

### Recovery Steps

#### Step 3: Restore from Backup (2 minutes)
```bash
cd /root/atlas

# Option A: Recent automatic backup (if exists)
if [ -f journal/trade_ledger_backup_$(date +%Y%m%d).json ]; then
    cp journal/trade_ledger.json journal/trade_ledger_CORRUPTED_$(date +%s).json
    cp journal/trade_ledger_backup_$(date +%Y%m%d).json journal/trade_ledger.json
    echo "✓ Restored from today's backup"
fi

# Option B: Restic backup
export RESTIC_PASSWORD="$RESTIC_PASSWORD"
export RESTIC_REPOSITORY="/root/backups/restic-repo"

restic restore latest --target /tmp/restore_ledger \
    --include '/root/atlas/journal/trade_ledger.json'

cp journal/trade_ledger.json journal/trade_ledger_CORRUPTED_$(date +%s).json
cp /tmp/restore_ledger/root/atlas/journal/trade_ledger.json journal/

# Verify restored file
python3 -c "
import json
ledger = json.load(open('journal/trade_ledger.json'))
print(f'✓ Ledger restored: {len(ledger[\"trades\"])} trades')
"
```

#### Step 4: Reconcile with Broker (5 minutes)
```bash
# Get broker trade history
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter
from datetime import datetime, timedelta
import json

broker = AlpacaAdapter(paper=False)

# Fetch last 90 days of trades
start = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')
activities = broker.client.get_activities(activity_types='FILL', date=start)

print(f"Broker trades (last 90 days): {len(activities)}")

# Export to temp file for comparison
with open('/tmp/broker_trades.json', 'w') as f:
    json.dump([{
        'symbol': a.symbol,
        'side': a.side,
        'qty': float(a.qty),
        'price': float(a.price),
        'timestamp': a.transaction_time.isoformat(),
        'order_id': a.order_id
    } for a in activities], f, indent=2)
    
print("✓ Exported to /tmp/broker_trades.json")
EOF

# Compare with ledger
python3 << 'EOF'
import json

ledger = json.load(open('journal/trade_ledger.json'))
broker_trades = json.load(open('/tmp/broker_trades.json'))

ledger_entries = [t for t in ledger['trades'] if t.get('broker_order_id')]
broker_ids = {t['order_id'] for t in broker_trades}
ledger_ids = {t['broker_order_id'] for t in ledger_entries}

missing_in_ledger = broker_ids - ledger_ids
missing_in_broker = ledger_ids - broker_ids

if missing_in_ledger:
    print(f"⚠ {len(missing_in_ledger)} trades in broker but not in ledger:")
    for order_id in list(missing_in_ledger)[:5]:
        trade = next(t for t in broker_trades if t['order_id'] == order_id)
        print(f"  {trade['timestamp'][:10]} {trade['side']} {trade['qty']} {trade['symbol']} @ ${trade['price']}")
        
if missing_in_broker:
    print(f"⚠ {len(missing_in_broker)} trades in ledger but not in broker (likely old)")
    
if not missing_in_ledger and not missing_in_broker:
    print("✓ Ledger matches broker perfectly")
EOF
```

#### Step 5: Rebuild Missing Entries (if needed)
```bash
# If trades are missing from ledger, reconstruct from broker data
python3 << 'EOF'
import json
from datetime import datetime

ledger = json.load(open('journal/trade_ledger.json'))
broker_trades = json.load(open('/tmp/broker_trades.json'))

# Identify missing trades
ledger_order_ids = {t.get('broker_order_id') for t in ledger['trades'] if t.get('broker_order_id')}
missing_trades = [t for t in broker_trades if t['order_id'] not in ledger_order_ids]

if missing_trades:
    print(f"Adding {len(missing_trades)} missing trades to ledger...")
    
    for bt in missing_trades:
        new_entry = {
            'id': len(ledger['trades']) + 1,
            'symbol': bt['symbol'],
            'action': bt['side'].lower(),
            'quantity': int(bt['qty']),
            'entry_price': bt['price'],
            'timestamp': bt['timestamp'],
            'broker_order_id': bt['order_id'],
            'status': 'closed',  # Adjust if needed
            'strategy': 'RECOVERED',  # Mark as recovered
            '_recovered_from_broker': True
        }
        ledger['trades'].append(new_entry)
    
    # Save updated ledger
    with open('journal/trade_ledger.json', 'w') as f:
        json.dump(ledger, f, indent=2)
    
    print(f"✓ Ledger updated: {len(ledger['trades'])} total trades")
else:
    print("✓ No missing trades to add")
EOF
```

#### Step 6: Verify Data Integrity
```bash
# Run integrity checks
python3 << 'EOF'
import json

ledger = json.load(open('journal/trade_ledger.json'))
positions = json.load(open('data/position_monitor/positions.json'))

# Check ledger structure
assert 'trades' in ledger, "Missing 'trades' key"
assert 'metadata' in ledger, "Missing 'metadata' key"

# Check for duplicate trade IDs
trade_ids = [t['id'] for t in ledger['trades']]
assert len(trade_ids) == len(set(trade_ids)), f"Duplicate trade IDs found: {len(trade_ids)} vs {len(set(trade_ids))}"

# Check open trades match positions
open_trades = [t for t in ledger['trades'] if t['status'] == 'open']
position_symbols = set(positions.get('positions', {}).keys())
ledger_symbols = {t['symbol'] for t in open_trades}

if position_symbols != ledger_symbols:
    print(f"⚠ Position mismatch:")
    print(f"  Broker: {sorted(position_symbols)}")
    print(f"  Ledger: {sorted(ledger_symbols)}")
else:
    print("✓ Open positions match ledger")

print(f"✓ Integrity checks passed:")
print(f"  Total trades: {len(ledger['trades'])}")
print(f"  Open trades: {len(open_trades)}")
print(f"  Closed trades: {len([t for t in ledger['trades'] if t['status'] == 'closed'])}")
EOF
```

---

### Verification Checklist

- [ ] Trade ledger is valid JSON
- [ ] All broker trades since last backup are in ledger
- [ ] Open positions match broker exactly
- [ ] PnL calculations match broker equity curve
- [ ] No duplicate trade IDs
- [ ] Equity snapshots align with trade history

### Resume Trading
```bash
# Re-enable cron jobs
crontab -e
# Remove '#' from execute_approved line

# Restart services
sudo systemctl start atlas-dashboard-refresh

# Send all-clear notification
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="✅ Database reconciliation complete. Trading resumed."
```

### Estimated Recovery Time
- **Restore from backup:** 2-5 minutes
- **Broker reconciliation:** 5-10 minutes
- **Full integrity verification:** 10-15 minutes
- **Total:** 15-30 minutes

### What If Backup Is Stale?
If backup is >7 days old:
1. Reconstruct ledger entirely from broker API (all closed orders)
2. Manually verify large trades against broker statements
3. Update `metadata.last_reconciliation` timestamp
4. Consider switching to real-time ledger replication

---

## Scenario 3: Broker API Outage

### Symptoms
- Alpaca API returning errors (500, 503, timeout)
- Cannot fetch positions or place orders
- Dashboard showing stale data
- Cron jobs failing with API errors

### Context: Automatic Retry Logic
Atlas has built-in retry logic for transient failures:
- **Exponential backoff:** 1s, 2s, 4s, 8s, 16s (max 5 retries)
- **Automatic failover:** Falls back to cached data for read operations
- **Order queue:** Failed orders retry on next cron cycle

**What happens automatically:**
- Data fetches retry silently
- Failed orders are logged and queued for next execution window
- Protective stops remain active at broker (independent of API)

---

### When to Intervene Manually

#### Decision Tree
```
API Error Detected
    │
    ├─ Is it market hours?
    │   ├─ YES → Monitor positions (Step 1)
    │   └─ NO → Wait for auto-recovery
    │
    ├─ Duration < 15 min?
    │   ├─ YES → Auto-retry handles it
    │   └─ NO → Manual intervention (Step 2)
    │
    └─ Are protective stops at risk?
        ├─ YES → Emergency hedge (Step 3)
        └─ NO → Monitor and wait
```

### Immediate Actions

#### Step 1: Verify Positions Are Safe (1 minute)
```bash
# Check broker web console (bypasses API)
open https://app.alpaca.markets/paper/portfolio/positions

# Check for protective stop orders
open https://app.alpaca.markets/paper/orders

# Verify:
# - All open positions have stop-loss orders
# - Stop prices are within expected range (1-2 ATR from entry)
# - No positions in extreme drawdown (>5% loss)
```

#### Step 2: Monitor Outage Status (ongoing)
```bash
# Check Alpaca status page
open https://status.alpaca.markets/

# Check Atlas error logs
tail -f /root/atlas/logs/atlas.log | grep -i "alpaca\|api\|error"

# Check last successful API call
grep "API success" /root/atlas/logs/atlas.log | tail -1

# Set up alert for resolution
while ! curl -s https://api.alpaca.markets/v2/account \
    -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
    -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" > /dev/null 2>&1; do
    echo "$(date): API still down"
    sleep 60
done
echo "$(date): API RESTORED"
# Send Telegram alert
```

#### Step 3: Emergency Hedge (only if positions at risk)
```bash
# If API down >30min during market hours and positions losing money:

# Option A: Web console (manual closure)
# 1. Go to https://app.alpaca.markets/paper/portfolio/positions
# 2. Click "Close All" or close individual losing positions

# Option B: Mobile app
# Download Alpaca mobile app and close positions manually

# Option C: Call broker support (last resort)
# Alpaca support: support@alpaca.markets
```

---

### Post-Outage Actions

#### Step 4: Verify System State After Recovery (5 minutes)
```bash
cd /root/atlas

# Refresh position monitor from broker
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter
import json

broker = AlpacaAdapter(paper=False)

# Fetch current positions
positions = broker.get_positions()
print(f"✓ Broker connectivity restored: {len(positions)} open positions")

# Rebuild position monitor
position_data = {
    'positions': {p.symbol: {
        'symbol': p.symbol,
        'qty': int(p.qty),
        'avg_entry_price': float(p.avg_entry_price),
        'current_price': float(p.current_price),
        'unrealized_pl': float(p.unrealized_pl),
        'market_value': float(p.market_value)
    } for p in positions},
    'last_update': __import__('datetime').datetime.now().isoformat()
}

with open('data/position_monitor/positions.json', 'w') as f:
    json.dump(position_data, f, indent=2)

print("✓ Position monitor rebuilt")
EOF

# Check for missed executions
python3 scripts/check_missed_executions.py --date $(date +%Y-%m-%d)

# Verify protective stops are active
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter

broker = AlpacaAdapter(paper=False)
orders = broker.client.list_orders(status='open')

stop_orders = [o for o in orders if o.type == 'stop']
print(f"Active stop orders: {len(stop_orders)}")

for order in stop_orders:
    print(f"  {order.symbol}: stop @ ${order.stop_price}")
    
if not stop_orders:
    print("⚠ WARNING: No protective stops found. Re-sync required.")
EOF
```

#### Step 5: Re-Sync Protective Orders (if needed)
```bash
# If stops are missing, manually trigger sync
cd /root/atlas
python3 scripts/sync_protective_orders.py --market sp500 --force

# Verify sync
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter
import json

broker = AlpacaAdapter(paper=False)
positions = broker.get_positions()
orders = broker.client.list_orders(status='open')

stop_map = {o.symbol: o for o in orders if o.type == 'stop'}

for pos in positions:
    if pos.symbol in stop_map:
        print(f"✓ {pos.symbol}: stop @ ${stop_map[pos.symbol].stop_price}")
    else:
        print(f"⚠ {pos.symbol}: MISSING STOP ORDER")
EOF
```

#### Step 6: Check for Execution Drift
```bash
# Compare planned vs actual executions
python3 << 'EOF'
import json
from datetime import datetime

# Load approved plan
plan_file = f'journal/plans/{datetime.now().strftime("%Y-%m-%d")}_sp500.json'
try:
    with open(plan_file) as f:
        plan = json.load(f)
except FileNotFoundError:
    print("No plan for today")
    exit(0)

planned_trades = plan.get('approved_trades', [])
print(f"Planned trades: {len(planned_trades)}")

# Load ledger
ledger = json.load(open('journal/trade_ledger.json'))
today_trades = [t for t in ledger['trades'] 
                if t['timestamp'].startswith(datetime.now().strftime('%Y-%m-%d'))]
print(f"Executed trades: {len(today_trades)}")

# Find mismatches
planned_symbols = {t['symbol'] for t in planned_trades}
executed_symbols = {t['symbol'] for t in today_trades}

missed = planned_symbols - executed_symbols
extra = executed_symbols - planned_symbols

if missed:
    print(f"⚠ Missed executions: {sorted(missed)}")
if extra:
    print(f"⚠ Unexpected executions: {sorted(extra)}")
if not missed and not extra:
    print("✓ All planned trades executed correctly")
EOF
```

---

### Verification Checklist

- [ ] Broker API responding normally
- [ ] Position monitor matches broker positions
- [ ] All open positions have protective stop orders
- [ ] No missed trade executions from outage window
- [ ] Trade ledger is up to date
- [ ] Dashboard showing live data (not cached)
- [ ] Cron jobs running successfully

### Estimated Downtime Impact
- **Outage <15 min:** No intervention needed (auto-retry)
- **Outage 15-60 min:** Positions safe if stops are active
- **Outage >60 min:** May miss execution window, manual trade entry required
- **Recovery time after API restore:** 5-10 minutes

### Prevention Measures
- [ ] Monitor Alpaca status page proactively
- [ ] Set up alerts for API errors (already in place via Telegram)
- [ ] Consider multi-broker setup (future enhancement)
- [ ] Keep broker web console credentials accessible

---

## Scenario 4: Bad Config Deployed

### Symptoms
- Unexpected trade signals
- Position sizing errors (too large/small)
- Wrong strategy weights
- Missing protective stops
- Trading mode changed (paper vs live)

### Immediate Actions

#### Step 1: Identify Bad Config (30 seconds)
```bash
cd /root/atlas

# Check current config version
cat config/active/sp500.json | jq '.version, .trading.mode, .risk.max_open_positions'

# Compare to expected
echo "Expected: v3.0, live, 10 max positions"

# Check recent changes
ls -lah config/active/sp500.json
ls -lah config/versions/ | tail -5

# Look for anomalies
jq '.strategies | to_entries | map(select(.value.enabled == true)) | .[].key' config/active/sp500.json
```

#### Step 2: Halt Trading Immediately
```bash
# Stop execution cron job
crontab -e
# Comment out: 15 23 * * 1-5 ... execute_approved.py

# Stop dashboard refresh (prevents new signals)
sudo systemctl stop atlas-dashboard-refresh

# Notify
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="🚨 BAD CONFIG DETECTED: Trading halted. Rollback in progress."
```

---

### Recovery Steps

#### Step 3: Rollback Config (2 minutes)
```bash
cd /root/atlas

# Option A: Rollback to previous version
PREV_VERSION="sp500_v3.0_live.json"  # Check config/versions/ for correct file
cp config/versions/$PREV_VERSION config/active/sp500.json

# Option B: Restore from backup
export RESTIC_PASSWORD="$RESTIC_PASSWORD"
export RESTIC_REPOSITORY="/root/backups/restic-repo"

restic restore latest --target /tmp/restore_config \
    --include '/root/atlas/config/active/sp500.json'

cp config/active/sp500.json config/active/sp500_BAD_$(date +%s).json
cp /tmp/restore_config/root/atlas/config/active/sp500.json config/active/

# Verify rollback
cat config/active/sp500.json | jq '.version, ._version_metadata.created_at'
```

#### Step 4: Verify Rollback Worked
```bash
# Run config validation
python3 << 'EOF'
import json
import sys

cfg = json.load(open('config/active/sp500.json'))

# Critical checks
checks = [
    (cfg['version'] == 'v3.0', "Version mismatch"),
    (cfg['trading']['mode'] == 'live', "Wrong trading mode"),
    (cfg['trading']['approval_required'] == True, "Approval disabled"),
    (cfg['risk']['max_open_positions'] == 10, "Wrong max positions"),
    (cfg['risk']['max_daily_drawdown_pct'] == 0.02, "Wrong circuit breaker"),
    (cfg['risk']['require_stop_loss'] == True, "Stops not required"),
]

failed = [msg for check, msg in checks if not check]

if failed:
    print("❌ Config validation FAILED:")
    for msg in failed:
        print(f"  - {msg}")
    sys.exit(1)
else:
    print("✓ Config validation PASSED")
    print(f"  Version: {cfg['version']}")
    print(f"  Mode: {cfg['trading']['mode']}")
    print(f"  Max positions: {cfg['risk']['max_open_positions']}")
    print(f"  Circuit breaker: {cfg['risk']['max_daily_drawdown_pct']*100}%")
EOF
```

#### Step 5: Check for Damage from Bad Config
```bash
# Check if any trades were executed with bad config
python3 << 'EOF'
import json
from datetime import datetime, timedelta

ledger = json.load(open('journal/trade_ledger.json'))

# Trades in last 24 hours
recent_cutoff = (datetime.now() - timedelta(hours=24)).isoformat()
recent_trades = [t for t in ledger['trades'] if t['timestamp'] > recent_cutoff]

print(f"Trades in last 24h: {len(recent_trades)}")

if recent_trades:
    print("\nRecent trade details:")
    for t in recent_trades:
        qty = t.get('quantity', 0)
        price = t.get('entry_price', 0)
        value = qty * price
        print(f"  {t['timestamp'][:19]} {t['action']:4} {qty:3} {t['symbol']:5} @ ${price:7.2f} = ${value:8.2f}")
        
    # Flag anomalies
    large_trades = [t for t in recent_trades if (t.get('quantity',0) * t.get('entry_price',0)) > 5000]
    if large_trades:
        print(f"\n⚠ WARNING: {len(large_trades)} trades exceed $5k position limit")
else:
    print("✓ No trades executed since bad config deployment")
EOF

# Check current positions
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter

broker = AlpacaAdapter(paper=False)
positions = broker.get_positions()

print(f"\nCurrent positions: {len(positions)}")

oversized = [p for p in positions if float(p.market_value) > 5000]
if oversized:
    print(f"⚠ {len(oversized)} positions exceed $5k limit:")
    for p in oversized:
        print(f"  {p.symbol}: ${float(p.market_value):.2f}")
        
missing_stops = []
orders = broker.client.list_orders(status='open')
stop_symbols = {o.symbol for o in orders if o.type == 'stop'}

for p in positions:
    if p.symbol not in stop_symbols:
        missing_stops.append(p.symbol)
        
if missing_stops:
    print(f"⚠ {len(missing_stops)} positions missing protective stops: {missing_stops}")
else:
    print("✓ All positions have protective stops")
EOF
```

#### Step 6: Fix Damage (if any)
```bash
# If oversized positions exist, reduce them
python3 << 'EOF'
from brokers.alpaca_adapter import AlpacaAdapter

broker = AlpacaAdapter(paper=False)
positions = broker.get_positions()

MAX_POSITION_VALUE = 5000

for pos in positions:
    value = float(pos.market_value)
    if value > MAX_POSITION_VALUE:
        # Calculate shares to sell
        current_qty = int(pos.qty)
        current_price = float(pos.current_price)
        target_qty = int(MAX_POSITION_VALUE / current_price)
        sell_qty = current_qty - target_qty
        
        print(f"Reducing {pos.symbol}: {current_qty} → {target_qty} shares (sell {sell_qty})")
        
        # Uncomment to execute:
        # broker.client.submit_order(
        #     symbol=pos.symbol,
        #     qty=sell_qty,
        #     side='sell',
        #     type='market',
        #     time_in_force='day'
        # )
EOF

# If protective stops missing, sync them
python3 scripts/sync_protective_orders.py --market sp500 --force
```

---

### Verification Checklist

- [ ] Config version matches expected (v3.0)
- [ ] Trading mode is correct (live/paper)
- [ ] Max positions limit is correct (10)
- [ ] Circuit breaker is enabled (2% daily loss)
- [ ] Approval required = true
- [ ] Protective stops required = true
- [ ] Strategy weights sum correctly
- [ ] No oversized positions at broker
- [ ] All positions have stop orders

### Resume Trading
```bash
# Re-enable services
sudo systemctl start atlas-dashboard-refresh

# Re-enable cron
crontab -e
# Uncomment execute_approved line

# Manual verification run
cd /root/atlas
python3 scripts/cli.py plan --market sp500 --date $(date +%Y-%m-%d)

# Review plan carefully before approving
cat /tmp/plan_preview.json | jq '.entries[] | {symbol, action, confidence, position_size}'

# Notify
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="✅ Config rollback complete. Trading resumed with v3.0."
```

### Estimated Recovery Time
- **Detection + halt:** 1-2 minutes
- **Rollback:** 2-5 minutes
- **Damage assessment:** 5-10 minutes
- **Remediation (if needed):** 10-30 minutes
- **Total:** 15-45 minutes

### Root Cause Analysis (Post-Incident)
```bash
# Document what went wrong
cat > /tmp/rca_config_$(date +%Y%m%d).md << 'EOF'
# Config Deployment Incident RCA

**Date:** $(date)
**Impact:** Trading halted for X minutes
**Root Cause:** [Describe what happened]

## Timeline
- [Time]: Bad config deployed
- [Time]: Anomaly detected
- [Time]: Trading halted
- [Time]: Config rolled back
- [Time]: Trading resumed

## What Went Wrong
- [Detail the specific config error]

## What Went Right
- Rollback procedure worked
- No trades executed with bad config (or: limited damage)

## Action Items
1. [ ] Add config validation pre-commit hook
2. [ ] Implement config diff review process
3. [ ] Add automated config sanity checks
4. [ ] Update config promotion checklist

EOF
```

---

## Scenario 5: Strategy Gone Haywire

### Symptoms
- Multiple rapid losses
- Equity curve dropping sharply
- Position sizing anomalies
- Strategy triggering on bad signals
- Daily loss approaching 2% (circuit breaker threshold)

### Automatic Protection: Circuit Breaker
**Config setting:** `risk.max_daily_drawdown_pct: 0.02` (2% daily loss limit)

**What happens automatically:**
1. Atlas calculates daily drawdown every execution cycle
2. If `(current_equity - starting_equity) / starting_equity >= 0.02`:
   - **All new entries blocked**
   - Telegram alert sent: "🔴 CIRCUIT BREAKER: Daily drawdown 2.0% >= 2.0%"
   - Existing positions remain (protective stops active)
3. Resets at midnight (daily equity snapshot)

**Verification:**
```bash
# Check if circuit breaker triggered today
grep "CIRCUIT BREAKER\|TRADING HALTED" /root/atlas/logs/atlas.log | grep $(date +%Y-%m-%d)

# Check current drawdown
python3 << 'EOF'
import json
from datetime import datetime

# Get starting equity (from midnight snapshot)
snapshot_file = f'data/snapshots/{datetime.now().strftime("%Y-%m-%d")}_sp500.json'
try:
    with open(snapshot_file) as f:
        snapshot = json.load(f)
        starting_equity = snapshot['equity']
except FileNotFoundError:
    # Use config value if no snapshot
    config = json.load(open('config/active/sp500.json'))
    starting_equity = config['risk']['starting_equity']

# Get current equity from broker
from brokers.alpaca_adapter import AlpacaAdapter
broker = AlpacaAdapter(paper=False)
account = broker.get_account()
current_equity = float(account.equity)

drawdown = (current_equity - starting_equity) / starting_equity
print(f"Starting equity: ${starting_equity:.2f}")
print(f"Current equity: ${current_equity:.2f}")
print(f"Drawdown: {drawdown*100:.2f}%")
print(f"Circuit breaker: {'TRIGGERED' if drawdown <= -0.02 else 'OK'}")
EOF
```

---

### Manual Intervention

#### Step 1: Assess Situation (1 minute)
```bash
# Check recent trade performance
python3 << 'EOF'
import json
from datetime import datetime, timedelta

ledger = json.load(open('journal/trade_ledger.json'))

# Last 5 trades
recent = sorted(ledger['trades'], key=lambda t: t['timestamp'], reverse=True)[:5]

print("Last 5 trades:")
for t in recent:
    pnl = t.get('realized_pnl', 0)
    pnl_pct = t.get('pnl_pct', 0)
    status = t.get('status', 'unknown')
    print(f"  {t['timestamp'][:19]} {t['symbol']:5} {t['action']:4} {status:6} PnL: ${pnl:7.2f} ({pnl_pct:+6.2f}%)")

# Today's PnL
today = datetime.now().strftime('%Y-%m-%d')
today_trades = [t for t in ledger['trades'] if t['timestamp'].startswith(today)]
today_pnl = sum(t.get('realized_pnl', 0) for t in today_trades)
print(f"\nToday's total PnL: ${today_pnl:.2f}")

# Identify losing strategy
from collections import defaultdict
strategy_pnl = defaultdict(float)
for t in today_trades:
    strategy_pnl[t.get('strategy', 'unknown')] += t.get('realized_pnl', 0)

print("\nPnL by strategy (today):")
for strat, pnl in sorted(strategy_pnl.items(), key=lambda x: x[1]):
    print(f"  {strat:20}: ${pnl:7.2f}")
EOF
```

#### Step 2: Halt Specific Strategy (2 minutes)
```bash
cd /root/atlas

# Identify culprit strategy (e.g., "momentum_breakout")
PROBLEM_STRATEGY="momentum_breakout"

# Disable in config
python3 << EOF
import json

cfg = json.load(open('config/active/sp500.json'))
cfg['strategies']['$PROBLEM_STRATEGY']['enabled'] = False

with open('config/active/sp500.json', 'w') as f:
    json.dump(cfg, f, indent=2)

print(f"✓ Disabled {PROBLEM_STRATEGY}")
EOF

# Verify
jq '.strategies.momentum_breakout.enabled' config/active/sp500.json
# Should output: false

# Notify
curl -X POST "https://api.telegram.org/bot<YOUR_TOKEN>/sendMessage" \
  -d chat_id=<YOUR_CHAT_ID> \
  -d text="🔧 Strategy disabled: $PROBLEM_STRATEGY (manual intervention)"
```

#### Step 3: Close Losing Positions (if needed)
```bash
# Check current open positions from problem strategy
python3 << 'EOF'
import json
from brokers.alpaca_adapter import AlpacaAdapter

ledger = json.load(open('journal/trade_ledger.json'))
broker = AlpacaAdapter(paper=False)

# Find open trades from problem strategy
PROBLEM_STRATEGY = "momentum_breakout"
problem_trades = [t for t in ledger['trades'] 
                  if t.get('strategy') == PROBLEM_STRATEGY and t['status'] == 'open']

print(f"Open positions from {PROBLEM_STRATEGY}: {len(problem_trades)}")

for t in problem_trades:
    symbol = t['symbol']
    # Get current position
    try:
        position = broker.client.get_position(symbol)
        unrealized_pl = float(position.unrealized_pl)
        unrealized_pct = float(position.unrealized_plpc) * 100
        
        print(f"  {symbol}: ${unrealized_pl:7.2f} ({unrealized_pct:+6.2f}%)")
        
        # If losing >3%, consider manual closure
        if unrealized_pct < -3:
            print(f"    ⚠ Consider closing (>3% loss)")
            
            # Uncomment to execute immediate market close:
            # broker.client.submit_order(
            #     symbol=symbol,
            #     qty=position.qty,
            #     side='sell',
            #     type='market',
            #     time_in_force='day'
            # )
            # print(f"    → Closed {symbol}")
            
    except Exception as e:
        print(f"  {symbol}: Error fetching position - {e}")
EOF
```

#### Step 4: Investigate Root Cause
```bash
# Check strategy signals from today
python3 << 'EOF'
import json
from datetime import datetime

# Load today's plan (if exists)
plan_file = f'journal/plans/{datetime.now().strftime("%Y-%m-%d")}_sp500.json'
try:
    with open(plan_file) as f:
        plan = json.load(f)
except FileNotFoundError:
    print("No plan file for today")
    exit(0)

PROBLEM_STRATEGY = "momentum_breakout"
problem_signals = [e for e in plan.get('entries', []) if e.get('strategy') == PROBLEM_STRATEGY]

print(f"Signals from {PROBLEM_STRATEGY} today: {len(problem_signals)}")

for sig in problem_signals:
    print(f"  {sig['symbol']:5} conf={sig.get('confidence', 0):.2f} size=${sig.get('position_size', 0):.0f}")
    
    # Check signal quality
    if sig.get('confidence', 0) < 0.7:
        print(f"    ⚠ Low confidence signal")
    
    # Check if market conditions were bad
    # (Add checks for VIX spike, gap, breadth, etc.)
EOF

# Check for data quality issues
python3 << 'EOF'
import pandas as pd
import json

# Load recent price data for symbols that lost money
ledger = json.load(open('journal/trade_ledger.json'))
today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
today_losses = [t for t in ledger['trades'] 
                if t['timestamp'].startswith(today) and t.get('realized_pnl', 0) < -100]

print(f"Significant losses today (>$100): {len(today_losses)}")

for t in today_losses:
    symbol = t['symbol']
    entry = t.get('entry_price', 0)
    exit_price = t.get('exit_price', 0)
    
    print(f"  {symbol}: ${entry:.2f} → ${exit_price:.2f} (PnL: ${t.get('realized_pnl', 0):.2f})")
    
    # Check for anomalies (gap down, news, etc.)
    # This requires fetching data - add if needed
EOF
```

---

### Verification Checklist

- [ ] Circuit breaker triggered correctly (if ≥2% loss)
- [ ] Problem strategy identified and disabled
- [ ] Remaining strategies still functional
- [ ] Losing positions closed or monitored
- [ ] Root cause documented (bad signals, data issue, market event)
- [ ] Protective stops still active on remaining positions

### Recovery Steps

#### Step 5: Fix Strategy or Wait
```bash
# Option A: Fix strategy parameters
python3 << 'EOF'
import json

cfg = json.load(open('config/active/sp500.json'))

# Example: Tighten entry criteria for problem strategy
cfg['strategies']['momentum_breakout']['lookback_days'] = 20  # Was 15
cfg['strategies']['momentum_breakout']['atr_stop_mult'] = 2.0  # Was 1.5
# Or increase min_confidence globally:
cfg['risk']['min_confidence'] = 0.75  # Was 0.65

with open('config/active/sp500.json', 'w') as f:
    json.dump(cfg, f, indent=2)

print("✓ Strategy parameters updated")
EOF

# Run backtest to verify fix (optional, time permitting)
cd /root/atlas
python3 scripts/backtest.py --config config/active/sp500.json --days 63 --market sp500

# Check results
# Look for improvement in Sharpe, win rate, max drawdown

# Option B: Keep strategy disabled until investigation complete
# Do nothing - wait until next day to re-evaluate
```

#### Step 6: Re-Enable (Next Day)
```bash
# After investigation and fix, re-enable strategy
python3 << 'EOF'
import json

cfg = json.load(open('config/active/sp500.json'))
cfg['strategies']['momentum_breakout']['enabled'] = True

with open('config/active/sp500.json', 'w') as f:
    json.dump(cfg, f, indent=2)

print("✓ Strategy re-enabled")
EOF

# Monitor closely for next 3-5 trades
# Consider reducing weight temporarily:
# cfg['strategies']['momentum_breakout']['weight'] = 0.05  # Was 0.094
```

---

### Estimated Recovery Time
- **Detection:** Automatic (circuit breaker) or 5-10 min (manual monitoring)
- **Strategy disable:** 2-5 minutes
- **Position closure:** 5-15 minutes (if needed)
- **Investigation:** 30-60 minutes
- **Fix + backtest:** 1-4 hours
- **Total downtime:** 1-6 hours (trading halted for strategy, other strategies continue)

### Prevention Measures
- [ ] Circuit breaker is working (verified in logs)
- [ ] Add per-strategy loss limits (future enhancement)
- [ ] Monitor strategy performance daily
- [ ] Set up alerts for strategy win rate drop
- [ ] Add data quality checks pre-execution

---

## Emergency Contacts

### Broker Support
- **Alpaca Markets**
  - Email: support@alpaca.markets
  - Status: https://status.alpaca.markets/
  - Hours: 24/7 (email), business hours (phone)
  - Account: [Your account ID]

### Infrastructure
- **VPS Provider:** [Your provider]
  - Support: [Contact info]
  - Console: [URL]
  - Account: [ID]

### Data Providers
- **Yahoo Finance:** API (no support, community forums)
- **IB Gateway:** See Interactive Brokers support

### Internal
- **System Owner:** [Your name/contact]
- **Telegram Bot:** @atlas_bot
- **Backup Admin:** [If applicable]

---

## Credentials & Access

### Location of Secrets
**Primary:** `~/.atlas-secrets.json` (chmod 600)
```json
{
  "ALPACA_API_KEY": "...",
  "ALPACA_SECRET_KEY": "...",
  "TELEGRAM_BOT_TOKEN": "...",
  "TELEGRAM_CHAT_ID": "..."
}
```

**Backup:** Encrypted offsite storage
- Location: [Your backup location]
- Encryption: [Method]
- Access: [Who has keys]

### Service Accounts
- Dashboard: `http://<server-ip>:8000` (auth: see config)
- Alpaca Console: https://app.alpaca.markets
- Server SSH: `root@<vps-ip>` (key-based auth)

---

## Post-Incident Checklist

After resolving ANY disaster scenario:

### Immediate (Within 1 hour)
- [ ] All systems operational
- [ ] Broker positions reconciled
- [ ] Trade ledger verified
- [ ] Protective stops active
- [ ] Telegram notifications working
- [ ] Incident timeline documented

### Short-term (Within 24 hours)
- [ ] Root cause identified
- [ ] Backup integrity verified
- [ ] Config rollback tested (if applicable)
- [ ] Monitoring alerts checked
- [ ] Team debriefed (if applicable)

### Medium-term (Within 1 week)
- [ ] Root cause analysis written
- [ ] Runbook updated with lessons learned
- [ ] Prevention measures implemented
- [ ] Backup retention verified
- [ ] Disaster recovery drill scheduled

### Long-term (Within 1 month)
- [ ] System hardening complete
- [ ] Redundancy gaps addressed
- [ ] Monitoring improved
- [ ] Documentation updated
- [ ] Quarterly DR test added to calendar

---

## Runbook Maintenance

**Review Schedule:** Quarterly  
**Last Reviewed:** 2026-03-24  
**Next Review:** 2026-06-24  

**Update Triggers:**
- After any disaster scenario occurs
- After major system changes (broker switch, architecture change)
- After backup system changes
- When new failure modes discovered

**Ownership:** Atlas Operations  
**Approval Required:** Yes (test procedures before finalizing)

---

## Appendix: Quick Reference Commands

### Health Check
```bash
# Full system status
systemctl status atlas-* | grep -E "(Loaded|Active)"
crontab -l | grep -v '^#'
docker ps | grep ibgateway

# Broker connectivity
python3 -c "from brokers.alpaca_adapter import AlpacaAdapter; print(AlpacaAdapter(paper=False).get_account().equity)"

# Data integrity
python3 -c "import json; print('Ledger OK:', len(json.load(open('journal/trade_ledger.json'))['trades']))"
```

### Emergency Stop
```bash
# Halt all trading
sudo systemctl stop atlas-dashboard-refresh
crontab -e  # Comment out execute_approved
```

### Emergency Close All Positions
```bash
# Via API
python3 -c "from brokers.alpaca_adapter import AlpacaAdapter; AlpacaAdapter(paper=False).client.close_all_positions()"

# Or web console: https://app.alpaca.markets/paper/portfolio/positions → "Close All"
```

### Logs
```bash
# Service logs
journalctl -u atlas-dashboard -f
journalctl -u atlas-telegram-bot -f

# Application logs
tail -f /root/atlas/logs/atlas.log
tail -f /root/atlas/logs/execute_approved.log
tail -f /root/atlas/logs/sync_protective.log
```

### Backup Quick Restore
```bash
export RESTIC_PASSWORD="$RESTIC_PASSWORD"
export RESTIC_REPOSITORY="/root/backups/restic-repo"
restic restore latest --target /tmp/emergency_restore \
    --include '/root/atlas/journal/trade_ledger.json' \
    --include '/root/.atlas-secrets.json'
```

---

**END OF RUNBOOK**

*This is a living document. Update after every incident and every major system change.*
