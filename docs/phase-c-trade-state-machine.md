# Phase C.1 — Trade State Machine (Formal, DB-Enforced)

**Status**: PLANNED — implementation deferred until Phase B.2 cutover is complete and 7-day zero-divergence window closes.  
**Estimated effort**: 2–3 weeks  
**Pre-requisites**: B.2 cutover complete; `core/reconcile.py` shadow mode validated; broker_orders table seeded.

---

## 1. Motivation

The current `trades` table uses a single `status` column with ad-hoc string values
(`'open'`, `'closed'`, `'error'`). This design has produced a class of silent bugs:

| Hotspot | Description |
|---------|-------------|
| **stop_order_id loss** | `sync_protective_orders` places a stop then fails to write `stop_order_id` back to SQLite. The row stays `status='open'` but has no canonical stop reference → de-link between DB and broker. |
| **TP-naked positions** | Entry fills, stop placed, TP never confirmed. `status='open'` says nothing about whether the position is actually protected. |
| **Plan duplicate INSERT** | `reconcile_entry_fills` can insert a trade row that already exists (no atomic check-then-insert) because there is no `SUBMITTED` gate. |
| **Phantom exits** | EOD settlement closes a trade in SQLite before the broker fill is confirmed. `status='closed'` but the broker still holds the position. |

A formal state machine makes illegal states unrepresentable and gives every write
path a deterministic guard.

---

## 2. State Definitions

| State | Description | DB writes allowed in this state |
|-------|-------------|--------------------------------|
| `PROPOSED` | Signal generated; plan entry created | Plan row, signal row |
| `APPROVED` | Passed risk + leverage gates; queued for execution | Config snapshot, plan approval timestamp |
| `SUBMITTED` | Entry order placed at broker; awaiting fill | `entry_order_id`, `submitted_at` |
| `FILLED` | Entry fill confirmed by broker | `entry_price`, `filled_at`, `qty` |
| `PROTECTED` | Stop **and** TP both placed + confirmed at broker | `stop_order_id`, `tp_order_id`, `stop_price` |
| `CLOSING` | Exit order placed at broker; awaiting fill | `exit_order_id`, `closing_at` |
| `CLOSED` | Exit fill confirmed by broker fill event | `exit_price`, `pnl`, `exit_at` |
| `SETTLED` | `broker_orders` row reconciled; P&L finalized | MAE/MFE computed, `regime_at_exit` recorded |

Terminal states: `CLOSED`, `SETTLED` (no further transitions).  
Error state: `ERROR` — catchall for unrecoverable mismatches; requires manual intervention.

---

## 3. Legal Transitions

```
PROPOSED → APPROVED → SUBMITTED → FILLED → PROTECTED → CLOSING → CLOSED → SETTLED
                                                                          ↑
                                                           ERROR (recoverable) ─┘
```

| From       | To         | Trigger                                      | Guard                                     |
|------------|------------|----------------------------------------------|-------------------------------------------|
| PROPOSED   | APPROVED   | `generate_plan()` approval gate passes        | Risk check + leverage gate pass           |
| APPROVED   | SUBMITTED  | `LiveExecutor._execute_entry()` places order  | `entry_order_id` not NULL                 |
| SUBMITTED  | FILLED     | `reconcile_entry_fills()` finds fill event    | Broker fill event with matching order ID  |
| SUBMITTED  | ERROR      | Order rejected / expired at broker            | Broker status = rejected/expired          |
| FILLED     | PROTECTED  | `sync_protective_orders` confirms stop+TP     | Both `stop_order_id` + `tp_order_id` set  |
| PROTECTED  | CLOSING    | Exit signal fired (stop hit, TP hit, manual)  | Exit order placed at broker               |
| CLOSING    | CLOSED     | `reconcile_exit_fills()` confirms fill        | Broker fill event with exit order ID      |
| CLOSING    | ERROR      | Exit order rejected / position mismatch       | Broker rejects exit order                 |
| CLOSED     | SETTLED    | `sync_broker_orders` reconciles fill price    | `broker_orders` row present + filled      |
| ERROR      | SUBMITTED  | Manual retry after investigation              | Operator-approved re-entry                |

