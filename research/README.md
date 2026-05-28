# Atlas Research System

Continuous experiment pipeline + knowledge layer that turns external claims
(papers, blogs) into testable hypotheses, surfaces divergences between
literature and Atlas's own measured results, and promotes the survivors
through `RESEARCH → PAPER → LIVE`.

The system is intentionally two-layered:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  EXPERIMENT LOOP  (research/queue.json, journal.json, experiments/)          │
│  Discover → Queue → Backtest → Evaluate → Promote                            │
└──────────────────────────────────────────────────────────────────────────────┘
                                  ▲           ▲
                  reads contradictions │      │ writes verdicts back
                                  │           │
┌──────────────────────────────────┼───────────┼───────────────────────────────┐
│  KNOWLEDGE LAYER  (SQL: sources, claims, contradictions, lifecycle_history)  │
│  Paper claims ◄══ contradiction sync ══► Atlas measured results               │
└──────────────────────────────────────────────────────────────────────────────┘
                                  │
                                  ▼
                       wiki materializer
                       Telegram digest
                       /api/knowledge/*
```

The experiment loop is the legacy/canonical layer. The knowledge layer was
added in Phases 0-7 of the 2026-05 consolidation (see
[`docs/specs/research-db-consolidation.md`](../docs/specs/research-db-consolidation.md)).

---

## Quick start

```bash
# 1. Apply the schema migration (idempotent).
python3 scripts/migrations/2026-05-28-knowledge-layer.py --apply

# 2. Backfill sources + shell claims from existing papers/specs on disk.
python3 scripts/backfill_knowledge.py --apply

# 3. Backfill historical lifecycle transitions from data/promotion_log.json.
python3 scripts/backfill_lifecycle_history.py --apply

# 4. Run the LLM metric extractor on a few claims (eyeball the output before
#    going bigger -- this is the decision gate from the original plan).
python3 scripts/extract_paper_metrics.py --apply --limit 5

# 5. After upserts and metric extractions, the sync hook auto-runs.  To
#    materialize contradictions across the whole DB manually:
python3 scripts/sync_contradictions.py --apply

# 6. Run the contradiction-driven queue channel.
python3 scripts/run_contradiction_channel.py --apply --limit 10

# 7. Render the SQL knowledge layer into reviewable markdown.
python3 scripts/materialize_wiki.py --apply
```

---

## Directory structure

```
research/
├── README.md                    # This file
├── models.py                    # QueueEntry/ExperimentEnvelope/JournalEntry + locked I/O
├── __init__.py                  # Public API (with Windows-compat guard)
├── queue.json                   # Prioritised experiment queue (CANONICAL today)
├── journal.json                 # Append-only verdict log (CANONICAL today)
├── experiments/                 # exp-<id>.json envelopes (CANONICAL today)
│
├── discovery/                   # Paper discovery + extractors
│   ├── discovery.py             # Daily orchestrator + Telegram digest
│   ├── arxiv_api.py             # Fetch new arxiv papers
│   ├── pdf_vision.py            # Vision-based figure extraction
│   ├── text_summary.py          # Text-based prompt feature builder
│   ├── contradiction_channel.py # Phase 5: contradictions → QueueEntry
│   ├── strategy_universe.py     # Mechanical experiment channels (ablation, etc.)
│   ├── dedup.py                 # URL + strategy-name dedup
│   ├── papers/                  # Downloaded PDFs (kept on disk)
│   ├── specs/                   # specs_*.json (extracted strategy specs)
│   ├── prompts/                 # Prompt templates
│   │   ├── extract.md           # Paper → strategy spec
│   │   ├── extract_metrics.md   # Phase 1.5: paper → claimed metrics
│   │   ├── filter.md
│   │   ├── generate.md
│   │   └── browse_*.md
│   └── extractors/              # Phase 1 + 1.5 extractors
│       ├── paper_metadata.py    # PDF → sources row
│       ├── spec_to_claims.py    # specs_*.json → shell claims
│       └── paper_metrics.py     # LLM → claim metrics (Sharpe, DD, etc.)
│
├── wiki/                        # Phase 7: SQL → markdown materializer
│   ├── __init__.py
│   ├── materializer.py          # Pure read-only, deterministic output
│   ├── overview.md              # Generated
│   ├── contradictions.jsonl     # Generated
│   └── strategies/              # Generated, one .md per (strategy, universe)
│
├── strategies/                  # Sandbox candidate strategy code (not yet promoted)
├── brain/                       # Execution + writer helpers
├── investigations/              # Time-bound research notes
├── best/                        # Per-strategy best JSON (legacy; also in research_best table)
└── snapshots/                   # Nightly JSONL snapshots of recent rows (planned)
```

The `wiki/` directory is generated content — operators commit it so changes
are diff-reviewable in PRs, but it is recreated from SQL on every run.

---

## The experiment loop (canonical)

### Lifecycle states

```
QUEUED → CLAIMED → RUNNING → EVALUATING → PASSED/FAILED/PARTIAL
                                              │
                                         PROMOTED / REJECTED / DEFERRED
```

| Status     | Meaning |
|------------|---------|
| queued     | Waiting for a worker to pick up |
| claimed    | Worker reserved this experiment (prevents double-pickup) |
| running    | Backtest/optimisation actively executing |
| evaluating | Results being analysed by analyst role |
| passed     | Met acceptance criteria |
| failed     | Did not meet acceptance criteria |
| partial    | Some criteria met, interesting but not actionable |
| promoted   | Config promoted to active (after human approval) |
| rejected   | Human rejected the promotion request |
| deferred   | Parked for future reconsideration |

### Queue categories (priority order)

1. **degradation** (P1) — Fix active strategy performance drops
2. **dormant** (P2) — Activate coded-but-unused strategies
3. **param_drift** (P3) — Re-optimise drifted parameters
4. **filter** (P3) — Test new signal filters (VIX, volume, SMA, …)
5. **contradiction** (P3) — *NEW (Phase 5)*: paper claim ≠ measured; retest
6. **new_strategy** (P4) — Develop entirely new strategies
7. **portfolio** (P4) — Portfolio-level construction improvements
8. **cross_market** (P5) — Cross-market correlation signals

### File ownership

| Role       | Reads                                              | Writes                                              |
|------------|----------------------------------------------------|-----------------------------------------------------|
| Researcher | journal, perf, queue, **wiki/overview.md, contradictions.jsonl** | queue.json (append)                                  |
| Backtester | queue.json                                         | experiments/*.json, queue.json (status updates)     |
| Analyst    | experiments/*.json                                 | journal.json (append), experiments/*.json (annotate) |
| Risk       | experiments/*.json, journal                        | config/candidates/*.json, promotion requests        |
| **Discovery extractors** | papers/, specs/                       | **sources, claims** (SQL)                            |
| **Wiki materializer**    | sources, claims, contradictions, lifecycle, journal | **research/wiki/** (markdown files)                  |

### Queue entry schema

```json
{
  "id": "20260227_150000_abc123",
  "title": "contradiction[critical]: donchian_breakout/sp500 sharpe",
  "category": "contradiction",
  "market": "sp500",
  "hypothesis": "Paper 'Turtle Trading Rules' claims sharpe=1.6 ...",
  "method": "single_strategy_test",
  "acceptance_criteria": {"min_sharpe": 1.28, "min_trades": 15},
  "estimated_runtime_min": 20,
  "priority": "P3",
  "status": "queued",
  "strategy_name": "donchian_breakout",
  "params_override": null,
  "tags": ["source:src-arxiv-2401.11111", "claim:clm-1",
           "contradiction:42", "channel:contradiction"],
  "claimed_by": null,
  "claimed_at": null,
  "created_at": "2026-05-28T12:00:00+00:00",
  "updated_at": "2026-05-28T12:00:00+00:00"
}
```

### Key design patterns

- **Multi-agent ready**: all inter-role comms via JSON files or SQL; no shared
  memory. File locking via `fcntl.flock()` (Windows: no-op shim, single-process
  testing only). Append-only journal (no edits, only appends). Experiment
  envelopes are fully self-contained.
- **Dual-write opt-in**: Phase 6 added SQL mirrors (`queue_mirror`,
  `journal_mirror`) behind `ATLAS_KNOWLEDGE_DB_QUEUE` / `ATLAS_KNOWLEDGE_DB_JOURNAL`
  env vars. Default off — JSON files remain canonical.

---

## The knowledge layer (Phases 0-7)

Built to close the loop: papers in → experiments out → results back into a
persistent memory that drives the next cycle.

### Tables

| Table                         | Phase | What it holds                                                                |
|-------------------------------|-------|-------------------------------------------------------------------------------|
| `sources`                     | 0     | One row per paper/blog/doc. sha256-deduped.                                  |
| `claims`                      | 0     | One row per (source, strategy) claim. Shell-first; metrics filled later.     |
| `contradictions`              | 0     | Materialised divergence between a claim and `research_best`. Severity-ranked. |
| `digest_history`              | 0     | One row per Telegram digest send. Drives "since last sent" windowing.        |
| `strategy_lifecycle_history`  | 3*    | Existing audit table, extended with `gate_results` and `experiment_id`.       |
| `queue_mirror`                | 6     | SQL shadow of `queue.json`. Dual-write opt-in.                               |
| `journal_mirror`              | 6     | SQL shadow of `journal.json`. Dual-write opt-in.                             |

\* Phase 3 also dropped the redundant Phase 0 `lifecycle_events` table after
discovering `strategy_lifecycle_history` already existed.

### Views

| View                              | Purpose                                                                |
|-----------------------------------|------------------------------------------------------------------------|
| `v_candidate_contradictions`      | Compute (claim × research_best) diff with severity classification.     |
| `v_open_contradictions`           | Unresolved rows joined to source info. Drives API + Telegram + wiki.   |
| `v_strategy_summary`              | Per-strategy roll-up: metrics, claim counts, lifecycle state.          |

### Closed-loop dataflow

```
                                ┌──────────────────────────────────────┐
                                │  CLAIMS REGISTRY (SQL knowledge)     │
                                │  sources, claims, contradictions     │
                                └─────────────────┬──────────────────┬─┘
                                                  │                  │
                  ┌───────────────────────────────┴──────┐           │
                  │                                      │           │
              Discovery                             Ideation         │
              extractors                            (channels)       │
                  │                                      │           │
                  ▼                                      ▼           │
              papers/ + specs/                     queue.json        │
                                                       │             │
                                                       ▼             │
                                                  Execution          │
                                                  (backtester)       │
                                                       │             │
                                                       ▼             │
                                                  experiments/       │
                                                       │             │
                                                       ▼             │
                                                  Evaluation         │
                                                  (analyst)          │
                                                       │             │
                              ┌─────────── journal.json ─────────────┘
                              │
                              ▼                  hooks fire on every
                       upsert_research_best ──── upsert / update:
                              │                  sync_contradictions()
                              ▼
                       contradictions table (materialised)
                              │
                              ▼
                       Telegram digest +
                       /api/knowledge/* +
                       wiki materializer
```

### Severity thresholds

`v_candidate_contradictions` classifies each (claim, metric) by the absolute
delta from the measured value:

| metric        | minor | major | critical |
|---------------|-------|-------|----------|
| sharpe        | ≥ 0.3 | ≥ 0.5 | ≥ 1.0    |
| max_dd_pct    | ≥ 5   | ≥ 8   | ≥ 15     |

Thresholds live in the view DDL; tune by editing
[`db/schema.sql`](../db/schema.sql) and re-running the migration's
`DROP VIEW + CREATE VIEW` block.

---

## Discovery pipeline

Daily orchestrator: `python3 -m research.discovery.run`.

### Step list

1. **Source rotation** — pick today's source (arxiv / quantpedia / ssrn / blogs)
2. **Fetch / browse** — download PDFs into `papers/`
3. **Filter** — relevance score ≥ 6 (LLM)
4. **PDF vision pre-pass** — figure extraction (`pdf_vision.py`)
5. **Spec extraction** — `prompts/extract.md` → `specs/specs_<date>.json`
6. **Spec dedup** — drop duplicate strategies vs existing
7. **Code generation** — `prompts/generate.md` → `research/strategies/*.py`
8. **Quick check** — fast smoke test
9. **Digest + knowledge enrichment** (Phase 4):
   - Counts contradictions since last digest (`digest_history.sent_at`)
   - Counts lifecycle transitions since last digest
   - Top 3 open contradictions rendered inline
   - `log_digest()` records the send + delivery status

### Extractors (Phase 1 + 1.5)

| Module                                                                    | What it produces                                |
|---------------------------------------------------------------------------|--------------------------------------------------|
| [`extractors/paper_metadata.py`](discovery/extractors/paper_metadata.py)  | `sources` row per PDF (sha256 + arxiv id parse) |
| [`extractors/spec_to_claims.py`](discovery/extractors/spec_to_claims.py)  | shell `claims` row per spec (NULL metrics)      |
| [`extractors/paper_metrics.py`](discovery/extractors/paper_metrics.py)    | Populates `claimed_*` columns via pi CLI         |

Backfill orchestrator: [`scripts/backfill_knowledge.py`](../scripts/backfill_knowledge.py).
LLM extraction CLI: [`scripts/extract_paper_metrics.py`](../scripts/extract_paper_metrics.py).

The LLM extractor uses `utils.pi_subprocess.call_pi` exclusively — which forces
the `--system-prompt` flag that routes to the Claude Max subscription per the
CRITICAL rule in [`CLAUDE.md`](../CLAUDE.md).

### Contradiction-driven channel (Phase 5)

[`research/discovery/contradiction_channel.py`](discovery/contradiction_channel.py)
turns unresolved `major`/`critical` contradictions into `QueueEntry` rows.

**Decay rule**: skip strategies with a `research_experiments` row in the last
30 days. One backtest run answers many contradictions for the same
(strategy, universe) — no need to re-run on every paper that mentions the
same strategy.

CLI: [`scripts/run_contradiction_channel.py`](../scripts/run_contradiction_channel.py)
(cron-friendly).

### Wiki materializer (Phase 7)

[`research/wiki/materializer.py`](wiki/materializer.py) reads the SQL knowledge
layer and writes:

- `research/wiki/overview.md` — top-level counts and by-state breakdown
- `research/wiki/strategies/<strategy>__<universe>.md` — per-pair page with
  measured metrics, open contradictions, recent lifecycle, recent journal
- `research/wiki/contradictions.jsonl` — one line per contradiction,
  severity-sorted for stable diffs

Deterministic output: re-running on unchanged SQL produces byte-identical
files. Commit the directory so PRs surface knowledge-layer changes.

CLI: [`scripts/materialize_wiki.py`](../scripts/materialize_wiki.py).

Note: the plan reserved space for an external `llm-wiki-agent` for prose
synthesis on top. The materializer is the load-bearing piece; layering a
synthesis agent on top is an operator decision after seeing the markdown.

---

## API surfaces

### Existing — `/api/research/*` ([services/api/research.py](../services/api/research.py))

| Route                                  | Purpose                                |
|----------------------------------------|----------------------------------------|
| `GET  /api/research/overview`          | Comprehensive research overview        |
| `GET  /api/research/leaderboard`       | Best strategy/universe combos          |
| `POST /api/research/prioritize`        | Update research priorities             |
| `GET  /api/research/summary`           | Aggregated experiment stats            |
| `GET  /api/research/experiments`       | Paginated experiment list              |
| `GET  /api/research/strategies`        | Per-strategy stats + best params       |
| `GET  /api/research/timeline`          | Daily experiment counts                |
| `GET  /api/research/discoveries`       | Discovery pipeline runs                |
| `GET  /api/research/brain`             | Brain knowledge entries                |
| `GET  /api/research/coverage`          | Strategies × universes coverage matrix |

### New — `/api/knowledge/*` (Phase 4, [services/api/knowledge.py](../services/api/knowledge.py))

All endpoints require HTTP Basic auth via `services.auth.check_auth`.

| Route                                                       | Purpose                                                              |
|-------------------------------------------------------------|----------------------------------------------------------------------|
| `GET  /api/knowledge/contradictions/open`                   | Paginated, severity-ordered. Filters: `severity`, `strategy`, `limit`. |
| `POST /api/knowledge/contradictions/{id}/resolve`           | Body: `{resolution, note}`. Hides the row from open-list.            |
| `GET  /api/knowledge/strategy/{strategy}/summary`           | Single-strategy roll-up + top-N open contradictions.                 |
| `GET  /api/knowledge/sources/{id}`                          | Source row + its claims (active by default).                         |

### Lifecycle API ([services/api/lifecycle.py](../services/api/lifecycle.py))

| Route                                                       | Purpose                                |
|-------------------------------------------------------------|----------------------------------------|
| `GET  /api/lifecycle`                                       | All strategy_lifecycle rows enriched   |
| `GET  /api/lifecycle/{strategy}/{universe}/history`         | Per-pair transition history            |
| `POST /api/lifecycle/transition`                            | Operator transition (graph-enforced)   |
| `POST /api/lifecycle/promote-paper`                         | Manual auto-promote trigger            |

---

## Scripts reference

### Experiment loop

| Script | Role | Purpose |
|--------|------|---------|
| [`strategy_evaluator.py`](../scripts/strategy_evaluator.py) | Backtester | Single strategy evaluation on any market |
| [`research_runner.py`](../scripts/research_runner.py) | Backtester | Experiment execution engine |
| [`reoptimize_parallel.py`](../scripts/reoptimize_parallel.py) | Backtester | Full parameter optimisation |
| [`validate_oos.py`](../scripts/validate_oos.py) | Analyst | Out-of-sample validation suite |
| [`auto_promote_paper_to_live.py`](../scripts/auto_promote_paper_to_live.py) | Risk | PAPER → LIVE promotion gates A-J |
| [`check_live_research_divergence.py`](../scripts/check_live_research_divergence.py) | Monitor | Divergence rollback |

### Knowledge layer (Phases 0-7)

| Script | Phase | Purpose |
|--------|-------|---------|
| [`migrations/2026-05-28-knowledge-layer.py`](../scripts/migrations/2026-05-28-knowledge-layer.py) | 0 | Apply schema (5 tables, 3 views, 9+ indexes, 2 ALTERs) |
| [`backfill_knowledge.py`](../scripts/backfill_knowledge.py) | 1 | Papers + specs → sources, shell claims |
| [`extract_paper_metrics.py`](../scripts/extract_paper_metrics.py) | 1.5 | LLM populates `claimed_*` on shell claims |
| [`sync_contradictions.py`](../scripts/sync_contradictions.py) | 2 | Manual full resync (hooks usually do this) |
| [`backfill_lifecycle_history.py`](../scripts/backfill_lifecycle_history.py) | 3 | `promotion_log.json` → `strategy_lifecycle_history` |
| [`run_contradiction_channel.py`](../scripts/run_contradiction_channel.py) | 5 | Contradictions → QueueEntry rows |
| [`materialize_wiki.py`](../scripts/materialize_wiki.py) | 7 | SQL → markdown wiki tree |

### Cron pattern (recommended)

```
# Daily (after the existing discovery cron)
*/20 * * * *  python3 scripts/sync_contradictions.py --apply
30 6 * * *    python3 scripts/extract_paper_metrics.py --apply --limit 10
0  7 * * *    python3 scripts/run_contradiction_channel.py --apply --limit 10

