# Portfolio page → the Paper Book (where forge PASSes feed in)

**Vision (operator):** the Portfolio tab becomes the **live paper-trading account that forge PASSes feed into**.
As the forge produces winners, they auto-paper-trade here on live data — accruing the forward-paper evidence the
board requires before any real capital.

## Board alignment (this is consistent)
- Board 2026-06-09: **paper-on-live-data (shadow) IS the gate**; PASS = candidate gathering forward evidence.
- So **PASS → paper is autonomous** (no real money → no human gate). Only **real capital** (live state) stays
  human-gated on forward-paper evidence + the AUM floor. The Paper Book is exactly the "shadow/paper" stage.

## Flow (reuses what we built; one new bridge)
```
forge PASS (all rails: CPCV/DSR/PBO + holdout + MCPT + beta-confound)
  └─► deploy_pass(): register DeployedStrategy(state="paper", broker=alpaca-paper) + provider     [NEW hook]
        └─► live/daily.py paper-trades it daily:
              provider → today's target weights → TargetExecutor places REAL **paper** orders       [flip dry_run]
              └─► Alpaca PAPER account (the flattened SP500 account, repurposed as the Paper Book)
                    └─► Portfolio "Paper Book": equity curve + positions + per-strategy P&L attribution [reframe]
                          └─► forward-paper evidence accrues → (human-gated) promote to live/real capital
```

## The bridge (the hard part — now GENERIC)
`live/providers.py :: forge_strategy_provider(strategy_path)` — loads an SDK-conformant forge strategy, runs its
`signal()` on the recent data panel (Sharadar, up to asof), and returns **today's** target weights `{symbol: w}`.
Any forge PASS becomes paper-tradable with **no per-strategy code**. (This is the productionization seam I flagged.)

## Components
1. `live/providers.py`: generic `forge_strategy_provider` + `deploy_pass(name, path, capital)` registrar.
2. **Forge hook:** on PASS (`sdk/notify.telegram_pass`), also call `deploy_pass` → strategy enters the Paper Book.
3. `live/daily.py`: **paper-state strategies place REAL paper orders** (dry_run only for *unapproved live*).
4. `/api/paper` (extend `/api/live`): paper account equity + positions + per-strategy P&L.
5. **Portfolio tab → "Paper Book":** reframe to read the paper book + list deployed PASS strategies + attribution.
   (Live tab stays the *pipeline/ops* view: deploy states, run logs, kill-switch.)

## Reuse the flattened SP500 Alpaca paper account as the Paper Book
It's already wired to the broker registry + dashboard; once flat it becomes the clean paper book the daily loop drives
(independent of the sp500 config's disabled strategies — the loop trades the account directly via target_executor).

## The one decision
**PASS auto-deploys to the Paper Book** (board-aligned: paper is the gate, no real money). Real capital remains
human-gated. — Recommend YES (autonomous paper, gated live).
