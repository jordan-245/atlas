# Atlas Research Promotion Diagnostic — Task #386

**Date:** 2026-05-29  
**Scope:** Diagnose the SP500 research window reporting `32 screened → 0 promoted → 0 kept` for `momentum_breakout` on 2026-05-28 23:00 AEST.

## Verdict

The 0/32 outcome is **not evidence of a broken promotion gate**. The scheduled SP500 sweep ran, wrote artifacts, wrote DB rows, and correctly rejected all 32 candidates at the fast solo-screen stage.

However, the run exposes two return/process issues:

1. **The 1h sweep budget never reached `profit_target_atr_mult` candidates**, even though that parameter is the recent high-impact dimension. Sweep ordering currently prioritizes parameters with more brain history, so older/stale dimensions consumed the entire window.
2. **Research-best and live-active momentum configs are materially divergent.** Research uses `research/best/momentum_breakout.json` as the best-known parameter source; live uses `config/active/sp500.json`. The active config still has materially different params, so nightly research is optimizing around a research candidate that is not necessarily the live config.

Do **not** soften promotion thresholds. Fix instrumentation, ordering, and config-drift review first.

## Evidence

### Primary artifacts

- Window log: `logs/research_window_sp500_20260528_230001.log`
- Strategy log: `logs/autoresearch_momentum_breakout_20260528.log`
- TSV: `research/results/momentum_breakout.tsv`
- DB: `data/atlas.db` tables `research_sessions`, `research_experiments`, `research_best`
- Best params: `research/best/momentum_breakout.json`
- Live config: `config/active/sp500.json`

### Window summary

From `logs/research_window_sp500_20260528_230001.log`:

- Started: `2026-05-28T23:00:01+10:00`
- Strategy filter: only `momentum_breakout`; all disabled SP500 strategies skipped.
- Summary: `32 screened → 0 promoted → 0 kept`
- Status: `completed_no_keeps`
- DB rows: `rows_added=33`, below `min_required=50`, but TSV showed sweep ran.
- LLM loop after the sweep timed out after 1800s; this did **not** affect the completed sweep.

### DB audit

For `2026-05-28 13:04:00` through `13:21:30` UTC:

- `research_sessions` row: id `221`, `experiments_run=32`, `experiments_kept=0`, `status=completed`.
- `research_experiments` rows: 33 total.
  - 1 baseline row: status `kept`, Sharpe `1.0245`, trades `382`, max DD `18.83%`, PF `1.5005`, CAGR `21.55%`.
  - 32 candidate rows: status `discard_solo`.
- Candidate Sharpe range: max `0.4938`, median `0.2827`, min `-0.2287`.
- Top candidate rows were still far below the fast-screen baseline and were rejected before combined verification.

### Gate path

`research/autoresearch_runner.py` uses default `--fast-screen`:

1. Build sweep plan from `ResearchSession._best_params`.
2. Run solo top-50 baseline.
3. For each candidate, run solo top-50 backtest.
4. Apply `research.loop.keep_or_discard()` to solo metrics.
5. Only candidates with `decision == keep` increment `promoted` and proceed to combined verification.

`research.loop.keep_or_discard()` requires:

- Sharpe improvement above threshold (`+0.01` for same-complexity changes).
- Trades not below 70% of baseline, floor 30.
- Drawdown not above `max(20%, 1.5 × baseline DD)`.
- DSR and window coverage checks when applicable.

All 32 scheduled candidates were rejected at stage 1 (`discard_solo`), so `promoted=0` is mechanically correct.

### Sweep-plan issue

Current `build_sweep_plan()` over current research-best params produces 38 candidates:

- `breakout_period`: 4 candidates
- `atr_stop_mult`: 6
- `max_hold_days`: 4
- `lookback_days`: 6
- `atr_period`: 6
- `trend_ma_period`: 6
- `profit_target_atr_mult`: 6

The 1h window screened exactly the first 32 candidates and stopped before `profit_target_atr_mult` candidates (#33-#38). This matters because `profit_target_atr_mult=2.2` was the recent manually-kept improvement.

### Config drift

`research/best/momentum_breakout.json` currently records research-best params:

```json
{
  "atr_stop_mult": 0.81,
  "max_hold_days": 15,
  "lookback_days": 22,
  "atr_period": 22,
  "trend_ma_period": 30,
  "breakout_period": 10,
  "profit_target_atr_mult": 2.2
}
```

`config/active/sp500.json` live params differ materially:

```json
{
  "atr_stop_mult": 0.61,
  "max_hold_days": 15,
  "lookback_days": 14,
  "atr_period": 18,
  "trend_ma_period": 27,
  "breakout_period": 10,
  "profit_target_atr_mult": 6.0
}
```

This means the scheduled research signal is not directly a verdict on the live active parameter set.

## Diagnosis

### What is working

- Active-config strategy filter worked: disabled SP500 strategies were skipped.
- The scheduled sweep executed and inserted DB rows.
- `completed_no_keeps` is the correct status: TSV/DB output exists, but no candidates passed gates.
- No evidence that promotion thresholds need weakening.

### What is weak

- The sweep planner is not budget-aware enough. A 1h nightly window can end before high-impact/newer parameters are tested.
- The run summary says `0 promoted`, but does not show why candidates failed or which parameter groups were not reached.
- The DB/TSV store `discard_solo` rows but not the rejection rationale (`delta_sharpe`, trade collapse, drawdown, DSR), making post-mortems harder.
- Research-best/live-active config drift is not visible in the nightly summary.

### Adjacent noise

There are additional `research_sessions` rows around 14:00–19:00 UTC with all-strategy strings and `silent_failure` status. These are not the 23:00 AEST SP500 window, but they can pollute operator perception. Current systemd timers show only `atlas-research-window@sp500.timer` active; keep #219 focused on detecting this kind of stale/legacy runner noise.

## Recommendations

1. **Do not soften gates.** The 0/32 result was a correct rejection of weak candidates.
2. **Add budget-aware sweep prioritization.** Prioritize recently high-impact params, params with recent keeps, and params not yet explored under the current best before stale high-history params.
3. **Add failure-rationale telemetry.** Persist solo baseline, `delta_sharpe`, `delta_trades`, `delta_dd`, and rejection reason for each `discard_solo` row.
4. **Add research-best vs live-active drift check.** Nightly summary should state whether the research baseline matches the live active config and flag drift.
5. **Use #219 regression harness before promotions.** The harness should cover the 33-row completed_no_keeps case, budget truncation, active-config filtering, and no threshold softening.
6. **Run a targeted/manual profit-target neighborhood sweep only after #219 instrumentation is in place**, not as an immediate promotion shortcut.

## Follow-up tasks

- Add a task to reconcile/evaluate research-best vs live-active `momentum_breakout` config drift before any promotion.
- Add a task to improve autoresearch sweep ordering and solo-discard telemetry.

## Completion criteria for #386

- Relevant artifacts identified: complete.
- 32-variant outcome summarized: complete.
- Gate behavior diagnosed: complete — correct rejection, not threshold issue.
- Silent-failure/data-quality evidence reviewed: complete — scheduled run OK, adjacent legacy sessions noisy.
- Next actions recommended: complete.
