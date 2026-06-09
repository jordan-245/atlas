# Atlas — Accumulated Lessons
*Patterns and rules from project history. Review at session start.*

---

## Research & Backtesting

### 1. Position contention is THE bottleneck, not strategy quality
High-signal-volume strategies (momentum_breakout: 460 trades, short_term_mr: 697 trades) flood a shared position pool, crowding out proven signals. Never evaluate "does this strategy add value?" without also testing "does it crowd out existing strategies?". Always run combined test, not just solo test.

### 2. Scoring function must prevent degenerate solutions
Original coord descent converged to 3-4 trade windows where PF=infinity. Fix: min_trades=15, cap PF at 4.0, trade count scaling ramp 15→50. Without this, optimizer finds degenerate "sharp peaks" not robust plateaus.

### 3. Blending doesn't improve robustness
v9.3 (50/50 blend of v9.2 and defaults) showed identical perturbation stability as v9.2 but 4.5% less CAGR. When the landscape has one ridge, blending moves you toward a lower point. Choose the better peak, don't average them.

### 4. Clean A/B toggle reveals what coord descent hides
Coord descent rejected SMA-200 filter (too few trades). Clean A/B toggle confirmed Sharpe +47%. Lesson: when an intervention reduces trades significantly, coord descent's trade-count penalty masks the quality improvement. Test filters as clean toggles, not as optimizable params.

### 5. VIX filter destroys alpha in MR-heavy portfolios
MR profits from panic (high-VIX entries). Do not apply VIX regime filter to any portfolio containing mean_reversion. VIX filter may work for trend-only portfolios — test independently.

### 6. OOS validation before ANY promotion
Three-test suite required: (1) time-split OOS, (2) perturbation (±15%), (3) walk-forward window win rate >50%. All three must pass. OOS Sharpe > 0 is minimum; OOS ratio > 0.7 is preferred.

### 7. Solo pass ≠ portfolio pass
Track record across Wave 1: 4/4 dormant strategies passed solo tests, 0/4 passed combined tests. Always run combined test with existing portfolio before declaring a strategy "ready".

### 8. filter_test experiments need `filter_param` + `variants` fields in queue
Infrastructure failure, not hypothesis failure. When filter experiments fail due to missing params, requeue with correct format — don't mark the hypothesis as rejected.

### 9. Control test is often the most valuable experiment
"Just increase max_positions for current strategies" (max_pos=10→15) gave +13% Sharpe. This outperformed adding any dormant strategy. Always include a control arm.

---

## Broker & Execution

### 10. Fee drag determines viable minimum account size
IBKR ASX: $6/order + $500 min parcel = $12 round-trip = 2.4% drag on smallest position. At $3,999 equity, this makes MR/OG unprofitable. Rule: for any market, verify `round_trip_fee / avg_position_size < 1.0%` before deploying.

### 11. Always test broker connectivity before going live
Moomoo AU API cannot place ASX orders (server-side block — not documented). IBeam REST API had session auth loop. Discover these constraints in dev/dry-run, not in production.

### 12. Broker offline → never write state
Broker returning $0 equity + $0 cash = broker offline, not empty portfolio. Check `broker_data_valid` before writing any state. All 7 write paths now have this guard. Never rely on a single guard.

### 13. Live executor must use MARKET orders for stop-loss exits
LIMIT orders at current price may not fill if price moves during order placement. Stop-loss exits are urgency-sensitive — MARKET order is correct. Entry and target-profit exits can use LIMIT.

### 14. Moomoo trade unlock failure must be fatal
`unlock_trade()` failing but `connect()` returning True causes every subsequent order to fail silently. Treat unlock failure as connection failure for live accounts.

---

## Code Architecture

### 15. Dormant strategies accumulate API drift bugs
All dormant strategies had silent bugs: `generate_signals()` signature mismatch, `calc_atr()` wrong call pattern, Series comparison ambiguity. Before running any dormant strategy in research, do a read-through for: ABC method signatures, scalar vs Series usage, kwargs that have changed.

### 16. Shared cache files need file locking
Multiple processes (EOD, research, CLI) write to the same parquet cache concurrently. Use atomic write (write-to-temp-then-rename) for all file writes that can race. This applies to: parquet cache, paper state JSON, plan files.

### 17. Parallel builders creating the same new file → merge conflict
When a parallel-agent tool task creates a new shared module, assign creation to ONE builder. Other builders depend on it or work on non-overlapping files. Never assign the same new file to multiple builders.

