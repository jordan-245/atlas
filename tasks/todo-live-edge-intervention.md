# Atlas Live-Edge Intervention — execution plan (2026-06-03)

Source: atlas/docs/atlas-live-edge-diagnostic-2026-06.md. LIVE system — safety first.

## Disciplined order (3 informs 2)
- [ ] #1 Execution-leak fix (same-bar stops -$67, trailing givebacks -$37): implement feasible
      mitigation in the executor + unit tests. Highest ROI, independent of allocation.
- [ ] #3 Formal walk-forward/OOS backtest of momentum_breakout + mean-reversion strategies →
      the EVIDENCE base (avoid rebalancing on 2.7mo live noise = performance-chasing).
- [ ] #2 Strategy-mix rebalance via config promotion gate + backup, JUSTIFIED by #3 (not the
      noisy live sample). Reversible (backup + risk-check).

## Safety rules
- No irreversible live change without a backup + risk-check (atlas_risk_* tools).
- Execution-code changes: unit-tested before commit; cron picks up fresh process next run.
- Config changes: atlas_risk_check_config_promotion -> promote_config (timestamped backup).
- Do NOT overfit the rebalance to a 2.7-month window; gate #2 on #3's walk-forward evidence.

## OUTCOME (2026-06-03) — rigorous evidence corrected the plan
- #3 (variance-vs-broken): RESOLVED = NOT broken. Knowledge layer: momentum_breakout sp500 backtest Sharpe ~1.0 (1.20 bull_risk_on / 0.74 transition) — a regime-dependent edge. Live (good Mar-Apr, collapsed May chop) is BEHAVING AS BACKTESTED, not broken. Fresh cli_backtest launched to confirm.
- #2 (rebalance to connors_rsi2): REJECTED (disciplined). connors_rsi2 was rigorously decommissioned (#340: Sharpe -0.51, p=0.63, no edge). Live +$7.80/56% = 9-trade noise. NOT executed — would be performance-chasing against a rigorous prior decision.
- #1 (execution leak): EXECUTED the root-cause fix. Live atr_stop_mult was 0.61 = ~half the research-validated ~1.2 (brain kept 1.19-1.26). Anomalously tight stop caused the same-bar round-trips (-$67). Promoted 0.61 -> 1.2 via risk gate (verdict=allow) + backup active_config_backup_20260603084310.json. RISK-NEUTRAL (risk-based sizing). Reversible. The intraday #316 fix remains a separate future item.
