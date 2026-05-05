#!/usr/bin/env python3
"""Helper: edit scripts/atlas.crontab for consolidation closure.

Removes 12 per-market cron lines for commodity_etfs + sector_etfs,
updates reconcile_ledger line to sp500-only, updates policy comment block.

Idempotent: if lines are already removed, script is a no-op.
Run standalone:  python3 scripts/_consolidation_edit_crontab.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ATLAS_HOME = Path(__file__).resolve().parent.parent
CRONTAB_PATH = ATLAS_HOME / "scripts" / "atlas.crontab"

# ── Lines to REMOVE (matched by regex anchored at start-of-line) ─────────────
# Patterns use .*<unique-identifier> (no space requirement before the id,
# since paths use '/' not ' ' as separator).
REMOVE_PATTERNS: list[str] = [
    r"^2,17,32,47 .*sync_protective_commodity",      # sync_protective_orders --market commodity_etfs
    r"^3,18,33,48 .*sync_protective_sector",          # sync_protective_orders --market sector_etfs
    r"^30 1-7 .*intraday_commodity_etfs",             # intraday_monitor -m commodity_etfs
    r"^32 1-7 .*intraday_sector_etfs",                # intraday_monitor -m sector_etfs
    r"^15 23 .*execute_approved_commodity",           # execute_approved.py -m commodity_etfs
    r"^20 23 .*execute_approved_sector",              # execute_approved.py -m sector_etfs
    r"^0 19 .*/pi-cron\.sh premarket commodity_etfs$",  # pi-cron.sh premarket commodity_etfs
    r"^0 19 .*/pi-cron\.sh premarket sector_etfs$",     # pi-cron.sh premarket sector_etfs
    r"^0 8 .*/pi-cron\.sh postclose commodity_etfs$",   # pi-cron.sh postclose commodity_etfs
    r"^0 8 .*/pi-cron\.sh postclose sector_etfs$",      # pi-cron.sh postclose sector_etfs
    r"^2 9 .*reconcile_commodity",                    # reconcile_positions --market commodity_etfs
    r"^5 9 .*reconcile_sector",                       # reconcile_positions --market sector_etfs
]

# ── Line to REPLACE: reconcile_ledger multi-market -> sp500-only ──────────────
RECONCILE_OLD = re.compile(
    r"^30 9 \* \* 2-6 .* for m in sp500 commodity_etfs sector_etfs"
)
RECONCILE_NEW = (
    "30 9 * * 2-6 /usr/bin/flock -n /tmp/reconcile_ledger.lock bash -c "
    "'cd /root/atlas && timeout 5m python3 scripts/reconcile_ledger.py --market sp500' "
    ">> /root/atlas/logs/reconcile_ledger.log 2>&1"
)

# ── Policy comment block: exact string replacements ───────────────────────────
OLD_POLICY_LIVE = (
    "# LIVE markets (full cron coverage below): sp500, sector_etfs, commodity_etfs"
)
NEW_POLICY_LIVE = (
    "# LIVE markets (full cron coverage below): sp500"
)

# Build research block using join to avoid em-dash (U+2014) encoding issues.
_EM = "\u2014"
OLD_RESEARCH_BLOCK = "\n".join([
    f"# RESEARCH-ONLY markets (NO live cron {_EM} sweep populates SQLite via systemd timers):",
    "#   - crypto         (mode=paper,    live_enabled=false)",
    "#   - gold_etfs      (mode=passive,  live_enabled=false)",
    "#   - treasury_etfs  (mode=passive,  live_enabled=false)",
    "#   - defensive_etfs (mode=passive,  live_enabled=false)",
])
NEW_RESEARCH_BLOCK = "\n".join([
    f"# RESEARCH-ONLY markets (NO live cron {_EM} sweep populates SQLite via systemd timers):",
    "#   - crypto         (mode=paper,    live_enabled=false)",
    f"#   - sector_etfs    (mode=passive,  live_enabled=false)  {_EM} consolidated 2026-05-05",
    f"#   - commodity_etfs (mode=passive,  live_enabled=false)  {_EM} consolidated 2026-05-05",
    "#   - gold_etfs      (mode=passive,  live_enabled=false)",
    "#   - treasury_etfs  (mode=passive,  live_enabled=false)",
    "#   - defensive_etfs (mode=passive,  live_enabled=false)",
])


def edit_crontab(path: Path) -> None:
    """Read, transform, write the crontab file. Idempotent."""
    content = path.read_text(encoding="utf-8")
    original = content

    # ── Step 1: Remove the 12 targeted lines ─────────────────────────────────
    remove_compiled = [re.compile(p) for p in REMOVE_PATTERNS]
    lines = content.splitlines(keepends=True)
    new_lines: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if any(rc.search(stripped) for rc in remove_compiled):
            print(f"  REMOVE: {stripped[:90]}", flush=True)
            removed += 1
            continue
        new_lines.append(line)
    content = "".join(new_lines)
    print(f"  Removed {removed} lines (expected 12).", flush=True)
    if removed != 12:
        print(f"  WARNING: expected 12 removals, got {removed} — verify crontab manually", flush=True)

    # ── Step 2: Replace reconcile_ledger line ─────────────────────────────────
    lines = content.splitlines(keepends=True)
    new_lines = []
    replaced = 0
    for line in lines:
        stripped = line.rstrip("\n").rstrip("\r")
        if RECONCILE_OLD.search(stripped):
            print(f"  REPLACE reconcile_ledger:", flush=True)
            print(f"    OLD: {stripped[:80]}", flush=True)
            print(f"    NEW: {RECONCILE_NEW[:80]}", flush=True)
            new_lines.append(RECONCILE_NEW + "\n")
            replaced += 1
        else:
            new_lines.append(line)
    content = "".join(new_lines)
    print(f"  Replaced {replaced} reconcile_ledger line(s) (expected 1).", flush=True)

    # ── Step 3: Update LIVE policy comment ───────────────────────────────────
    if OLD_POLICY_LIVE in content:
        content = content.replace(OLD_POLICY_LIVE, NEW_POLICY_LIVE, 1)
        print("  UPDATED: LIVE markets policy comment", flush=True)
    elif NEW_POLICY_LIVE in content:
        print("  SKIP (already): LIVE markets policy comment", flush=True)
    else:
        print("  WARN: could not find LIVE policy comment line", flush=True)

    # ── Step 4: Update RESEARCH-ONLY block ───────────────────────────────────
    if OLD_RESEARCH_BLOCK in content:
        content = content.replace(OLD_RESEARCH_BLOCK, NEW_RESEARCH_BLOCK, 1)
        print("  UPDATED: RESEARCH-ONLY block", flush=True)
    elif "consolidated 2026-05-05" in content:
        print("  SKIP (already): RESEARCH-ONLY block", flush=True)
    else:
        print("  WARN: could not find RESEARCH-ONLY block to update", flush=True)

    # ── Write back if changed ─────────────────────────────────────────────────
    if content == original:
        print("  Crontab already up to date (idempotent).", flush=True)
        return

    path.write_text(content, encoding="utf-8")
    print(f"  Written: {path}", flush=True)


if __name__ == "__main__":
    print(f"Editing crontab: {CRONTAB_PATH}", flush=True)
    edit_crontab(CRONTAB_PATH)
    print("Done.", flush=True)