### 18. Research runner exit code matters
Code errors (TypeError, AttributeError) in experiment execution must exit with code 2, not 0. When exit 0, auto-recovery never fires. Distinguish: code bug (exit 2) vs research failure (exit 0).

### 19. Per-market plan files prevent market overwrites
Plan files must be named `plan_{market_id}_{date}.json`. Shared `plan_{date}.json` means the last market to generate clobbers the other. Same pattern applies to any market-specific output file.

### 20. IBeam REST vs IB Gateway socket
IBeam REST API has a known bug: browser auth session not inherited by REST endpoint → `authenticated=False` loop. Use `ib_insync` + IB Gateway Docker instead. IBeam is abandoned.

---

## Operational

### 21. US Friday session = Saturday AEST
US market hours (9:30-16:00 EST) map to Saturday AEST for Friday's session. Overnight/postclose crons must use day-of-week `2-6` (Tue-Sat), not `1-5`. Premarket (evening AEST) stays `1-5`.

### 22. Parallel-work coordinator must not pre-scout
Reading files before launching a parallel-agent tool defeats the purpose of the scout phase. Scouts find unexpected things. Coordinator writes objectives + acceptance criteria, dispatches, tracks. Does NOT read code or run experiments.

### 23. Builder scope = file ownership
Split parallel-agent tool tasks by FILE, not by concern. Each file belongs to exactly one builder. When you find yourself thinking "both builders need to touch this file" — that's a merge conflict waiting to happen. Assign the file to one builder.

### 24. Parallel subagents are allowed; retired swarm/orchestrator is not
User correction 2026-06-02: do not conflate the retired swarm/parallel code-modification orchestrator with the separate focused subagent workflow. Keep using parallel subagents for scouting, research, read-only analysis, review, test running, and clearly file-owned bounded implementation when useful. Avoid only the retired hidden code-modification orchestrator; preserve explicit scope, ownership, acceptance criteria, and verification.

### 25. Config version naming convention
Use `{market}_{version_label}_{YYYYMMDD}.json` for snapshots. Semantic labels (v9.3, v2.2) for promoted configs. Pre-promotion backups: `{market}_pre_{action}_{YYYYMMDD_HHMMSS}.json`.

### 25. Stale ASX cache had US tickers with .AX suffix
`data/cache/asx/` had 36 US ticker parquets with `.AX` suffix from an earlier pipeline bug. `strategy_evaluator.py` loaded ALL parquets, contaminating ASX backtests. Fix: filter loaded tickers against `market.get_formatted_tickers()` before loading.

### 26. Weekly maintenance prevents disk/log bloat
Cron Sunday 06:00: rotate large logs, delete old pi-cron logs (>14d), purge pycache, sweep root-level cache parquets. Without this, atlas.log hits 9+ MB and telegram_bot.log hits 2+ MB within days.

---

## Research Process

### 27. Hypothesis must come BEFORE data
Logging "it passed because X" after seeing results is confusing correlation with causation. Queue entries must have a specific, falsifiable hypothesis BEFORE the backtest runs.

### 28. Volume filter 1.5x is the threshold where quality jumps
Below 1.0x: minimal improvement. At 1.5x: MR Sharpe -0.02→0.38, PF 1.30→1.62, DD reduced. At 2.0x: too few trades (115), Sharpe drops again. The transition is sharp — don't assume linear scaling.

### 29. Sector rotation needs rebalance-aware backtest support
Standard walk-forward engine treats sector rotation as a signal-per-bar strategy. It rebalances every N days, so results with standard engine are unreliable. Don't promote sector rotation results from the current engine until rebalance-aware support is added.

### 30. Solo param sweeps are unreliable at low equity
With $4K starting equity, ALL strategies show negative Sharpe in solo mode due to fee drag ($10 round-trip on $75-180 stocks). Combined portfolio has Sharpe 0.87. Solo sweeps only give relative rankings (e.g., hold=10 > hold=15), not absolute quality. Use combined-mode sweeps for promotion decisions.

### 31. filter_test doesn't support nested config params
filter_test sets `s_cfg[filter_param] = value` but many strategy params are nested (e.g., `volume.min_ratio`). Setting `volume_min_ratio` at top level is ignored. Fix: support dot-path params or deep-merge dicts. Same issue for any new filter type (TOM, regime, etc.)

### 32. New strategies accumulate calc_position_size dict bugs
`calc_position_size()` returns a dict `{shares: N, ...}`, not an int. New strategy code (e.g., connors_rsi2) compared `dict <= 0` which throws TypeError in Python 3.12+ or ambiguous comparison error. Always extract `pos_result["shares"]`.

