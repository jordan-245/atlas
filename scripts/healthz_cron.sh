#!/bin/bash
# ═══════════════════════════════════════════════════════════════
# Atlas Daily Health Check — runs before premarket to catch issues
# early. Sends Telegram alert only on warnings or failures.
#
# Cron: 30 18 * * 1-5 (18:30 AEST, 30min before premarket)
# ═══════════════════════════════════════════════════════════════
set -uo pipefail

PROJECT="/root/atlas"
HEALTHZ="$PROJECT/pi-package/atlas-ops/skills/atlas-healthz/atlas-healthz/scripts/healthz.py"
LOG_DIR="$PROJECT/logs"
TIMESTAMP="$(date '+%Y%m%d_%H%M%S')"
LOG_FILE="$LOG_DIR/healthz_${TIMESTAMP}.log"

export TZ="Australia/Brisbane"
export HOME="${HOME:-/root}"

mkdir -p "$LOG_DIR"

# Run health check — human report for log, JSON for Telegram parsing
cd "$PROJECT"
python3 "$HEALTHZ" --market sp500 2>/dev/null > "$LOG_FILE"
EXIT_CODE=$?

# Send Telegram alert if not fully healthy (exit 1=warn, 2=fail)
if [ "$EXIT_CODE" -ne 0 ]; then
    # Run JSON mode and build+send Telegram message entirely in Python
    # (avoids fragile shell variable interpolation that breaks on
    #  special characters or SDK stdout noise)
    python3 "$HEALTHZ" --market sp500 --json 2>/dev/null | python3 -c "
import sys, json

try:
    report = json.load(sys.stdin)
except Exception as e:
    print(f'Failed to parse healthz JSON: {e}', file=sys.stderr)
    sys.exit(1)

sys.path.insert(0, '$PROJECT')
from utils.telegram import send_message

s = report['summary']
overall = s['overall'].upper()
icon = '❌' if s['fail'] > 0 else '⚠️'

# Collect non-ok issues
issues = []
for sec in report['sections'].values():
    for c in sec['checks']:
        if c['verdict'] != 'ok':
            v_icon = '⚠️' if c['verdict'] == 'warn' else '❌'
            issues.append(f'{v_icon} {c[\"check\"]}: {c[\"message\"]}')

lines = [
    f'{icon} <b>Atlas Health Check — {overall}</b>',
    f'✅ {s[\"ok\"]} ok  ⚠️ {s[\"warn\"]} warn  ❌ {s[\"fail\"]} fail',
    '',
]
lines.extend(issues[:15])
lines.append('')
lines.append('<i>Premarket runs in 30 min. Fix issues now.</i>')

send_message('\n'.join(lines))
" 2>>"$LOG_DIR/telegram.log"
fi

# Clean old healthz logs (keep 14 days)
find "$LOG_DIR" -name "healthz_*.log" -mtime +14 -delete 2>/dev/null

exit $EXIT_CODE
