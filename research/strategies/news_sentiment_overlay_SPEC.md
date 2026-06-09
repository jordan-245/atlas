# Implementation Spec: news_sentiment_overlay (Phase A)

> Companion to pre-registration `research/brain/hypotheses/news_sentiment_overlay.md`.
> Board memo: `ceo-board/memos/2026-06-05-alpaca-sip-and-sleeve-funding`.
> Gates/kill-criteria + method guards live in the hypothesis doc — do not restate/tune here.
> **Gate 0 already PASSED** (Benzinga historical news free on our creds to 2016).

## Objective (Phase A)

Decide whether a deterministic, backtestable news-sentiment signal **beats the base equity book on
net-of-cost OOS Sharpe**, with **zero live-engine changes** — same returns-based-proxy + cross-OOS
battery pattern that killed the long-short sleeve cleanly.

## A1 — Data layer (no paid data, no engine change)

### New file: `data/benzinga_news.py` (ingester)
- Pull Alpaca `GET https://data.alpaca.markets/v1beta1/news` with existing
  `ALPACA_API_KEY`/`ALPACA_SECRET_KEY` (free Basic tier — verified).
- Params: `start`, `end`, `limit=50`, `sort=asc`, follow `next_page_token` until exhausted.
- Window: **2018-01-01 → present** (2016–17 exist but symbol tagging is sparse; 2018+ is clean).
  ~130 articles/day × ~3000 days ≈ ~400k articles — paginate in monthly chunks; checkpoint/resume;
  be polite to rate limits (Basic = 200 req/min).
- Store raw to a local table/parquet: `data/cache/benzinga_news/` (parquet by month) with fields
  `created_at, updated_at, headline, summary, source, symbols[], id`. Idempotent (dedup on `id`).
- **Point-in-time integrity:** persist `created_at` UTC verbatim; never backfill a symbol tag.

### New file: `research/sentiment/lm_score.py` (deterministic scorer)
- Loughran-McDonald finance sentiment dictionary (positive/negative word lists; ship the lexicon
  in-repo or vendored — it is free/public). **Frozen before backtest.**
- Per article: tokenise headline+summary, polarity = (pos − neg) / (pos + neg + 1); also emit
  `n_words`, `negation_flag`. **No LLM** (keeps it reproducible + backtestable — the whole point
  vs the #215 LLM overlay).
- Aggregate to a **daily per-symbol panel** `sentiment[symbol, date]`:
  - `sent_mean` (mean article polarity that day), `sent_sum`, `news_count` (attention),
    `sent_surprise` = sent_mean − trailing-N mean.
  - Articles are assigned to the trading day per the **publication-lag rule**: news with
    `created_at` after the prior close and before day D's decision point feeds day D's signal,
    traded at **D+1 open** (pre-register: no same-day-close look-ahead).

## A2 — Edge evaluation (returns-based proxy + battery)

### New file: `research/proxy/news_sentiment_proxy.py`
Follow `research/proxy/cross_sectional_long_short_proxy.py` structure (build_panels / regime_series /
adapter.assemble_bundle / evaluate_tiers). Run **two pre-registered tests**, report both:

1. **Cross-sectional sentiment tilt on csm (primary):**
   - Take the existing csm composite rank; **blend** a standardized sentiment score
     (`w_sent · z(sent_surprise)`), pre-specified small `w_sent` grid {0.0, 0.25, 0.5}.
   - Long-only (the long-short kill stands — no short leg). Compare net-of-cost battery Sharpe of
     `csm + sentiment` vs `csm` alone (apples-to-apples, same construction/costs).
   - PASS requires incremental ≥ +0.10 Sharpe (see hypothesis doc).

2. **Sizing/gating overlay + event-study (secondary):**
   - Event-study: forward returns at horizons {1,3,5,10d} after **strong negative** vs **strong
     positive** symbol-day sentiment, net of costs, by regime. Must be monotonic + net-positive in
     the predicted direction.
   - Overlay test: trim position size (e.g., ×0.5 or exit) on strong negative news on held names;
     does it improve the base book's net Sharpe / MaxDD without killing return?

### Costs & guards
- Volume-aware slippage on any sentiment-driven turnover; commission $0.
- **Look-ahead test is mandatory:** run the primary with proper D+1 entry AND a deliberately
  look-ahead variant (same-day close) — if the edge only exists in the look-ahead variant, KILL.
- Pre-specified horizons; no post-hoc lexicon/threshold tuning.

### Outputs
- `backtest/results/battery_news_sentiment_<ts>.json` (battery scoring).
- Append `research/results/news_sentiment_overlay.tsv`
  (`timestamp sharpe trades max_dd_pct pf cagr_pct params_changed status description`).
- Verdict written back into the hypothesis doc results section; queue entry status updated.

### Suggested CLI
```
python3 data/benzinga_news.py --start 2018-01-01 --resume          # A1 ingest
python3 research/proxy/news_sentiment_proxy.py --market sp500 --w-sent 0,0.25,0.5
```

## Decision after Phase A
- **PASS** → wire as `overlay/` **log-only** signal, forward-paper-validate, then live overlay
  (sizing/gating only) behind the existing overlay gates. Never a standalone live strategy.
- **KILL** → honest null in TSV + hypothesis doc; keep base book; **do not tune to rescue**; record
  that systematic daily news-sentiment on liquid large-caps has no net edge here. Re-deploy bandwidth.

## Phase B (BLOCKED — overlay integration, only if A2 passes + forward paper)
- Add a deterministic sentiment source under `overlay/sources/` (NOT the LLM path).
- Feed `overlay/engine.py` as a numeric, logged signal in `log_only` mode first; evaluate via
  `overlay/evaluator.py` shadow-PnL before any sizing effect goes live.
- No new recurring cost; no $99/mo subscription.
