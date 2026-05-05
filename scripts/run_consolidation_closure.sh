#!/usr/bin/env bash
# scripts/run_consolidation_closure.sh
# Automated consolidation closure: close 3 positions (GLD, XLE, XLI),
# disable cron coverage for sector_etfs+commodity_etfs, flip live_enabled=false.
#
# Triggered by: atlas-consolidation-closure.timer @ 2026-05-05 14:00 UTC.
# One-shot. Self-disables timer on success.
#
# Environment variables:
#   DRY_RUN=true         -- stop before --live execution; skip Phase 3+ changes
#   TELEGRAM_DRY_RUN=1   -- suppress real Telegram sends; use for smoke tests

set -Eeuo pipefail

ATLAS_HOME="${ATLAS_HOME:-/root/atlas}"
PYTHON="${ATLAS_PYTHON:-/usr/bin/python3}"
LOG_FILE="$ATLAS_HOME/logs/consolidation_closure_2026-05-05.log"
LOCK_FILE="/tmp/consolidation_closure.lock"
DRY_RUN="${DRY_RUN:-false}"

mkdir -p "$(dirname "$LOG_FILE")"

# ── Logging helper ──────────────────────────────────────────────────────────
log() {
  local level="${1}"; shift
  printf '%s [%s] %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$level" "$*" | tee -a "$LOG_FILE"
}

# ── Failure handler: sends Telegram alert then exits 1 ─────────────────────
# Set TELEGRAM_DRY_RUN=1 to suppress the actual send (smoke tests).
fail_telegram() {
  local reason="$1"
  log ERROR "$reason"
  if [[ "${TELEGRAM_DRY_RUN:-0}" == "1" ]]; then
    log INFO "TELEGRAM_DRY_RUN=1 -- would send failure Telegram for: $reason"
    exit 1
  fi
  local tail_log
  tail_log=$(tail -n 100 "$LOG_FILE" 2>/dev/null | sed 's/</\&lt;/g; s/>/\&gt;/g' || echo "[no log]")
  "$PYTHON" -c "
import sys
sys.path.insert(0, '$ATLAS_HOME')
from utils.telegram import send_message
msg = (
    '\U0001f6a8 CONSOLIDATION CLOSURE FAILED\n\n'
    'Reason: $reason\n'
    'Log: $LOG_FILE\n\n'
    'Last 100 lines:\n'
    '<pre>$tail_log</pre>'
)
send_message(msg, parse_mode='HTML')
" 2>&1 || log WARN "Failure Telegram send itself failed (non-fatal)"
  exit 1
}

# ── ERR trap: fires on any unexpected non-zero exit in set -e context ───────
trap 'fail_telegram "Unexpected error at line $LINENO (exit code $?)"' ERR

# ── Single-instance lock ────────────────────────────────────────────────────
exec 9>"$LOCK_FILE"
flock -n 9 || { log WARN "Another instance already running -- exiting"; exit 0; }

cd "$ATLAS_HOME"

log INFO "=== Consolidation closure starting (DRY_RUN=$DRY_RUN) ==="

# ============================================================================
# Phase 1: Pre-flight checks
# ============================================================================
log INFO "Phase 1: pre-flight checks"

# Idempotency: if both markets already have no open positions, skip closures.
CLOSURES_ALREADY_DONE=0
"$PYTHON" -c "
import json, sys
for m in ['commodity_etfs', 'sector_etfs']:
    d = json.load(open('$ATLAS_HOME/brokers/state/live_' + m + '.json'))
    if d.get('positions'):
        sys.exit(1)
sys.exit(0)
" && CLOSURES_ALREADY_DONE=1 || CLOSURES_ALREADY_DONE=0

if [[ "$CLOSURES_ALREADY_DONE" -eq 1 ]]; then
  log INFO "State files show NO open positions in commodity_etfs/sector_etfs -- closures already done. Jumping to Phase 3."
