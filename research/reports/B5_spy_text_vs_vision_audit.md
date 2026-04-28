# B5 — SPY text vs vision divergence audit

**Date**: 2026-04-28
**Investigator**: Engineering audit (Wave B)
**Status**: Investigated — refines task #258 scope (do not close)

## Conclusion

The literal pairing of `"distribution top"` (vision) with `"broadly bullish"` (text) does **not** appear in current overlay logs (post-2026-04-17). It may exist in pre-Apr-17 archived logs, which were not exhaustively swept.

The closest structural-top label found is **`"double-top"`** in vision output dated 2026-04-21 — but text agreed to tighten that day, so there was no divergence in the action recommendation.

## Independent structural finding (worth pursuing regardless)

While auditing, discovered a real architectural gap that explains why text/vision divergence is plausible whether or not the literal entry is found:

- **`overlay/sources/chart_intel.py:379` `_build_summary()`** hard-codes the string `"Broadly bullish"` based on SMA position alone.
- The text path has **no OBV** (On-Balance Volume), **no volume-profile** features, and **no price-volume divergence** indicators.
- It is **structurally incapable** of detecting distribution tops — patterns where price drifts up on falling volume.
- Vision is doing real work that the text feature set cannot currently replicate.

## Recommendation

**Refine** task #258 (do not close). Two follow-up actions:

1. **Confirm-or-deny**: Sweep pre-Apr-17 archived overlay logs for the literal `"distribution top"` + `"broadly bullish"` pairing. Confirms whether the original entry existed and was simply rotated out.
2. **Spec a new task** to upgrade the text-summary feature set:
   - (a) OBV slope feature
   - (b) Multi-month resistance anchor
   - (c) Price-volume divergence slope
   - (d) Suppression guard in `_build_summary()` that downgrades `"Broadly bullish"` when at multi-month resistance on falling/below-average volume.
