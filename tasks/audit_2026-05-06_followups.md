# Audit 2026-05-06 Follow-up Tasks

Tracks deferred items from the research-system-audit-2026-05-06. All immediate
gate fixes (Rec 1.1-1.4, 1.6) were shipped in commit A of the same session.

## Pending

- [ ] **Audit Rec 1.5 — Paper-trade phase**: implement 30-day paper-trade phase before live promotion. Spec in `docs/audits/research-system-audit-2026-05-06.md` (bottom section). Est 2-3 days. Depends on: nothing — gate fixes + divergence monitor (Rec 4) are independent.

## Done in this session

- [x] **Rec 1.1** — DSR gate: per-strategy variance (was cross-strategy, inflated to >3.0 sanity cap every session). Fixed in `research/loop.py` `_get_dsr_stats(strategy, market)`.
- [x] **Rec 1.2** — IS Sharpe floor raised from `> 0` to `>= 0.5` in `_sanity_check`. OOS Sharpe floor raised from `> 0` to `>= 0.3` in `_run_oos_validation`.
- [x] **Rec 1.3** — OOS trade-count floor 10 → 30 in both `_run_oos_validation` and `keep_or_discard`.
- [x] **Rec 1.4** — CAGR degradation gate (trivially passes at negative CAGR) replaced by absolute OOS CAGR ≥ 5% floor.
- [x] **Rec 1.6** — Pre-commit hook blocks direct edits to `config/active/*.json` without auto_promote audit trail. Bypass: `BYPASS_RESEARCH_GATE="reason" git commit` or `git commit --no-verify`.
