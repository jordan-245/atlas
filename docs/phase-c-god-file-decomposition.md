# Phase C.4 — God File Decomposition

**Status**: PLANNED — highest-risk phase. Implementation deferred until C.1 state machine is landed and B.2 cutover is complete.  
**Estimated effort**: 2+ engineer-weeks  
**Pre-requisites**: C.1 state machine (DB-enforced transitions in place), B.2 cutover (canonical reconcile path active), full regression suite passing.

---

## 1. Motivation

Three files account for **75% of the reported bug surface** in Atlas:

| File | Lines | % of reported bugs | Primary issues |
|------|-------|--------------------|----------------|
| `brokers/live_executor.py` | 2,914 | ~35% | Mixed concerns: entry, exit, reconcile, PDT, leverage, protective orders all in one class |
| `services/chat_server.py` | 3,385 | ~25% | API routes, WebSocket, business logic, authentication all interleaved |
| `db/atlas_db.py` | 2,891 | ~15% | CRUD functions for 15+ concerns in a single 2,891-line module |

Large files correlate with:
- **Merge conflicts** (multiple concurrent writers touch the same file)
- **Import coupling** (tests must import 2,800 lines to test 20)
- **Review fatigue** (reviewers stop reading after ~400 lines)
- **Hidden side effects** (module-level state shared across unrelated functions)

---

## 2. Decomposition Targets

### 2a. `brokers/live_executor.py` → `executor/` package

**Current structure**: One 2,914-line class `LiveExecutor` with all concerns mixed.

**Proposed structure**:

```
executor/
  __init__.py          # Re-exports LiveExecutor for backward compat
  entry.py             # _execute_entry, _build_order_request, atomic bracket logic
  exit.py              # _execute_exit, _build_exit_order, trailing stop logic
  protective.py        # place_order wrapper, _is_market_halted, sync_protective_orders call
  reconcile.py         # reconcile_entry_fills, reconcile_exit_fills
  pdt.py               # PDT detection, retry window, deferred execution logic
  leverage.py          # gross_exposure_guard, margin calculation, cross-universe check
```

**Re-export shim** (`executor/__init__.py`):
```python
# Preserves old import path: `from brokers.live_executor import LiveExecutor`
from executor.entry import EntryMixin
from executor.exit import ExitMixin
from executor.protective import ProtectiveMixin
from executor.reconcile import ReconcileMixin
from executor.pdt import PDTMixin
from executor.leverage import LeverageMixin

class LiveExecutor(EntryMixin, ExitMixin, ProtectiveMixin,
                   ReconcileMixin, PDTMixin, LeverageMixin):
    """Assembled LiveExecutor — backward-compatible interface."""
    pass
```

Old import `from brokers.live_executor import LiveExecutor` continues to work
during transition via the shim at `brokers/live_executor.py`:
```python
from executor import LiveExecutor  # forward to new location
```

**Estimated sub-file sizes**:
- `entry.py` ~350 lines
- `exit.py` ~400 lines  
- `protective.py` ~200 lines
- `reconcile.py` ~500 lines
- `pdt.py` ~200 lines
- `leverage.py` ~150 lines

### 2b. `services/chat_server.py` → `services/api/` + `services/ws/`

**Current structure**: One 3,385-line FastAPI app with all routes inline.

**Proposed structure**:

```
services/
  app.py               # FastAPI app construction, middleware, auth
  api/
    __init__.py
    portfolio.py        # /api/dashboard-data, /api/positions/risk, /api/equity-curve
    research.py         # /api/research/*, /api/signals/*, /api/regime/*
    finance.py          # /api/finance/*, /api/heartbeats, /api/system/health
    plans.py            # /api/plans, /api/signals, /api/strategy-health
    risk.py             # /api/risk/*, /api/positions/risk
  ws/
    chat.py             # /ws/chat WebSocket handler
```

**Router pattern** (FastAPI native):
```python
# services/app.py
from fastapi import FastAPI
from services.api.portfolio import router as portfolio_router
from services.api.research import router as research_router

app = FastAPI()
app.include_router(portfolio_router, prefix="/api")
app.include_router(research_router, prefix="/api")
```

**Chat server shim** (`services/chat_server.py`):
```python
# Preserves `python3 services/chat_server.py` startup
from services.app import app
import uvicorn
if __name__ == "__main__":
    uvicorn.run(app, ...)
```

**Estimated sub-file sizes**:
- `api/portfolio.py` ~600 lines
- `api/research.py` ~500 lines
- `api/finance.py` ~400 lines
- `api/plans.py` ~300 lines
- `api/risk.py` ~350 lines
- `ws/chat.py` ~500 lines
- `app.py` ~200 lines

### 2c. `db/atlas_db.py` → `db/` sub-modules

**Current structure**: One 2,891-line file with CRUD for every concern.

**Proposed structure**:

```
db/
  __init__.py          # Re-exports get_db, init_db
  connection.py        # get_db(), init_db(), _db_path_override, WAL setup
  trades.py            # record_trade_entry, record_trade_exit, get_open_trades, transition_trade
  equity.py            # record_equity_snapshot, get_equity_curve, get_closed_trades
  signals.py           # record_signal, get_signals, get_regime_history
  regime.py            # record_regime, get_regime_history, record_regime_distribution
  broker_orders.py     # get_broker_fill_price, get_broker_orders, upsert_broker_order
  misc.py              # heartbeats, system_log, news_intel, portfolio_snapshots
```

**Re-export shim** (`db/atlas_db.py`):
```python
# Backward compat: `from db.atlas_db import get_db` still works
from db.connection import get_db, init_db, _db_path_override  # noqa: F401
from db.trades import record_trade_entry, record_trade_exit    # noqa: F401
from db.equity import record_equity_snapshot                   # noqa: F401
# ... all other public names
```