### 33. Parallel research runner has file locking issues
`run_wave2_parallel.py` with ProcessPoolExecutor causes concurrent writes to queue.json and journal.json. Updates are lost silently. Sequential `--run-all` works correctly. Fix parallel runner's file locking before using it for production runs.

### 34. stage_candidate() clobbers reoptimizer output
`stage_candidate()` starts from `get_active_config()` and applies `strategy_params` on top. When the research-loop agent calls it for reoptimization experiments without passing `strategy_params`, it overwrites the candidate file (already correctly saved by `reoptimize_parallel.py`) with a verbatim copy of the active config. This silently invalidated wave5_full_reopt results and caused OOS to validate the wrong config. **Fix:** `stage_candidate()` now preserves existing candidate files when no new changes are requested. **Rule:** Any function that writes to a path that another process also writes to must check-before-clobber.

### 35. Double-multiplication bug in Telegram promotion formatter
`run_backtest()` returns `cagr_pct`, `max_drawdown_pct`, `win_rate_pct` already in percent form (e.g. 38.14 = 38.14%). The Telegram formatter in `send_promotion_request()` had a single `_PCT_METRICS` set that treated ALL percent-related metrics as decimals and multiplied by 100 — producing 3814% CAGR. **Fix:** Split into `_DECIMAL_PCT` (needs ×100) and `_ALREADY_PCT` (display as-is). **Rule:** When a metric name includes `_pct`, the value is already in percent — never multiply again.

### 15. Strategy correlation clusters invalidate naive diversification
Mean_reversion, connors_rsi2, and opening_gap are 0.94-0.95 correlated on daily returns — they're essentially one bet. Allocating independently to each (as the optimizer naively does) concentrates ~58% of capital in one correlated cluster. Always cluster strategies by correlation FIRST, then allocate across clusters.

### 16. Solo backtests at $4K equity with Moomoo fees produce useless Sharpe ratios
Fee drag at low equity destroys strategy performance metrics. The same strategy can show Sharpe -3.67 at $4K/Moomoo and Sharpe +0.23 at $25K/$0 commission. Always run portfolio optimization analysis at $0 commission (Alpaca mode) with realistic equity to get comparable cross-strategy metrics.

### 17. Infrastructure blockers masquerade as research failures
8 infra blockers contaminated 15+ experiments in the weekly report. Always verify whether a failing experiment is a hypothesis failure or an infrastructure failure before drawing conclusions. Key tells: identical results across variants (test harness not varying the param), confidence filtering killing all signals, wrong default config paths.

### 18. cli_paper_run → live-run execution path: use LiveExecutor, not broker.sell()
The old `cmd_live_run` called `broker.sell()` directly, bypassing `LiveExecutor._execute_exit()` which handles cancelling protective orders (SL/TP) before selling. This caused Alpaca "insufficient qty" rejections when stop-loss orders held shares. **Fix (2026-03-26):** Rewrote `cmd_live_run` to route through `LiveExecutor.execute_plan()`. Also fixed: `pythonExecutable()` defaulted to `"python"` (not found on system, only `python3`), and `cli_paper_run` mapped to non-existent `paper-run` command instead of `live-run --auto`. All live execution MUST go through `LiveExecutor` — never call `broker.sell()` directly for exits.

---

## Session 2026-04-29 — Phase B.4 + Phase C planning

### 36. position_protective_orders ledger is the fix for multi-writer drift
Single canonical row per open position (market_id+ticker PK) with broker-verified
stop_order_id + tp_order_id. Any code that places or cancels a protective order
writes through this ledger → drift class eliminated at source.

### 37. broker_orders as fill-price oracle: three-tier priority chain
Priority: broker_orders > inferred from position avg_entry + WARNING > NULL + ERROR + Telegram.
Never fabricate a fill price. If broker_orders is absent, log WARNING and record NULL,
then alert Telegram — silent fabrication caused CHTR phantom-price bug class.

### 38. Shadow mode for reconcile cutover
Ship new `core/reconcile.py` alongside old scripts. Alert on divergence (Telegram, 6h throttle).
Cutover only after 7 consecutive clean days. This pattern validates the new code against
production traffic before switching the live path — without any downtime window.

### 39. AST lint with grandfather baseline blocks new offenders cleanly
839 existing bare-excepts are grandfathered in `baseline.txt`.
Any NEW bare-except added after the baseline is caught in CI with exit 1.
Doesn't require fixing all 839 today; just enforces "don't add more."

