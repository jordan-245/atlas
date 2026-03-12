#!/usr/bin/env python3
"""Backfill delta_vs_baseline on research journal entries.

For each entry without delta_vs_baseline, computes delta relative to:
- The active config baseline metrics (from the first solo baseline run of that strategy)
- For "combined" strategy entries, uses first combined entry as reference

Populates: sharpe, cagr_pct, max_drawdown_pct, total_trades, profit_factor, win_rate_pct
"""

import json
import copy
from pathlib import Path
from collections import defaultdict

JOURNAL_PATH = Path(__file__).resolve().parent.parent / "research" / "journal.json"
ACTIVE_CONFIG = Path(__file__).resolve().parent.parent / "config" / "active" / "sp500.json"

DELTA_FIELDS = ["sharpe", "cagr_pct", "max_drawdown_pct", "total_trades", "profit_factor", "win_rate_pct", "sortino"]


def load_journal():
    with open(JOURNAL_PATH) as f:
        return json.load(f)


def save_journal(entries):
    with open(JOURNAL_PATH, "w") as f:
        json.dump(entries, f, indent=2, default=str)


def get_baseline_metrics(strategy: str, entries: list) -> dict:
    """Find the best baseline reference metrics for a strategy.
    
    Strategy: use the first entry for that strategy that has non-zero metrics.
    If the strategy is "combined" or "unknown", use the first combined-portfolio entry.
    """
    candidates = [e for e in entries if (e.get("strategy") or "unknown") == strategy]
    
    for e in candidates:
        km = e.get("key_metrics", {})
        # Need at least a sharpe value and some trades
        sharpe = km.get("sharpe", 0) or 0
        trades = km.get("total_trades", 0) or 0
        if trades > 0 or abs(sharpe) > 0.001:
            return km
    
    return None


def compute_delta(entry_metrics: dict, baseline_metrics: dict) -> dict:
    """Compute delta between entry and baseline metrics."""
    delta = {}
    for field in DELTA_FIELDS:
        e_val = entry_metrics.get(field, 0) or 0
        b_val = baseline_metrics.get(field, 0) or 0
        diff = round(e_val - b_val, 4)
        if abs(diff) > 0.0001:
            delta[field] = diff
    return delta


def main():
    entries = load_journal()
    
    # Group entries by strategy to find baselines
    strat_entries = defaultdict(list)
    for e in entries:
        strat = e.get("strategy") or "unknown"
        strat_entries[strat].append(e)
    
    # Get baseline for each strategy
    baselines = {}
    for strat, group in strat_entries.items():
        bl = get_baseline_metrics(strat, entries)
        if bl:
            baselines[strat] = bl
            print(f"  Baseline for {strat}: sharpe={bl.get('sharpe', 0):.4f}, trades={bl.get('total_trades', 0)}")
        else:
            print(f"  ⚠ No baseline found for {strat}")
    
    # Backfill
    updated = 0
    skipped = 0
    already_has = 0
    
    for e in entries:
        # Skip entries that already have delta
        existing_delta = e.get("delta_vs_baseline", {})
        if existing_delta and existing_delta != {}:
            already_has += 1
            continue
        
        strat = e.get("strategy") or "unknown"
        km = e.get("key_metrics", {})
        
        # Skip entries with no meaningful metrics
        sharpe = km.get("sharpe", 0) or 0
        trades = km.get("total_trades", 0) or 0
        if trades == 0 and abs(sharpe) < 0.001:
            skipped += 1
            continue
        
        # Skip if no baseline for this strategy
        if strat not in baselines:
            skipped += 1
            continue
        
        baseline = baselines[strat]
        
        # Don't compute delta for the baseline entry itself
        if km is baseline:
            skipped += 1
            continue
        
        delta = compute_delta(km, baseline)
        if delta:
            e["delta_vs_baseline"] = delta
            updated += 1
        else:
            skipped += 1
    
    print(f"\n  Results: {updated} updated, {already_has} already had delta, {skipped} skipped")
    print(f"  Total entries: {len(entries)}")
    
    # Save
    save_journal(entries)
    print(f"  ✅ Saved to {JOURNAL_PATH}")
    
    # Verify
    with open(JOURNAL_PATH) as f:
        verify = json.load(f)
    with_delta = sum(1 for e in verify if e.get("delta_vs_baseline") and e["delta_vs_baseline"] != {})
    print(f"  Verification: {with_delta}/{len(verify)} entries now have delta_vs_baseline ({with_delta/len(verify)*100:.0f}%)")


if __name__ == "__main__":
    main()
