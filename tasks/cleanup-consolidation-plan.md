# Atlas File Structure Consolidation Plan

## Current State: 30 top-level directories

```
atlas/
├── art/              9 files   184K   Generative art (unrelated to trading)
├── artifacts/        5 files   372K   Chart PNGs from research
├── audit/            1 file     28K   Single audit report
├── backtest/        34 files   1.1M   Engine + results
├── brokers/         19 files   520K   Alpaca broker
├── config/          81 files   936K   Active, candidates, versions, inactive
├── dashboard/       26 files   1.2M   Streamlit dashboard
├── data/          1603 files    51M   Cache, processed, snapshots, monitor
├── docker/           5 files    28K   IBKR docker (DEAD — broker removed)
├── docs/             9 files   204K   Decision docs, audit reports
├── jobs/            19 files    80K   Job run artifacts (atlas_jobs_run)
├── journal/          7 files   1.2M   Decision journal, trade ledger
├── logs/           254 files    28M   All logs
├── markets/          5 files   108K   Market definitions
├── memory/           1 file     12K   SUMMARY.md (overlap with research/brain)
├── monitor/          7 files   140K   Iran/ceasefire monitor
├── pi-package/      31 files   724K   Extensions + skills
├── plans/           15 files   296K   Trade plans
├── research/       619 files   6.2M   Results, experiments, brain, strategies
├── scripts/         87 files   1.5M   CLI, cron, utilities (30 unused)
├── services/         5 files   196K   Dashboard, telegram bot, job server
├── specs/           11 files    48K   Runbook specs
├── strategies/      12 files   380K   Strategy implementations
├── systemd/          4 files    20K   Service files (partial — most in /etc/systemd)
├── tasks/           20 files   288K   Plans, lessons, todo
├── tests/            9 files   124K   Test files
├── universe/         2 files    52K   Ticker universes
└── utils/           13 files   416K   Shared utilities
```

---

## Proposed Consolidation

### DELETE — Dead files/dirs with zero references

| Item | Why | Size |
|------|-----|------|
| `docker/` | IBKR-only, broker removed, container exited 9 days ago | 28K |
| `art/` | Generative art, unrelated to trading system | 184K |
| `data/cache/hk/` | HK market deactivated | 4.2M |
| `data/dividends/` | Empty dir | 0 |
| `data/earnings/` | Empty dir | 0 |
| `data/fred/` | Empty dir | 0 |
| `plans/plan_hk_*.json` (3 files) | HK market deactivated | ~20K |
| `research/vault_archive/` | Old obsidian vault, superseded by research/brain/ | ~100K |
| `monitor/archive/` | Archived copies of active files | ~10K |

### ARCHIVE — Move 30 unused scripts to scripts/archive/

These scripts are not referenced by any cron, systemd, or active code:

```
allocation_comparison.py    backfill_delta.py         backtest.py
anneal.py                   correlation_check.py      data_scientist.py
equity_sweep.py             iran_monitor.py           iran_monitor_update.py
migrate_to_brain.py         news_intel.py             oos_risk_validate.py
position_allocation_research.py
position_allocation_research_parallel.py
principal.py                profile_backtest.py       rebuild_monitor_positions.py
reoptimize_parallel.py      research_fred_features.py run_multioffset.py
sage.py                     sanity_check.py           serve_dashboard.py
task92_portfolio_research.py test_alpaca.py
validate_oos_parallel.py    autoresearch.sh
```

Keep `autoresearch.py` — it's the research loop entry point even if not in cron currently.

### MERGE — Consolidate overlapping directories

#### 1. `memory/` → `research/brain/`
- `memory/SUMMARY.md` (65 lines) overlaps with `research/brain/` (structured knowledge)
- Move to `research/brain/SUMMARY.md`, delete `memory/`
- Update references: `memory/SUMMARY.md` → `research/brain/SUMMARY.md`

