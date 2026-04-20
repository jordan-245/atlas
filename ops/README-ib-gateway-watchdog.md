# IB Gateway Watchdog

Auto-restart watchdog for IB Gateway Docker containers (Atlas live + Cronus paper).

## What It Does

- Monitors both IB Gateway containers every 10 minutes
- Checks process health (container running) AND port connectivity (4001, 4002)
- Auto-restarts containers if down
- Sends Telegram alerts on failures and recoveries
- Logs heartbeat every hour (6th check)

## Files

- `/root/scripts/ib-gateway-watchdog.sh` - Main watchdog script
- `/etc/systemd/system/ib-gateway-watchdog.service` - Systemd service
- `/etc/systemd/system/ib-gateway-watchdog.timer` - Timer (runs every 10min)
- `/var/log/ib-gateway-watchdog.log` - Log file
- `/var/run/ib-gateway-watchdog.state` - Counter state

## Monitored Containers

| Container | Port | Purpose |
|-----------|------|---------|
| `atlas-ibgateway` | 4001 | Live trading (Atlas SP500) |
| `cronus-ibgateway-paper` | 4002 | Paper trading (Cronus commodities) |

## Commands

```bash
# Check timer status
systemctl status ib-gateway-watchdog.timer

# View next scheduled run
systemctl list-timers | grep ib-gateway

# View recent logs
journalctl -u ib-gateway-watchdog -n 20

# View watchdog log file
tail -f /var/log/ib-gateway-watchdog.log

# Run manually (for testing)
/root/scripts/ib-gateway-watchdog.sh

# Restart timer
systemctl restart ib-gateway-watchdog.timer
```

## Telegram Alerts

Sends alerts on:
- 🟡 Gateway DOWN detected (before restart attempt)
- 🟢 Gateway recovered successfully (after restart)
- 🔴 Gateway restart FAILED (manual intervention needed)

## Known Behavior

### 2FA Authentication
After restart, IB Gateway may require Second Factor Authentication (2FA). This causes:
- Container status: `(health: starting)` 
- Port unreachable until 2FA completed
- Watchdog will report "restart FAILED" but this is expected

**Action:** Check IBKR mobile app for 2FA notification, or use IBKR website to approve.

### Startup Time
IB Gateway takes 30-60 seconds to fully start. The watchdog waits 30 seconds after restart before verifying.

## Log Rotation

Add to `/etc/logrotate.d/ib-gateway-watchdog`:

```
/var/log/ib-gateway-watchdog.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
```

## Deployment

Created: 2026-03-24  
Priority: P3 (Infrastructure)  
Related: Atlas #177 (IB Gateway auto-restart)
