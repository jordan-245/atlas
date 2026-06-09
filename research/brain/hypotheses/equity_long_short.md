# Equity Long-Short / Short Mean-Reversion (cross_sectional_long_short)

> **PRE-REGISTERED 2026-06-05** before any backtest, per board memo
> `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding`. Decision: 5 FOR / 0 AGAINST.
> This is the board-designated next return lever (#388 "one additive, OOS-validated strategy").
> Gates and kill-criteria below are fixed BEFORE measurement and must not be moved to rescue
> a disproven result (see lessons: a too-good backtest is a bug until proven; never recalibrate
> a gate to save a strategy).

## Hypothesis

Converting Atlas's existing **long-only cross-sectional factor book** (`cross_sectional_momentum`:
6-1 momentum + low-vol/quality, gross Sharpe 0.75 / CPCV-median 0.72 as of 2026-06-03) into a
**dollar/beta-neutral long-short book** — long the top-ranked names, short the bottom-ranked
names (and/or short overbought short-MR names), ETB-only — **improves out-of-sample risk-adjusted
return (OOS Sharpe) net of borrow and slippage**, by removing market beta and harvesting the
short-side of the cross-sectional spread.

**Falsifiable null:** the short leg adds no incremental net-of-cost OOS Sharpe over the long-only
book. If true → kill the short leg, keep long-only.

## Why this sleeve (priors)

- **Uses what we already own.** Long leg exists (`research/strategies/cross_sectional_momentum.py`);
  reuses 7yr daily OHLCV, the universe builder, and the cross-OOS battery. **Zero new data, zero
  new paid subscription** (the board deferred Alpaca Algo Trader Plus $99/mo for exactly this reason).
- **Broker supports it.** Alpaca trades ETB names short at **$0 borrow**, 2x overnight BP, needs
  ≥$2k equity (see `docs/ALPACA_API_CAPABILITIES.md`).
- **Breadth = more independent bets** → higher achievable Deflated Sharpe under the battery, and it
  scales with AUM (unlike single-name timing strategies that are exhausted, e.g. momentum_breakout).
- Market-neutral construction is the canonical way to lift portfolio Sharpe without adding gross
  exposure — directly serves the standing "raise Sharpe / raise returns" objective.

## ⚠️ Key implementation constraint (discovered 2026-06-05)

**Atlas is hard-coded long-only end-to-end.** `strategies/base.py::Signal.__post_init__` raises
`ValueError` unless `direction == "long"` (and enforces stop < entry, take_profit > entry — long
semantics). Grep of `engine/` and `risk/` shows **no short-side handling** anywhere. The Alpaca
broker layer (`brokers/alpaca/broker.py`) *can* place shorts, but the internal
signal → plan → risk → sizing pipeline cannot represent a short position.

**Consequence — the sleeve is strictly two-phase:**

| Phase | Scope | Touches live engine? | Status |
|---|---|---|---|
| **A — Edge research** | Evaluate the long-short *edge* in the cross-OOS **battery harness** (research-side portfolio eval, NOT the live Signal path). Extend the cross_sectional ranking to emit short candidates + dollar/beta-neutral construction, net of modeled borrow + slippage. | **No** | Eligible once harness trust certified |
| **B — Engine plumbing** | Make shorts first-class in Atlas: `Signal.direction` short, short stop/TP semantics, short position sizing & risk, broker short orders, daily ETB re-check, borrow-fee accounting. | **Yes (real build)** | **BLOCKED** until Phase A passes AND AUM gates met |

Phase A answers "is there an edge?" with zero engine risk. Phase B is only justified if Phase A clears.

## Pre-registered gates / kill-criteria