**Disallowed transitions** (enforced by `db.transition_trade`):
- CLOSED → PROTECTED (cannot re-protect a closed trade)
- SETTLED → anything (terminal state)
- Any backward transition without an explicit ERROR gateway

---

## 4. DB Enforcement

### 4a. New column

```sql
ALTER TABLE trades ADD COLUMN state TEXT DEFAULT NULL
    CHECK (state IS NULL OR state IN (
        'PROPOSED', 'APPROVED', 'SUBMITTED', 'FILLED',
        'PROTECTED', 'CLOSING', 'CLOSED', 'SETTLED', 'ERROR'
    ));
```

The `NULL` default is intentional: during the shadow-write phase (Step 3 below),
existing rows have `NULL` state while the backfill runs. The CHECK constraint only
fires on explicit writes, not on legacy NULL rows.

### 4b. Transition helper

```python
# db/atlas_db.py
_LEGAL_TRANSITIONS: dict[str | None, set[str]] = {
    None:         {"PROPOSED", "SUBMITTED"},   # legacy rows
    "PROPOSED":   {"APPROVED", "ERROR"},
    "APPROVED":   {"SUBMITTED", "ERROR"},
    "SUBMITTED":  {"FILLED", "ERROR"},
    "FILLED":     {"PROTECTED", "CLOSING", "ERROR"},
    "PROTECTED":  {"CLOSING", "ERROR"},
    "CLOSING":    {"CLOSED", "ERROR"},
    "CLOSED":     {"SETTLED", "ERROR"},
    "SETTLED":    set(),                        # terminal
    "ERROR":      {"SUBMITTED", "PROPOSED"},   # operator retry
}

def transition_trade(trade_id: int, to_state: str, *, db=None) -> None:
    """Atomically move a trade to a new state, enforcing legal transitions."""
    with (get_db() if db is None else contextlib.nullcontext(db)) as conn:
        row = conn.execute(
            "SELECT state FROM trades WHERE id = ?", (trade_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"Trade {trade_id} not found")
        from_state = row["state"]
        allowed = _LEGAL_TRANSITIONS.get(from_state, set())
        if to_state not in allowed:
            raise ValueError(
                f"Illegal trade transition: {from_state!r} → {to_state!r} "
                f"(trade_id={trade_id})"
            )
        conn.execute(
            "UPDATE trades SET state = ? WHERE id = ?",
            (to_state, trade_id),
        )
```

---

## 5. Migration Strategy

### Step 1 — Add `state` column (backward-compatible, NULL default)

```python
# scripts/migrations/phase-c-1-add-state-column.py
with get_db() as conn:
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN state TEXT DEFAULT NULL")
    except sqlite3.OperationalError:
        pass  # already added
```

Zero downtime. All existing code still works — reads/writes state=NULL until backfill.

### Step 2 — Backfill existing rows (infer state from available columns)

| Condition | Inferred state |
|-----------|---------------|
| `status = 'closed'` | `SETTLED` |
| `stop_order_id IS NOT NULL AND tp_order_id IS NOT NULL` | `PROTECTED` |
| `stop_order_id IS NOT NULL AND tp_order_id IS NULL` | `FILLED` (partial protection) |
| `entry_price IS NOT NULL AND stop_order_id IS NULL` | `FILLED` |
| `status = 'open'` (fallback) | `SUBMITTED` |
| `status = 'error'` | `ERROR` |

Backfill script: `scripts/migrations/phase-c-1-backfill-states.py`.  
Idempotent (re-run safe). Estimated runtime: < 5 seconds on 200 rows.

### Step 3 — Shadow-write phase (dual-write, no enforcement)

