# Atlas Git Hooks

This directory contains the canonical version of Atlas git hooks.
The live hooks live under `.git/hooks/` (not tracked by git).

## Install

```bash
bash scripts/install-git-hooks.sh
```

This copies `scripts/git-hooks/pre-commit` → `.git/hooks/pre-commit` and marks it executable.

## What the pre-commit hook does

1. **Secret file block** — prevents committing `.env`, `.secrets.json`, `atlas-secrets`
2. **Credential pattern scan** — blocks obvious credential patterns in staged Python/JSON/YAML
3. **Bash syntax check** — runs `bash -n` on staged `.sh` files
4. **Python syntax check** — runs `python3 -m py_compile` on staged `.py` files
5. **Config/active research gate** _(added 2026-05-06, Rec 1.6; fixed #319 2026-05-11)_ — blocks
   direct edits to `config/active/*.json` unless:
   - A matching `auto_promote()` log entry exists within the last 24h, OR
   - `BYPASS_RESEARCH_GATE` env var is set (see below), OR
   - You pass `--no-verify` (git-traced override)

## Config/active gate rationale

The `config/active/*.json` files control which strategies run live with real money.
Historically these were edited by hand, bypassing the research-to-promotion pipeline.
The gate ensures every production config change has an `auto_promote()` audit trail
in `config/promotion_log.json`.

## Legitimate bypasses

**Env var (recommended):**
```bash
BYPASS_RESEARCH_GATE="emergency rollback" git commit -m "chore: roll back sector_etfs config"
BYPASS_RESEARCH_GATE="infra only, no param change" git commit -m "chore: update comment in sp500.json"
```

**--no-verify (last resort):**
```bash
git commit --no-verify -m "..."   # skips ALL hook checks; git records this in reflog
```

> **Note (#319, 2026-05-11):** An in-commit-message marker (e.g. `BYPASS_RESEARCH_GATE: reason`
> in `-m "..."`) does **not** work. Git does not write `.git/COMMIT_EDITMSG` before the
> pre-commit hook runs, so the marker cannot be read. Use the env var approach instead.

## Fresh-clone setup

```bash
git clone <repo>
cd atlas
bash scripts/install-git-hooks.sh
```
