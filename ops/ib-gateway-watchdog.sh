#!/bin/bash
#
# IB Gateway Watchdog - Auto-restart if down
# Monitors both atlas-ibgateway and cronus-ibgateway-paper containers
# Checks process health and port connectivity
#

set -euo pipefail

LOGFILE="/var/log/ib-gateway-watchdog.log"
STATEFILE="/var/run/ib-gateway-watchdog.state"
SECRETS_FILE="/root/.atlas-secrets.json"

# Read Telegram credentials
TELEGRAM_TOKEN=$(jq -r '.telegram_bot_token' "$SECRETS_FILE")
TELEGRAM_CHAT_ID=$(jq -r '.telegram_chat_id' "$SECRETS_FILE")

# Heartbeat counter (log only every 6th run = hourly with 10min timer)
HEARTBEAT_INTERVAL=6

# Function: Send Telegram alert
send_alert() {
    local message="$1"
    curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage" \
        -d "chat_id=${TELEGRAM_CHAT_ID}" \
        -d "text=${message}" \
        -d "parse_mode=HTML" >/dev/null 2>&1 || true
}

# Function: Log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOGFILE"
}

# Function: Check if container is running
check_container() {
    local container_name="$1"
    docker ps --filter "name=${container_name}" --filter "status=running" --format '{{.Names}}' | grep -q "^${container_name}$"
}

# Function: Check if port is reachable
check_port() {
    local port="$1"
    timeout 5 bash -c "echo -n > /dev/tcp/localhost/${port}" 2>/dev/null
}

# Function: Restart container
restart_container() {
    local container_name="$1"
    log "Restarting container: ${container_name}"
    docker restart "${container_name}" >/dev/null 2>&1
}

# Function: Wait and verify restart
verify_restart() {
    local container_name="$1"
    local port="$2"
    
    log "Waiting 30 seconds for ${container_name} to start..."
    sleep 30
    
    if check_container "${container_name}" && check_port "${port}"; then
        log "✅ ${container_name} successfully restarted and port ${port} is reachable"
        send_alert "🟢 <b>IB Gateway Recovered</b>%0A%0AContainer: ${container_name}%0APort: ${port}%0AStatus: Running"
        return 0
    else
        log "❌ ${container_name} restart FAILED - still down after 30s"
        send_alert "🔴 <b>IB Gateway Restart FAILED</b>%0A%0AContainer: ${container_name}%0APort: ${port}%0AAction required: Manual intervention needed"
        return 1
    fi
}

# Main watchdog logic
main() {
    # Increment heartbeat counter
    if [[ -f "$STATEFILE" ]]; then
        COUNTER=$(cat "$STATEFILE")
        COUNTER=$((COUNTER + 1))
    else
        COUNTER=1
    fi
    echo "$COUNTER" > "$STATEFILE"
    
    # Heartbeat log (every 6th run)
    if (( COUNTER % HEARTBEAT_INTERVAL == 0 )); then
        log "Heartbeat: IB Gateway watchdog running (check #${COUNTER})"
    fi
    
    # Check Atlas IB Gateway (live trading)
    ATLAS_DOWN=false
    if ! check_container "atlas-ibgateway"; then
        log "⚠️  Atlas IB Gateway container not running"
        ATLAS_DOWN=true
    elif ! check_port 4001; then
        log "⚠️  Atlas IB Gateway port 4001 unreachable"
        ATLAS_DOWN=true
    fi
    
    if [[ "$ATLAS_DOWN" == "true" ]]; then
        send_alert "🟡 <b>IB Gateway DOWN - Atlas Live</b>%0A%0AContainer: atlas-ibgateway%0APort: 4001%0AAction: Attempting restart..."
        restart_container "atlas-ibgateway"
        verify_restart "atlas-ibgateway" 4001
    fi
    
    # Check Cronus IB Gateway (paper trading)
    CRONUS_DOWN=false
    if ! check_container "cronus-ibgateway-paper"; then
        log "⚠️  Cronus IB Gateway container not running"
        CRONUS_DOWN=true
    elif ! check_port 4002; then
        log "⚠️  Cronus IB Gateway port 4002 unreachable"
        CRONUS_DOWN=true
    fi
    
    if [[ "$CRONUS_DOWN" == "true" ]]; then
        send_alert "🟡 <b>IB Gateway DOWN - Cronus Paper</b>%0A%0AContainer: cronus-ibgateway-paper%0APort: 4002%0AAction: Attempting restart..."
        restart_container "cronus-ibgateway-paper"
        verify_restart "cronus-ibgateway-paper" 4002
    fi
    
    # All good
    if [[ "$ATLAS_DOWN" == "false" && "$CRONUS_DOWN" == "false" ]]; then
        if (( COUNTER % HEARTBEAT_INTERVAL == 0 )); then
            log "✅ Both IB Gateway instances healthy (Atlas:4001, Cronus:4002)"
        fi
    fi
}

# Run with error handling
if ! main; then
    log "ERROR: Watchdog encountered an error"
    exit 1
fi
