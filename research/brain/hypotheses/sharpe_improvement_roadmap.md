# From 0.69 to 1.0+ Sharpe: a systematic roadmap for momentum strategies

**Your Momentum Breakout strategy at 0.69 Sharpe already sits near the ceiling for naive long-only momentum — but a realistic path to 0.9–1.1 exists through volatility scaling, strategy combination, and regime filtering, without leverage or shorting.** The academic literature documents that volatility-managed momentum roughly doubles risk-adjusted returns (Barroso & Santa-Clara, 2015), while combining your momentum and mean reversion strategies exploits their **–35% correlation** to deliver portfolio-level Sharpe ratios significantly above any individual strategy. The critical constraint is that all your strategies share long-only US equity market beta, capping the diversification benefit. Below is a prioritized, parameterized playbook of testable hypotheses drawn from peer-reviewed research and practitioner evidence.

---

## Volatility scaling is the single highest-impact enhancement

The most robust finding in the momentum literature is that **scaling exposure inversely to recent realized volatility nearly doubles Sharpe ratios** — and this works specifically because momentum crashes are predictable from their own trailing variance.

Barroso & Santa-Clara (2015) demonstrated that targeting **12% annualized volatility** using the trailing 126-day (6-month) realized standard deviation of momentum returns improved Sharpe from **0.53 to 0.97**, virtually eliminated crash risk, and converted the return distribution from left-skewed (–2.47) to nearly Gaussian (–0.42). Daniel & Moskowitz (2016) refined this with a dynamic strategy that forecasts both mean and variance, achieving **Sharpe of 1.18** — roughly 4× the static momentum baseline across US equities.

For a long-only, no-leverage implementation, you cannot lever up during calm periods, so the full doubling is unattainable. The realistic improvement comes from **scaling down during high-volatility periods**, which is where the largest losses occur. Man AHL research (Harvey et al., 2018) found that volatility targeting improved US equity Sharpe from 0.40 to 0.48–0.51 — a **20–28% improvement** — using an exponentially weighted volatility estimate with a **half-life of 20 days**. Bongaerts, Kang & Van Dijk (2020) showed that a conditional approach — reducing exposure only during extreme high-vol states, holding steady otherwise — **more than doubled Sharpe for momentum** while cutting turnover from 2.1× to 1.4× annually and slashing maximum drawdown from 54.1% to 20.1%.

**Testable parameters for Atlas:**
- Compute trailing portfolio volatility using 20, 40, 60, and 126-day lookback windows with exponential decay (half-life = 20 days)
- Target annualized volatility of 10%, 12%, and 15%
- Scale factor = min(1.0, target_vol / realized_vol) — capped at 1.0 since no leverage
- Conditional variant: only reduce exposure when trailing vol exceeds its 80th percentile over the past 2 years; otherwise hold full allocation
- **Expected Sharpe improvement: 0.69 → 0.80–0.90** for long-only implementation

---

## Risk-adjusted stock selection transforms the momentum signal

Rather than ranking stocks by raw 12-month returns, ranking by **risk-adjusted momentum** (return divided by volatility) produces dramatically better results. Blitz, Huij & Martens (2011) documented that residual momentum — stripping out market factor exposure and ranking on idiosyncratic returns scaled by idiosyncratic volatility — approximately **doubled the Sharpe ratio from 0.45 to 0.90**. This approach is used in production by Robeco and AQR.

A UK market study found that generalized risk-adjusted momentum (return/volatility^N) improved Sharpe from **0.67 to 1.18**, a 76% improvement, while reducing annualized standard deviation from 27% to 19%. The mechanism is straightforward: raw momentum rankings overweight high-volatility lottery stocks that contribute noise and crash risk. Risk-adjusting the signal naturally filters toward stocks with genuine persistent momentum.

Novy-Marx (2012) further showed that **intermediate-horizon momentum** (months 12 to 7, skipping the recent 6 months) generated monthly profits of **1.20%** versus 0.67% for the recent-past signal, and was more robust among the largest, most liquid stocks.

**Testable parameters for Atlas:**
- Replace raw return ranking with return/volatility ratio using 6-month or 12-month returns divided by same-period realized volatility
- Test idiosyncratic momentum: regress each stock's returns on SPY, rank on cumulative residuals (months 12 to 2), scaled by residual standard deviation
- Compare lookback windows: 12-2 (standard), 12-7 (intermediate horizon), and 6-2 (recent)
- Test the 12-1 momentum with 1-month skip versus no skip

---

## A simple market regime filter halves drawdowns with minimal cost

