# Vault-as-Brain: Structured Research Memory

## Problem

The research engine and the knowledge base are disconnected:

1. **sweep.py** runs 24/7, grinds parameter grids, writes to `journal.json` + `best/*.json` + `results/*.tsv` — no vault awareness
2. **KNOWLEDGE_BASE.md** is a 427-line monolith that I (the agent) manually maintain — gets stale, burns context to read
3. **vault_writer.py** exists but nothing calls it from the live pipeline
4. **research_daemon.py** has vault integration but is unused — sweep.py replaced it
5. **build_obsidian_vault.py** is a 1,431-line batch rebuilder — generates Obsidian notes from journal.json after the fact

Result: the agent has no structured memory. Every session starts by reading KNOWLEDGE_BASE.md (full context dump) or grepping journal.json (5,726 lines). Findings from sweep.py never reach the vault. The vault is a dead artifact.

## Design: Directory-Structured Brain

Replace the monolithic KNOWLEDGE_BASE.md with a **directory tree** where each file is small, focused, and independently loadable. The sweep engine writes to it in real-time. Agents navigate the tree by reading index files and drilling into what's relevant.

### Inspired by autoresearch

Keep the core loop: `propose → run → evaluate → keep/discard → write findings`. The vault is the **shared state** between the mechanical sweeper and the LLM agent. The sweeper writes structured findings; the agent reads them, reasons, and queues new work.

### Directory Structure

```
research/brain/
├── INDEX.md                        # 30-line overview: what's here, how to navigate
├── state.json                      # Machine-readable system state (replaces "System State" section)
│
├── strategies/                     # One file per strategy — the source of truth
│   ├── _index.md                   # Table: strategy, status, best_sharpe, trades, last_updated
│   ├── mean_reversion.md           # Current params, metrics, history, open questions
│   ├── trend_following.md
│   ├── opening_gap.md
│   └── ...
│
├── params/                         # What we've learned about each parameter
│   ├── _index.md                   # Table: param, optimal, range_tested, confidence
│   ├── rsi_period.md               # Findings: RSI(14) optimal, tested 5-21, confident
│   ├── risk_per_trade.md           # Findings: 0.35% optimal, cliff at 0.40%
│   └── ...
│
├── patterns/                       # Confirmed rules — never violate
│   ├── _index.md                   # Table: pattern, status, impact
│   ├── fee_drag.md
│   ├── position_contention.md
│   ├── etf_adaptation_fails.md
│   └── ...
│
├── experiments/                    # Recent experiment results (rolling window)
│   ├── _index.md                   # Table: last 50 experiments, verdict, strategy, delta
│   ├── 20260312_mr_rsi7.md         # Individual experiment detail
│   └── ...
│
├── decisions/                      # Closed decisions — don't revisit
│   ├── _index.md                   # Table: decision, date, rationale
│   ├── sma200_promoted.md
│   ├── risk_035_promoted.md
│   ├── vix_filter_closed.md
│   └── ...
│
├── hypotheses/                     # Open questions to test
│   ├── _index.md                   # Table: hypothesis, status, priority
│   ├── allocation_pools.md
│   └── ...
│
├── sweeps/                         # Sweep session summaries (auto-written by sweep.py)
│   ├── _index.md                   # Table: date, strategies_swept, improvements, runtime
│   ├── 20260312_0930.md            # Per-session summary: what changed, what didn't
│   └── ...
│
└── regime/                         # Market regime analysis
    ├── current.md                  # Latest regime state + implications
    ├── equity_scaling.md           # How edge scales with capital
    └── per_regime_performance.md   # Bull/neutral/bear strategy breakdown
```

### Key Principles

1. **Agent reads `INDEX.md` first** — 30 lines, tells you where everything is, what's changed recently
2. **`_index.md` in each directory** — summary table, never more than 50 lines. Agent decides whether to drill deeper.
3. **One file per concept** — strategy, parameter, pattern, decision. Never a monolith.
4. **`state.json` for machines** — sweep.py reads/writes this for current best params, metrics, queue state. No markdown parsing.
5. **Sweep.py writes directly** — after each kept/discarded result, updates the relevant strategy file and sweep session log. No batch rebuild.
6. **Rolling window for experiments** — keep last 100 experiment files. Older ones get summarized into strategy cards, then deleted.
7. **Agents navigate by drilling** — read INDEX → read strategies/_index.md → read strategies/mean_reversion.md. Only load what's needed.

### What Changes in the Code

#### sweep.py (the mechanical body)
After each keep/discard decision:
1. Update `brain/strategies/{strategy}.md` — append result to history section, update metrics
2. Update `brain/params/{param}.md` — record what value was tested and outcome
3. Update `brain/sweeps/{session}.md` — append one-line result
4. Update `brain/state.json` — current best params/metrics (already does this via `save_best()`)

New: at end of each sweep session:
5. Rebuild `brain/strategies/_index.md` from all strategy files
6. Rebuild `brain/sweeps/_index.md`
7. Update `brain/INDEX.md` last-updated timestamp

This is ~100 lines of code in sweep.py. No LLM needed.

#### Agent (the brain)
At session start:
1. Read `brain/INDEX.md` (30 lines) — get orientation
2. Read `brain/strategies/_index.md` — which strategies have open questions
3. Drill into specific strategy/param files as needed

When making decisions:
1. Write to `brain/decisions/` — rationale and evidence
2. Update `brain/patterns/` if new pattern discovered
3. Queue new hypotheses in `brain/hypotheses/`

#### Delete / Deprecate
- `build_obsidian_vault.py` — replaced by real-time writes
- `research/vault/` (old Obsidian directory) — archived, brain/ takes over
- `KNOWLEDGE_BASE.md` — replaced by brain/INDEX.md + directory structure
- `research_daemon.py` — sweep.py absorbs its vault-write responsibilities
- `vault_writer.py` — replaced by simpler, focused write functions in brain/writer.py

### Migration

1. Build `brain/writer.py` — focused module: ~200 lines, functions like `update_strategy()`, `record_experiment()`, `update_param_insight()`, `write_sweep_session()`
2. Seed `brain/` from current KNOWLEDGE_BASE.md + best/*.json + journal.json — one-time migration script
3. Wire `brain/writer.py` into sweep.py at the keep/discard decision points
4. Verify: run one sweep session, check brain/ files are written correctly
5. Update the agent skills (atlas-research, atlas-research-loop) to read from brain/ instead of KNOWLEDGE_BASE.md
6. Archive old vault/ and KNOWLEDGE_BASE.md
7. Update INDEX.md with final structure

### Non-Goals (keep it simple)

- ❌ No Obsidian-specific features (graph view, backlinks syntax). Plain markdown.
- ❌ No LLM in the write path. Sweep.py writes mechanically; agent reads and reasons.
- ❌ No separate daemon. Sweep.py does everything.
- ❌ No database. Files are the database. JSON for machine state, MD for human/agent reading.
- ❌ No real-time agent wake-up. Agent reads brain/ at session start, not on file watch.

### Cost

- `brain/writer.py`: ~200 lines new
- `sweep.py` changes: ~50 lines (add write calls after keep/discard)
- Migration script: ~150 lines (one-time, extract from journal.json + best/)
- Agent skill updates: minor (change file paths in SKILL.md files)
- Delete: ~3,000 lines (vault_writer.py + build_obsidian_vault.py + research_daemon.py)

Net: **-2,600 lines**, simpler architecture, live-updated brain.