All write paths call `transition_trade(trade_id, to_state)` in addition to existing
`status` writes. The function logs a WARNING on illegal transitions but does not raise.
This exposes transition violations in logs before enforcement is enabled.

Duration: at least one full trading week (5 days), monitoring `system_log` for
`ILLEGAL_TRANSITION` entries.

### Step 4 — Enforcement phase

After zero violations in Step 3:
1. Remove the `WARNING`-only mode from `transition_trade` (replace with `raise`).
2. Add `CHECK` constraint via table-recreation migration (SQLite requires this pattern).
3. Remove legacy `status` column writes from all call sites (or keep for
   backward-compatibility with legacy readers).
4. Add `NOT NULL` constraint to `state` column.

---

## 6. Risks and Rollback

| Risk | Severity | Mitigation |
|------|----------|------------|
| Backfill assigns wrong state to an edge-case trade | Medium | Manual review of all 200 rows before enabling enforcement; state can be corrected via `transition_trade` with ERROR → target |
| New code path raises `ValueError` on an unexpected transition | High | Shadow mode catches all violations before enforcement; feature flag `ATLAS_STATE_MACHINE_ENFORCE=0` disables raises |
| Third-party scripts bypass `transition_trade` and write `status` directly | Medium | Lint rule: ban direct `UPDATE trades SET status =` without calling `transition_trade`; enforced in pre-commit hook |
| `SETTLED` trades need amendment (e.g., MAE/MFE recompute) | Low | Add `SETTLED` → `AMENDING` → `SETTLED` micro-transition for audit purposes |

**Rollback path**: `state` column is nullable. Dropping enforcement reverts to
shadow mode. Dropping the column entirely restores pre-C.1 behavior with no data loss
(all business logic reads `status`, not `state`, until cutover).

---

## 7. Estimated Effort

| Sub-task | Estimate |
|----------|----------|
| Step 1+2: migration + backfill | 2 days |
| Step 3: shadow-write instrumentation in all 12 write paths | 3 days |
| Shadow monitoring (passive) | 5 trading days |
| Step 4: enforcement + lint rule | 2 days |
| Tests (unit + integration) | 3 days |
| **Total** | **~3 weeks** |

---

## 8. Pre-requisites

1. **B.2 cutover complete** — `core/reconcile.py` is the canonical reconcile path;
   old scripts retired. State machine transitions must only be called from the new
   consolidated paths (not from 3 different legacy scripts).
2. **B.0 wire-up mature** — `position_protective_orders` table is live and
   `sync_protective_orders` writes `stop_order_id`/`tp_order_id` reliably.
3. **broker_orders table seeded** — Required to correctly infer `SETTLED` vs `CLOSED`
   in Step 2 backfill (needs `broker_orders` fill confirmation).

---

## 9. Action Items / Sub-tasks

- [ ] Write migration: `scripts/migrations/phase-c-1-add-state-column.py`
- [ ] Write backfill: `scripts/migrations/phase-c-1-backfill-states.py`
- [ ] Implement `transition_trade()` in `db/atlas_db.py` (shadow mode first)
- [ ] Instrument `LiveExecutor._execute_entry()` → APPROVED→SUBMITTED
- [ ] Instrument `reconcile_entry_fills()` → SUBMITTED→FILLED
- [ ] Instrument `sync_protective_orders` → FILLED→PROTECTED
- [ ] Instrument `eod_settlement` / exit paths → PROTECTED→CLOSING→CLOSED
- [ ] Instrument `sync_broker_orders` → CLOSED→SETTLED
- [ ] 5-day shadow monitoring window with `system_log` alert on ILLEGAL_TRANSITION
- [ ] Enforcement migration (table-recreation pattern, SQLite CHECK constraint)
- [ ] Remove `status` column writes from all call sites (or keep as alias)
- [ ] Add `test_trade_state_machine.py` (legal/illegal transition assertions)
- [ ] Update `db/schema.sql` with `state` column + CHECK constraint

---

*Design doc authored: 2026-04-29. Review before implementation kickoff.*