### 40. "Days since X happened" healthcheck catches silent feature failures
A `days_since_last_signal_write` check catches a 10-day write gap immediately.
A `days_since_research_experiment` catches a 37-day research block the morning after.
Pattern: for any background process whose failure is invisible to traders, add a
DB-query-based staleness check to healthcheck_pipelines.py.

### 41. TP-coverage healthcheck: 5-min debounce + state file = fail-loud
Without debounce: alerts fire on every cron cycle during the 30s after entry fill
while the TP order is being placed. With 5-min debounce + state file tracking
first_missing_at, the alert fires only after a genuine gap — and second run is
idempotent (state file already has the timestamp).

### 42. CHECK constraints + JSON drift detector = SQLite canonical, JSON observed
Add CHECK on `state`, `stop_price` direction, exit/entry date ordering to trades.
JSON state files are observed output, not canonical storage — any drift from
SQLite is a bug. The `verify_dual_write.py` canary detects this automatically.

### 43. Per-instance warning throttle: `self._warned = False` flag
`sync_protective_orders._handle_held_stops` was firing 30+/15min Telegram storms.
Fix: instance-level flag reset at the start of each cron invocation; flag set on
first WARNING; subsequent WARNINGs within the same cron instance are suppressed.
Rule: any code that can fire alerts from a loop needs per-invocation throttling.

### 44. Phase 0 broker verification before any DB-only fix
"TP-naked CAT" looked like a missing DB tp_order_id. Actually: orphan TP order
existed at broker, linked to a different DB row. DB-only fix would have created
duplicate TP orders. ALWAYS verify broker state with `broker.get_open_orders()`
before any DB reconciliation action.

### 45. `dict.get(key, default)` does NOT default when value is None
`t.get("pnl", 0)` returns `None` if `t["pnl"] is None` — the default only kicks
in when the key is *missing*. Reconciled broker-fill stubs in `closed_trades`
carry `pnl=None` (no entry_price → no PnL computable), and any `sum(...)` over
them crashes with `TypeError: float + NoneType`. Crashed eod_settlement on
2026-05-07 in two places (`live_portfolio.record_equity`, `eod_settlement.main`,
`telegram._format_postclose_summary`). Correct idiom: `(t.get("pnl") or 0)`.
Already used elsewhere in same files. Sweep `t.get("pnl", 0)` callers when
convenient — `backtest/metrics.py` and `scripts/strategy_evaluator.py` still
have unprotected sites that would crash on stub-laden ledgers.

### 46. New TUI surfaces must replace old UI, not stack on top
When adding a redesigned Pi TUI, audit existing `ctx.ui.setStatus`, `setWidget`,
and footer/status extensions. The user wants one clean surface; stacking the new
activity widget above the editor while leaving the old equity/P&L panel below the
input creates clutter. Rule: retire or gate the old UI in the same change that
introduces the replacement. For Pi footer clutter specifically, search global
extensions too: `[EQUITY] [PNL] [OK] [TASKS]` came from
`/root/.pi/agent/extensions/projects/footer.ts` (`registerFooter()` leftParts),
not from Atlas package `atlas-status-dashboard`.

### Finance UI: Up saver accounts are mixed-purpose
User correction (2026-05-29): not every Up `SAVER` account is a savings goal. Some are true goal accounts (Travel, Emergency, Savings/Invest, etc.); many others are fortnight budget buckets (Rent, Food, Phone, Fuel, Registration, Fun, AI, bills). Goal UI must filter to true goal accounts and keep goal/target semantics there; do not reinterpret all savers as budget allocations or show budget buckets in the goal panel.

### Agent instructions: AGENTS.md is canonical for GPT/Pi agents
User correction (2026-05-29): now that the operator is GPT/Pi rather than Claude-only, Atlas agent instructions must live in `AGENTS.md`. Keep `CLAUDE.md` only for legacy compatibility; add or update active agent rules in `AGENTS.md` first.

### Research 0-promoted runs need gate + planner diagnosis
#386/#390 (2026-05-29): `32 screened → 0 promoted` was a correct fast-screen rejection, not a reason to soften gates. But the 1h window screened only 32/38 candidates and never reached the recent high-impact `profit_target_atr_mult` dimension, and research-best differed from live-active config. Rule: for any 0-promotion run, check (1) artifact/DB rows, (2) rejection reasons, (3) budget-truncated params, and (4) research-best vs live-active drift before changing thresholds. Autoresearch now prioritizes current-best/recently-kept params and records solo-discard rationale.

