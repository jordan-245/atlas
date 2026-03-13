# Autoresearch Alignment Audit

## Karpathy's Autoresearch Core Principles

From the original design (and program.md):

1. **Fixed evaluation** — the evaluation function is immutable (like `prepare.py`)
2. **Binary keep/discard** — every experiment either advances the best or reverts
3. **Simple results tracking** — TSV per strategy, best-known JSON
4. **Simplicity criterion** — complexity cost vs improvement magnitude
5. **Never stop** — runs indefinitely, agent is autonomous
6. **The LLM is the intelligence** — the code just runs experiments, the agent proposes what to try
7. **One tight loop** — propose → run → keep/discard → repeat

## Current Atlas Implementation vs Principles

### ✅ Aligned

| Principle | Implementation | Status |
|-----------|---------------|--------|
| Fixed evaluation | `strategy_evaluator.py` + `backtest/engine.py` are immutable | ✅ Solid |
| Binary keep/discard | `keep_or_discard()` in `loop.py` — Sharpe gate + trade gate + DD gate | ✅ Solid |
| Simple tracking | `results/*.tsv` + `best/*.json` | ✅ Solid |
| Simplicity criterion | `params_added` penalty in `keep_or_discard()` | ✅ Present |
| Never stop | `sweep.py --cycles 0`, systemd restart-on-failure | ✅ Works |
| Combined portfolio test | Every keep is gated on `_test_combined()` | ✅ Good addition |

### ⚠️ Partially Diverged

| Principle | What Changed | Concern |
|-----------|-------------|---------|
| **LLM is the intelligence** | sweep.py is a mechanical grid search — no LLM in the loop | The "body" works but the "brain" (LLM) is completely absent from the sweep. The LLM interactive mode exists (`ResearchSession`) but nobody uses it. The sweep is dumb coordinate descent, not intelligent exploration. |
| **One tight loop** | Now THREE systems: sweep.py (grid), runner_daemon.py (queue), loop.py (interactive) | The queue-based runner is a whole separate experiment framework bolted on top. It has its own lifecycle (QUEUED→CLAIMED→RUNNING→PASSED), its own evaluator, its own journal format. This is no longer "one tight loop". |
| **Propose based on what you learned** | sweep.py proposes nothing — it blindly iterates a fixed grid | The key insight of autoresearch is that each experiment informs the next. The sweep ignores all results history. It doesn't notice "rsi_period=10 always loses" and stop testing it. The expand_grid fix helps but it's still random jitter, not learned exploration. |

### ❌ Diverged