**Estimated sub-file sizes**: 250–450 lines each.

---

## 3. Migration Strategy

### Phase 1 — Extract with re-export shim (zero-breakage)

1. Create target package directories and `__init__.py` stubs.
2. Move functions from god file to sub-module, one logical group at a time.
3. Immediately add re-export in the god file so existing imports still resolve.
4. Run full regression suite after each move — must stay green.
5. Never rename or change function signatures during this phase.

### Phase 2 — Update call sites (optional optimization)

After Phase 1 is stable (1+ week in production):
- Update internal call sites to use direct sub-module imports (avoids the shim indirection).
- Old import paths remain valid via the shim indefinitely (no forced breakage).

### Phase 3 — Deprecate god file (future, optional)

After 3+ months with zero import-path regressions:
- Add `DeprecationWarning` to the god-file shim's `__init__.py`.
- Migrate any remaining external consumers (scripts, tests).
- Delete god file.

This is genuinely optional — the re-export shim carries negligible runtime cost.

---

## 4. Risks

**This is the HIGHEST-RISK phase in the C roadmap.** Risks in descending severity:

| Risk | Severity | Mitigation |
|------|----------|------------|
| Circular import: `executor/entry.py` imports `db/trades.py` which imports `broker/types.py` which imports `executor/...` | CRITICAL | Map full import graph before any move. Resolve cycles by extracting shared types to `core/types.py`. |
| Module-level side effects (singletons, global state) break on package split | HIGH | Audit all module-level statements (`_regime_model_cache`, `_client_lock`, `_wal_initialized_paths`). Move to `core/singletons.py` before splitting. |
| Test suite imports the god file directly and breaks on split | HIGH | All 200+ test files reference `from db.atlas_db import ...` — the shim preserves this. Verify with `grep -r "from db.atlas_db import" tests/` before cutover. |
| FastAPI router split changes middleware application order | MEDIUM | Auth middleware must apply before all routers; test with `TestClient` after each router extraction. |
| git blame / git log history fragmented across split | LOW | Use `git mv` + copy-then-delete pattern to preserve blame history. |

**Rule**: Run the full test suite (`python3 -m pytest tests/ --timeout=30`) after every sub-file move. Any red test blocks the next move.

---

## 5. Pre-requisites

1. **C.1 state machine landed** — All write paths go through `db.transition_trade()`.
   This removes the most complex cross-concern coupling from `live_executor.py`
   (which currently mixes state transitions with order placement).

2. **B.2 cutover complete** — `core/reconcile.py` is the canonical reconcile path.
   `reconcile_entry_fills` / `reconcile_exit_fills` in `live_executor.py` can then be
   moved to `executor/reconcile.py` without worrying about the old `reconcile_ledger.py`
   interaction.

3. **Full regression suite passing** — Before any god-file move, establish a clean
   green baseline. The 839 grandfathered bare-except offenders should be reduced first
   (or at minimum not increased) so coverage data is reliable.

4. **Import graph mapped** — Run `pydeps brokers/live_executor.py` and `pydeps db/atlas_db.py`
   to visualize actual import dependencies before designing the sub-module split.

---

## 6. Estimated Effort

| Target | Sub-task | Estimate |
|--------|----------|----------|
| `db/atlas_db.py` | Import graph + sub-module design | 1 day |
| `db/atlas_db.py` | Extract 6 sub-modules + shim | 3 days |
| `db/atlas_db.py` | Regression suite green | 1 day |
| `services/chat_server.py` | Router extraction (5 routers) | 4 days |
| `services/chat_server.py` | Middleware order validation | 1 day |
| `brokers/live_executor.py` | Import graph (highest complexity) | 2 days |
| `brokers/live_executor.py` | Extract 6 sub-modules + shim | 5 days |
| `brokers/live_executor.py` | Regression + integration tests | 2 days |
| **Total** | | **~19 days (~4 weeks, 1 engineer)** |

Minimum viable version (db split only, easiest target): ~5 days.  
Full decomposition (all three god files): ~4 weeks.

---

## 7. Action Items

- [ ] Run `pydeps` import graph on all 3 god files; document cycles
- [ ] Audit module-level singletons in `live_executor.py` (`_regime_model_cache`, etc.)
- [ ] Extract `core/types.py` with shared dataclasses to break anticipated cycles
- [ ] **Start with `db/atlas_db.py`** (lowest risk, best test coverage)
  - [ ] `db/connection.py` — `get_db`, `init_db`, `_db_path_override`
  - [ ] `db/trades.py` — trade CRUD
  - [ ] `db/equity.py` — equity curve + portfolio snapshots
  - [ ] `db/signals.py` — signals + regime
  - [ ] `db/broker_orders.py` — broker_orders helpers
  - [ ] `db/misc.py` — heartbeats, system_log, news_intel
  - [ ] Re-export shim in `db/atlas_db.py`
  - [ ] Full test suite green
- [ ] **Then `services/chat_server.py`** (medium risk; FastAPI router split)
  - [ ] `services/app.py` — app factory + middleware
  - [ ] 5 API router files
  - [ ] `services/ws/chat.py`
  - [ ] Full test suite green (including UI audit)
- [ ] **Finally `brokers/live_executor.py`** (highest risk; last)
  - [ ] 6 mixin files in `executor/`
  - [ ] `brokers/live_executor.py` shim
  - [ ] Full integration test suite green
- [ ] Update `docs/architecture.md` with new package structure
- [ ] Add import-graph validation to pre-commit hook (prevent new cycles)

---

*Design doc authored: 2026-04-29. Review before implementation kickoff.*