# Weekly (Sundays)
0  3 * * 0    python3 scripts/materialize_wiki.py --apply
```

---

## Configuration / feature flags

| Env var                          | Default | Effect                                                              |
|----------------------------------|---------|---------------------------------------------------------------------|
| `ATLAS_TEXT_SUMMARY_V2`          | `0`     | Enable enriched LLM prompt features (volume, regime context).       |
| `ATLAS_KNOWLEDGE_DB_QUEUE`       | `0`     | Phase 6: dual-write `queue.json` → `queue_mirror` table.            |
| `ATLAS_KNOWLEDGE_DB_JOURNAL`     | `0`     | Phase 6: dual-write `journal.json` → `journal_mirror` table.        |
| `ATLAS_PROJECT_ROOT`             | unset   | Override `/root/atlas` path resolution (Windows dev/test).          |
| `ATLAS_SECRETS_PATH`             | `~/.atlas-secrets.json` | Dashboard auth credentials file.                        |
| `ANTHROPIC_API_KEY`              | unset   | **Don't set.** All LLM calls must go through `pi` for Max routing.  |

### Phase 6 rollout (when ready)

1. `export ATLAS_KNOWLEDGE_DB_QUEUE=1 ATLAS_KNOWLEDGE_DB_JOURNAL=1`
2. Restart workers. JSON files stay canonical; SQL mirrors shadow every write.
3. Compare counts after a week: `SELECT COUNT(*) FROM queue_mirror` vs
   `wc -l research/queue.json` (count entries — JSON is a list).
4. When counts match for ≥ 7 days, plan the read-flip (rename `*_mirror`
   tables → `queue`/`journal`, repoint readers).
5. Retire JSON writes; archive the files.

---

## Promotion lifecycle — pre-live validation gates

Before any strategy enters PAPER (let alone LIVE), it must pass several
checks. The canonical promotion state machine lives in
[`monitor/strategy_lifecycle.py`](../monitor/strategy_lifecycle.py);
lifecycle API endpoints are in
[`services/api/lifecycle.py`](../services/api/lifecycle.py).

### 1. Research-best contamination check

The `research_best` SQLite table is the canonical source of best-known
parameters per `strategy × universe`. Before considering a candidate for
promotion:

- Run [`scripts/data_integrity_monitor.py`](../scripts/data_integrity_monitor.py)
  — flags cross-universe identical-metric patterns. Any flagged candidate
  is contaminated; do NOT promote.
- Confirm the row's `updated_at` is after **2026-04-22** (P1.1 universe
  isolation fix). Rows from before that date may carry contaminated baselines.
- Check `is_solo` in `research/best/<strategy>.json`; `is_solo=False` rows
  are blocked by `_run_promotion_sweep()` in `autoresearch_nightly.py`.

### 2. Paper validation period

After RESEARCH → PAPER transition (`monitor/strategy_lifecycle.transition()`),
the strategy must run on the Alpaca paper broker for ≥ 30 trading days with:

- Paper trades count ≥ 30
- Paper Sharpe ≥ 0.3 (gate C threshold)
- OOS Sharpe ≥ 0.3, OOS trades ≥ 30, OOS CAGR ≥ 5% (gates G/H/I)
- No divergence alert active for ≥ 7 consecutive days (gate J)

Paper fills are written to the `paper_trades` table. Execution routing:
[`scripts/execute_approved.py`](../scripts/execute_approved.py) splits plan
entries by `monitor.strategy_lifecycle.is_paper()` — PAPER-state strategies
route to the Alpaca paper broker regardless of universe `trading.mode`.

### 3. LIVE promotion (gates A–J)

PAPER → LIVE auto-promotion runs via
[`scripts/auto_promote_paper_to_live.py`](../scripts/auto_promote_paper_to_live.py)
(cron: weekly, Mon 22:00 UTC). It evaluates all ten gates (see
`docs/ARCHITECTURE.md` § Strategy Lifecycle for the full gate table). On
all-pass:

1. Writes entry to `data/promotion_log.json` (legacy; also captured in SQL
   via `strategy_lifecycle_history.gate_results` JSON column as of Phase 3).
2. Calls `monitor.strategy_lifecycle.transition(strategy, universe, 'LIVE',
   gate_results={...})`.
3. Sends Telegram notification.

Manual promotion override: `POST /api/lifecycle/promote-paper`, requires
operator credentials.

After LIVE promotion, the divergence monitor
(`scripts/check_live_research_divergence.py`, `run_divergence_check()`) runs
continuously. If live-equivalent PnL diverges from research-best over a
rolling window, `process_rollbacks()` fires LIVE → force-to-watch health
escalation or PAPER → RESEARCH auto-rollback (with
`operator='rollback'` on the lifecycle history row as of Phase 3).

### Promotion mechanism

State transitions use
`monitor.strategy_lifecycle.transition(strategy, universe, new_state, ...)`.

Never edit `config/active/*.json` directly to enable a strategy — the
pre-commit hook (lifecycle 1.6 guard) blocks commits that bypass the
promotion audit trail. Use `BYPASS_RESEARCH_GATE="<reason>" git commit`
only with a documented operational reason.

---

## Schema reference

The full schema lives in [`db/schema.sql`](../db/schema.sql). Knowledge-layer
tables start at the "RESEARCH KNOWLEDGE LAYER" divider.

Highlights for the knowledge layer:

```sql
-- sources: dedupe by sha256
sources(id PK, kind, url, title, authors JSON, venue, published_at,
        sha256 UNIQUE, local_path, ingested_at, extracted_by, notes)

-- claims: shell first, metrics filled by Phase 1.5 LLM extractor
claims(id PK, source_id FK, strategy, universe, regime_state,
       period_start, period_end,
       claimed_sharpe, claimed_solo_sharpe, claimed_max_dd_pct,
       claimed_trades, claimed_cagr_pct, claimed_profit_factor,
       claimed_avg_hold_days,
       extraction_confidence DEFAULT 'medium',
       status DEFAULT 'active',  -- 'active'|'dismissed'|'superseded'
       dismissed_reason, notes,
       created_at, updated_at)

-- contradictions: materialised, with explicit resolution lifecycle
contradictions(id PK, claim_id FK, strategy, universe, metric,
               claimed_value, measured_value, delta, delta_abs, severity,
               first_seen_at, last_checked_at,
               resolution,  -- 'retested'|'claim_rejected'|'measurement_corrected'|'deferred'
               resolution_note, resolved_at,
               UNIQUE(claim_id, metric))

-- strategy_lifecycle_history: extended in Phase 3
-- New columns: gate_results (JSON), experiment_id (TEXT)
```

---

## Testing

```bash
# All knowledge-layer phases (Phase 0-7):
PYTHONUTF8=1 pytest tests/test_knowledge_schema.py \
                    tests/test_extractors.py \
                    tests/test_paper_metrics.py \
                    tests/test_contradiction_hooks.py \
                    tests/test_lifecycle_phase3.py \
                    tests/test_knowledge_api.py \
                    tests/test_digest_phase4.py \
                    tests/test_contradiction_channel.py \
                    tests/test_phase6_dual_write.py \
                    tests/test_wiki_materializer.py

# Existing research/lifecycle regression suite:
pytest tests/test_research_best_solo_sharpe.py \
       tests/test_oos_columns_research_best.py \
       tests/test_strategy_lifecycle.py \
       tests/test_divergence_rollback.py
```

Tests are isolated per-test via the autouse `_isolate_prod_db` fixture in
[`tests/conftest.py`](../tests/conftest.py).  Each test gets a fresh
SQLite file with the full schema applied.

### Windows-compat notes

The codebase originally targeted Linux. Phase 0-7 added small compat shims so
the test suite runs on Windows:

- `research/models.py` — no-op `fcntl` shim when the module is unavailable.
- `services/chat_server.py` — `signal.SIGHUP` guarded with `hasattr`;
  `PROJECT_ROOT` falls back to the repo root when `/root/atlas` doesn't exist.
- `research/__init__.py` and `research/discovery/__init__.py` — defensive
  re-export wrappers so subpackages remain importable even if the eager
  imports fail.

Always pass `PYTHONUTF8=1` on Windows so `init_db()` can read `schema.sql`
(which contains UTF-8 box-drawing dividers).

---

## Pi skills

| Skill | Description |
|-------|-------------|
| `atlas-research-loop` | Daily research cycle (researcher → backtester → analyst → risk) |
| `atlas-research` | Ad-hoc research and validation |
| `atlas-reoptimize` | Optimisation and config promotion |

---

## Further reading

- [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) — full system architecture, gate tables
- [`docs/strategy-promotion.md`](../docs/strategy-promotion.md) — promotion mechanics
- [`docs/runbooks/promote-strategy-paper-to-live.md`](../docs/runbooks/promote-strategy-paper-to-live.md) — operator runbook
- [`docs/specs/research-db-consolidation.md`](../docs/specs/research-db-consolidation.md) — knowledge-layer spec
- [`CLAUDE.md`](../CLAUDE.md) — pi CLI routing rule and GitNexus discipline
