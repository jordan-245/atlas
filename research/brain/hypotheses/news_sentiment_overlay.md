# News-Sentiment Overlay (Benzinga historical → systematic sentiment signal)

> **PRE-REGISTERED 2026-06-05** before any backtest, per board memo
> `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding` (fast-follow #2, promoted after the
> equity long-short sleeve was killed the same day). Gates fixed BEFORE measurement; do not move
> them to rescue a result. Honest prior is **LOW** (see #215 + efficient-market caveat below).

## Hypothesis

A **systematic, backtestable** news-sentiment signal — deterministic Loughran-McDonald sentiment
scored on **Benzinga** headlines+summaries (free Alpaca `/v1beta1/news`, history to ~2016),
aggregated to a daily per-symbol panel — **improves the net-of-cost OOS risk-adjusted return** of
the SP500 equity book, EITHER as (a) a cross-sectional sentiment tilt on the existing long-only
factor book (`cross_sectional_momentum`), OR (b) a position-level sizing/gating overlay that
trims/raises exposure on strong negative/positive news. Measured through the SAME cross-OOS battery
the promotion gate uses.

**Falsifiable null:** news-sentiment adds **no** incremental net OOS Sharpe over the base book, and
the sentiment-sorted forward-return drift is not positive net of costs after a realistic
publication lag. If true → kill, keep the base book.

## Why this sleeve (priors — and why LOW confidence)

- **Data is FREE and deep (Gate 0 PASSED 2026-06-05).** Alpaca `/v1beta1/news` returns Benzinga
  articles back to 2016 on our existing Basic creds, symbol-tagged, paginated — HTTP 200 at 2016 /
  2018 / 2022 / recent. **No $99/mo subscription needed** (the board deferred it). This is the one
  genuinely additive move whose data we don't pay for.
- **Augments rather than fights the book.** Unlike the killed long-short (which removed the beta
  that was paying), a sentiment tilt/overlay *adds* to the long factor book.
- **⚠️ Prior is LOW, by evidence:**
  - **#215** — the existing **LLM-discretionary** overlay (`overlay/engine.py`, news+charts→size/gate)
    reviewed BLOCKED/INSUFFICIENT_DATA: headline accuracy inflated, only 1/17 "tighten" calls caught
    real downside, 2 shadow-PnL events. Discretionary news overlay has not shown value.
  - Daily news sentiment on **liquid large-caps** is the hardest case — news is priced in fast;
    published-sentiment drift after costs is weak/decayed in the literature.
  - This pre-registration is therefore **skeptical by design**: the signal must *beat* the base
    book, not merely correlate with returns.

## What is genuinely NEW here (vs existing infra)

| Existing | This sleeve |
|---|---|
| `overlay/engine.py` = LLM judgment, **forward-only**, not backtestable (→ #215 stuck) | Deterministic LM sentiment, **fully backtestable** on 8+ yr history |
| `overlay/sources/news.py` = **Brave real-time** headlines (last 24h) | **Benzinga historical** to 2016, symbol-tagged |
| `news_intel` table = 2.3k rows, 2wk (Apr 2026), Finviz/OpenInsider | New per-symbol daily sentiment panel 2018–2026 |

So this needs a **small new data layer** (news ingester + deterministic scorer), not engine changes
for Phase A. It plugs into `overlay/` as a log-only signal only if it passes.

## Phases

| Phase | Scope | Paid data? | Engine change? | Status |
|---|---|---|---|---|
| **Gate 0 — data feasibility** | Confirm historical Benzinga free on our creds | No | No | ✅ **PASSED 2026-06-05** (to 2016) |
| **A1 — data build** | Ingest Benzinga `/v1beta1/news` (paginated) → local store; deterministic LM sentiment → daily per-symbol panel 2018–2026 | No | No | pending |
| **A2 — edge eval** | (i) cross-sectional sentiment tilt on csm; (ii) sentiment sizing/gating overlay + event-study. Score via cross-OOS battery, net of costs + publication lag. | No | No | pending (after A1) |
| **B — integrate** | Wire as `overlay/` log-only signal → forward paper → live | No | overlay only | BLOCKED until A2 passes + forward paper |

## Pre-registered gates / kill-criteria

### Method guards (news-specific — fixed before any backtest; violating any = invalid result)
1. **No look-ahead / point-in-time.** Use each article's `created_at` (UTC). A signal used for
   trading day D may only use news published **before the decision point** (entry at D+1 open, or
   D-close signal → D+1 trade). The classic news-backtest look-ahead bug (using same-day news to
   trade same-day close) is disqualifying.
2. **Sentiment model frozen before backtest.** Loughran-McDonald finance lexicon; pre-specified
   aggregation = mean polarity per symbol-day + a news-count/attention feature. **No post-hoc
   lexicon or threshold tuning.**
3. **Pre-specified horizons** {1, 3, 5, 10d} — report all, do not cherry-pick the best.
4. **Symbol mapping to point-in-time tradable universe**; handle ticker changes; drop non-US/OTC.
5. **Costs:** existing volume-aware slippage; no commission. Overlay turnover counted.

### PASS (all must hold)
- Net-of-cost cross-OOS battery **median Sharpe of the sentiment-enhanced book ≥ base book's net
  OOS Sharpe**, with **incremental ≥ +0.10 Sharpe** (must *add*, not match).
- Event-study: **monotonic, net-of-cost forward drift** in the predicted direction, positive in
  **≥ 2 regimes**, surviving the publication lag.
- **Effective-N Deflated Sharpe positive** (correlated-trials corrected); battery tier ≥ SCREEN.

### KILL (any one)
- Incremental net Sharpe **≤ 0** vs base book, OR
- No net-of-cost event drift after publication lag, OR
- Edge only at $0 cost, or only in one regime, OR
- Removing look-ahead (proper t+1 entry) collapses it, OR
- Battery tier FAIL.
→ On kill: document honest null in this doc + TSV; keep base book; **do not tune to rescue**;
record that systematic daily news-sentiment on liquid large-caps has no net edge here.

## RESULT — 2026-06-05 — KILL (honest null)

Built A1 (ingester `data/benzinga_news.py` → **110,201** Benzinga articles, 24 months 2021-07..2023-06,
FREE; deterministic LM scorer `research/sentiment/lm_score.py`) and ran A2
`research/proxy/news_sentiment_proxy.py` (sentiment tilt blended into the csm factor rank, long-only,
5bps slippage, scored via the same cross-OOS battery; lag=1 safe + lag=0 falsification + event study).

| Test | Best tilt incremental vs pure factor | Verdict |
|---|---|---|
| Full window 2021-2026 (lag=1) | **+0.013** Sharpe (w=0.25); w=0.5/1.0 negative | FAIL |
| Undiluted news window 2021-2023 (lag=1) | **+0.015** Sharpe (w=0.25); w=0.5 −0.15, w=1.0 −0.20 | FAIL |
| Look-ahead (lag=0) | +1.157 vs +1.155 — sentiment near-zero even WITH look-ahead | not look-ahead-driven |
| Event study fwd drift (pos−neg) | +0.01..+0.02pp @1-5d, **−0.25pp @10d** | no monotonic net drift |

**Pre-registered KILL fired:** best incremental (+0.015) is far below the **+0.10** bar; heavier
sentiment weight *hurts*; no net-of-cost event drift; not even a look-ahead artifact. Daily news
sentiment on liquid large-caps adds **nothing** to a momentum/quality book after costs — the
information is already in the price (and partly in momentum). Consistent with **#215** (LLM overlay
added no value) and the efficient-market prior.

**Decision:** KILL. Keep the long-only `cross_sectional_momentum` book as the sole additive sleeve.
**Do NOT** tune to rescue, **do NOT** expand the ingest to 2016 (a ~zero forward drift won't flip on
more history), **do NOT** build Phase B overlay integration. Both board fast-follows (long-short,
news-sentiment) are now honest nulls → at this scale/universe the return lever is the long-only factor
book; exotic overlays add nothing net of costs.

**Honest caveats (do not change the verdict):** (a) baseline itself fails the battery (PBO ~0.3-0.74)
— neither book is a clean edge, but the *sentiment comparison* is the point and it is null; (b) lexicon
is a compact LM subset (146 pos / 216 neg) — but the null is driven by absence of forward drift in the
event study, not lexicon coverage, so a fuller dictionary cannot rescue it. Cached data + scorer remain
reusable if a materially different thesis (e.g., news *surprise/volume* spikes, not polarity) is ever
pre-registered.

## Review cadence
- Gate 0 done. **A1+A2 are a ≤2-week time-box** from the day A1 starts. Kill if no net edge by then.
- Board re-review **2026-06-19** or on the A2 gate result.

## Artifacts
- Gate 0 probe result: this doc (PASS, 2026-06-05) + session log.
- Implementation spec: `research/strategies/news_sentiment_overlay_SPEC.md`
- Existing overlay (LLM, #215): `overlay/engine.py`, `overlay/evaluator.py`
- Capability audit: `docs/ALPACA_API_CAPABILITIES.md`
- Board memo: `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding/memo.md`
- Queue entry (deferred, pending A1 build): `news_sentiment_overlay_phaseA_20260605`
