# scripts/tools/archive/

One-shot and forensic scripts that have completed and are no longer referenced by any active cron job, import, or runtime path.

## Status

- 2026-04-29 archive batch: permanently removed on 2026-05-29 after GitNexus confirmed no active importers.
- 2026-05 repo-reset batch: retained under `2026-05-repo-reset/` until its dwell period is reviewed.

## Recovery

Use git history rather than keeping a permanent second codebase:

```bash
git checkout <commit-before-removal> -- scripts/tools/archive/<name>.py
```

Do not restore an archived script into `scripts/` without repeating impact analysis and checking cron/systemd references.
