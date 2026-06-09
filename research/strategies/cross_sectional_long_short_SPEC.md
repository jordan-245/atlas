# Implementation Spec: cross_sectional_long_short (Phase A)

> Companion to the pre-registration `research/brain/hypotheses/equity_long_short.md`.
> Board memo: `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding`.
> **Build only after research-harness trust is certified.** Gates/kill-criteria live in the
> hypothesis doc — do not restate thresholds here; this spec is the *how*.

## Objective (Phase A)

Measure whether a **dollar/beta-neutral long-short** book built from the existing cross-sectional
factor ranking **beats the long-only `cross_sectional_momentum` book on net-of-cost OOS Sharpe**,
using the cross-OOS battery scoring — with **zero changes to the live engine**.

## Why a returns-based proxy (not a BaseStrategy)

Atlas `Signal` is hard-coded long-only (`strategies/base.py::Signal.__post_init__` raises on
`direction != "long"`; stops/TPs assume long). `BacktestEngine` runs that long-only Signal path.
Therefore Phase A is implemented as a **returns-based portfolio evaluator** in `research/proxy/`,
following the existing pattern in **`research/proxy/pairs_stat_arb.py`** (market-neutral, returns-
level, bypasses the Signal engine). No short Signal ever enters the live pipeline.

## New file

`research/proxy/cross_sectional_long_short_proxy.py`

### Inputs / reuse
- **Ranking:** reuse the composite from `research/strategies/cross_sectional_momentum.py`
  (`_factor_row`, `_rank_universe`: 6-1 momentum + low-vol/quality z-scores). Import or
  copy the ranking; do **not** re-derive the factor.
- **Universe:** the existing sp500 universe builder + daily OHLCV cache (no new data).
- **Scoring:** `research/cross_oos/metrics.py` (CPCV / PBO / effective-N DSR / regime /
  leave-one-group-out / forward) — same scorer the battery uses.

### Construction (pre-registered primary)
1. Each rebalance day, rank the tradable universe by the composite score.
2. **Long leg:** top `top_n` names (reuse current default top_n=30, trend filter on).
3. **Short leg:** bottom `short_n` names **restricted to the ETB-eligible short universe**
   (see ETB handling). Primary signal = cross-sectional bottom-rank (low momentum + high vol).
4. **Weights:** dollar-neutral (+1/Nlong on longs, −1/Nshort on shorts; gross = 1.0, net ≈ 0).
   Add an optional **beta-neutral** variant (scale short notional so portfolio beta ≈ 0 using
   trailing 126d beta to SPY).
5. **Hysteresis:** reuse `exit_rank` logic on both legs to limit turnover.
6. **Rebalance cadence:** match the long-only book; expose as a param.

### Pre-registered secondary test (run as a labelled variant, not a substitute)
- **Short-MR overlay:** add overbought names (high RSI / high IBS, above SMA) to the short leg.
  Report long-short-with-MR-overlay separately so we can attribute edge to bottom-rank vs short-MR.

### ETB handling (known limitation — document explicitly)
- Live, Alpaca exposes `easy_to_borrow` per asset, refreshed daily; **historical ETB status is
  not stored**, so the backtest cannot know past ETB membership exactly.
- **Conservative proxy:** restrict the short universe to the **top liquidity/market-cap decile**
  (large caps are almost always ETB) and **exclude** small/illiquid names from shorting. This
  biases *against* finding edge (we forgo the hardest-to-borrow, often highest-short-alpha names),
  which is the correct direction for an honest test.
- Record this as a caveat in the result; live Phase B will use the real daily ETB flag.

### Cost model (must be realistic — edge must survive costs)
- **Borrow:** $0 for ETB, **but run a borrow-fee sensitivity sweep** {0, 25, 50 bps annualized}
  on the short leg; the edge must not depend on $0 borrow.
- **Slippage:** existing volume-aware model applied to **both** legs (entry + exit).
- **Shorting constraints:** ETB-only (no HTB), respect 2x overnight buying power on gross.
- Commission $0 (Alpaca).

### Sanity gate for the build itself (before trusting any number)
- With the **short leg disabled**, the proxy's equity curve must **reproduce the long-only
  `cross_sectional_momentum` net result** on the same battery (apples-to-apples baseline). If it
  doesn't, the proxy is mis-wired — fix before interpreting long-short results.

### Outputs
- `backtest/results/battery_cross_sectional_long_short_<ts>.json` (battery scoring, same shape as
  `battery_cross_sectional_momentum_*.json`).
- Append a row to `research/results/cross_sectional_long_short.tsv`
  (cols: `timestamp sharpe trades max_dd_pct pf cagr_pct params_changed status description`).
- A short markdown summary appended back into the hypothesis doc's results section.

### Suggested CLI
```
python3 research/proxy/cross_sectional_long_short_proxy.py \
    --market sp500 --top-n 30 --short-n 30 --neutral dollar \
    --borrow-bps 0,25,50 --output backtest/results/battery_cross_sectional_long_short.json
```

## Decision after Phase A
- **PASS** (per hypothesis-doc gates) → proceed to **paper validation**, then Phase B engine work
  behind the AUM/equity gates.
- **KILL** → record honest null in the TSV + hypothesis doc, keep the long-only book, **do not tune
  to rescue**. Re-deploy bandwidth to the news-sentiment overlay (fast-follow #2).

## Phase B (BLOCKED — engine plumbing, only if Phase A passes + AUM gates)
Real build, do not start now. Required changes (impact-analyze each before touching):
1. `Signal`: allow `direction="short"` with inverted stop/TP semantics (stop > entry, TP < entry).
2. `engine/` + `risk/`: short position sizing, exposure/risk accounting, neutrality constraints,
   per-leg and gross/net exposure limits.
3. `brokers/alpaca/broker.py`: submit/track short orders; **daily ETB re-check** (ETB→HTB flips);
   borrow-fee accounting; forced-buy-in handling.
4. Plan generator / reconciliation: represent and reconcile short positions.
5. Live gates: ≥$2k equity + margin + ~$25K AUM + auto-revert kill-switch (per hypothesis doc).