The **S&P 500 above its 200-day simple moving average** is the single most researched and robust regime filter. Faber (2007) documented that the 10-month SMA timing rule improved Sharpe from 0.30 to 0.46 (a 53% improvement) while cutting volatility roughly in half, with fewer than one round-trip trade per year. Post-publication (2006–2012), the filter delivered **Sharpe of 0.61** versus 0.16 for buy-and-hold, with maximum drawdown of just –9.5% versus –46.0%.

The filter's robustness is unusually strong: Faber confirmed "broad parameter stability" across 3-month through 12-month SMAs — no cliff effects. The S&P 500 historically delivered **+11.9% annualized when above the 200-day MA** versus **–4.3% when below it**. For momentum strategies specifically, bear markets trigger the "momentum crash" phenomenon documented by Daniel & Moskowitz, where past losers suddenly outperform past winners during sharp market rebounds.

Breadth confirmation adds a second layer. When fewer than **50% of S&P 500 stocks** trade above their own 200-day moving average, market breadth is deteriorating even if the index itself hasn't broken down. Below 30% historically marks bear-market conditions where momentum signals become unreliable. The Zweig Breadth Thrust — a rare but powerful signal where the 10-day EMA of NYSE advancing issues rises from below 0.40 to above 0.615 within 10 days — has a **100% hit rate** at 6-month and 12-month horizons, with average subsequent 1-year gains of **+23.3%**.

**Testable parameters for Atlas:**
- Primary filter: go to cash (or reduce exposure to 25%) when S&P 500 closes below its 200-day SMA; restore full exposure when above
- Test variants: 150-day, 200-day, and 250-day SMA; 10-month SMA on monthly data
- Breadth overlay: only take new momentum entries when >50% of S&P 500 constituents are above their 200-day MA
- VIX overlay: reduce exposure by 50% when VIX > 25; go to cash when VIX > 30
- VIX-adjusted momentum: divide daily returns by VIX before computing momentum signal (Varadi/CSSA method)
- **Expected Sharpe improvement from 200-day filter alone: +0.10 to +0.20 absolute** (primarily through drawdown reduction)

---

## Stop-losses help momentum but hurt mean reversion

Exit optimization is highly strategy-dependent, and the evidence is unambiguous on this split. Han, Zhou & Zhu (2016) tested stop-losses on monthly momentum strategies across all US stocks from 1926–2013 and found that a **10% stop-loss more than doubled Sharpe** from 0.166 to 0.371, improved skewness from –1.18 to +1.86, and reduced worst monthly loss from –49.8% to –11.4%. The 15% stop level achieved **Sharpe of 0.40** with worst monthly loss of –17.4%. Break-even transaction costs were 3.18–7.18% — confirming the result is robust to realistic trading costs.

Conversely, Connors' research on mean reversion swing trading found that **no stop-loss level improved performance** — the optimal approach was no stops at all. This makes intuitive sense: mean reversion strategies enter after a decline, so a stop-loss triggers precisely when the mean-reversion signal is strengthening.

For your multi-day swing trading timeframe, ATR-based exits are the standard. The consensus across practitioner literature converges on **2× ATR(14) from the highest close** as the workhorse trailing stop for swing trades, with 3× ATR for more volatile names or stronger trends. Close-based stops (exit on close below level rather than intraday breach) preserve more profit by filtering out intraday noise.

The "right tail" problem is real for momentum: fixed profit targets cut off the biggest winners that drive the strategy's edge. The best resolution is **scaling out** — take 50% at a fixed 3:1 risk-reward target, then trail the remaining 50% with a 2× ATR stop. López de Prado's triple barrier method formalizes this with three simultaneous exits: profit target, stop-loss, and time expiration, whichever triggers first.

**Testable parameters for Atlas:**
- For momentum strategies only: test initial stops at 1.5×, 2×, 2.5×, and 3× ATR(14) below entry
- Trailing stops: 2× and 3× ATR(14) from highest close, activated after position reaches 1× ATR profit
- Scale-out: 50% at 3:1 risk-reward, trail remainder with 2× ATR stop
- Time stops: force exit after 15, 20, 25, and 30 trading days if neither target nor stop hit
- For mean reversion strategies: test with NO stop-loss versus 3× and 4× ATR stops to confirm Connors' finding
- Close-based versus intraday stop triggers
- **Expected improvement from stop-loss overlay on momentum strategies: Sharpe roughly doubles** (per Han et al., though results will be more modest for long-only vs. long-short)

---

## Combining momentum and mean reversion exploits a –35% correlation

The mathematical foundation for multi-strategy portfolios is the generalized square root rule from Bailey & López de Prado (2013):

**Portfolio Sharpe = SR̄ × √(N / (1 + (N-1) × ρ̄))**

