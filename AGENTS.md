# Atlas — agent guide

Execution + dashboard system. Runs the survivors Crucible forges: paper (shadow) → human-gated
real capital, via broker adapters; serves the dashboard. Branch: `main`.

How to work here: see [`.pi/APPEND_SYSTEM.md`](.pi/APPEND_SYSTEM.md) — **simplest form, subtract
before you add, leave the tree more findable than you found it.** This file is the repo map and
the rules you must not break.

## Memory
- **Read `memory/SUMMARY.md` at the start of every session.**
- After any correction, discovery, or decision, update it.
- Keep it under 100 lines — consolidate repeated patterns into single rules; don't append endlessly.

## Where things live
| Path | What |
|---|---|
| `atlas/` | The Python package. Layered **`kernel` ← `db` ← `brokers` ← `execution` ← `dashboard`** (one direction). |
| `atlas/kernel/` | Config, `notify` (outbound Telegram), primitives. |
| `atlas/db/` | SQLite (`atlas.db`): trades, returns, books. |
| `atlas/brokers/` | Broker adapters (`alpaca`, `ib`, `ib_web`), selected dynamically by name. |
| `atlas/execution/` | **The capital path:** `kill_switch`, `registry` (deploy gate), `daily` (loop), `providers.deploy_pass` (Crucible seam), `reconcile_books`, `track_expectation`. |
| `atlas/dashboard/` | FastAPI app (`uvicorn atlas.dashboard.app:app`, :8899), `static/`, `chat/pi_session.py` (the sole pi call site). |
| `config/` | Live config (`live_strategies.json`, `active/sp500.json`, `price_arbiter.json`). Runtime caches are gitignored. |
| `dashboard-ui/` | Vite/TS frontend (builds to `dist/`). |
| `pi-package/` | **Live** Pi extension package (`atlas-ops`: risk-gates, elastic-agents). |
| `ceo-board/` | Governance briefs/debates (prose — the board decisions of record). |
| `ops/` | Shell entry points (`forward-paper.sh`, `ib-gateway/`, `cleanup_sediment.py`). |
| `systemd/` | Units; `install.sh` durably retires removed ones. |
| `scripts/` | Lint + hygiene (`lint_bare_except.py`, `lint_pi_system_prompt.py`, `git-hooks/check_no_runtime_artifacts.py`). |
| `tests/` | pytest (importlib mode; `slow` marker). |
| `memory/SUMMARY.md` | Session memory — read first. |
| `docs/` | Runbooks (`OPERATIONS.md`, `ARCHITECTURE.md`, `DISASTER_RECOVERY.md`). |

For deep navigation / blast-radius, this repo is GitNexus-indexed — see the GitNexus section below.

## Commands
```bash
pip install -r requirements.txt
pre-commit install                          # set up the lint hooks
pytest                                      # full suite (importlib mode, per pytest.ini)
pytest -m "not slow"                        # skip backtest / network tests
pre-commit run --all-files                  # bare-except + --system-prompt + runtime-artifact lints
python -m atlas.execution.kill_switch status|halt|resume    # the trading kill switch
python -m atlas.execution.registry state|approve <name>     # deploy registry (the human capital gate)
uvicorn atlas.dashboard.app:app             # dashboard (atlas-dashboard.service serves it on :8899)
cd dashboard-ui && npm install && npm run build             # frontend
```

## Never break these (invariants)
- **Kill switch.** `atlas/execution/kill_switch.py` L1–L4 is checked **fail-closed inside
  `TargetExecutor` before any order.** Never bypass, weaken, or make it fail-open.
- **Capital is human-gated:** `shadow → canary → live`. A canary/live book stays dry-run until
  `approved == True` AND the loop runs `--mode live`. Approval is a human CLI action
  (`registry approve`). Never auto-approve or add an auto-promote-to-live path.
- **Model seam / Claude Max routing:** every `pi`/`claude` subprocess MUST include
  `--system-prompt` (see the section below). Sole call site: `atlas/dashboard/chat/pi_session.py`.
- **Dependency layering** `kernel ← db ← brokers ← execution ← dashboard` is one-directional.
  It is **enforced in review, not by a linter** — keep it clean by hand; never import "upward".
- **The cross-repo seam from Crucible is frozen:** `config/live_strategies.json`,
  `data/live/<name>/`, `atlas.execution.providers.deploy_pass`. Don't change a file shape unilaterally.

## Conventions
- Two ratchet lint baselines you must respect: `bare_except_baseline.txt` (no NEW bare excepts)
  and `pi_system_prompt_baseline.txt` (every pi call carries `--system-prompt`). Fix the code; don't grow the baseline.
- **Generated/runtime data stays out of git** — `scripts/git-hooks/check_no_runtime_artifacts.py`
  enforces it. Such files belong on disk, not the index; `.gitignore` mirrors the hook's patterns.
- One authoritative doc per topic. `CLAUDE.md` is a pointer to this file — don't duplicate content into it.

## Gotchas
- `pi-package/` is **live** (risk-gates does config rollback; elastic-agents reads
  `config/agent-scale-policy.yaml`) — not vendored clutter.
- `atlas-sp500-flatten` units were **retired** (their script was deleted as a stale-target hazard) — don't resurrect them.
- VPS has 8 cores — fan out compute-heavy work (backtests) across them.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **atlas** (46227 symbols, 76607 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/atlas/context` | Codebase overview, check index freshness |
| `gitnexus://repo/atlas/clusters` | All functional areas |
| `gitnexus://repo/atlas/processes` | All execution flows |
| `gitnexus://repo/atlas/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

## Claude Max routing — CRITICAL

Every `pi`/`claude` CLI subprocess MUST pass `--system-prompt "You are Claude Code, Anthropic's
official CLI for Claude."` — this routes to the Claude Max subscription. Without it, calls route to
pay-per-token "extra usage" and fail with `400 out of extra usage` once credits exhaust. Any
non-empty string works; the Claude Code string is the most future-proof.

Enforced by `tests/test_no_raw_pi_subprocess.py` + the `lint-pi-system-prompt` pre-commit hook.
Sole call site: `atlas/dashboard/chat/pi_session.py` (async streaming, inline flag).

If you see `400 ... out of extra usage`: (1) **first**, grep every `pi`/`claude` subprocess for a
missing `--system-prompt` — the #1 cause; (2) then consider the Max window exhausted, a stray
`Anthropic()` client, or an expired token (`pi login`). Never "fix" it by adding API credits.
