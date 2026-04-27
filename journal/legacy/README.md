# Legacy journal files (deprecated 2026-04-28 — Wave D1)

JSON files in this directory are **historical snapshots** preserved for audit
and rollback purposes only. They are NOT live and are NOT updated.

## What was deprecated

| File | Replaced by | Replaced when |
|------|-------------|---------------|
| `trade_ledger.json` | SQLite `trades` table (`data/atlas.db`) | 2026-04-28 (Wave D1) |
| `decision_journal.json` | SQLite `signals` table (`data/atlas.db`) | 2026-04-28 (Wave D1) |

SQLite has been the source of truth for these tables since the 2026-03 dual-write
migration. The dual-write gate passed 5/5 consecutive cron checks. Wave D1 retires
the JSON writers and moves the historical files here.

## Rollback procedure

If a critical bug is discovered post-cutover and JSON dual-write must be restored:

```bash
bash scripts/dual_write_d1_rollback.sh <timestamp_dir>
# e.g.
# bash scripts/dual_write_d1_rollback.sh 20260427_160601
```

The rollback script restores files from this directory and prints `git revert`
instructions for the code-side changes.

## What is NOT here (deferred to Wave D2)

Broker state JSON files (`brokers/state/live_*.json`) carry `halt`, `halt_reason`,
`daily_high_water`, and `equity_history` fields with **no SQLite equivalent yet**.
They remain authoritative until Wave D2 builds the missing schema.
