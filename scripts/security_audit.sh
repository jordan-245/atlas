#!/bin/bash
# Atlas Security Audit — run periodically to verify credential hygiene
set -uo pipefail

echo "╔══════════════════════════════════════════════════════╗"
echo "║         SECURITY AUDIT — Atlas Credentials          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo ""

PASS=0
FAIL=0

pass() { echo "   ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "   ❌ $1"; FAIL=$((FAIL + 1)); }
warn() { echo "   ⚠️  $1"; }

# 1. Plaintext password scan
echo "1. Plaintext password scan"
DIRS="/root/atlas/ /root/.atlas-secrets.json /tmp/"
FOUND=$(grep -rl "APCA-API-SECRET-KEY\|alpaca_secret" $DIRS 2>/dev/null \
    | grep -v __pycache__ | grep -v ".git/" | grep -v "security_audit" \
    | grep -v "secrets.py" | grep -v "secrets.json" || true)
if [ -z "$FOUND" ]; then
    pass "No leaked credentials found"
else
    fail "Credential leakage in: $FOUND"
fi

echo ""

# 2. File permissions
echo "2. File permissions"
check_perms() {
    local FILE=$1 EXPECTED=$2
    if [ -e "$FILE" ]; then
        ACTUAL=$(stat -c "%a" "$FILE" 2>/dev/null)
        if [ "$ACTUAL" = "$EXPECTED" ]; then
            pass "$FILE ($ACTUAL)"
        else
            fail "$FILE is $ACTUAL, should be $EXPECTED"
        fi
    fi
}
check_perms "/root/.atlas-secrets.json" "600"

echo ""

# 3. Secrets file
echo "3. Secrets file"
python3 -c "
import json, sys
errors = 0
with open('/root/.atlas-secrets.json') as f:
    s = json.load(f)
# Check Alpaca keys present
if s.get('ALPACA_API_KEY') and s.get('ALPACA_SECRET_KEY'):
    print('   ✅ Alpaca API keys present')
else:
    print('   ❌ Alpaca API keys missing'); errors += 1
# Check Telegram keys present
if s.get('telegram_bot_token') and s.get('telegram_chat_id'):
    print('   ✅ Telegram credentials present')
else:
    print('   ⚠️  Telegram credentials missing')
sys.exit(errors)
"
PYRET=$?
if [ $PYRET -eq 0 ]; then PASS=$((PASS + 1)); else FAIL=$((FAIL + PYRET)); fi

echo ""

# 4. Git tracking
echo "4. Git tracking"
cd /root/atlas
GIT_BAD=$(git ls-files --cached 2>/dev/null \
    | grep -E "\.env$|secrets\.json|password" \
    | grep -v "example\|secrets\.py\|\.gitignore" || true)
if [ -z "$GIT_BAD" ]; then
    pass "No credential files tracked"
else
    fail "Tracked: $GIT_BAD"
fi

echo ""

# 5. Gitignore coverage
echo "5. Gitignore coverage"
cd /root/atlas
for PATTERN in ".atlas-secrets.json" "*.env"; do
    if grep -q "$PATTERN" .gitignore 2>/dev/null; then
        pass ".gitignore covers $PATTERN"
    else
        warn ".gitignore missing $PATTERN"
    fi
done

echo ""
echo "══════════════════════════════════════════════════════"
echo "  Results: $PASS passed, $FAIL failed"
if [ $FAIL -gt 0 ]; then
    echo "  ⚠️  ACTION REQUIRED — fix failures above"
    exit 1
else
    echo "  ✅ All checks passed"
    exit 0
fi
