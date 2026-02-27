# Exchange Stop Orders + Broker Reconciliation

## Tasks
- [x] Add `place_protective_stop()` to live executor — STOP SELL or TRAILING_STOP SELL after entry fill
- [x] Add `place_stops_for_plan()` — auto-determines stop type per strategy config
- [x] Add `cancel_protective_stop()` — cancels stop before planned exit (prevent double-sell)
- [x] Track `stop_order_id` on Position — persisted in paper state JSON (backward compatible)
- [x] Add `reconcile_stops()` to live executor — detect broker-side stop fills, sync paper state
- [x] Wire into `execute_plan()` — auto-places stops after entries, returns stop_order_ids
- [x] Wire into `_execute_live()` (telegram bot) — reconcile before execution, save stop IDs to paper
- [x] Wire into EOD settlement — reconcile before paper stop checks, skip exchange-protected positions
- [x] Update intraday monitor — 🛡 indicator for exchange-protected positions
- [x] Update Moomoo broker `place_order` — pass trailing stop params (trail_type, trail_value, trail_spread)
- [x] Test backward compatibility — old state files without stop_order_id load cleanly
- [x] Test exchange-protected positions skip paper stop check
- [x] Test strategy routing — MR→fixed STOP, TF→TRAILING_STOP with ATR×mult

## Done ✅
