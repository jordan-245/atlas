# Atlas Attic — 2026-05 Cleanup Wave

The 2026-05 attic dwell period has ended. Archived code/data artifacts were permanently removed on 2026-05-29 after GitNexus and text-reference audits found no active runtime importers.

## Recovery
- Use git history, not the working tree:
  - Single file: `git checkout pre-cleanup-2026-05-12 -- <original-path>` when the original path is known.
  - Attic snapshot: `git checkout HEAD^ -- _attic/2026-05/<dir>/<file>` from the commit before permanent removal.
- Do not restore attic content directly into active paths without repeating impact analysis and running the relevant tests.

## Policy
- New cleanup waves should use a new dated attic directory and a bounded dwell period.
- During dwell, active code must not import attic files.
- After dwell, delete artifacts rather than accumulating a permanent second codebase.
