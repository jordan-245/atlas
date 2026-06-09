# Knowledge Extraction Review — 2026-06-01

## Context

Follow-up to #395/#397 after restoring source-derived shell claims for PDFs in `sources`.

Production state before this review:
- `sources`: 103
- `claims`: 103 source-derived shell claims
- `contradictions`: 0
- one prior smoke extraction had returned `not_found`

## Commands Run

```bash
python3 scripts/extract_paper_metrics.py --apply --limit 25 --timeout 600
python3 scripts/materialize_wiki.py
python3 - <<'PY'
# SQLite count summary over sources/claims/contradictions/pending/attempted failures
PY
```

No trading, broker, active config, or systemd changes were made.

## Results

The 25-claim extraction batch completed, but **0/25 produced metrics**:

```json
{
  "total": 25,
  "ok": 0,
  "skipped": 0,
  "failed": 25,
  "by_reason": {"not_found": 25},
  "mode": "apply"
}
```

Post-run production counts:

| Metric | Count |
|---|---:|
| sources | 103 |
| claims | 103 |
| claims with extracted metrics | 0 |
| contradictions | 0 |
| attempted phase1.5 failures | 26 |
| default pending shell claims | 77 |

Sample failure notes show the source corpus is mostly unrelated to trading/finance, e.g. papers about fusion-device electromagnetic models, LLM guardrails/philosophy, speech translation, AI safety, and astrophysics. The new low-confidence failed-extraction filter worked: these `phase1.5:` failures are now excluded from default retry and only retryable with `--include-low-confidence`.

Wiki materializer remains healthy:

- dry-run renders 48 strategy pages
- contradictions emitted: 0

## Assessment

#395 successfully restored the **mechanics** of the pipeline (`sources -> claims -> LLM extraction candidates -> wiki/contradiction surfaces`), but #397 shows a **source-quality gap**:

1. The current 103 PDFs are not curated to trading/strategy/backtest papers.
2. The source-derived fallback correctly avoids fabricating metrics or contradictions.
3. Running the remaining 77 pending claims with the current unfiltered corpus is likely wasteful and will mostly produce `not_found`.
4. The original missing upstream `specs_*.json` remains strategically important: specs should encode why a paper is strategy-relevant before claim creation.

## Recommendation

Do **not** keep spending LLM calls over the full unfiltered PDF corpus. Add a pre-extraction relevance filter / spec-generation repair before the next large batch.

Suggested gate before future `extract_paper_metrics.py --apply` batches:

- title/abstract/PDF text must indicate trading, portfolio construction, alpha, backtest, Sharpe, drawdown, asset allocation, factor investing, or market prediction;
- otherwise mark the source as non-trading / dismissed or skip claim creation;
- preserve current safety rule: no contradiction until resolved strategy + metrics exist.

## Status

- #397 review is complete.
- Knowledge pipeline is mechanically restored but not useful yet because source relevance is poor.
- Follow-up work should focus on source/spec relevance filtering rather than more raw extraction.
