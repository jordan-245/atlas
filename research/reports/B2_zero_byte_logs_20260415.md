# B2 — Zero-byte logs on 2026-04-15

**Date**: 2026-04-28
**Investigator**: Engineering audit (Wave B)
**Status**: Investigated — no fix required (already addressed)

## Conclusion

The five zero-byte log files dated 2026-04-15 in `logs/` were **logrotate stubs**, NOT evidence of service failures. Real log content was rotated to `*.log-20260417` archives.

## Evidence

1. **Rotated archives have content**: `logs/*.log-20260417` archives for the Apr 15 services range from 2,883 to 4,640 bytes each — well within normal cadence.
2. **Database confirms activity**: `research_experiments` table shows **3,788 experiments inserted on 2026-04-15** — the highest count in the surrounding 3-day window. The pipeline ran successfully; only the live `*.log` symlink was rotated to a stub.
3. **Watchdog stub filter merged**: commit `1deedcf5` (2026-04-19) added a filter so `silent_failure_watchdog` no longer alarms on zero-byte rotated stubs.
4. **TimeoutStartSec extended**: commit `0e42f652` (2026-04-28) lifted `TimeoutStartSec` from 1h → 2.5h on the long-running research timers.

## Bonus catch

While investigating, noticed Apr 18 had a **real SIGTERM at midnight** — the prior 1h `TimeoutStartSec` was too short for the sp500 sweep, which legitimately runs >1h. This is now fixed by `0e42f652` (2.5h ceiling).

## Recommendation

Close task **#259** as: "investigated, root cause was logrotate stub artifacts, no fix needed (already addressed by commits `1deedcf5` + `0e42f652`)".