where SR̄ is average individual Sharpe, N is number of strategies, and ρ̄ is average pairwise correlation. For your 10 positive-Sharpe strategies with average SR of ~0.51, the portfolio Sharpe depends entirely on correlation:

| Average correlation (ρ̄) | Portfolio Sharpe | Improvement vs. best individual |
|---|---|---|
| 0.0 | 1.61 | 2.3× |
| 0.1 | 1.17 | 1.7× |
| 0.2 | 0.96 | 1.4× |
| 0.3 | 0.83 | 1.2× |
| 0.5 | 0.68 | 1.0× |

Balvers & Wu (2006) documented that momentum and mean reversion strategies exhibit a **–35% correlation** across 18 developed equity markets. Your momentum cluster (Momentum Breakout, Trend Following, ADX Trend Pullback, Donchian Breakout) and mean reversion cluster (Short Term MR, Connors RSI2, Lower Band Reversion, BB Squeeze) should exhibit low or negative cross-group correlation, offset by positive within-group correlation from shared market beta. Realistic average pairwise correlation for your all-long-only equity strategies is likely **0.20–0.35**, yielding an estimated portfolio Sharpe of **0.83–0.96**.

The Treynor-Black theorem states that for uncorrelated strategies, the optimal portfolio's squared Sharpe equals the sum of squared individual Sharpes. For your top 10 strategies, this gives SR²_max = 0.69² + 0.66² + 0.58² + 0.58² + 0.51² + 0.49² + 0.42² + 0.41² + 0.41² + 0.32² = 2.70, or SR_max = **1.64** in the theoretical uncorrelated case. With realistic correlations, expect **0.9–1.1** from optimal weighting.

**Actionable implementation for Atlas:**
- Compute the full 10×10 (or 29×29) correlation matrix from daily backtest P&L streams
- Verify that momentum-mean reversion cross-group correlation is below 0.2
- Start with inverse-volatility weighting as baseline, then add a Sharpe-ratio tilt: w_i ∝ SR_i / σ_i
- Apply Ledoit-Wolf shrinkage to the covariance matrix before any optimization
- Cap any single strategy at 20% of capital; minimum 5%
- Exclude strategies with Sharpe below 0.25 (Opening Gap at 0.12 and all negative/zero strategies)
- Tier allocation: ~60% to Tier 1 (SR > 0.55), ~35% to Tier 2 (SR 0.40–0.55), ~5% to Tier 3 (SR 0.25–0.40)
- Rebalance monthly; reduce any strategy's allocation by 50% if its trailing 3-month Sharpe drops below 0.2

---

## Universe and signal filters that compound returns

Several filters act as force multipliers on the primary momentum signal. Volume confirmation — requiring breakout-day volume to exceed **1.5× to 2.0× the 20-day average** — filters out low-conviction breakouts. One practitioner study found that adding an ATR filter (14-period ATR > 50-period MA of ATR) improved profit factor by **~40%** and nearly halved maximum drawdown.

Earnings momentum provides genuinely orthogonal alpha. Novy-Marx (2015) documented that SUE (Standardized Unexpected Earnings) momentum achieved a net Sharpe of **0.55** — the only momentum variant with higher net Sharpe than the market. Price momentum purged of earnings momentum generated only two-thirds the spread of standard momentum, suggesting earnings surprises drive much of what we attribute to price momentum. Combining price momentum ranking with a top-quintile SUE filter creates a dual-signal that captures both sources.

Sector-neutral construction matters more than most traders realize. Constraining momentum portfolios to equal picks per GICS sector improved Sharpe from **0.38 to 0.59** in a 1999–2019 study — a 55% improvement — by preventing the dangerous sector concentration that causes momentum crashes when a formerly hot sector reverses. Moskowitz & Grinblatt (1999) demonstrated that industry momentum explains much of individual stock momentum.

For universe construction, the research converges on a specific configuration:

- **Market cap floor**: $2B+ (mid-caps from $2–15B offer the strongest momentum signal with adequate liquidity)
- **Minimum average daily dollar volume**: $5M+ for swing trading
- **Price floor**: $5 per share minimum
- **Profitability screen**: positive trailing 12-month EPS or EBITDA
- **Trend confirmation**: stock above its 100-day SMA (pre-filters for uptrending stocks only)

---

## Your 0.69 Sharpe is borderline significant after testing 29 strategies

The multiple testing problem is the elephant in the room. Bailey & López de Prado's Deflated Sharpe Ratio framework shows that when you test 29 strategies and select the best one, the expected maximum Sharpe under the null hypothesis (pure noise) is substantial. With 5 years of monthly data and 29 trials, an annualized Sharpe of **0.92 would be expected by chance alone**. Your 0.69 actually falls below this threshold, meaning it may not be statistically distinguishable from luck without a longer track record.

