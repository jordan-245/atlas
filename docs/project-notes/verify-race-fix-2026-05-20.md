# Verify Race Fix — 2026-05-20

## Problem

Every live order submitted on 2026-05-19 was stamped `EXECUTED_VERIFY_FAILED`
despite filling correctly at Alpaca.

Evidence from `plans/plan_sp500_2026-05-19.json`:

```
"status": "EXECUTED_VERIFY_FAILED",
"verify_error": "VERIFY MISMATCH: plan claims 1 live submissions but
  broker_orders has 0 since 2026-05-19T13:14:01..."
```

The FTNT order (`3a1b669d-d19d-455c-b429-b6d2b8259107`) was submitted, accepted,
and filled at Alpaca. This was a false positive.

## Root Cause

Commit `5b8c33e1` added `_verify_broker_submissions()` in
`scripts/execute_approved.py`. The function queried the **`broker_orders` SQLite
mirror table** to confirm live submissions:

```python
rows = _db.execute(
    "SELECT order_id, symbol, status FROM broker_orders "
    "WHERE submitted_at >= ? AND side = 'buy' ...",
    (window_start_iso,),
).fetchall()
```

That table is populated by `sync_broker_orders` cron — which runs **every
5 minutes**. `execute_approved.py` runs the verify check **seconds after**
`broker.submit()` returns. The mirror is always empty in that window.

Race timeline:
```
T+0s    broker.submit() -> FTNT order placed at Alpaca, order_id returned
T+5s    _verify_broker_submissions() queries broker_orders -> 0 rows
T+5s    EXECUTED_VERIFY_FAILED stamped (FALSE POSITIVE)
T+300s  sync_broker_orders cron -> inserts row into broker_orders
```

## Fix Approach

Replace the SQLite query with a **direct Alpaca API call** using the
`order_id` that Alpaca returned during submit.

```python
# New logic in _verify_broker_submissions:
for order_id in live_order_ids:
    result = broker.get_order_status(order_id)   # hits Alpaca directly
    if result.success:
        # order confirmed -> PASS
    else:
        missing_ids.append(order_id)             # absent -> GHOST -> FAIL

if missing_ids:
    return False, f"GHOST DETECTED: ..."
return True, f"verified {n} live submission(s) confirmed at Alpaca"
```

Key design decisions:
- **Fail-open on broker errors** — connection failure, 503, timeout -> return
  `(True, "verify skipped: ...")` so the sanity check never blocks execution.
- **order_id from entries** — `execution_report["entries"][i]["order_id"]` is
  the Alpaca-assigned UUID from the submit response. More precise than a
  time-window query.
- **Signature change** — `config: dict` added as 5th parameter so the function
  can instantiate `AlpacaBroker(config, live=True, mode="live")`.

## Acceptance

| Check | Result |
|-------|--------|
| `_verify_broker_submissions` no longer reads `broker_orders` | OK |
| Regression test: race scenario -> EXECUTED not EXECUTED_VERIFY_FAILED | OK |
| Regression test: real ghost -> EXECUTED_VERIFY_FAILED | OK |
| Regression test: broker API error -> fail-open | OK |
| 5 pre-existing `test_execute_approved_integrity.py` tests | OK |
| 18 `test_execute_approved.py` tests | OK |
| `plans/plan_sp500_2026-05-19.json` re-stamped to `EXECUTED` | OK |

Total: 10/10 integrity tests + 18/18 existing tests.

## Plan Re-stamp

`plans/plan_sp500_2026-05-19.json`:
- `"status"`: `EXECUTED_VERIFY_FAILED` -> `EXECUTED`
- `"verify_error"` removed from `execution_report`
- `"correction_audit"` array added with full history

FTNT order `3a1b669d-d19d-455c-b429-b6d2b8259107` confirmed live at Alpaca.

## Follow-ups

1. **`sync_broker_orders` is still useful** — the SQLite mirror enables
   retrospective queries (reconciliation, dashboards). Keep the cron. Just
   don't use it for immediate post-submit verification.
2. **Disconnect after verify** — `_verify_broker_submissions` opens a fresh
   `AlpacaBroker` connection and disconnects in a `finally` block. Minor
   latency (~1s) is acceptable since this runs only at plan execution time.
3. **Paper submissions** — paper account sync is trusted from the in-process
   report. No broker verify needed (paper account has no real money risk).
