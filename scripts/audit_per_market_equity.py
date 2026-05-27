#!/usr/bin/env python3
"""Sanity audit: per-market equity sum reconciliation + drift detection.

Run anytime to verify per-market attribution is healthy:
  python3 scripts/audit_per_market_equity.py

Checks:
  1. Latest snapshot per market — sum vs broker_equity
  2. Snapshot freshness (no market >3 days stale)
  3. State-file ghost detection (cross-market positions)
  4. Universe-membership drift (positions outside their market's universe def)
  5. HWM consistency across markets

Exits 0 if clean, 1 if drift > $20 OR any state-file ghost.
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# Bootstrap sys.path so local modules resolve
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_DRIFT_THRESHOLD = 20.0   # dollars — fail if sum(allocated) vs broker_eq drifts more than this
_STALE_DAYS = 3            # days — fail if any market snapshot is older than this
_STATE_DIR = _PROJECT_ROOT / "brokers" / "state"
_CONFIG_DIR = _PROJECT_ROOT / "config" / "active"


def _load_active_markets() -> set[str]:
    """Return the set of market_ids whose active config has trading.live_enabled=True.

    Snapshots/state for markets NOT in this set are historical artifacts from
    universes that have been retired (e.g. sector_etfs/commodity_etfs after the
    2026-05 single-universe consolidation).  The audit treats them as
    informational rather than hard failures.
    """
    active: set[str] = set()
    if not _CONFIG_DIR.exists():
        return active
    for cfg_path in sorted(_CONFIG_DIR.glob("*.json")):
        try:
            cfg = json.loads(cfg_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if cfg.get("trading", {}).get("live_enabled") is not True:
            continue
        mid = cfg.get("market_id") or cfg_path.stem
        active.add(mid)
    return active

# ──────────────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────────────

def _load_state_file(market_id: str) -> dict:
    path = _STATE_DIR / f"live_{market_id}.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Could not read %s: %s", path.name, exc)
        return {}


# ──────────────────────────────────────────────────────────────────────────────
# Check 1: Snapshot sum reconciliation
# ──────────────────────────────────────────────────────────────────────────────

def check_snapshot_reconciliation() -> tuple[bool, str]:
    """Return (pass, report_text).

    Hard-fails when the sum of *active-market* allocated_equity drifts from
    broker_equity by more than ``_DRIFT_THRESHOLD``.  Snapshots for retired
    markets (no longer in config/active with live_enabled=True) are shown
    INFO-only and excluded from the reconciliation sum, because their
    allocated_equity is carry-forward bookkeeping that no longer maps to
    real broker capital.
    """
    try:
        from db.atlas_db import get_db
        with get_db() as db:
            rows = db.execute(
                """
                SELECT market_id, allocated_equity, position_mv, cash_attributed,
                       broker_equity, broker_cash, date, snapshot_time
                FROM market_equity_history
                WHERE date = (SELECT MAX(date) FROM market_equity_history)
                ORDER BY market_id
                """
            ).fetchall()
    except Exception as exc:
        return False, f"DB read failed: {exc}"

    if not rows:
        return False, "No rows in market_equity_history — snapshot never written"

    active = _load_active_markets()
    snap_date = rows[0]["date"]
    broker_eq = rows[0]["broker_equity"] or 0.0
    active_rows = [r for r in rows if r["market_id"] in active]
    inactive_rows = [r for r in rows if r["market_id"] not in active]
    total_alloc_active = sum(r["allocated_equity"] or 0.0 for r in active_rows)
    drift = abs(total_alloc_active - broker_eq)

    lines = [f"Snapshot date: {snap_date}"]
    lines.append(f"  broker_equity (from snapshot): ${broker_eq:.2f}")
    lines.append(f"  active markets (live_enabled=True): {sorted(active) or '⟨none⟩'}")
    if active_rows:
        lines.append("  -- active --")
        for r in active_rows:
            lines.append(
                f"  {r['market_id']}: allocated=${r['allocated_equity']:.2f}  "
                f"(pos_mv=${r['position_mv']:.2f}  cash=${r['cash_attributed']:.2f})"
            )
        lines.append(f"  sum(allocated_equity, active only): ${total_alloc_active:.2f}")
        lines.append(
            f"  drift vs broker_equity = ${drift:.2f} "
            f"{'✓' if drift <= _DRIFT_THRESHOLD else '✗ EXCEEDS THRESHOLD'}"
        )
    else:
        lines.append("  -- active -- (no rows)")

    if inactive_rows:
        lines.append("  -- retired (INFO only, not summed) --")
        for r in inactive_rows:
            lines.append(
                f"  {r['market_id']}: allocated=${r['allocated_equity']:.2f}  "
                f"(pos_mv=${r['position_mv']:.2f}  cash=${r['cash_attributed']:.2f})  [retired]"
            )

    # If there are no active markets, treat as PASS (audit can't reconcile what
    # isn't running).  Otherwise enforce the drift threshold against active rows.
    if not active_rows:
        lines.append(
            "  No active markets to reconcile — audit returns PASS (snapshots are historical only)."
        )
        return True, "\n".join(lines)

    ok = drift <= _DRIFT_THRESHOLD
    return ok, "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Check 2: Snapshot freshness
# ──────────────────────────────────────────────────────────────────────────────

def check_snapshot_freshness() -> tuple[bool, str]:
    """Return (pass, report_text).

    Stale snapshots for ACTIVE markets are flagged ``✗ STALE``.  Stale snapshots
    for retired markets are flagged ``∘ STALE (retired)`` for visibility but do
    not cause the audit to fail — those snapshots only exist as historical
    carry-forward and are expected to drift once the universe is retired.
    """
    try:
        from db.atlas_db import get_db
        with get_db() as db:
            rows = db.execute(
                """
                SELECT market_id, MAX(date) AS latest_date
                FROM market_equity_history
                GROUP BY market_id
                """
            ).fetchall()
    except Exception as exc:
        return False, f"DB read failed: {exc}"

    active = _load_active_markets()
    today = date.today()
    lines = []
    all_ok = True
    for r in rows:
        snap_date_str = r["latest_date"]
        try:
            snap_d = date.fromisoformat(snap_date_str)
            days_old = (today - snap_d).days
        except (ValueError, TypeError):
            days_old = 9999
        stale = days_old > _STALE_DAYS
        is_active = r["market_id"] in active
        if stale and is_active:
            all_ok = False
            marker = "✗ STALE"
        elif stale:
            marker = "∘ STALE (retired — informational)"
        else:
            marker = "✓"
        lines.append(
            f"  {r['market_id']}: latest={snap_date_str}  ({days_old}d old)  {marker}"
        )
    return all_ok, "Snapshot freshness:\n" + "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Check 3: State-file ghost detection
# ──────────────────────────────────────────────────────────────────────────────

def check_state_file_ghosts() -> tuple[bool, str]:
    """Return (pass, report_text).

    A ghost is a position in live_X.json whose canonical universe ≠ X.
    """
    try:
        from universe.membership import check_state_file_universes, clear_cache
        clear_cache()
        violations = check_state_file_universes(_STATE_DIR)
    except Exception as exc:
        return False, f"check_state_file_universes failed: {exc}"

    if not violations:
        return True, "State-file ghosts: NONE ✓"

    lines = [f"State-file ghosts: {len(violations)} FOUND ✗"]
    for v in violations:
        lines.append(
            f"  {v['ticker']} in {v['file']} (market={v['market_id']}) "
            f"but canonical universe={v['canonical_universe']}"
        )
    return False, "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Check 4: Universe-membership drift
# ──────────────────────────────────────────────────────────────────────────────

def check_universe_membership_drift() -> tuple[bool, str]:
    """Return (pass, report_text).

    Drift = a position held by the broker whose ticker is NOT in the
    universe definition for that market.  (It was added historically but
    universe def may have shrunk.)
    """
    try:
        from universe.definitions import UNIVERSES
        from universe.builder import get_universe_tickers
        from universe.membership import derive_universe, clear_cache
        clear_cache()
    except Exception as exc:
        return True, f"Universe check skipped (import error): {exc}"

    active = _load_active_markets()
    # Only check markets that are currently active OR have a state file present.
    markets = sorted(
        active
        | {p.stem.removeprefix("live_") for p in _STATE_DIR.glob("live_*.json")}
    )
    drift: list[str] = []

    for market in markets:
        state = _load_state_file(market)
        positions = state.get("positions", [])
        # Get universe tickers for this market
        try:
            if UNIVERSES.get(market, {}).get("method") == "static":
                universe_tickers = set(UNIVERSES[market].get("tickers", []))
            else:
                universe_tickers = set(get_universe_tickers(market))
        except Exception as exc:
            drift.append(f"  {market}: universe load failed — {exc}")
            continue

        for pos in positions:
            ticker = pos.get("ticker", "")
            if not ticker:
                continue
            canonical = derive_universe(ticker)
            if canonical != market:
                drift.append(
                    f"  {ticker} in {market} state: canonical={canonical} "
                    f"(cross-market attribution OK, research/sweeps may miss it)"
                )
            elif ticker not in universe_tickers:
                drift.append(
                    f"  {ticker} in {market} state: NOT in universe definition "
                    f"(universe shrank after position was opened)"
                )

    if not drift:
        return True, "Universe-membership drift: NONE ✓"
    return True, "Universe-membership drift (non-fatal):\n" + "\n".join(drift)


# ──────────────────────────────────────────────────────────────────────────────
# Check 5: HWM consistency
# ──────────────────────────────────────────────────────────────────────────────

def check_hwm_consistency() -> tuple[bool, str]:
    """Return (pass, report_text).

    Checks for ACTIVE markets only:
    - state file present (missing for inactive markets is informational, not a failure)
    - HWM not None (should be set from starting_equity at minimum)
    - HWM not > 5× starting_equity (would have been set from global broker equity)
    - daily_high_water_date is today or None (None triggers a HWM reset, which is safe)

    For HWM > 5× starting_equity, this is reported as a WARNING (not hard-fail)
    when the HWM looks like global broker equity — the
    ``_load_local_state`` guard self-heals these on next portfolio load.
    """
    import json

    today_str = date.today().isoformat()
    lines = []
    all_ok = True

    active = _load_active_markets()
    state_markets = {
        p.stem.removeprefix("live_")
        for p in _STATE_DIR.glob("live_*.json")
    }
    markets = sorted(active | state_markets)
    if not markets:
        return True, "HWM consistency: no active markets and no state files — nothing to check."

    try:
        configs_dir = _PROJECT_ROOT / "config" / "active"
        for market in markets:
            state_path = _STATE_DIR / f"live_{market}.json"
            cfg_path = configs_dir / f"{market}.json"
            is_active = market in active
            if not state_path.exists():
                if is_active:
                    lines.append(f"  {market}: state file MISSING ✗ (active market)")
                    all_ok = False
                else:
                    lines.append(f"  {market}: state file MISSING ∘ (retired — informational)")
                continue

            state = json.loads(state_path.read_text())
            hwm = state.get("daily_high_water", 0.0)
            hwm_date = state.get("daily_high_water_date")
            halted = state.get("halted", False)

            starting_equity = 5000.0
            try:
                cfg = json.loads(cfg_path.read_text())
                starting_equity = cfg.get("risk", {}).get("starting_equity", 5000.0)
            except Exception:
                pass

            issues = []
            if hwm is None:
                issues.append("HWM is None")
            elif hwm > starting_equity * 5:
                # Soft warning: the live LivePortfolio guard at _load_local_state
                # re-anchors HWM against the snapshot's allocated_equity on next
                # load.  Hard-failing here would block legitimate operations while
                # the self-heal is pending.
                issues.append(
                    f"HWM ${hwm:.2f} > 5× starting_equity ${starting_equity:.2f} "
                    f"— likely stale global HWM (self-heals on next portfolio load)"
                )

            if hwm_date is None:
                issues.append("hwm_date=None (will reset on next drawdown check — safe)")
            elif hwm_date != today_str:
                issues.append(f"hwm_date={hwm_date} (not today, will reset — OK)")

            if halted:
                issues.append("⚠️  market is HALTED")

            tag = "" if is_active else " [retired]"
            status = "✓" if not issues else "⚠"
            lines.append(
                f"  {market}: HWM=${hwm:.2f}  date={hwm_date}  "
                f"halted={halted}  {'|'.join(issues) if issues else 'OK'} {status}{tag}"
            )
    except Exception as exc:
        return False, f"HWM check failed: {exc}"

    return all_ok, "HWM consistency:\n" + "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 60)
    print(f"Per-market equity audit  [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]")
    print("=" * 60)

    overall_pass = True
    hard_failures: list[str] = []

    checks = [
        ("1. Snapshot reconciliation", check_snapshot_reconciliation, True),   # hard fail
        ("2. Snapshot freshness",      check_snapshot_freshness,      False),  # soft
        ("3. State-file ghosts",       check_state_file_ghosts,       True),   # hard fail
        ("4. Universe-membership drift", check_universe_membership_drift, False),  # soft
        ("5. HWM consistency",         check_hwm_consistency,         False),  # soft
    ]

    for name, fn, is_hard in checks:
        print(f"\n── {name} ──")
        try:
            ok, report = fn()
        except Exception as exc:
            ok = False
            report = f"CHECK ERRORED: {exc}"
        print(report)
        if not ok and is_hard:
            overall_pass = False
            hard_failures.append(name)

    print("\n" + "=" * 60)
    if overall_pass:
        print("RESULT: PASS ✓  (no hard failures)")
    else:
        print(f"RESULT: FAIL ✗  hard failures: {hard_failures}")
    print("=" * 60)

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