| Principle | What Happened | Impact |
|-----------|--------------|--------|
| **Agent proposes experiments** | Sweep proposes nothing, runner reads a pre-populated queue | Neither system has an agent proposing hypotheses. The queue was hand-populated or populated by the director (a cron script). There's no continuous hypothesis generation → test → learn cycle. |
| **Simplicity of design** | 6 subsystems: loop.py, sweep.py, runner_daemon.py, evaluator.py, promoter.py, models.py + queue.json + journal.json + experiments/ + brain/ + directives.json | 6065 lines of research code across 9 files. Karpathy's original was ~200 lines. The complexity has compounded far beyond the "simple loop" intent. |
| **Results inform next experiment** | sweep.py doesn't read its own results history | After 2350 experiments, the system has learned nothing about which parameter directions are promising. Each cycle is as blind as the first. The brain/ markdown files exist but nothing reads them to propose experiments. |
| **Single source of truth** | Results in: results/*.tsv, best/*.json, journal.json, experiments/*.json, brain/params/*.md, queue.json status fields | Five separate places to look for "what happened". The brain docs duplicate the TSV data in markdown. The queue has its own pass/fail tracking separate from the TSV verdicts. |

## The Fundamental Problem

The system has split into two separate philosophies that don't talk to each other:

**System A: Autoresearch (loop.py + sweep.py)**
- Binary keep/discard on Sharpe improvement
- TSV tracking, best-known JSON
- Simple, mechanical, works well for grid search
- But: no intelligence, no learning, grid exhaustion

**System B: Research Pipeline (runner_daemon.py + evaluator.py + models.py)**
- Queue-based with lifecycle stages (solo → optimize → combined → OOS → promote)
- DSR statistical tests, acceptance criteria per stage
- Experiment envelopes, multi-agent file ownership
- But: the queue is manually populated, runs are slow (1 per 4 min), 52 items sit queued

These two systems evolved independently and now they:
- Use different journal formats (sweep appends to TSV + journal.json directly; runner writes to experiments/ envelopes + queue.json status)
- Have different promotion gates (sweep uses `auto_promote()` with cooldown; runner uses `ExperimentEvaluator.auto_advance()` with lifecycle stages)
- Compete for CPU (runner_daemon yields to sweep via lock file)
- Don't share learnings (sweep doesn't read queue results; runner doesn't read TSV history)

## What Autoresearch Would Actually Look Like

If we went back to first principles:

```
ONE LOOP:
  1. Load current best params for all strategies
  2. Pick the most promising thing to try next (INTELLIGENT SELECTION)
  3. Run the experiment (~2 min)
  4. Binary keep/discard
  5. Record what happened (one place)
  6. Update the "what to try next" model based on results
  7. GOTO 1
```

The intelligence in step 2 is the key. It should:
- Know which parameters have been tested and their results
- Avoid re-testing things that already failed
- Notice patterns ("every time atr_stop_mult goes below 1.5, it fails")
- Explore in promising directions (Bayesian-style: if 2.5 was good, try 2.3 and 2.7)
- Switch strategies when diminishing returns set in
- Occasionally try radical changes (exploration vs exploitation)

This is exactly what the LLM was supposed to do in interactive mode. But nobody runs interactive mode because the sweep is always running.

## Concrete Differences Summary

| Aspect | Autoresearch Intent | Current Atlas |
|--------|-------------------|---------------|
| Experiment proposal | LLM reasons about what to try | Fixed grid iteration (sweep) or pre-populated queue (runner) |
| Learning from results | Agent reads history, adjusts approach | No learning — grid is re-swept identically each cycle |
| System count | 1 loop, 1 tracking file | 2 parallel systems, 5+ tracking locations |
| Experiment cost | 2-5 min each, ~20/hour | Same per-experiment, but throughput wasted on redundant tests |
| Intelligence location | In the agent's reasoning | Nowhere — sweep is mechanical, runner is a job queue |
| Promotion path | Agent decides after combined test | Two separate paths: auto_promote (sweep) vs lifecycle stages (runner) |
| Statistical rigor | Not in original; simple Sharpe comparison | DSR, acceptance criteria per stage — good addition but adds complexity |

## Recommendations

### Option A: Simplify Back to Autoresearch (radical)
- Kill runner_daemon.py, evaluator.py lifecycle stages, queue.json
- Keep sweep.py as the one loop
- Add intelligence: replace grid iteration with result-aware proposal
- One tracking format: TSV + best JSON (drop experiment envelopes)
- One promotion path: combined test → human approval
- DSR stays as an informational metric, not a gate

### Option B: Keep Both, Fix the Intelligence Gap (pragmatic)
- Keep sweep as the mechanical workhorse
- Keep runner for the queue experiments (portfolio-level, cross-strategy)
- Add a "sweep advisor" that reads results history and adjusts the grid
- Unify the journal format so both systems write the same way
- The expand_grid fix is step 1; step 2 is parameter-level win/loss tracking

### Option C: Replace Sweep with LLM-in-the-Loop (original vision)
- Run the LLM agent continuously (like the research-loop skill)
- Agent reads results, reasons about what to try, calls ResearchSession
- More compute (LLM calls cost money) but genuinely intelligent exploration
- This is what program.md describes but nobody actually runs

**My recommendation: Option B.** The sweep is good at mechanical coverage. The runner handles structured experiments. Neither is intelligent. The fix is adding intelligence to the sweep (result-aware grid expansion, parameter-level tracking) rather than tearing everything down. The expand_grid commit was step 1.
