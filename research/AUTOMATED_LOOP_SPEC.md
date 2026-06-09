# Automated Research Loop — Spec (industrialize free search, safely)

> Board memo `ceo-board/memos/2026-06-05-atlas-research-strategy-free-compute` (5-0): compute is
> free/unlimited, so industrialize the search — but ONLY behind the three integrity rails (now all
> implemented: `research/INTEGRITY_RAILS_SPEC.md`). Core principle:
>
> **Generation + screening run unlimited and free. PROMOTION is gated (holdout + FDR bar +
> deployment-sanity + forward confirmation + human approval) and rate-limited to the scarce
> resources: integrity, capital, and operator attention.**
>
> The loop is ~80% built already and intentionally OFF. This spec wires the rails into it and defines
> safe re-activation. It does NOT propose new infrastructure where existing components suffice.

---

## Existing components (reuse, don't rebuild)

| Stage | Component | Status |
|---|---|---|
| Generate (parametric) | `scripts/director_cron.py` (atlas-director) — tops up queue when < 5 pending (MAX_GEN=10) | OFF |
| Generate (creative) | `research/discovery/discovery.py` — LLM: papers → filter → spec → `strategy_factory.build_strategy` → quick_check → Telegram digest | OFF |
| Queue | `research/queue.json` + `research/models.py` (QueueEntry state machine) | live (manual) |
| Screen/execute | `scripts/research_runner.py` (atlas-research-runner) — claims queued experiments, runs them, updates state | OFF |
| **Battery + RAILS** | `scripts/run_strategy_battery.py` — now has Rail 1 (holdout quarantine), Rail 3 (deployment-sanity auto-FAIL), Rail 2 (FDR-aware promote bar) baked in | live |
| Stage/triage | `research/pipeline.py` (#420) — `queued→battery→screen→paper→microlive_gate→microlive→scale` | live (manual) |
| Forward gate | `research/forward_evidence.py` (#418/#420 forward clock) | live |
| Micro-live gate | `research/microlive_gate.py` (#419) | live |
| Holdout gate | `research/cross_oos/holdout.py` (Rail 1, single-use) | live |
| Registry | `research/cross_oos/registry.py` (Rail 2) | live |

---

## End-to-end flow (with rails as hard gates)

```
 GENERATION (free, unlimited)                          SCREENING (free, unlimited)
 director_cron: parametric param/universe families  ┐
 discovery: LLM paper-driven new families            � ─► queue.json ─► research_runner
 (broaden: small/mid-cap, new families, ensembles)  ┘                    │ per entry
                                                                          ▼
                                       run_strategy_battery (RAIL 1 quarantine: search < holdout_start)
                                          ├─ RAIL 3 deployment-sanity ──fail──► FAIL (logged, dropped)
                                          ├─ RAIL 2 FDR bar = promote_dsr(n_families)
                                          └─ tier ∈ {FAIL, SCREEN, PROMOTE}; append hypothesis_registry
                                                                          │
                              FAIL ◄── (most) ──┤                         │ PROMOTE (rare)
                                                ▼ SCREEN                  ▼
                                       pipeline: paper stage      RAIL 1 holdout-eval (ONCE, single-use)
                                       forward_evidence clock        ├─ fail ─► FAIL (candidate BURNED)
                                          │ PASS                      └─ pass ─► pipeline: paper
                                          ▼                                         │ forward PASS
                                 ════════ HUMAN-GATED FROM HERE ════════            ▼
                                 microlive_gate (drilled+confirmed) ─► micro-live ─► scale
                                          ▲ operator approval + capital gates (~$25K AUM)
```

**Throughput over latency** (already the pipeline's design): thousands of candidates screen in parallel
and FAIL cheaply; many sit in `paper` concurrently; the FIRST to clear forward + human review advances.
Slow/weak ideas simply die or wait. No human sees anything before a PROMOTE clears the holdout.

---

## Changes required to wire the rails in

1. **Route new-strategy screening through the rail-equipped battery.** DONE 2026-06-05.
   Verification gate found the runner screened via `strategy_evaluator.evaluate_strategy` (NO rails).
   Fix: extracted `run_strategy_battery.run_battery(a)` as a callable single-source-of-truth (CLI +
   loop both use it); `research_runner._run_single_strategy` now routes `category=='new_strategy'`
   through `_run_battery_screen` -> `run_battery(holdout_eval=True, quarantine on)`; dormant/other solo
   checks keep the light evaluator. Verified: routing test (new_strategy->battery, dormant->evaluator),
   CLI smoke (artifact carries deployment+multiple_testing, exit 0), 18 rail tests + 65 cross_oos green.
   (Remaining: `combined`/`oos` new-strategy paths can also be routed if/when used - single screen is
   the rail-critical one.)
2. **Insert a `holdout` gate in `research/pipeline.py`** between `battery`(PROMOTE) and `paper`: a PROMOTE
   candidate calls `holdout.evaluate_holdout(...)` ONCE; fail → `failed` (burned); pass → `paper`.
   (The battery's `--holdout-eval` already implements the eval + single-use; the pipeline just calls it at
   the PROMOTE transition rather than on every screen.)
3. **Director generation breadth** (`director_cron.py` -> `queue_discovery_batch`): PARTIAL 2026-06-05.
   - Wire-in part DONE: new strategy FAMILIES already flow via discovery's STRATEGY_UNIVERSE +
     `get_next_experiments`; each distinct family is a Rail-2 registry family.
   - NOT a wire-in (separate DATA-INFRA task): **small/mid-cap universe expansion**. `vo.load_data`
     only knows sp500 / sector_etfs / commodity_etfs; pointing generation at small/mid-cap needs a new
     universe definition + data ingestion + sector map. Tracked as its own scoped item, not done here
     (don't fake it). Ensembles/rebalance-frequency families CAN be added as generation content on the
     existing universe.
4. **Discovery smoke-filter** (`queue_discovery_batch`): DONE 2026-06-05. A `new_strategy` must pass a
   `deployment_smoke` (run default config once -> deployment-sanity) before earning a queue slot;
   non-deploying strategies are skipped (fail-open on smoke error). `research/cross_oos/deployment.py::
   deployment_smoke`. Verified on csm (peak 13 / 11 sectors / pass). quick_check filtering already
   existed in `_generate_strategies`. NOTE: largely a queue-hygiene optimization now — Rail 3 already
   auto-FAILs non-deploying strategies at screen time.

---

## Hypothesis generation strategy (the free firehose, pointed well)

- **Parametric families** (director): sweep economically-motivated axes within a family; the grid is
  DSR-deflated within, the family counts once in Rail 2. Cheap, high-volume.
- **Creative families** (discovery): LLM proposes genuinely new ideas from literature; these are the
  high-value, low-correlation additions that move the search beyond the exhausted large-cap-daily slice.
- **Universe expansion**: the 3 nulls were all on liquid large-cap daily (most efficient slice). Point
  generation at **small/mid-cap, more tickers, intraday (gated on paid data), cross-asset** — where
  inefficiencies actually live.
- **Ensembles**: combine SCREEN-tier survivors (uncorrelated) into ensemble candidates (the #423 path).
- **Anti-spam**: generation is free but the queue is not infinite; cap queue depth, dedup by family +
  config hash, and require a passing smoke (quick_check + deployment-sanity) before queueing.

---

## Triage + operator-attention throttle (the real scarce resource)

- The human sees **only** candidates that have cleared: battery PROMOTE → holdout → forward clock. Expect
  a trickle, not a flood. Everything else lives/dies in the registry + pipeline without human involvement.
- **Daily digest** (extend the discovery Telegram digest): N families generated, N screened, tier
  histogram, current `paper`-stage candidates + their forward progress, and any candidate that reached the
  human-gated boundary. One glanceable message.
- **Promotion queue**: candidates at the microlive_gate boundary wait for explicit human approval +
  capital; rate-limit to ≤1 promotion decision pending at a time (no firehose of approvals).

---

## Safety, governance, kill-switches

- **Rails are non-bypassable**: the loop calls the battery; the battery quarantines the holdout, auto-FAILs
  deployment artifacts, and applies the FDR bar. There is no code path from generation to live that skips
  them. The holdout is single-use-ledgered; the registry is append-only.
- **Resource governance**: research workers stay `nice +10`, bounded to `min(N, cores-2)` so the loop never
  starves live daily ops (premarket plan, settlement, health) or trips the CPU governor. Research runs only
  in a defined window (the `atlas-research-window` unit) or below a load threshold.
- **No live mutation**: the loop may write candidates/queue/registry/sandbox only. It may NOT promote a
  config, change `trading.mode`, or touch `config/active/*` — those stay human-gated (existing
  `atlas_risk_*` promotion tools + approval).
- **Kill-switches**: a single `research/LOOP_DISABLED` flag halts generation+execution; per-service
  systemctl stop; heartbeats (`director_cron` already writes one) with a watchdog that alerts on staleness.
- **Audit**: every run → registry; every holdout touch → ledger; every promotion → existing audit records.

---

## Re-activation plan (staged; do NOT flip everything at once)

1. **Wire-in (no daemons):** make the 4 changes above; run `research_runner.py --run-all` manually on a
   small generated batch; confirm artifacts carry `deployment` + `multiple_testing`, the registry grows,
   and the holdout stays quarantined. (Acceptance gate before any daemon.)
2. **Shadow week (director only):** enable `atlas-director` to generate + queue, but leave the runner
   manual. Review the generated families for sanity (are they sensible ideas?).
3. **Bounded runner:** enable `atlas-research-runner` inside a restricted window / load cap. Watch CPU vs
   live ops; watch the tier histogram (expect mostly FAIL/SCREEN; a flood of PROMOTEs = a gate bug → stop).
4. **Discovery on:** enable LLM `discovery.py` cadence once parametric flow is proven stable.
5. **Full loop:** all services on, daily digest to Telegram, human reviews only the promotion-boundary
   trickle. Re-evaluate the deferred paid-data ($99/mo) decision once the free-universe loop is saturated.

---

## Acceptance criteria

- A generated, non-deploying strategy is auto-FAILed (Rail 3) and never reaches a human.
- A strategy that PROMOTEs in-search but degrades on the holdout is burned (Rail 1) and cannot be retried.
- The promote bar visibly rises as the registry grows (Rail 2); a 0.90-DSR idea stops promoting once many
  families exist.
- Live daily ops are never delayed by research load (governance).
- The human's only recurring touch is a daily digest + the occasional promotion approval.
- No code path promotes to live without human approval + capital gates.

## Open decisions for the human
- Generation breadth priority order (small/mid-cap first? new families first? ensembles?).
- Research window / load cap values (how much of the 8 cores to cede to research vs keep free).
- Telegram digest cadence (daily? on-promotion-only?).
- Whether to seed the registry with this session's already-tested families (long-short, news-sentiment,
  pairs, csm) so the FDR bar starts pre-loaded, or start the count fresh.
