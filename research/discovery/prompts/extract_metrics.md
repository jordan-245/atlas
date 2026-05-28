# Paper Metric Extractor — Headline Backtest Numbers

You are a quantitative analyst extracting the headline backtest performance
metrics that a single trading strategy paper *claims* about itself. These
numbers will be compared against Atlas's own measured results to surface
contradictions between the literature and reality.

## Inputs

**Strategy being investigated**

```
{strategy_name}
```

**Parameters from the spec extractor** (already on file — for cross-reference):

```json
{parameters_json}
```

**Source title (for sanity check):**

```
{source_title}
```

**Paper text** (truncated to first {text_chars} characters; tables and
figure captions usually within this window):

```
{pdf_text}
```

## Task

Find the paper's **headline** Sharpe, drawdown, CAGR, and related numbers for
the strategy named above. The headline is the figure the paper itself
foregrounds — usually the in-sample full-period summary table or the abstract's
top-line claim. If the paper distinguishes in-sample vs out-of-sample, prefer
**in-sample** (it is what the authors are actually defending; OOS belongs in a
separate claim row).

If the paper presents multiple variants of the strategy, pick the variant whose
parameters most closely match the spec's parameters above. If unclear, take
the best-performing variant.

If the paper does not actually report performance numbers for this strategy
(e.g. it's a literature review, a theoretical piece, or it only discusses the
methodology without backtesting), set `"found": false` and leave the numeric
fields null.

## Output Schema

Reply with a **single JSON object** matching this schema. No markdown fences,
no preamble, no trailing commentary.

```json
{
  "found": true | false,
  "claimed_sharpe": <number or null>,
  "claimed_solo_sharpe": <number or null>,
  "claimed_max_dd_pct": <positive number or null>,
  "claimed_trades": <integer or null>,
  "claimed_cagr_pct": <number or null>,
  "claimed_profit_factor": <positive number or null>,
  "claimed_avg_hold_days": <positive number or null>,
  "period_start": "<ISO date YYYY-MM-DD or null>",
  "period_end": "<ISO date YYYY-MM-DD or null>",
  "extraction_confidence": "high" | "medium" | "low",
  "notes": "<one short sentence: where the numbers came from (table/section), and any caveats>"
}
```

## Field Notes

- **`claimed_sharpe`**: The overall reported Sharpe ratio (assume risk-free
  rate already netted out per paper's convention). If the paper labels a
  number "Sharpe" without qualifiers, use it here.
- **`claimed_solo_sharpe`**: Use this **only** if the paper explicitly
  distinguishes single-strategy / standalone Sharpe from a portfolio-blended
  Sharpe. Otherwise leave null and just populate `claimed_sharpe`.
- **`claimed_max_dd_pct`**: Always positive (e.g. 12.5 for "12.5% drawdown",
  not -12.5). If the paper reports the dollar amount, leave null.
- **`claimed_trades`**: Total trade count over the backtest. If the paper
  reports only trades-per-year or per-month, leave null.
- **`claimed_cagr_pct`**: Annualised return. If the paper reports total
  return over N years, convert to CAGR; flag in `notes` if you did.
- **`claimed_profit_factor`**: Gross profits / gross losses (always positive).
- **`claimed_avg_hold_days`**: Average days a position is held.
- **`period_start` / `period_end`**: ISO `YYYY-MM-DD`. Use Jan 1 / Dec 31
  if the paper reports years only (e.g. "1990-2020" → 1990-01-01 / 2020-12-31).
- **`extraction_confidence`**: Self-assess.
  - `"high"`: Numbers came from a clearly labelled summary table and match the
    abstract's stated headline.
  - `"medium"`: Numbers extracted from prose or figure captions; some inference
    needed.
  - `"low"`: Significant inference required (e.g. CAGR back-computed from
    total return; numbers spread across sections; values ambiguous).
- **`notes`**: One short sentence — *where* the numbers came from and any
  caveat the reader should know. E.g. `"Headline from Table 2; OOS Sharpe in
  Table 5 was 1.1, lower."`

## Inference Guardrails

- Do **not** invent a number that is not in the paper. Leave nulls.
- Do **not** average across multiple regime cells. Take the cross-regime /
  overall number unless only regime-conditioned numbers exist (in which case
  set `extraction_confidence: "low"` and explain in notes).
- If two tables disagree (e.g. abstract vs results table), prefer the
  results table and note the discrepancy.
- If the paper's `{strategy_name}` does not appear to be the actual subject
  of the paper (extraction error upstream), set `found: false` with a note.

Now extract and return the JSON object for `{strategy_name}` from the paper above.
