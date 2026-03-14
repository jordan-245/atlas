# band_mult

> Parameter tested across strategies. Shows what values work and where.

| Date | Strategy | Change | Result | Sharpe Δ | New Sharpe |
|------|----------|--------|--------|----------|------------|
| 2026-03-14 03:40 | lower_band_reversion | None → 2.0 | ✅ kept | +0.0359 | -0.1649 |
| 2026-03-14 03:40 | lower_band_reversion | None → 2.5 | ❌ discard | +0.0000 | -0.2008 |
| 2026-03-14 03:40 | lower_band_reversion | None → 1.5 | ❌ discard | -2.5009 | -2.7017 |
| 2026-03-14 03:40 | lower_band_reversion | None → 1.0 | ❌ discard | -2.2352 | -2.4360 |
| 2026-03-14 05:33 | lower_band_reversion | 2.0 → 2.5 | ❌ discard | -0.0359 | -0.2008 |
| 2026-03-14 05:33 | lower_band_reversion | 2.0 → 1.5 | ❌ discard | -2.5368 | -2.7017 |
| 2026-03-14 05:33 | lower_band_reversion | 2.0 → 1.0 | ❌ discard | -2.2711 | -2.4360 |
| 2026-03-14 10:11 | lower_band_reversion | 2.0 → 2.5 | ❌ discard | -0.0221 | 0.3745 |
| 2026-03-14 10:11 | lower_band_reversion | 2.0 → 1.5 | ❌ discard | +0.0003 | 0.3969 |
| 2026-03-14 10:11 | lower_band_reversion | 2.0 → 1.0 | ❌ discard | -0.1172 | 0.2794 |