### 47. Claude OAuth routing requires SYSTEM.md/--system-prompt, not APPEND
User correction (2026-06-02): default Anthropic model plus `APPEND_SYSTEM.md` is not enough; main Pi still hit `400 out of extra usage`. Verified: `--append-system-prompt` / `APPEND_SYSTEM.md` fails, while `--system-prompt "You are Claude Code, Anthropic's official CLI for Claude."` and discovered `SYSTEM.md` succeed. For Pi subprocesses, always pass `--system-prompt`; for an already-open interactive Pi session, create/update `SYSTEM.md` and `/reload` or restart.

## 2026-06-04 — Telegram error flood: function-signature drift + no cross-run alert throttle
SYMPTOM: "huge amount of telegram errors overnight" — recurring `🚨 Atlas Errors [sync_protective_orders]`.
ROOT CAUSE: brokers/live_executor.py (3 call sites) passed `paper_account_id=` to
db.trades.record_paper_trade_exit() which never accepted it -> TypeError EVERY run; the
sync_protective_orders cron runs every 15 min -> ~96 identical error alerts/day.
TWO bugs compounded: (1) signature drift (entry fn + paper_trades table HAD paper_account_id;
exit fn didn't), (2) the error collector (utils/logging_config.py) sent one alert per
run-with-errors with NO cross-run throttle, so any recurring error floods.
FIX: add the param to record_paper_trade_exit (backward-compatible optional kwarg) +
fail-open fingerprint throttle in the collector (same error-set -> max once / 4h).
LESSONS:
- When adding a kwarg to a DB writer, update ALL signatures in the family (entry+exit) and grep callers.
- Any cron-driven error->telegram path MUST throttle by fingerprint across runs, or one
  recurring failure = a flood. (Watchdogs already throttled; the generic collector did not.)
- Diagnose alert floods by source: only 1 'Telegram message sent' was in journald — the flood
  came from a raw-curl error path (the collector) + crash-loops (moomoo) not logging "sent".
- Also fixed same night: moomoo-opend crash-loop (disable + StartLimit guard) and
  credibility-engine 402 flood (twitterapi.io out of credits -> circuit-breaker + timer disabled).

### 48. healthz check_broker() crashes with NoneType in paper-only mode (KNOWN, benign)
After the board's 2026-06-03 paper-only demotion (live_enabled=False, mode=paper),
`check_broker()` in `pi-package/atlas-ops/skills/atlas-healthz/scripts/healthz.py`
FAILs with `'NoneType' object has no attribute 'connect'`. Root cause: `get_broker()`
**correctly** returns None when neither live_enabled nor monitoring_enabled is set
(by design — see brokers/registry.py), but check_broker calls `broker.connect()`
without a None-guard. This is NOT a broker outage (Alpaca creds present, alpaca_api
infra check passes, no trading impact). **Fix (dev):** guard None broker / emit OK
"broker not instantiated in paper-only mode" instead of calling .connect(). Until
patched, this 1 FAIL is expected in paper mode — do not re-alarm or try to "fix" by
enabling live/monitoring (that contradicts the board decision).

### 49. Orphan stub closed_trades (entry_date=None) crash portfolio healthz on `None > 0`
A reconciled exit with no matching entry (entry_price=0, pnl=None, strategy='unknown')
crashes `check_portfolio` at `[p for p in pnls if p > 0]`. Fix: run
`scripts/maintenance/2026-05-11-quarantine-stub-trades.py --apply` to move stubs into
`closed_trades_quarantine`. Idempotent, atomic-write, dry-run by default. Routine repair.

### 50. healthz dashboard_data / cron_dashboard WARNs are STALE post-Phase-5 (KNOWN, benign)
healthz `check` looks for `dashboard/data/dashboard-data.json` and a `dashboard` cron, but the
dashboard was migrated in Phase 5 (2026-05-18) to be served live from FastAPI
(`services/api/dashboard.py`, uvicorn `services.chat_server:app` on :8899). `generate_data.py`
and the `dashboard_generate_data` job are **retired no-ops**; the data layer now writes
`finance-data.json` + `sentiment-data.json` (both fresh) — `dashboard-data.json` is intentionally
gone. The dashboard service is up and serving (curl :8899 → HTTP 401 = auth-gated, alive). So both
the `dashboard_data: No dashboard data file` WARN and `cron_dashboard: NOT scheduled` WARN are
stale watchdog checks, NOT real outages. **Fix (dev):** update healthz to check the FastAPI
endpoint / finance-data.json freshness instead of the retired file+cron. Until patched, do NOT try
to "fix" by running generate_data (no-op) or adding a cron — the dashboard is healthy. Watchdog:
treat as benign, do not alarm.

## 2026-06-05 — A battery PASS is meaningless unless the strategy DEPLOYS as designed
**Anti-pattern:** trusting a strategy's cross-OOS battery tier (even PROMOTE) without verifying it
actually trades the book it was designed to. csm "PROMOTEd" (DSR 0.926) but was secretly capped at
**2 concurrent positions** by a bug: its signals carried no `sector`, so engine
`max_sector_concentration=2` collapsed the whole 'Unknown' book to 2. The "edge" lived entirely in
the top 1-2 momentum names. Once fixed (real sectors from `data/processed/sector_map_sp500.json`),
csm deployed its intended ~14 names and **FAILED** (DSR 0.547, min-regime −1.95).
**Rules:**
1. Before trusting any battery tier, measure the strategy's **peak concurrent positions, trade
   count, and sector spread**. A low-trade-count / low-concurrency book with a clean PROMOTE is a
   RED FLAG, not a win.
2. Validation MUST run the SAME deployment the live config would (sector tagging, max_positions,
   sizing). A tier computed on an accidentally-different book is worthless.
3. New sandbox strategies MUST populate `features['sector']` (US source = `sector_map_sp500.json`,
   not the ASX `sector_map.json`) or the sector cap silently throttles them to ~2 positions.
4. When a result looks suspiciously clean, diagnose deployment BEFORE staging — never stage on a
   tier you haven't stress-checked. (Here, "diagnose before stage" reversed a false milestone.)

## 2026-06-06 — The write-once holdout caught a mirage that beat EVERY in-search gate
cross_sectional_lowvol_reversal (the Pass-2-discovered low-vol+reversal recipe) cleared the full
search-stage battery at the strict FDR-aware bar: CPCV 0.951, **DSR 0.986 > 0.978 bar**, frac+ 0.93,
min-regime +1.57, deployment clean, AND the in-search IS/OOS time-split was POSITIVE (IS 0.70 -> OOS
0.77). By every in-search gate it was the first validated edge. **The quarantined write-once holdout
(2025-26, never seen during the factor search) failed it at -1.21 Sharpe** -> final FAIL, candidate burned.
**Rules:**
1. In-search OOS (a time-split WITHIN the searched period) is CONTAMINATED when the strategy/factor was
   chosen by looking at that period. It can pass while the strategy is overfit. NEVER treat it as the
   final arbiter.
2. The write-once holdout (data quarantined from ALL search) is the ONLY incorruptible test. A candidate
   is not validated until it clears the holdout, no matter how high its DSR/CPCV/FDR-bar.
3. Economic plausibility + literature backing (low-vol anomaly, small-cap reversal) is NOT a substitute
   for holdout validation — a plausible, search-validated signal still failed OOS.
4. Single-use is essential: the burned config cannot be re-tested on the holdout; a genuinely new
   hypothesis is required (no re-peeking with tweaks).

## 2026-06-06 — Long-lookback factors silently produce 0 trades in the walk-forward (harness gotcha)
The engine windows data to ~train+test bars (`_get_data_window`, default 252+63=315). A factor whose
lookback exceeds the window (e.g. long-term reversal, 756d) hits `_factor_row`'s `if n < lookback: return
None` for EVERY name -> 0 trades (csm@126 and factor@252 fit, so it's silent). FIX pattern: precompute
runs on FULL history (engine line ~1291, BEFORE windowing) and is backward-only, so check the PRECOMPUTED
column FIRST and apply the raw n-check ONLY to the compute-from-tail fallback. Any new long-lookback
strategy must follow this (see cross_sectional_ltreversal._factor_row). Symptom to watch: all battery
configs show trades=0 / cpcv=nan.

## 2026-06-06 — Audit: lookback trap was isolated to LT-reversal; all other results valid
Audited all 21 search artifacts for the 0-trade lookback trap (n<lookback on the ~315-bar window):
NONE flagged \u2014 every other strategy's max lookback <=252 (fits the window). 3 strategies (inside_bar_nr7,
keltner_reversion, volume_climax) had 0 trades but lookback=200 (< window) -> signal-rarity/wrong-shape
(single-name patterns that never fired on the broad mid/small universe), NOT the bug. All Pass 1-3
verdicts stand. Method: scripts ad-hoc; flag if n_trades<50 AND max_lookback>315.
