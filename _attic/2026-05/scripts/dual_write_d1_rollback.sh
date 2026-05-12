#!/usr/bin/env bash
# Rollback Wave D1 — restore JSON dual-write for journal files.
#
# This script is NOT executable by default. Invoke explicitly with `bash`:
#   bash scripts/dual_write_d1_rollback.sh <timestamp_dir>
#
# Example:
#   bash scripts/dual_write_d1_rollback.sh 20260427_160601
#
# What this does:
#   1. Restores journal/trade_ledger.json and journal/decision_journal.json
#      from journal/legacy/<timestamp_dir>/.
#   2. Reminds you to revert the code-side changes (DecisionJournal._save,
#      TradeLedger._save, healthz.py SQLite query, execution.py fallback,
#      verify_dual_write.py checks list) via `git revert <wave-d1-commit>`.
#   3. Reminds you to verify the next cron run of verify_dual_write.py
#      passes once trades+signals checks are re-enabled.
#
# This script does NOT auto-revert code — that's a manual `git revert` step
# so you can inspect the diff first.

set -euo pipefail

if [ "$#" -ne 1 ]; then
    echo "ERROR: missing timestamp_dir argument"
    echo "Usage: bash $0 <timestamp_dir>"
    echo "Available legacy directories:"
    ls -1 journal/legacy/ 2>/dev/null | grep -v README || echo "  (none)"
    exit 1
fi

TS_DIR="$1"
LEGACY_DIR="journal/legacy/$TS_DIR"

if [ ! -d "$LEGACY_DIR" ]; then
    echo "ERROR: $LEGACY_DIR does not exist"
    exit 1
fi

if [ ! -f "$LEGACY_DIR/trade_ledger.json" ] || [ ! -f "$LEGACY_DIR/decision_journal.json" ]; then
    echo "ERROR: $LEGACY_DIR is missing one or both expected files"
    exit 1
fi

echo "Restoring JSON files from $LEGACY_DIR ..."
cp "$LEGACY_DIR/trade_ledger.json" journal/trade_ledger.json
cp "$LEGACY_DIR/decision_journal.json" journal/decision_journal.json
echo "  OK: journal/trade_ledger.json restored"
echo "  OK: journal/decision_journal.json restored"
echo
echo "MANUAL STEPS REMAINING:"
echo "  1. git revert <wave-d1-commit-sha>   # to restore JSON writes in journal/logger.py"
echo "                                       # and re-enable trades+signals checks in verify_dual_write.py"
echo "                                       # and revert healthz.py + execution.py changes"
echo "  2. systemctl restart atlas-dashboard atlas-telegram-bot   # pick up code changes"
echo "  3. Wait for next cron run (10:00 UTC weekdays) and confirm verify_dual_write.py passes 6/6"
echo
echo "Rollback file restoration complete. Code-side rollback is manual."
