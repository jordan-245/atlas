#!/usr/bin/env bash
# Pre-commit hook: blocks enabling strategies that lack a LIVE/PAPER lifecycle row.
#
# Called from:
#   - scripts/git-hooks/pre-commit  (chained at end of raw bash hook)
#   - .pre-commit-config.yaml local hook (pre-commit framework path)
#
# Bypass:
#   git commit --no-verify                 (skips ALL hooks)
#   BYPASS_RESEARCH_GATE="reason" git commit  (bypasses config/active guards)
set -euo pipefail

PROJECT_ROOT="$(git rev-parse --show-toplevel)"
DB_PATH="${PROJECT_ROOT}/data/atlas.db"
GUARD_SCRIPT="${PROJECT_ROOT}/scripts/git-hooks/check_lifecycle_for_enabled.py"

# Honour the same bypass env-var used by the research gate
if [ -n "${BYPASS_RESEARCH_GATE:-}" ]; then
    echo "⚠️  lifecycle-enabled guard skipped — BYPASS_RESEARCH_GATE is set: ${BYPASS_RESEARCH_GATE}"
    exit 0
fi

# Get staged config/active/*.json files
STAGED=$(git diff --cached --name-only --diff-filter=ACM | grep -E '^config/active/[^/]+\.json$' || true)
if [ -z "$STAGED" ]; then
    exit 0
fi

# Use python helper to check each staged file
# shellcheck disable=SC2086
python3 "${GUARD_SCRIPT}" "${DB_PATH}" ${STAGED}