The Bonferroni correction for 29 tests requires a t-statistic above **2.94** (versus 1.96 for a single test). For a monthly Sharpe of 0.20 (annualized 0.69), you need approximately **T = (2.94/0.20)² ≈ 216 months ≈ 18 years** of data. The Benjamini-Hochberg FDR procedure is less conservative and more appropriate when strategies are correlated, but still demands substantial track records.

Practical robustness checks that should be run on every strategy before deployment include parameter stability testing (vary each parameter ±10–20% — neighboring values should produce similar performance), sub-period analysis (split the backtest into 3–5 non-overlapping windows and verify profitability in the majority), universe sensitivity (test on S&P 500, Russell 1000, and Russell 3000), and transaction cost stress testing at 2× estimated costs. Walk-forward analysis should show OOS performance at **50–70% of in-sample** performance; degradation beyond 50% is a strong overfitting signal.

Combinatorial Purged Cross-Validation (CPCV), developed by López de Prado, is superior to standard walk-forward for financial time series. It generates a distribution of OOS performance metrics rather than a single path, enables computation of the Probability of Backtest Overfitting, and demonstrates "marked superiority in mitigating overfitting risks." Implementation is available in Python's `skfolio` library with typical configuration of N=6 folds, k=2 test folds.

---

## Realistic expectations and the diminishing returns frontier

The honest assessment of achievable Sharpe ratios for long-only, no-leverage US equity momentum paints a clear picture. The S&P 500's long-term Sharpe is approximately **0.4–0.5**. The iShares MTUM ETF has achieved **0.81** since its 2013 inception. Berkshire Hathaway's **0.79** over 1976–2017 represents the highest verified 30+ year track record. Long-lived CTAs converge to approximately **0.5** as track records lengthen. Professional systematic trading desks sustain **1.0–2.0** Sharpe, but with leverage, shorting, and infrastructure advantages unavailable to retail.

For a retail systematic trader running long-only US equity momentum without leverage, the realistic ceiling is **0.7–0.9** for a single strategy and **0.9–1.1** for a well-constructed multi-strategy portfolio. Your current 0.69 is genuinely good — near the single-strategy ceiling. The path to 1.0+ runs through portfolio construction, not through squeezing more alpha from individual signals.

The synthesized improvement waterfall, accounting for non-additivity:

| Enhancement layer | Cumulative estimated Sharpe | Priority |
|---|---|---|
| Baseline (Momentum Breakout) | 0.69 | — |
| + Volatility scaling (conditional) | 0.79–0.86 | Highest |
| + Risk-adjusted stock selection | 0.85–0.95 | High |
| + 200-day MA regime filter | 0.90–1.00 | High |
| + Multi-strategy combination (10 strategies) | 0.95–1.10 | High |
| + Stop-loss overlay (momentum strategies) | 1.00–1.15 | Medium |
| + Universe/sector optimization | 1.05–1.20 | Medium |
| + Earnings momentum overlay | 1.08–1.22 | Medium |

These layers are **not fully additive** — each marginal enhancement delivers less than its standalone improvement because the techniques overlap in what risk they address. The realistic combined outcome for a long-only, no-leverage implementation is a portfolio Sharpe in the **0.9–1.1 range**, with the possibility of reaching 1.2 if correlations between your strategies are genuinely low and the signal enhancements prove robust out-of-sample.

## Conclusion: three moves that matter most

The research converges on three actions with the strongest evidence-to-effort ratio. First, **combine your strategies** — computing the correlation matrix of your 10 positive-Sharpe strategies and allocating via inverse-volatility weighting with a Sharpe tilt is likely to yield 0.85–0.95 portfolio Sharpe immediately, exploiting the natural momentum-mean reversion hedge. Second, **add conditional volatility scaling** — reducing exposure when trailing portfolio volatility enters its top quintile protects against the crash episodes that destroy momentum Sharpe ratios, adding 0.10–0.20 to risk-adjusted returns. Third, **implement a 200-day MA market filter** — the simplest, most robust regime filter available, with over a century of supporting evidence and negligible overfitting risk. Together, these three changes attack different failure modes (crash risk, individual strategy weakness, and bear-market exposure) and should bring the aggregate portfolio into the **0.95–1.10 Sharpe range** — at which point further optimization enters the domain of diminishing returns and increasing overfitting risk. Every enhancement beyond these three should be validated with CPCV and walk-forward analysis before deployment, with the Deflated Sharpe Ratio applied to correct for your 29-strategy multiple testing burden.
