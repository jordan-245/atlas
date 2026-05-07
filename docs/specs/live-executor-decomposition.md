# LiveExecutor Decomposition (Candidate #2)

**Status:** design sketch — engineering-ready
**Predecessor:** `BrokerRoutingPolicy` (commits `e9154e02`, `0e97ca4f`, `1dcd2aa0`)
**Target:** `brokers/live_executor.py` — currently 3,183 L, 34 methods (1 class), 8 entangled concerns
**Goal:** shrink LiveExecutor to ~400-500 L of pure orchestration; lift remaining concerns into focused modules

## Constraints discovered during research

These are non-negotiable. Decomposition must respect them:

1. **`__new__` bypass pattern is load-bearing.** 5 ops scripts (`sync_protective_orders.py`, `eod_settlement.py` ×2, `consolidation_close_positions.py`) instantiate LiveExecutor via `LiveExecutor.__new__(LiveExecutor)` and manually set `_broker`, `_connected`, `_mode`, `config`. There is a named test (`test_sync_protective_source_contains_mode_fix`) asserting this ordering. **`_broker`, `_connected`, `_mode`, `config` are de-facto public attributes — do not rename.**
2. **`EXECUTION_LOG` path has 3 external readers.** `research/brain/execution.py`, `scripts/slippage_calibration.py`, and `healthz.py` each independently resolve `PROJECT_ROOT / "logs" / "live_executions.jsonl"`. Path must stay stable when the journal moves to its own module.
3. **`execute_plan` return dict is a contract.** Cron callers (`execute_approved.py` for sp500/commodity_etfs/sector_etfs at 23:15-23:20 AEST), `/api/approve`, and Telegram approval all consume the same dict shape: `successful_entries`, `successful_exits`, `entries[]`, `exits[]`, `error`, `circuit_breaker_tripped`, `volatility_gate{}`. Cannot change.
4. **`self._policy` already routes 5 sites.** Lines 268, 1310, 1721, 2782, 2835, 3054 — 4× `is_paper`, 1× `trade_table()`, 1× construction. Do not re-extract. Extend the policy where a fork still exists (see PR1).

---

## File layout (ASCII tree)

```
brokers/
├── live_executor.py                  ~400-500 L  — orchestration only
├── routing_policy.py                 (existing; extend +is_dry_run)
├── execution_journal.py              NEW  ~ 50 L — _journal_entry + EXECUTION_LOG
├── preflight.py                      NEW  ~150 L — config + order + protected-check guards
├── protective_orders.py              NEW  ~430 L — place/cancel stop/TP, place_stops_for_plan
├── execution_reconciler.py           NEW  ~520 L — reconcile_entry_fills + reconcile_exit_fills
├── execution_analytics.py            NEW  ~200 L — get_fee/slippage/execution_history
└── alerts.py                         DEFERRED — see Decision 2 below
```

Net: **3,183 L → ~450 L LiveExecutor + ~1,350 L extracted modules + ~150 L tests-per-module = same LOC, far better locality and testability.**

---

## New modules — concrete shape

### `brokers/execution_journal.py` (~50 L)

What lives here: the JSONL appender and its path constant. Pure module-level — no class.
Public surface:
```python
EXECUTION_LOG: Path = PROJECT_ROOT / "logs" / "live_executions.jsonl"
def journal_entry(event: str, data: dict) -> None: ...
```
Path constant must stay stable for 3 external readers. Atomic `.tmp` write pattern preserved.

### `brokers/preflight.py` (~150 L)

What lives here: config-time and order-time safety validators. Pure functions, no broker state.
Public surface:
```python
class PreflightError(Exception): ...
def preflight_check_config(config: dict) -> list[str]: ...
def preflight_check_order(ticker, qty, price, side, config) -> list[str]: ...
def is_already_protected(broker, ticker: str) -> bool: ...
def protective_ledger_enabled() -> bool: ...
```
Migrated as-is from module-level helpers in live_executor.py (lines 88–209). Zero behavioural change.

### `brokers/protective_orders.py` (~430 L)

What lives here: protective-order lifecycle. Functions take `(broker, policy, journal_writer)` explicitly — no class state to thread.
Public surface:
```python
def place_protective_stop(broker, ticker, qty, stop_price, *, trailing_atr=0.0) -> OrderResult | None: ...
def cancel_protective_stop(broker, order_id: str, ticker: str = "") -> bool: ...
def cancel_open_orders_for_ticker(broker, ticker: str) -> int: ...
def place_take_profit(broker, ticker, qty, limit_price) -> OrderResult | None: ...
def place_stops_for_plan(broker, policy, plan: dict, trade_date: str) -> dict: ...
```
Internal: per-leg helper for STOP vs TRAILING_STOP construction (currently a branch inside `place_protective_stop`). Caller pattern: `from brokers.protective_orders import place_stops_for_plan; place_stops_for_plan(self._broker, self._policy, plan, td)`.

