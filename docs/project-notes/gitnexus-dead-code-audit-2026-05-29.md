# GitNexus Dead-Code Audit — 2026-05-29

## Scope

Conservative cleanup pass requested by operator: remove dead/unused code using GitNexus while avoiding live trading paths.

The repository already had a large dirty working tree, so this pass avoided broad refactors and targeted only code/artifacts already quarantined in archive/attic areas with no active importers.

## GitNexus checks

- `npx gitnexus status` — index up to date before audit.
- `npx gitnexus query "dead unused archived scripts cleanup attic"` — surfaced archived/attic code and dead-import guard tests.
- Cypher audit for incoming references to `_attic/2026-05/` found only skipped tests referencing `backfill_orphan_trades.py`; those skipped tests were removed.
- Cypher audit for incoming references to `scripts/tools/archive/` found no active importers, only containment edges.
- Re-ran `npx gitnexus analyze` after cleanup.
- Final Cypher audit showed no active import/call edges into removed archive/attic code.

## Removed

- Permanently removed `_attic/2026-05/` contents after dwell period, leaving only `_attic/2026-05/README.md` as a marker.
- Removed obsolete skipped tests that referenced attic-only scripts:
  - `TestBackfillOrphanTradesIdempotent` from `tests/test_dual_write_leak_regression.py`
  - `TestBackfillRefusesInvertedStop` from `tests/test_trade_invariants.py`
  - `test_rollback_script_bash_syntax_valid` from `tests/test_journal_d1_cutover.py`
- Permanently removed old top-level `scripts/tools/archive/*.py` scripts archived on 2026-04-29 after GitNexus confirmed no active importers.
- Kept `scripts/tools/archive/2026-05-repo-reset/` because it is a newer May-26 archive batch and should complete its own dwell review.

## Verification

- `python3 -m pytest -q tests/test_no_dead_imports.py tests/test_trade_invariants.py tests/test_dual_write_leak_regression.py tests/test_journal_d1_cutover.py` → 34 passed, 1 skipped.
- Combined regression bundle with recent plan/remediation/overlay tests → 85 passed, 1 skipped.
- GitNexus final audit: only containment edges remain for archive marker/readme paths; no active runtime/test importers into removed code.

## Notes

- `gitnexus detect-changes` still reports critical overall risk because the working tree already contains many unrelated uncommitted changes across dashboard, services, research, and Atlas runtime defaults. That risk is not attributable solely to this dead-code cleanup pass.
- Recovery for removed attic/archive files is via git history, not by restoring from the working tree.
