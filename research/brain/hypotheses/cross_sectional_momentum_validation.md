# cross_sectional_momentum — Clean Validation (PROMOTE vs SCREEN)

> **PRE-REGISTERED 2026-06-05.** Full run spec + frozen config + decision tree:
> `research/strategies/cross_sectional_momentum_VALIDATION_SPEC.md`. Board #388 lever; the only
> additive sleeve surviving net-of-cost OOS after the long-short + news-sentiment kills.

## Hypothesis
On a pre-registered, unbiased run (`--select default`, committed grid+seed, full history), csm's
**search-deflated Sharpe (DSR) clears the 0.90 PROMOTE bar**. Null: DSR stays in [0.70, 0.90) →
csm is a stable, validated **SCREEN** edge (keep forward-tracking, do not promote to live).

## Current state (newgate battery 2026-06-04)
SCREEN. Passes every gate except DSR: CPCV 0.72, PBO 0.13, regime-conc 1.18, per-regime ok,
min-regime 0.88, top_group 0.094, loo ok, fwd +59 — **DSR 0.772 < 0.90 is the only gap.**

## Decision (pre-registered)
- PROMOTE (DSR≥0.90 + all gates) → staged candidate config (human approval) → paper-forward → live only at ~$25K AUM.
- SCREEN (0.70≤DSR<0.90) → forward paper clock (#420), re-evaluate after ≥3mo; NOT live.
- FAIL → diagnose regression first.
Anti-gaming: no grid-shrink / window-shorten to inflate DSR; default selection only; gate-integrity regression check.

## ⚠️ SUPERSEDED — the PROMOTE below was a DEPLOYMENT-BUG ARTIFACT. True verdict = FAIL (see bottom).

## RE-VALIDATION AFTER SECTOR FIX — 2026-06-05 — **FAIL** (the honest verdict)

After fixing the sector-tagging bug (csm now deploys its intended ~14-name book, 246 trades vs 78),
re-ran the identical pre-registered battery (full history, --select default, grid 12, max_pos 35).
Artifact: `backtest/results/battery_csm_revalidated_sectorfix_20260605.json`.

**TIER: FAIL.** CPCV 0.511 (was 1.039), **DSR 0.547 FAIL** (was 0.926; below even the 0.70 screen bar),
**min-regime Sharpe −1.95 FAIL** (was +0.54), PBO 0.102 (good), top_ticker 0.04 (now diversified),
forward_net +209. Time split IS Sharpe 0.076 / OOS 1.873, CAGR degradation **604%** — wildly unstable.

**Interpretation (important):** the earlier PROMOTE was an ARTIFACT of the sector bug. The edge lived
entirely in the **top 1-2 momentum names** (which the bug accidentally isolated). The momentum signal
decays fast across ranks — by the time csm holds the ~14-name breadth book it was DESIGNED for, the
edge dilutes to nothing and one regime goes sharply negative (−1.95). The 'cross-sectional breadth
factor book' thesis does NOT survive honest deployment. A 2-name book isn't an investable strategy
(extreme idiosyncratic risk) and its 0.926 DSR was computed on a bug-induced sample.

**DECISION: csm does NOT validate. Do NOT stage. Shelve.** Per pre-registration discipline, accept
the outcome — do not re-roll or tune top_n to rescue the concentrated artifact (that would be
reverse-engineering from a bug). After long-short (kill), news-sentiment (kill), and now
csm-properly-deployed (FAIL), Atlas has NO battery-validated additive strategy; the live book stays
empty pending a genuinely new, pre-registered hypothesis. Possible (fresh, pre-registered) follow-up:
test whether a DELIBERATELY concentrated top-N (e.g. top 5) momentum book validates with correct
sector tagging — but concentration risk at our AUM makes this dubious; do NOT pursue without a clean
pre-registration. The sector-tagging fix itself is a real bug fix worth keeping.

---

## RESULT — 2026-06-05 — PROMOTE (marginal, honest) — ⚠️ SUPERSEDED, bug artifact (see above)

Pre-registered clean run on **full history** (201 tickers, ~7yr; the one legitimate DSR lever),
`--select default`, grid_size 12 seed 42, max_positions 35. Artifact:
`backtest/results/battery_csm_clean_validation_20260605.json`.

**TIER: PROMOTE** — all 11 gates pass. CPCV median **1.039** (was 0.72 on shorter window),
frac+ 0.933, PBO 0.335 (up from 0.13), **DSR effective-N 0.926** (clears 0.90 — MARGINAL;
grid-proxy variant 0.877), regime-conc 1.58, per-regime ok, min-regime 0.54 (down from 0.88),
top_group 0.111, loo ok, forward_net +56.76. Time split: IS Sharpe 0.413 / **OOS Sharpe 0.729**
(OOS > IS, no Sharpe overfit), IS/OOS CAGR 7.67%/9.80%, degradation 27.79% (gate pass).

**Honest caveats:** DSR 0.926 is barely above 0.90 (grid-proxy 0.877 < 0.90); PBO rose to 0.335 and
min-regime fell to 0.54 vs the shorter-window run — a borderline PROMOTE, not a slam-dunk. This is
the *pre-registered committed* config (full history fixed before run; no re-roll, no selection bias),
so PROMOTE stands. Gate-integrity check passed same session (pure noise -> DSR 0.001 FAIL).

**Decision (pre-registered PROMOTE arm):** csm eligible for a **staged candidate config (human
approval — never auto)**. Live SP500 book is currently empty (0 strategies, paper, live_enabled=False)
so csm repopulates it as the sole strategy (no contention). Path: stage candidate -> human approve ->
runs in PAPER / forward-track (#420) -> live only at material AUM (~$25K). Do NOT go live-money on a
marginal first promote.

## COMBINED / PORTFOLIO CONFIRM — 2026-06-05

Re-ran the identical pre-registered battery at the **live constraint max_positions=10**
(artifact `backtest/results/battery_csm_combined_maxpos10_20260605.json`). Result: **PROMOTE, byte-
identical metrics** to the max_positions=35 run (CPCV 1.039, DSR 0.926, PBO 0.335, 78 trades).

**Why identical — and the key finding:** csm's **PEAK concurrent positions = 2** (measured directly;
78 trades over ~7yr ≈ 11 trades/yr). Despite `top_n=30`, the entry logic + risk sizing means csm
actually runs a **highly concentrated, low-turnover ~1-2 name book**, so the max_positions cap (10 or
35) NEVER binds. The engine DOES enforce the cap (backtest/engine.py 725/758/779) — it just never
triggers.

**Implications (honest):**
- ✅ Portfolio fit at the live constraint: csm transfers cleanly to the live config — max_positions=10
  is non-binding, so validated behavior == live behavior. The combined confirm PASSES.
- ⚠️ The 'breadth = many independent bets' rationale for csm is FALSE in practice (it holds ~2, not
  30). The marginal DSR (0.926) and CPCV rest on ~78 trades at high single-position concentration,
  not breadth. This is a concentrated book — more single-name risk + slow forward-evidence accrual
  (~11 trades/yr) than the design implied.
- ❓ Open question: is ~2-concurrent intended selectivity, or under-deployment (holding 2 of a
  possible 30 = idle capital, lower returns than a fuller book)? Worth a quick diagnosis before/while
  paper-forwarding — a fuller book could raise both return AND genuine DSR-via-breadth.

**Net:** combined confirm PASSES; csm is promotable to a PAPER candidate at live max_positions=10.
But the low concurrency + marginal DSR reinforce: stage -> PAPER-forward (accumulate real trades) ->
NO live-money yet; and consider investigating the position-deployment question.

## CONCURRENCY DIAGNOSIS — 2026-06-05 — ROOT CAUSE = BUG (under-deployment), not selectivity

Instrumented one full backtest. csm **emits ~15-18 entry signals/day** (mean 15.4, median 16, max 26,
zero empty days) — it WANTS a broad book. But the engine skips ~all of them:
`SKIP <tkr>: sector 'Unknown' already has 2 positions (max=2)` across the whole universe.

**Root cause:** csm sets `features={rank,composite,mom,vol}` — **no `sector`**. The engine reads
`signal.features['sector']` (engine.py:800); it defaults to `'Unknown'`. With
`risk.max_sector_concentration=2`, the ENTIRE book is treated as one 'Unknown' sector and hard-capped
at **2 positions total**. Secondary throttle: at ~$1.3k equity many expensive names size to 0 shares.
Confirmed PEAK concurrent 2, avg 1.46; exits 34 stop / 31 time / 11 signal.

**Implication — the PROMOTE was on a CRIPPLED 2-position config.** The intended ~15-name breadth book
(the actual thesis) is UNTESTED. Principled fix: populate `features['sector']` via
`utils/dividends.get_sector_for_ticker` (+ `data/processed/sector_map.json`) so the 2/sector cap
diversifies across REAL sectors (~up to 2x11 names) instead of collapsing the book. Do NOT just
disable the cap (would let csm pile into one hot sector).

**PLAN CHANGE (stop-and-replan):** do NOT stage the crippled 2-position version. New sequence:
(1) fix sector population in csm signals; (2) RE-VALIDATE via the battery (the corrected ~15-name book
is a DIFFERENT strategy — may score better via genuine breadth, or worse via diluted names);
(3) decide staging on the corrected result. The 2-position PROMOTE is shelved pending re-validation.