### `brokers/execution_reconciler.py` (~520 L)

What lives here: backfill loop that resolves broker fills against ledger/SQLite. Used by `sync_protective_orders.py` (the script) on a 15-min cadence.
Public surface:
```python
def reconcile_entry_fills(broker, policy, plan: dict | None = None) -> list[dict]: ...
def reconcile_exit_fills(broker, policy) -> list[dict]: ...
```
Internal: `_resolve_entry_zombie(...)` (the EBAY guard), `_classify_exit_reason(client_order_id)`, `_dedup_query(table)`. The `__new__` bypass pattern in `sync_protective_orders.py` is **eliminated** — script imports these functions directly.

### `brokers/execution_analytics.py` (~200 L)

What lives here: read-only history queries. Functions take `(broker)` only.
Public surface:
```python
def get_fee_analysis(broker, days: int = 90) -> dict: ...
def get_slippage_analysis(broker, days: int = 90) -> dict: ...
def get_execution_history(broker, days: int = 30) -> dict: ...
```
Currently zero coverage — this PR is the first chance to add unit tests.

### What stays in `live_executor.py` (~450 L)

- `__init__`, `connect`, `disconnect`
- Properties: `is_live_enabled`, `is_dry_run`, `safety`, `max_daily_loss_pct`
- Circuit-breaker state: `_reset_circuit_breaker_if_new_day`, `_capture_start_equity`, `_check_circuit_breaker`
- Halt machinery: `emergency_halt`, `clear_halt`, `check_market_state`, `_run_volatility_gate`
- Cached account info: `_get_cached_account_info`
- Thin delegations: `get_account_info`, `get_positions`, `get_open_orders`
- **Orchestration**: `execute_plan` (slimmed from 404 L → ~200 L), `_execute_entry` (slimmed from 509 L → ~250 L), `_execute_exit` (slimmed from 349 L → ~180 L), `place_order` (~40 L), `cancel_unfilled_limits` (~95 L stays — it's a bulk-cancel operation, not a reconcile)
- `_error_report` helper

`_execute_entry` and `_execute_exit` slim by lifting: protective-order helpers → `protective_orders`, journal calls → `execution_journal`, preflight → `preflight`. The DB write fork at lines 1310 / 1721 stays inline — it's a 1-liner gated by `self._policy.is_paper` already.

---

## Migration strategy: Hybrid (Strategy C)

**Why C over A/B:** Atlas trades live capital ($5,289 equity, 7 open positions). Big-bang (A) is unacceptable risk. Pure strangler (B) is too many small commits — review fatigue and stale branches. Three logical PRs, each shippable independently, each green at every commit.

### PR1 — Leaf extractions (LOW risk)

Goal: lift everything with zero coupling to broker state.

| # | Commit | Files touched | Risk |
|---|--------|---------------|------|
| 1 | `feat: extract execution_journal module` | new `execution_journal.py`; live_executor.py imports `journal_entry` | LOW |
| 2 | `feat: extract preflight module` | new `preflight.py`; live_executor.py imports the 4 functions | LOW |
| 3 | `feat: extract execution_analytics module` | new `execution_analytics.py`; live_executor.py methods become 2-line delegations | LOW |
| 4 | `feat: add is_dry_run to BrokerRoutingPolicy` | `routing_policy.py` adds property; live_executor.py reads `self._policy.is_dry_run` | LOW |
| 5 | `chore: remove redundant self._mode` | live_executor.py — drop attr, update 2 log lines to `self._policy.mode` | LOW |
| 6 | `chore: wire policy.protective_table() at the one inline site` | live_executor.py line ~1760 | LOW |

**Test gate:** full pytest green; premarket dry-run identical output (see §Test Strategy).
**Rollback:** revert single commit. Each touches ≤2 files.

**PR1 completion status (2026-05-07):**
- PR1.1 ✅ fd1633ee — execution_journal module extracted
- PR1.2 ✅ a862313c — preflight module extracted
- PR1.3 ✅ 6fa43668 — execution_analytics module extracted
- PR1.4 ✅ 404778e7 — is_dry_run added to BrokerRoutingPolicy
- PR1.5 DEFERRED — drop self._mode deferred to PR3 (4 __new__ bypass scripts set _mode not _policy; lines 2801/2806 in reconcile_entry_fills reachable from bypass paths)
- PR1.6 AUDITED (no-op) — grep confirmed zero inline `paper_position_protective_orders` ternaries in live_executor.py; protective_table() exists in routing_policy.py (PR-predecessor commit) but has no wire-up site yet in live_executor.py; the inline site referenced by the spec is in future PR2/PR3 code (place_stops_for_plan / _execute_exit paths not yet restructured)

### PR2 — Protective orders (MED risk)

| # | Commit | Files touched | Risk |
|---|--------|---------------|------|
| 7 | `feat: extract protective_orders module` | new `protective_orders.py`; live_executor.py methods become delegations | MED |
| 8 | `test: protective_orders unit tests` | new `tests/test_protective_orders.py` (covers RCA #7 double-stop guard, trailing branch, OCO/bracket paths) | LOW |

**Test gate:** dry-run + `place_stops_for_plan` integration test must pass.
**Rollback:** revert commit 7. The 5 protective methods on LiveExecutor become 2-line wrappers — if downstream callers exist (none found, but `__new__` bypass scripts could call them), they continue to work.
**Watch:** the B.0 protective ledger writes inside `place_stops_for_plan` (lines ~2150-2200). Move with the function, gated by env var as today.

### PR3 — Reconciler + slim core (MED-HIGH risk)

| # | Commit | Files touched | Risk |
|---|--------|---------------|------|
| 9 | `feat: extract execution_reconciler module` | new `execution_reconciler.py`; live_executor.py methods → 2-line delegations | MED |
| 10 | `refactor: sync_protective_orders.py imports reconciler functions directly` | `scripts/sync_protective_orders.py` — removes `__new__` bypass | MED |
| 11 | `refactor: slim _execute_entry by lifting safety guards into composable list` | live_executor.py — guards run as `for guard in [kill_switch, cross_universe, gross_exposure, leverage]: ...` | HIGH |
| 12 | `refactor: slim _execute_exit similarly` | live_executor.py | HIGH |
| 13 | `refactor: extract execute_plan phase helpers` | live_executor.py — `_run_exits_phase`, `_run_entries_phase`, `_place_stops_phase` as private methods | MED |

**Test gate:** PR3 commits 11-13 require BOTH full pytest AND the morning dry-run identity test (see below) green at each commit.
**Rollback:** commits 11-13 are the danger zone. Each must be independently revertable. If commit 11 ships and commit 12 breaks, revert 12 alone — the codebase is left in a half-refactored but working state.

**Roll-back plan if any commit breaks live trading:**
- Atlas runs at 23:15 AEST; window opens at 23:30. **Hard rule:** PR3 commits 11-13 must be merged before 12:00 AEST same day, leaving 11h of paper-validation runway before next live cycle.
- `git revert <sha>` + redeploy is ~5 min. The `__new__` bypass tests catch the most likely break (attribute renaming).
- If a commit ships and the night cycle errors: kill-switch via `HALT_FILE` write halts further trades, then revert.

---

## Test strategy

### What becomes unit-testable in isolation post-decomposition

| New module | Unit test categories | Currently covered? |
|-----------|----------------------|--------------------|
| `execution_journal` | Atomic-write semantics; JSONL format; permission errors | partial via integration |
| `preflight` | Each `preflight_check_*` returns expected error list per config; `is_already_protected` mocks broker | partial |
| `protective_orders` | RCA #7 double-stop guard; trailing vs static branch; OCO/bracket leg construction; cancel on already-filled order | partial via integration |
| `execution_reconciler` | EBAY zombie guard; client_order_id reason inference; dedup SELECT against `paper_trades` vs `trades`; close-protective-record semantics | strong existing coverage — migrate |
| `execution_analytics` | Per-side slippage math; calibration recommendation thresholds; fee aggregation | **none today — first coverage** |

### Tests to migrate / split / rewrite

- `tests/test_live_executor*.py` (42 files, 334+ tests) — most stay put on LiveExecutor. The reconcile-fills, protective-orders, and preflight tests **move** with their target modules.
- `test_sync_protective_source_contains_mode_fix` — must pass unchanged after PR3 commit 10. The `__new__` bypass goes away in `sync_protective_orders.py`, but the test asserts an ordering that no longer exists once the script imports reconciler functions directly. **Update the test to assert the new contract: reconciler functions called with the right `(broker, policy)` args.**
- New tests required: `test_protective_orders.py`, `test_execution_reconciler.py`, `test_execution_analytics.py`, `test_execution_journal.py`, `test_preflight.py` — categories above.

### Acceptance gate per commit

1. `pytest -x` full suite green (currently ~334 tests).
2. **Morning dry-run identity test (the critical one):**
   ```bash
   # Pre-refactor baseline (capture once before PR1 commit 1)
   ATLAS_DRY_RUN=1 python -m scripts.execute_approved sp500 --capture-output baseline.json

   # After every commit
   ATLAS_DRY_RUN=1 python -m scripts.execute_approved sp500 --capture-output current.json
   diff baseline.json current.json   # must be empty
   ```
   Same test for `sync_protective_orders.py` against an `ATLAS_DRY_RUN=1` snapshot.
3. JSONL journal output for the dry-run must be byte-identical (or differ only in timestamp field).

### Coverage of the 5 currently-untested methods

- `get_execution_history`, `get_fee_analysis`, `get_slippage_analysis` → first tests in PR1 commit 3.
- `cancel_unfilled_limits` → first tests in PR3 (stays in live_executor.py).
- `check_market_state` → first tests in PR3 (stays in live_executor.py).
- `place_stops_for_plan` → first tests in PR2 commit 8.

---

## Decision points + recommendations

### D1. The 6 safety guards — class hierarchy or flat sequence?

**Recommendation: flat sequence in `preflight.py` + composable list in `_execute_entry`.**

Rationale: 6 guards is below the threshold where polymorphism pays. A `Guard` ABC with 6 subclasses is 200 L of ceremony for 6 functions of ~30 L each. Two of the 6 are already external (`cross_universe_guard`, `gross_exposure_gate` — imported as functions). Flat composition matches the existing pattern.

Shape: `def kill_switch_guard(entry, broker, config) -> tuple[bool, str | None, dict | None]: ...` returning `(proceed, reason_if_blocked, telegram_alert_payload_or_None)`. `_execute_entry` runs them as `for guard in [...]: proceed, reason, alert = guard(...); if not proceed: return abort(reason, alert)`.

Defer the class hierarchy until guard count exceeds 10 OR a dynamic-config use case appears.

### D2. Telegram alerts — extract `AlertManager` now?

**Recommendation: defer to a separate refactor (candidate #7).**

Rationale: alerts are already half-extracted — `cross_universe_guard.telegram_alert`, `gross_exposure_gate.telegram_alert_gross_exposure`, `volatility_gate.send_volatility_alert` are external functions. Inline sites in live_executor (circuit-breaker trip, kill-switch entry block, leverage gate block) are tightly coupled to local context. Extracting a unified `AlertManager` requires deciding the message-formatting contract across all 6 sites — own scoped refactor. Decomposition #2 ships fine without it.

### D3. `execution_reconciler.py` — extract or defer?

**Recommendation: extract (PR3 commit 9).**

Rationale: 514 combined lines, two methods, dense existing test coverage, AND extracting it lets PR3 commit 10 eliminate the `__new__` bypass in `sync_protective_orders.py`. That's a strict architectural win. Coupling cost is low — only `(broker, policy)` need to be threaded.

### D4. `place_stops_for_plan` AND `sync_all_protective_orders` — both in `protective_orders.py`?

**Note:** `sync_all_protective_orders` is **not** a method on LiveExecutor — it's the `scripts/sync_protective_orders.py` orchestrator. The LiveExecutor method is `place_stops_for_plan`.

**Recommendation:** `place_stops_for_plan` moves into `protective_orders.py` (PR2). The script `sync_protective_orders.py` stays as an orchestrator script but switches from `__new__`-bypass + `reconcile_entry_fills`/`reconcile_exit_fills` calls to direct imports of the new reconciler functions (PR3 commit 10). No new module split needed.

### D5. (Bonus) Should the inline DB-write paper/live forks at lines 1310/1721 be extracted to a `TradeWriter`?

**Recommendation: NO, keep inline.**

Rationale: the routing decision is already in `self._policy.is_paper` — a 1-line `if` is the cheapest possible expression. A `TradeWriter` abstraction adds an interface for ~30 lines of code. Pulls weight only if a third backend (e.g. cloud DB) appears; defer.

---

## Summary

- **3 PRs, 13 commits, ~2 weeks calendar at normal cadence.**
- **PR1 (LOW risk):** journal + preflight + analytics + policy extensions. Days 1-3.
- **PR2 (MED risk):** protective orders. Days 4-6.
- **PR3 (MED-HIGH risk):** reconciler + slim core + drop `__new__` bypass. Days 7-10.
- **Final state:** LiveExecutor ~450 L (down from 3,183 L — 86% reduction), 6 focused modules each <600 L, 5 untested methods now have first coverage, `__new__` bypass pattern eliminated from one ops script.
- **What is explicitly NOT in scope (deferred):** `AlertManager` extraction, `TradeWriter` abstraction, `_execute_entry`/`_execute_exit` further decomposition beyond guard-list lifting.