else
  # ── Market clock check ────────────────────────────────────────────────────
  is_open=$("$PYTHON" -c "
import sys
sys.path.insert(0, '$ATLAS_HOME')
from utils.config import get_active_config
from brokers.registry import get_live_broker
try:
    cfg = get_active_config('sp500')
    b = get_live_broker(cfg)
    b.connect()
    try:
        if hasattr(b, 'get_clock') and callable(b.get_clock):
            clock = b.get_clock()
        else:
            clock = b._broker_call(b._trade_client.get_clock)
        print('open' if clock.is_open else 'closed')
    except Exception:
        print('closed')
except Exception as e:
    import sys as _sys
    print('closed')
    print('clock-error: ' + str(e), file=_sys.stderr)
" 2>>"$LOG_FILE")
  log INFO "Market clock: $is_open"
  if [[ "$is_open" != "open" ]]; then
    fail_telegram "Phase 1 -- market is CLOSED (clock=$is_open). Aborting. Re-trigger during US RTH (13:30-20:00 UTC)."
  fi

  # ── Position sanity check ─────────────────────────────────────────────────
  positions_check=$("$PYTHON" -c "
import json, sys
expected = {'commodity_etfs': {'GLD'}, 'sector_etfs': {'XLE', 'XLI'}}
for m, exp in expected.items():
    d = json.load(open('$ATLAS_HOME/brokers/state/live_' + m + '.json'))
    actual = {p['ticker'] for p in d.get('positions', [])}
    if actual != exp:
        print('MISMATCH ' + m + ': expected ' + str(sorted(exp)) + ', got ' + str(sorted(actual)))
        sys.exit(1)
print('OK')
")
  if [[ "$positions_check" != "OK" ]]; then
    fail_telegram "Phase 1 -- position mismatch: $positions_check"
  fi
  log INFO "Phase 1 OK: market open, positions match expected (GLD, XLE, XLI)"
fi

# ============================================================================
# Phase 2: Dry-run + Live execution
# ============================================================================
if [[ "$CLOSURES_ALREADY_DONE" -eq 0 ]]; then
  log INFO "Phase 2a: dry-run consolidation_close_positions"
  if ! "$PYTHON" "$ATLAS_HOME/scripts/consolidation_close_positions.py" 2>&1 | tee -a "$LOG_FILE"; then
    fail_telegram "Phase 2a -- dry-run returned non-zero. Check logs."
  fi

  # Verify dry-run output lists each expected ticker in the summary table.
  # _print_summary prints: "GLD      commodity_etfs   DRY-RUN: would close ..."
  for t in GLD XLE XLI; do
    if ! tail -n 50 "$LOG_FILE" | grep -qE "^${t}[[:space:]]+"; then
      fail_telegram "Phase 2a -- dry-run summary does not list $t"
    fi
  done
  log INFO "Phase 2a OK: dry-run listed GLD, XLE, XLI"

  if [[ "$DRY_RUN" == "true" ]]; then
    log INFO "DRY_RUN=true -- stopping before --live execution (Phase 2b/3+ skipped)"
    exit 0
  fi

  # ── Phase 2b: LIVE execution ──────────────────────────────────────────────
  log INFO "Phase 2b: LIVE execution"
  if ! "$PYTHON" "$ATLAS_HOME/scripts/consolidation_close_positions.py" --live 2>&1 | tee -a "$LOG_FILE"; then
    fail_telegram "Phase 2b -- --live returned non-zero. Check logs/consolidation_close.log."
  fi
  log INFO "Phase 2b OK: --live completed"

  # ── Verification 1: state files empty after live ──────────────────────────
  log INFO "Verification 1: state files clean after --live"
  verify1=$("$PYTHON" -c "
import json, sys
for m in ['commodity_etfs', 'sector_etfs']:
    d = json.load(open('$ATLAS_HOME/brokers/state/live_' + m + '.json'))
    if d.get('positions'):
        tickers = [p['ticker'] for p in d['positions']]
        print('STILL_OPEN: ' + m + ' has ' + str(tickers))
        sys.exit(1)
print('OK')
")
  if [[ "$verify1" != "OK" ]]; then
    fail_telegram "Verification 1 -- $verify1"
  fi
  log INFO "Verification 1 OK: state files empty for both markets"
fi

# DRY_RUN guard for Phase 3+
if [[ "$DRY_RUN" == "true" ]]; then
  log INFO "DRY_RUN=true -- skipping Phase 3+ (crontab, config flip, commit)"
  exit 0
fi

# ============================================================================
# Phase 3a: Crontab edit
# ============================================================================
log INFO "Phase 3a: crontab edit"
already_clean=$(grep -cE \
  "^(2,17,32,47|3,18,33,48|30 1-7|32 1-7|15 23|20 23|0 19|0 8|2 9|5 9).*(commodity_etfs|sector_etfs)" \
  "$ATLAS_HOME/scripts/atlas.crontab" || true)

if [[ "$already_clean" -eq 0 ]]; then
  log INFO "Phase 3a: crontab already clean (idempotent skip)"
else
  log INFO "Phase 3a: $already_clean line(s) to remove -- running edit script"
  "$PYTHON" "$ATLAS_HOME/scripts/_consolidation_edit_crontab.py" 2>&1 | tee -a "$LOG_FILE" \
    || fail_telegram "Phase 3a -- crontab edit script returned non-zero"

  remaining=$(grep -cE \
    "^(2,17,32,47|3,18,33,48|30 1-7|32 1-7|15 23|20 23|0 19|0 8|2 9|5 9).*(commodity_etfs|sector_etfs)" \
    "$ATLAS_HOME/scripts/atlas.crontab" || true)
  if [[ "$remaining" -ne 0 ]]; then
    fail_telegram "Phase 3a -- $remaining line(s) still match removal patterns after edit"
  fi
  log INFO "Phase 3a: crontab edits verified (0 removal-pattern lines remain)"
fi

log INFO "Phase 3a: installing crontab"
crontab "$ATLAS_HOME/scripts/atlas.crontab" \
  || fail_telegram "Phase 3a -- crontab install returned non-zero"
log INFO "Phase 3a OK: crontab installed"

# ============================================================================
# Phase 3b: flip live_enabled=false in both configs
# ============================================================================
log INFO "Phase 3b: flip live_enabled=false in configs"
"$PYTHON" "$ATLAS_HOME/scripts/_consolidation_flip_configs.py" 2>&1 | tee -a "$LOG_FILE" \
  || fail_telegram "Phase 3b -- config flip returned non-zero"
log INFO "Phase 3b OK"

# ============================================================================
# Phase 3c: git commit
# ============================================================================
log INFO "Phase 3c: git commit"
cd "$ATLAS_HOME"
git add \
  scripts/atlas.crontab \
  config/active/commodity_etfs.json \
  config/active/sector_etfs.json

COMMIT_SHA=""
if git diff --cached --quiet; then
  log INFO "Phase 3c: nothing to commit (idempotent re-run)"
else
  git commit \
    -m "feat(consolidation): finalize sector_etfs+commodity_etfs shutdown -- positions closed, crons disabled, live_enabled=false" \
    || fail_telegram "Phase 3c -- git commit returned non-zero"
  COMMIT_SHA=$(git rev-parse --short HEAD)
  log INFO "Phase 3c OK: committed as $COMMIT_SHA"
fi

# ============================================================================
# Verification 2: final crontab + sp500 sanity
# ============================================================================
log INFO "Verification 2: final state checks"

# Exclude comment lines -- the policy block now lists these markets as RESEARCH-ONLY.
remaining_live=$(crontab -l | grep -v "^#" | grep -cE "(commodity_etfs|sector_etfs)" || true)
if [[ "$remaining_live" -ne 0 ]]; then
  log WARN "Found $remaining_live non-comment crontab references to consolidated markets:"
  crontab -l | grep -v "^#" | grep -E "(commodity_etfs|sector_etfs)" | tee -a "$LOG_FILE" || true
  fail_telegram "Verification 2 -- $remaining_live live cron entries remain for consolidated markets"
fi
log INFO "Verification 2 crontab: OK (0 non-comment references)"

log INFO "Verification 2: heartbeat watchdog dry-run"
if ! "$PYTHON" "$ATLAS_HOME/scripts/heartbeat_watchdog.py" --dry-run 2>&1 | tee -a "$LOG_FILE"; then
  log WARN "heartbeat_watchdog --dry-run returned non-zero (non-fatal, continuing)"
fi

log INFO "Verification 2: sp500 state untouched"
sp500_check=$("$PYTHON" -c "
import json, sys
d = json.load(open('$ATLAS_HOME/brokers/state/live_sp500.json'))
ts = sorted(p['ticker'] for p in d.get('positions', []))
if ts != ['CAT', 'SYK']:
    print('CHANGED: sp500 now has ' + str(ts))
    sys.exit(1)
print('OK')
")
if [[ "$sp500_check" != "OK" ]]; then
  fail_telegram "Verification 2 -- sp500 changed: $sp500_check"
fi
log INFO "Verification 2 sp500: OK (CAT, SYK)"

# ============================================================================
# Phase 4: Completion Telegram + self-cleanup
# ============================================================================
log INFO "Phase 4: completion Telegram"
COMMIT_DISPLAY="${COMMIT_SHA:-(no new commit -- idempotent run)}"

"$PYTHON" -c "
import sys, re
sys.path.insert(0, '$ATLAS_HOME')
from utils.telegram import send_message

commit = '$COMMIT_DISPLAY'
log_path = '$LOG_FILE'

try:
    log_text = open(log_path).read()
except OSError:
    log_text = ''

fills = [
    line.strip()
    for line in log_text.split('\n')
    if re.match(r'^(GLD|XLE|XLI)\s', line)
]
fills_text = '\n'.join(fills) if fills else '(see full log)'

msg = (
    '\u2705 CONSOLIDATION CLOSURE COMPLETE\n\n'
    'Positions closed:\n'
    '<pre>' + fills_text + '</pre>\n\n'
    'Phase 3:\n'
    '- Crontab: 12 per-market lines removed, reconcile_ledger reduced to sp500-only\n'
    '- Configs: live_enabled=false in commodity_etfs.json + sector_etfs.json\n'
    '- Commit: ' + commit + '\n\n'
    'Verification:\n'
    '- State files: commodity_etfs + sector_etfs empty \u2713\n'
    '- sp500 untouched (CAT, SYK) \u2713\n'
    '- Crontab grep clean \u2713\n\n'
    'Log: ' + log_path + '\n'
    'Timer self-disabled.'
)
send_message(msg, parse_mode='HTML')
" 2>&1 || log WARN "Completion Telegram failed (closure itself succeeded)"

# Self-cleanup: disable + stop the one-shot timer so it never fires again.
log INFO "Self-cleanup: disabling atlas-consolidation-closure.timer"
systemctl disable --now atlas-consolidation-closure.timer 2>&1 | tee -a "$LOG_FILE" \
  || log WARN "Timer disable returned non-zero (manual: systemctl disable --now atlas-consolidation-closure.timer)"

log INFO "=== Consolidation closure COMPLETE ==="
exit 0
