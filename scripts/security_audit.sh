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
# Build search pattern at runtime to avoid self-matching
SEARCH_PAT="Atl""as20""26"
DIRS="/root/atlas/ /opt/moomoo_OpenD*/ /opt/Futu_OpenD*/ /root/.atlas-secrets.json /tmp/"
FOUND=$(grep -rl "$SEARCH_PAT" $DIRS 2>/dev/null | grep -v __pycache__ | grep -v ".git/" | grep -v "security_audit" || true)
if [ -z "$FOUND" ]; then
    pass "No plaintext password found"
else
    fail "Plaintext password in: $FOUND"
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
check_perms "/opt/moomoo_OpenD_9.6.5618_Ubuntu18.04/moomoo_OpenD_9.6.5618_Ubuntu18.04/OpenD.xml" "600"
check_perms "/root/.com.moomoo.OpenD" "700"
check_perms "/root/.com.moomoo.OpenD/Log" "700"

echo ""

# 3. Secrets file
echo "3. Secrets file"
python3 -c "
import json, sys
with open('/root/.atlas-secrets.json') as f:
    s = json.load(f)
content = json.dumps(s)
errors = 0
if 'Atl' + 'as20' + '26' in content:
    print('   ❌ Plaintext password in secrets!'); errors += 1
md5 = s.get('MOOMOO_LOGIN_PWD_MD5', '')
if len(md5) == 32:
    print(f'   ✅ MD5 hash present ({md5[:6]}...)')
else:
    print(f'   ❌ MD5 hash missing/wrong'); errors += 1
if 'MOOMOO_LOGIN_PWD' in s:  # exact key, not the MD5 variant
    print('   ❌ Plaintext password key exists!'); errors += 1
sys.exit(errors)
"
PYRET=$?
if [ $PYRET -eq 0 ]; then PASS=$((PASS + 2)); else FAIL=$((FAIL + PYRET)); fi

echo ""

# 4. OpenD.xml
echo "4. OpenD.xml"
XML="/opt/moomoo_OpenD_9.6.5618_Ubuntu18.04/moomoo_OpenD_9.6.5618_Ubuntu18.04/OpenD.xml"
if [ -f "$XML" ]; then
    grep -q "login_pwd_md5" "$XML" && pass "Uses MD5 hash" || fail "No MD5 hash"
    grep -q "<login_pwd>" "$XML" && fail "Contains plaintext <login_pwd>" || pass "No plaintext password tag"
fi

echo ""

# 5. Git
echo "5. Git tracking"
cd /root/atlas
GIT_BAD=$(git ls-files --cached 2>/dev/null | grep -E "\.env$|secrets\.json|OpenD\.xml|password" | grep -v "example\|secrets\.py\|\.gitignore" || true)
if [ -z "$GIT_BAD" ]; then
    pass "No credential files tracked"
else
    fail "Tracked: $GIT_BAD"
fi

echo ""

# 6. Network
echo "6. Network binding"
for PORT_NUM in 11111 22222; do
    BIND=$(ss -tlnp 2>/dev/null | grep ":$PORT_NUM " | awk '{print $4}')
    if echo "$BIND" | grep -q "127.0.0.1"; then
        pass "Port $PORT_NUM: localhost only"
    elif [ -z "$BIND" ]; then
        warn "Port $PORT_NUM: not listening"
    else
        fail "Port $PORT_NUM: bound to $BIND"
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
