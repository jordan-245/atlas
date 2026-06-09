# Research-Best vs Live-Active Drift Reconciliation — Task #389

**Date:** 2026-05-29  
**Strategy:** `momentum_breakout`  
**Market:** `sp500`

## Decision

**Keep live active config unchanged. Do not stage the research-best config yet. Do not retire the research-best record yet.**

Rationale: Task #386 showed the latest 0/32 sweep was a correct fast-screen rejection, but also showed that nightly research is optimizing around `research/best/momentum_breakout.json`, which differs materially from the live `config/active/sp500.json`. The research-best record is useful as a candidate, but it has not been revalidated through the current #219/#354 gates and should not be treated as a live-return signal.

## Current drift

The new read-only drift checker reports:

| Param | Research-best | Live active | Change |
|---|---:|---:|---:|
| `atr_stop_mult` | 0.81 | 0.61 | -24.69% |
| `lookback_days` | 22 | 14 | -36.36% |
| `atr_period` | 22 | 18 | -18.18% |
| `trend_ma_period` | 30 | 27 | -10.00% |
| `profit_target_atr_mult` | 2.2 | 6.0 | +172.73% |

## Implementation added

`research/autoresearch_runner.py` now surfaces this drift during autoresearch via `compare_research_best_vs_active(...)` and includes the warning/recommendation in the returned summary. It is strictly read-only: no live config mutation, no staging, no auto-promotion.

## Gate policy

Before staging or promoting any research-best candidate:

1. #219 research regression harness must be green.
2. #354 stale SP500 phase2 tests must be fixed.
3. Candidate must pass OOS/config-promotion guardrails.
4. Human approval remains required for live config changes.

## Operational conclusion

For now, live remains `config/active/sp500.json`. Nightly research remains useful for candidate discovery, but any drift warning means: **do not read the nightly baseline as live performance until reconciliation gates pass.**
