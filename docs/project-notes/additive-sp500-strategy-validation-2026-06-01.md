# Additive SP500 Strategy Validation — Task #388

**Date:** 2026-06-01
**Mode:** Backtest / OOS-style analysis ONLY. **No live config, broker, or systemd changes.**
**Candidate chosen:** `mean_reversion` (validated additively against the live `momentum_breakout`-only config).
**Verdict:** ❌ **NO-PROMOTE** (2/6 additive gates pass). Diversifying but not promotion-grade — and, more importantly, **not harvestable** under the current portfolio-construction layer.

Artifacts:
- Harness: `scripts/analyze_additive_sp500_strategy.py`
- Tests: `tests/test_additive_sp500_strategy_analysis.py` (17 pass)
- Result JSON: `backtest/results/additive_sp500_mean_reversion.json`

---

## 1. Candidate selection — why `mean_reversion`

Checked `research_best`, `strategy_lifecycle`, and `research/best/*.json` in `data/atlas.db` for all SP500 strategies. Clean (post-#327 rerun) solo Sharpe on SP500:

| Strategy | Clean solo Sharpe | Trades | maxDD | Lifecycle | Notes |
|---|---|---|---|---|---|
| **mean_reversion** | **+0.2933** (edge p=0.010 ✅) | 351 | 11.5% | **PAPER** | Only positive, statistically-significant non-breakout edge; designated next-for-validation |
| sector_rotation | +0.4496 (portfolio 0.044) | 647 | 0.0%* | RESEARCH | maxDD 0% is suspicious (sparse/non-overlapping); portfolio Sharpe collapses to 0.04 |
| connors_rsi2 | −0.2433 | 977 | 12.9% | PAPER | Demoted from LIVE (negative edge) |
| opening_gap | −0.2575 | 928 | 19.6% | RESEARCH | Negative |
| consecutive_down_days | −0.4754 | 1304 | 12.1% | RESEARCH | Negative |
| short_term_mr | −0.7818 | 825 | 12.2% | PAPER | Negative on clean rerun (legacy 1.27 claim contradicted) |
| March `legacy_portfolio` set (adx_trend_pullback, donchian_breakout, lower_band_reversion, stochastic_oversold) | n/a (pre-contamination-rerun) | — | — | RESEARCH (sandbox) | Pre-#327 metrics, lower confidence; deferred |

**Chosen: `mean_reversion`.** It is the single defensible additive candidate:
1. Only SP500 strategy besides momentum_breakout with a **positive, significant** clean solo edge (Sharpe 0.29, p=0.010).
2. Already in **PAPER** lifecycle (promoted 2026-05-14) — this validation is exactly the gate it was staged for.
3. Structurally complementary: momentum buys strength, mean-reversion buys weakness → expected low/negative return correlation (confirmed below).
4. Has a maintained, risk-controlled config block in the live config (v3.2.4 falling-knife SMA-200 guard).

**Alternatives rejected/deferred:** all other candidates either have negative clean solo edge (connors_rsi2, opening_gap, consecutive_down_days, short_term_mr), a suspicious/zero maxDD with collapsing portfolio Sharpe (sector_rotation), or only pre-contamination "legacy_portfolio" metrics from March that would need fresh screening first (the March sandbox set). None is a higher-confidence diversifier than the already-PAPER mean_reversion.

**Params tested:** the **live active-config block** (maintained v3.2.4: `sma200_filter=true`, `zscore_entry=-2.0`, `atr_stop_mult=1.5`) — i.e. *what would actually be deployed*. Deliberately **not** the riskier `research/best/mean_reversion.json` params (`sma200_filter=false`, `zscore_entry=-0.9`, **maxDD 37%**), which would fail the drawdown gate on contact.

---

## 2. Method

`scripts/analyze_additive_sp500_strategy.py` reuses the canonical production strategy factory + data loader (`scripts/strategy_evaluator.py`) and runs walk-forward backtests in parallel (fork + `ProcessPoolExecutor`, 8 cores). All configs are **deep-copied in memory** — the live `config/active/sp500.json` is never touched.

- **Data:** 197–198 SP500 tickers, 2019-05-30 → 2026-05-28, identical for every run.
- **Backtests (9):** baseline (MB-only), combined (MB+MR), solo MR — each over the full window; baseline/combined over an IS half (≤2023-12-31) and an OOS half (≥2024-01-01); plus a capacity-relief diagnostic at `max_open_positions=15`.
- **Correlation (per-strategy return streams):**
  - *Primary* — engine's native `calc_strategy_correlation` (daily P&L attribution spread across holding days → Pearson). This is the same metric the system's own concentration gate uses.
  - *Cross-check* — Pearson correlation of the solo equity-curve return streams.
- **OOS-style validation:** the full walk-forward is OOS by construction (each test window unseen by training); plus an explicit 2024-2025 holdout half and per-window consistency over all 69 windows.

---

## 3. Results

### Full-window walk-forward

| Run | Sharpe | PF | maxDD | Trades | PnL |
|---|---|---|---|---|---|
| baseline (MB only) | **+0.1249** | 1.139 | 31.11% | 441 | +$425.90 |
| combined (MB+MR) | **+0.0899** | 1.129 | 27.98% | 440 | +$379.19 |
| solo MR | +0.30–0.32 | 1.63–1.72 | 9–13% | 165–168 | +$492 |

**Delta (combined − baseline):** Sharpe **−0.035**, PF −0.010, maxDD −3.13pp (better), PnL **−$46.71**, trades −1.

### The crowding finding (root cause)

| | MB trades | MB PnL | MR trades | MR PnL |
|---|---|---|---|---|
| solo MR | — | — | **165** | +$491.77 |
| combined | 426 | +$362.87 | **14** | +$16.32 |
| baseline | 441 | +$425.90 | — | — |

Adding MR **crowds its own entries from 165 → 14 trades** (−91%) and displaces ~15 profitable MB trades (−$63). MR's 14 surviving trades (+$16, 57% win rate) do not compensate. **Net portfolio effect is slightly negative.**

**Capacity diagnostic — the cap is NOT the binding constraint.** Raising `max_open_positions` 10→15 produced an **identical** result (combined Sharpe 0.0899, PnL $379.19, MR still 14 trades). The suppression is therefore **not** the position-count cap. It is the shared portfolio-construction budget: strategies are processed in config order (MB first each day), and MR entries are then blocked by **available capital**, the **1.75× gross-exposure cap**, **sector-concentration (max 2/sector)**, and **ticker dedup**. This is the **#399 sizing-and-capacity architecture** issue, not a candidate flaw.

### Correlation — genuinely diversifying

- **Primary (engine daily-PnL):** r = **−0.0007** (essentially zero).
- **Cross-check (solo equity returns):** r = **+0.115**.
- Concentrated pairs (|r|>0.6): **none**.

MR is genuinely uncorrelated with MB — exactly the diversification the roadmap wants — but the benefit cannot be harvested through the current portfolio-construction layer.

### OOS-style validation

- **2024-2025 holdout half:** `combined_oos` is **identical** to `baseline_oos` (Sharpe −0.4407, 121 trades, −$48.54) — **MR contributed zero trades** in the recent regime. Both negative (the live strategy itself is weak in 2024-2025).
- **IS half (≤2023):** MR helped marginally (Sharpe −0.146→−0.085, PnL +$19→+$58, +11 MR trades). So MR's modest additive value is concentrated in 2019-2023 and absent in the period that matters most for a deploy decision.
- **Walk-forward window consistency (69 windows):** combined ≥ baseline in only **55.1%** of windows; window-return correlation **0.927** (combined barely differs from baseline); combined mean window return 0.527% vs baseline 0.573% (slightly worse).

---

## 4. Gate verdict

| Gate | Result | Detail |
|---|---|---|
| G1 Sharpe improves | ❌ | combined 0.0899 < baseline 0.1249 |
| G2 OOS Sharpe ≥ 0.6 | ❌ | combined full-WF Sharpe 0.0899 |
| G3 Profit factor ≥ 1.2 | ❌ | combined PF 1.129 |
| G4 Max drawdown | ✅ | combined 27.98% ≤ baseline 31.11% (not materially worse; absolute 15% bar not met but baseline is 31%) |
| G5 Correlation < 0.7 | ✅ | \|r\| = 0.0007 (and 0.115 cross-check) |
| G6 OOS-half consistency | ❌ | OOS combined Sharpe = baseline (MR added nothing) |

**VERDICT: NO-PROMOTE (2/6).** Do **not** enable `mean_reversion` in the live SP500 config.

---

## 5. Interpretation & recommendation

- The candidate is **not** the problem: solo `mean_reversion` is the best standalone SP500 strategy on file (Sharpe ~0.31, PF ~1.7, maxDD ~9-13%, +$492 on 165 trades) and is **genuinely uncorrelated** with momentum_breakout.
- The **portfolio-construction layer** is the problem: under shared capital / 1.75× gross-exposure / sector-2 / ticker-dedup, a second strategy added *behind* momentum_breakout has its entries suppressed by ~90% and slightly degrades the portfolio. **Raising the position cap does not unlock it.** Naively flipping `enabled=true` would lower Sharpe and PnL.
- This is a concrete, quantified instance of the **#399** sizing/capacity blocker: Atlas currently cannot absorb a diversifying strategy additively because capacity is consumed by the incumbent before the diversifier is considered.

**Recommended follow-up (gated, not actioned here):**
1. Treat the additive-strategy question as **downstream of #399**. The harvestable path is portfolio-construction reform — e.g. reserved per-strategy capacity (the `allocation.pools` block, currently disabled), confidence-blind round-robin across strategies, or sizing that frees exposure budget — **not** enabling MR in the current capped pipeline.
2. Keep `mean_reversion` in **PAPER** (unchanged). Its standalone edge justifies continued shadow tracking; it is not promotion-grade as a live additive today.
3. Re-run this exact harness after any #399 capacity/allocation change to measure whether the (real) diversification benefit becomes harvestable.

**Limitations:**
- Per-strategy correlation is a daily-P&L-attribution proxy (PnL spread evenly across holding days), not true mark-to-market daily returns — adequate for a < 0.7 gate, both methods agree near zero.
- Solo-MR metrics showed minor run-to-run variance (165 vs 168 trades; maxDD 13.0% vs 9.0%); baseline and combined were bit-identical across runs, so the verdict is robust.
- OOS half uses a single 2024-01-01 split; the first ~252 trading days of the slice are consumed as the walk-forward train window.
- This is a backtest-only study; not promotion-grade and not run through staged `validate_oos.py` (which would require a staged candidate config).

---

## Commands run

```bash
# Focused unit tests for the harness helpers (17 pass)
python3 -m pytest tests/test_additive_sp500_strategy_analysis.py -q

# Full additive validation + capacity-relief diagnostic (9 parallel walk-forward backtests)
python3 scripts/analyze_additive_sp500_strategy.py \
    --candidate mean_reversion --market sp500 \
    --alt-max-positions 15 \
    --output backtest/results/additive_sp500_mean_reversion.json
```
