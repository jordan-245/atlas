#!/bin/bash
# Atlas Research Window — per-universe sweep + optional LLM loop
# Usage: research_window_universe.sh <universe>
# Called by atlas-research-window@<universe>.service (systemd templated unit)

set -euo pipefail

UNIVERSE="${1:-}"
if [ -z "$UNIVERSE" ]; then
    echo "ERROR: universe argument required" >&2
    exit 2
fi

# Trap SIGTERM from systemd
cleanup() {
    echo "$(date -Iseconds) SIGTERM received ($UNIVERSE) — killing child processes" | tee -a "${LOGFILE:-/dev/null}"
    kill $(jobs -p) 2>/dev/null || true
    wait 2>/dev/null || true
    exit 143
}
trap cleanup SIGTERM SIGINT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT/logs"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
LOGFILE="$LOG_DIR/research_window_${UNIVERSE}_${TIMESTAMP}.log"

mkdir -p "$LOG_DIR"
cd "$PROJECT"

# Per-universe params: hours, workers, do_llm
# Validate the universe name first so unknown universes hit the error branch
# below before the missing-active-config skip can mask a typo as success.
case "$UNIVERSE" in
    sp500)           HOURS=1.0;  WORKERS=3; DO_LLM=1; SWEEP_TIMEOUT=4200 ;;
    commodity_etfs)  HOURS=0.5;  WORKERS=2; DO_LLM=1; SWEEP_TIMEOUT=2400 ;;
    sector_etfs|gold_etfs|treasury_etfs|defensive_etfs|crypto)
                     HOURS=0.25; WORKERS=1; DO_LLM=0; SWEEP_TIMEOUT=1200 ;;
    *)
        echo "ERROR: unknown universe '$UNIVERSE'" >&2
        exit 2
        ;;
esac

# Guard: skip known universes with no active config (retired/archived markets).
# #372 fix — non-SP500 timers may still fire after their active configs were
# archived; previously the sweep ran with no enabled strategies and the LLM
# loop produced ResearchSession market mismatches. Detect early and exit 0
# cleanly so systemd treats it as success and the tiny-log sentinel is not
# triggered (we exit before reaching that check). Only reachable for known
# universes — unknown names already exited above with code 2.
ACTIVE_CFG="$PROJECT/config/active/${UNIVERSE}.json"
if [ ! -f "$ACTIVE_CFG" ]; then
    msg="$(date -Iseconds) [$UNIVERSE] SKIPPED -- no active config at $ACTIVE_CFG (universe retired or never enabled)"
    echo "$msg" | tee -a "$LOGFILE"
    exit 0
fi

echo "$(date -Iseconds) [$UNIVERSE] sweep start (hours=$HOURS, workers=$WORKERS, llm=$DO_LLM)" | tee -a "$LOGFILE"

timeout --signal=TERM --kill-after=60 "$SWEEP_TIMEOUT" python3 research/autoresearch_nightly.py \
    --universe "$UNIVERSE" \
    --market "$UNIVERSE" \
    --hours "$HOURS" \
    --workers "$WORKERS" \
    >> "$LOGFILE" 2>&1 || true

echo "$(date -Iseconds) [$UNIVERSE] sweep done" | tee -a "$LOGFILE"

if [ "$DO_LLM" = "1" ]; then
    echo "$(date -Iseconds) [$UNIVERSE] checking Pi CLI for LLM loop" | tee -a "$LOGFILE"
    if python3 scripts/claude_auth_check.py >> "$LOGFILE" 2>&1; then
        echo "$(date -Iseconds) [$UNIVERSE] starting LLM loop (25 min)" | tee -a "$LOGFILE"
        python3 research/llm_loop_runner.py \
            --minutes 25 \
            --universe "$UNIVERSE" \
            >> "$LOGFILE" 2>&1 || true
        echo "$(date -Iseconds) [$UNIVERSE] LLM loop done" | tee -a "$LOGFILE"
    else
        echo "$(date -Iseconds) [$UNIVERSE] LLM SKIPPED — Pi CLI not available" | tee -a "$LOGFILE"
    fi
fi

echo "$(date -Iseconds) [$UNIVERSE] research window finished" | tee -a "$LOGFILE"

# Sanity check: warn if log is suspiciously small (silent failure indicator)
LOG_SIZE=$(stat -c%s "$LOGFILE" 2>/dev/null || echo 0)
if [ "$LOG_SIZE" -lt 500 ]; then
    echo "$(date -Iseconds) [$UNIVERSE] WARN: log file is tiny (${LOG_SIZE} bytes) — possible silent failure" | tee -a "$LOGFILE"
    python3 -c "from utils.telegram import send_message; send_message('⚠️ research_window: $UNIVERSE produced near-empty log (${LOG_SIZE}b) — investigate')" 2>/dev/null || true
fi

exit 0