#### 2. `specs/` → `docs/runbooks/`
- 11 runbook specs with no direct code references
- Rename to `docs/runbooks/` to group all documentation together

#### 3. `audit/` → `docs/`
- Single file `FULL_AUDIT.md`
- Move to `docs/FULL_AUDIT.md`, delete `audit/`

#### 4. `artifacts/` → `logs/charts/`
- 5 chart PNGs from research reports
- Move to `logs/charts/` (they're output artifacts, not source code)

#### 5. `systemd/` → reconcile with /etc/systemd/system/
- Only 4 files, most service files live in `/etc/systemd/system/`
- These are just backup copies. Move to `docs/systemd/` as reference or delete.

#### 6. `journal/` → flatten
- Contains: `decision_journal.json`, `trade_ledger.json`, `logger.py`, `__init__.py`, 2 stale .md/.json files
- `logger.py` + `__init__.py` = Python module (must stay importable)
- Move stale docs: `hk_initial_backtest.md`, `allocation_research.md/json` → `docs/archive/`

### TRIM — Clean up data accumulation

| Item | Action | Saves |
|------|--------|-------|
| `config/versions/` (31 files) | Keep latest 5, delete rest | ~500K |
| `logs/` (242 files) | Keep last 14 days, archive rest | ~20M |
| `jobs/` (19 files) | Keep last 10, delete rest | ~40K |
| `plans/archive/` (2 files) | Delete (already archived) | ~10K |
| `research/experiments/` (147 files) | Keep — these are the brain's raw data | — |

### FIX — Stale references to update

| Reference | In | Fix |
|-----------|------|-----|
| `"research": "atlas-research"` | `services/job_server.py` | Point to atlas-research-loop (atlas-research skill deleted) |

---

## Result: 22 directories (from 30)

```
atlas/
├── backtest/          Engine + results
├── brokers/           Alpaca broker
├── config/            active/, candidates/ (trim versions/)
├── dashboard/         Streamlit UI
├── data/              cache/sp500, cache/asx, processed, snapshots, position_monitor
├── docs/              All documentation: decisions, audits, runbooks, systemd refs
├── jobs/              Job run artifacts (auto-trimmed)
├── journal/           Decision journal, trade ledger, logger module
├── logs/              All logs + charts (auto-trimmed)
├── markets/           Market definitions
├── monitor/           Iran/ceasefire monitor
├── pi-package/        Extensions + skills (the "nervous system")
├── plans/             Trade plans
├── research/          results/, experiments/, brain/, best/, strategies/, waves/
├── scripts/           Active scripts only (30 archived)
├── services/          Dashboard, telegram bot, job server
├── strategies/        Strategy implementations
├── tasks/             Plans, lessons, todo
├── tests/             Test files
├── universe/          Ticker universes
├── utils/             Shared utilities
└── (no more: art, artifacts, audit, docker, memory, specs, systemd)
```

### Directories removed: 8
- `art/` (deleted), `artifacts/` (→ logs/charts/), `audit/` (→ docs/)
- `docker/` (deleted), `memory/` (→ research/brain/), `specs/` (→ docs/runbooks/)
- `systemd/` (→ docs/systemd/), `monitor/archive/` (deleted)

### Files cleaned: ~60
- 30 unused scripts → archive/
- 26 old config versions → deleted
- 3 HK plan files → deleted
- 129 HK cache files → deleted
- Stale journal docs → docs/archive/

---

## Execution Order

1. **Delete dead dirs**: docker/, art/, data/cache/hk/, empty data dirs, HK plans
2. **Archive unused scripts** (move to scripts/archive/)
3. **Merge overlapping dirs**: memory→brain, specs→docs, audit→docs, artifacts→logs, systemd→docs
4. **Trim accumulation**: config/versions, old logs, old jobs
5. **Fix stale references**: job_server.py research alias
6. **Update skills**: atlas-codebase, atlas-state-queries, atlas-brain (new paths)
7. **Verify**: all crons pass, healthz runs, extensions load, skills discoverable
