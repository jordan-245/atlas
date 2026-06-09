## Vote: RESEARCH_TESTNET_ONLY

---

### Top Recommendation

Run a **time-boxed 3-week research spike** — no live capital, no leverage, testnet only — with a single binary question: *does a demonstrable edge exist in BTC/ETH perpetuals intraday after fees, funding, and slippage?* If yes, it changes everything. If no, we kill it cleanly with minimal sunk cost.

---

### Reasoning From My Lens

Everyone here will correctly say "Atlas first." I want to ask the harder question: **are we optimizing a dying strategy while a better one sits dormant?**

Atlas is generating ~$283 realized PnL on $1,373 capital — equity momentum in a regulated, commission-free market with `approval=false`. It's working, but it's slow. Crypto perpetuals are a fundamentally different beast: 24/7 continuous signal, massive retail inefficiency, higher vol = more edge expression per day, and leverage that converts a 0.3% move into a meaningful return.

The prior Q2 memo said "stop thinking about Midas." That was the right call *at that moment* — we were chasing distraction. But conditions change. The ask now is more specific: **leveraged intraday, liquid majors, defined risk controls**. That's not Midas-revival nostalgia. That's a real product question.

The moonshot framing: what if crypto intraday generates 10x the learning-per-week compared to Atlas equity momentum? Faster feedback loops compound faster. The downside of a 3-week testnet spike is small. The upside of discovering a genuine edge is a second income engine with uncorrelated returns.

**But I reject the framing of "build the bot first."** That's the wrong problem. The right problem is: *prove the edge exists, then build the infrastructure to extract it.*

---

### Mandatory Gates Before Live Capital

1. **Edge gate**: Backtested Sharpe ≥ 1.0 on BTC/ETH perpetuals, 2022–2025, after 0.06% taker fees + funding rate drag + 2bp slippage assumption. Both long AND short sides tested — not just trend-following in bull market.
2. **Paper gate**: 30+ simulated trades on testnet with live Bybit feed, P&L positive after synthetic funding.
3. **Atlas gate**: SP500 30+ closed trades AND AUM > $25K (prior conditional still applies — don't waive it).
4. **Operational gate**: kill switch tested, liquidation-distance alerts proven, 24/7 monitoring confirmed.

---

### Mandatory Risk Limits If Ever Live

- **Max leverage**: 3x to start. No exceptions regardless of conviction. Bybit offers 100x; ignore it.
- **Max capital**: $500 initial. Hard cap.
- **Daily loss limit**: 5% of capital = auto-kill.
- **Liquidation buffer**: Never enter a position where liquidation distance < 20% from entry.
- **Position concentration**: One position at a time during paper phase.
- **Funding awareness**: No position held across a funding cycle unless funding rate < 0.01%.

---

### Preferred Technical Approach

**Start with Midas/Freqtrade.** It's 70% built. The moonshot principle: don't reinvent infrastructure — reinvent the strategy. Use Freqtrade's battle-tested risk engine, Bybit connector, and backtesting framework. Spend engineering time on the *edge* (signal research), not on reconnection logic and order state machines.

Custom infrastructure only if we hit a wall that Freqtrade genuinely can't solve — and that's a bridge we cross in week 3, not week 1.

---

### Relative Priority vs. Atlas Blockers

**This does NOT displace #399, #400, or #215.** Those are Atlas's critical path — blocking live approval and scale. They get resolved first by the Atlas team.

The crypto spike is **parallel research** — one person, three weeks, testnet only, zero capital. The constraint isn't engineering bandwidth for Atlas; it's whether we can afford *one* researcher running this concurrently.

If we can't staff a parallel spike without compromising Atlas: **defer**. The edge opportunity in crypto perpetuals isn't going anywhere. BTC will still be volatile in Q3.

---

### The Asymmetric Bet

The downside of the spike: 3 weeks, some compute, one person's focus. The upside: a second uncorrelated alpha engine that compounds 24/7 while Atlas sleeps. That's the moonshot worth checking. Don't build the bot — **prove the edge first.**
