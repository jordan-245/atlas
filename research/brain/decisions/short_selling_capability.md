# Short Selling Capability

**Date:** 2026-03-14 (updated 2026-03-15)  
**Status:** IMPLEMENTED — backtest engine + live executor + protective orders all direction-aware

## Decision

Added short selling infrastructure to enable bearish strategies. Currently gated behind config flags — not active in live trading.

## What's Complete

1. **Signal validation** — Signal dataclass accepts `direction="short"` with inverted validation (stop above entry, TP below entry)
2. **MR short signal generation** — `strategies/mean_reversion.py` `_generate_short_signals()` triggers on RSI > 70, z-score > +2.0 (overbought conditions)
3. **MR exit logic** — Direction-aware exits: short take-profit when price drops to target, short stop-loss when price rises above stop
4. **Alpaca verification** — `verify_shorting_enabled()` confirms account has shorting enabled (multiplier=2, margin account)
5. **Backtest engine P&L** — `_build_trade_record()` and `_force_close_all()` use direction-aware P&L: `(entry - exit) × shares` for shorts
6. **Backtest trailing stops** — Shorts track lowest price (not highest), trigger when price rises above trailing stop
7. **Backtest MAE/MFE** — Inverted for shorts: adverse = price rise (high - entry), favorable = price drop (entry - low)
8. **Backtest max-loss exits** — Direction-aware unrealized P&L in `_process_max_loss_exits()`
9. **Live executor order sides** — SELL to open (short entry), BUY to cover (short exit)
10. **Protective order types** — Inverted for shorts: BUY stop for stop-loss, BUY limit for take-profit
11. **Live preflight check** — Rejects short entries when `short_enabled=false`
12. **Ledger direction field** — Trade records include `direction` field

## Rollout Plan

1. ~~Complete FIX-1 (backtest engine) and FIX-2 (live executor)~~ ✅ Done
2. Run 3-month backtest with MR `short_enabled=true`
3. Validate short trade P&L accuracy against manual calculations
4. Paper-trade with short signals for 1 month
5. Enable in live config only after validation period

## Config

- `mean_reversion.short_enabled: false` — strategy-level kill switch

## Files

- `strategies/mean_reversion.py` — short signal generation
- `backtest/engine.py` — direction-aware P&L, trailing stops, MAE/MFE
- `brokers/live_executor.py` — direction-aware order sides, protective orders, preflight
- `brokers/alpaca/broker.py` — `verify_shorting_enabled()`
- `tests/test_short_selling.py` — 43 tests (signal + strategy level)
- `tests/test_engine_shorts.py` — 23 tests (engine direction-aware P&L)
- `tests/test_executor_shorts.py` — 40 tests (executor order sides, preflight)

## Risk Assessment

- $3,500 account with 2x margin — max short exposure $3,500
- Unlimited loss potential on shorts — strict stop-losses required
- Short squeeze risk on small-cap stocks — S&P 500 universe mitigates this
- Borrow costs not modeled yet — Alpaca charges hard-to-borrow fees
