# Atlas Memory

Read this at the start of every session. Update after corrections or discoveries.

## System Architecture
- **Live trading**: config `asx.json` → `mode: live`, `broker: moomoo`, `dry_run_first: false`
- **Broker**: Moomoo OpenD on `127.0.0.1:11111`. Trade API works for AU. Quote API doesn't — use yfinance for prices.
- **Dashboard**: `services/dashboard_server.py` → auth-protected on `:8899` → Cloudflare Tunnel → `atlas.getflowtide.com`
- **Telegram bot**: `services/telegram_bot.py` — sends plans with Approve/Reject buttons, executes on callback
- **Cron**: `08:30 premarket` (ingest + plan + telegram approval), `17:00 postclose` (settlement + dashboard)
- **Portfolio state**: `paper_engine/state/asx.json` (per-market file takes priority over legacy `portfolio_state.json`)
- **Paper state tracks Atlas metadata** (strategy, entry_date, stop_price, confidence) that the broker doesn't store

## Manual Positions
- User holds WDS.AX and US.XOP manually — these are NOT Atlas-managed
- They appear in a separate "Manual Holdings" dashboard section
- They do NOT count toward Atlas's 10 position slots
- Their P&L is included in the combined net figure

## Key Gotchas
- Moomoo AU needs `moomoo_OpenD` binary (not FutuOpenD). XML root must be `<moomoo_opend>`
- `_save_cache()` writes to both `data/cache/asx/` and `data/cache/` (dual-path compat)
- Any portfolio summary MUST fetch current prices first or PnL shows as $0
- Scoring/fitness functions MUST cap all components and enforce min trade counts
- DividendCapture strategy is not implemented — exclude from optimization

## User Preferences
- Prefers clean, minimal solutions — no over-engineering
- Wants Telegram approval for all trades (no auto-execution)
- Wants manual positions visible but separate from Atlas
- Values security — real money, auth everything

## Decisions Log
- 2026-02-26: Option B (start fresh) for go-live — no mid-trade entry
- 2026-02-26: Starting equity $5,000 for Atlas, reset portfolio to zero positions
- 2026-02-26: Live equity curve persisted to `logs/live_equity_curve.json` (not paper state)
- 2026-02-26: Dashboard pulls equity/cash from Moomoo when live, falls back to paper
