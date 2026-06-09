---
id: 2026-06-09-forge-go-live-policy
title: "Should the Forge Go Live with Real Capital on PASS, and Under What Execution Policy?"
created: 2026-06-09T03:21:54.844Z
profile: deep

---

# Should the Forge Go Live with Real Capital on PASS, and Under What Execution Policy?

## Situation

Hephaestus, our autonomous strategy-research forge, is now hardened end-to-end. Today it proved itself: it discovered a strategy that cleared every stage-1 gate (DSR 0.994, holdout 1.31) and passed cross-market generalization (2/2 cap tiers) — and our stage-2 tools (a Monte-Carlo permutation test plus a new beta-confound gate) correctly REJECTED it as long-only equity beta masquerading as alpha. Two strategies have now passed strict gates and been caught as confounds. The pipeline produces "battle-tested" candidates via stage-1 (CPCV/DSR/PBO/write-once holdout/deployment-sanity/beta-confound) then stage-2 (permutation + generalization OR forward-validation).

The operator wants to extend the pipeline from PASS to real LIVE trading at scale, on whichever broker fits each asset class, and believes a paper trial is unnecessary "because the gates are so strict."

The CEO's analysis: the gates validate ALPHA (a frictionless returns series) but NOT EXECUTION (order sizing, real fills/slippage, short borrow, broker-API failure, live-data gaps, reconciliation). These are orthogonal axes. Recommendation: skip the long paper-ALPHA trial (the gates + forward-validation already do that), but keep a short execution-VALIDATION bridge — shadow (zero capital) then canary (tiny real capital) then a track-vs-expectation gate before full allocation. No strategy has yet cleared full stage-2 (both candidates so far were rejected), so this sets policy before the first real PASS arrives. Existing infra: Atlas paper engine on Alpaca (equities), Interactive Brokers (micro-futures), a perp venue for crypto-carry.

## Stakes

First-ever irreversible capital deployment. Live account is retail scale now (~$1-5K) but growing — this sets the risk-policy precedent for every future PASS and for scaling capital 10-100x. Downside: an execution/sizing bug or bad-fill assumption can lose real money on day one even with a genuine edge — a class of failure the entire research stack is structurally blind to. Upside/opportunity cost: capturing live returns is the entire point of the research program; over-cautious gating wastes validated edges and operator time. Reputational/psychological cost of a blown-up first deployment is high (could end the program). Getting the execution policy and broker/venue choice right is worth more than speed.

## Constraints

AU-based operator (ASIC; venue/custody/regulatory limits; Polymarket unavailable; crypto custody risk). Retail capital ~$1-5K, growing. Free compute (Claude Max OAuth, $0). Non-negotiable invariants: research rails non-bypassable; NO autonomous capital (human-gated before any money). Brokers available: Alpaca (US equities, commission-free, FRACTIONAL shares — best small-capital fit; shorts need marginable borrow), Interactive Brokers (only realistic retail futures; micro contracts ~$1-5K fixed notional each), a crypto perp venue for funding-carry (regulatory/custody risk). KEY CONSEQUENCE: the new beta-confound gate means equity strategies that now pass are likely LONG-SHORT / low-beta (real selection alpha), which needs shorting+borrow+margin — operationally hard at $1-5K — so futures or crypto-carry may be the more tradable first venue.

## Key Questions

1. Go live in principle on a stage-2 PASS? And what is the alpha bar to promote to live capital — full stage-2 (permutation + generalization/forward) sufficient, or require a live forward-validation period on top?
2. Execution-validation policy: adopt shadow -> canary -> track-vs-expectation -> full (CEO recommendation), or the operator's straight-to-capital? If a bridge, how long and how much capital for the canary, and what is the track-vs-expectation halt rule?
3. Which broker/asset class goes live FIRST — Alpaca fractional equities (capital-efficient but passing strategies are likely long-short/borrow-hard), IB micro-futures (clean but fixed ~$1-5K notional), or crypto-carry (small-viable but custody/regulatory risk)? Let the gates+capital pick, or set a preference?
4. Capital policy: total initial live budget, per-strategy cap, portfolio drawdown kill-switch level, and the ramp schedule as capital grows and strategies prove out?
5. Autonomy boundary: what may execute AUTOMATICALLY within a pre-approved risk envelope (e.g. daily rebalances of an already-live, already-canaried strategy) versus what requires explicit human approval each time (go-live, scale-up, new broker)?