### Precondition (hard)
- **Research-harness trust: SATISFIED as of 2026-06-04.** #219 research sweep harness completed
  ~2026-06-01 (live mode 7/7 green); the cross-OOS validation battery (CPCV / PBO / effective-N
  DSR / regime / forward) was ported, board-calibrated (regime-gate 5-0), bias-hardened
  (pre-registered selection), and parallelized 2026-06-03..04, and is in active daily use screening
  free candidates (csm, shmr, gap-fade #421, pairs/stat-arb #422, ensembles #423). The remaining
  blocker for this experiment is therefore NOT the harness — it is building the Phase A proxy
  (`cross_sectional_long_short_SPEC.md`). The auto research-runner *service* stays intentionally off;
  Phase A is run via `scripts/run_strategy_battery.py` / the rapid-pipeline orchestrator.
- **Relevant recent prior (caution):** #422 pairs/stat-arb — a market-neutral approach — was tested
  2026-06-04 and found **no edge net-of-cost**. This is a different construction (statistical pairs,
  not cross-sectional factor long-short) so it does NOT pre-empt this hypothesis, but it raises the
  prior that net-of-cost market-neutral edge is hard here. Hold the gates firm.
- Phase A baseline must re-measure the **long-only** `cross_sectional_momentum` net-of-cost OOS
  Sharpe on the *same* battery (current 0.75 is gross rf=0) for an apples-to-apples comparison.

### Phase A PASS (all must hold)
1. **Construction:** dollar-neutral (≥ beta-neutral) long top-N vs short bottom-N, ETB-eligible
   shorts only (no HTB — not shortable).
2. **Cost realism:** model $0 ETB borrow **but** apply daily ETB→HTB flip risk; volume-aware
   slippage (existing model) on BOTH legs; respect 2x overnight BP.
3. **Net-of-cost edge:** cross-OOS battery **median Sharpe ≥ 0.5** after costs, **AND ≥ the long-only
   book's net OOS Sharpe** on the same battery (must *beat* long-only, not just match).
4. **Robustness:** positive per-regime net expectancy in **≥ 2 regimes**; **≥ 50 trades**;
   effective-N Deflated Sharpe positive (correlated-trials corrected).

### Phase A KILL (any one)
- Net-of-cost battery median Sharpe < 0.3, OR
- Short leg adds **no** incremental net OOS Sharpe over long-only (null not rejected), OR
- Negative net expectancy in any regime, OR
- < 50 trades, OR
- Edge survives only at $0 slippage/borrow (i.e., it's a cost artifact).
→ On kill: document as honest null in this file + TSV, keep long-only book, **do not tune to rescue**.

### Phase B → live gates (unchanged standing requirements)
- Phase A passed + ≥ 40–50 **paper** trades with +ve net-of-cost expectancy + auto-revert kill-switch armed.
- Material AUM **~$25K** + ≥ **$2k** account equity + margin enabled before a single live short.

## RESULT — 2026-06-05 — KILL (honest null)

Phase A proxy `research/proxy/cross_sectional_long_short_proxy.py` run on sp500, 150 most-liquid
(ETB proxy), 2019-05..2026-06, identical construction with the short leg ON vs OFF, net of 5bps/side
slippage + borrow sweep {0,25,50}bps:

| Book | Net Sharpe | Battery tier |
|---|---|---|
| Long-only leg (proxy baseline) | **+1.67** | FAIL (PBO 0.75, regime_conc 3.31 — simple grid, not a clean edge either; engine csm ref +0.905) |
| **Long-short @0bps borrow** | **+0.01** | FAIL (CPCV +0.045, DSR 0.17, per_regime_ok False) |
| Long-short @50bps borrow | **−0.01** | FAIL |

**Every pre-registered KILL condition fired:** net-of-cost (worst borrow) Sharpe < 0.30; short leg
adds **no** incremental net OOS Sharpe (incremental −1.66); does not beat long-only; edge only
survives at $0 borrow (and barely). The short leg gives back essentially the entire factor return
(it was long-beta + long-factor tilt) and costs finish it. Consistent with the #422 pairs/stat-arb
net-of-cost null (market-neutral has no net edge in this liquid universe).

**Decision:** KILL the short leg. Keep the long-only factor book (`cross_sectional_momentum`) as the
return lever. Per board memo, redeploy bandwidth to the **news-sentiment overlay** (fast-follow #2).
Do NOT tune to rescue (result is decisive, not a near-miss). Phase B engine plumbing stays unbuilt
(correctly — never needed). Artifacts: `research/results/cross_sectional_long_short.tsv`.

**Secondary insight:** the long cross-sectional factor (not neutralization) is where the return is —
reinforces investing in `cross_sectional_momentum`, not long-short, as the additive sleeve.

## Review cadence
- **2-week checkpoint** from the day Phase A backtest starts. Kill if no +OOS edge after costs by then.
- Board re-review **2026-06-19** or on Phase A gate result, whichever first.

## Artifacts
- Implementation spec: `research/strategies/cross_sectional_long_short_SPEC.md`
- Long leg (existing): `research/strategies/cross_sectional_momentum.py`
- Capability audit: `docs/ALPACA_API_CAPABILITIES.md`
- Board memo: `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding/memo.md`
- Queue entry (deferred, pending harness trust + Phase A build): `cross_sectional_long_short_phaseA_20260605`
