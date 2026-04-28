#!/usr/bin/env python3
"""Kill switch CLI.
  python3 scripts/halt_trading.py --halt "reason"
  python3 scripts/halt_trading.py --resume [--market MARKET]
  python3 scripts/halt_trading.py --status
"""
import argparse
import sys
sys.path.insert(0, "/root/atlas")
from brokers.kill_switch import halt, resume, is_halted, halt_reason


def _clear_market_halt(market: str) -> None:
    """Clear market_state.halted=0 and live_<market>.json halted=false.

    Mirrors the dual-write pattern used in live_portfolio.save_state() to
    ensure all three halt artefacts are cleared on resume.
    """
    import json
    import sqlite3
    from pathlib import Path

    atlas_root = Path("/root/atlas")

    # 1. Clear market_state DB row
    db_path = atlas_root / "data" / "atlas.db"
    try:
        with sqlite3.connect(str(db_path)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute(
                "UPDATE market_state SET halted=0, halt_reason=NULL, halted_at=NULL "
                "WHERE market_id=?",
                (market,),
            )
        print(f"market_state: cleared halted for '{market}'")
    except Exception as e:
        print(
            f"WARNING: failed to clear market_state for '{market}': {e}",
            file=sys.stderr,
        )

    # 2. Clear live_<market>.json halted=false
    json_path = atlas_root / "brokers" / "state" / f"live_{market}.json"
    if json_path.exists():
        try:
            with open(json_path) as f:
                state = json.load(f)
            state["halted"] = False
            state["halt_reason"] = ""
            with open(json_path, "w") as f:
                json.dump(state, f, indent=2)
            print(f"JSON state: cleared halted for '{market}' ({json_path.name})")
        except Exception as e:
            print(
                f"WARNING: failed to update JSON state for '{market}': {e}",
                file=sys.stderr,
            )
    else:
        print(
            f"JSON state: no live_{market}.json found — skipping "
            f"({json_path} does not exist)"
        )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Atlas kill-switch CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--halt", type=str, metavar="REASON", help="Engage kill switch")
    g.add_argument("--resume", action="store_true", help="Release kill switch")
    g.add_argument("--status", action="store_true", help="Show current state")
    ap.add_argument(
        "--market", type=str, default=None,
        help=(
            "Market ID to target for --resume dual-clear "
            "(e.g. sp500, commodity_etfs, sector_etfs). "
            "Required to also clear market_state DB + live_*.json."
        ),
    )
    args = ap.parse_args()

    if args.halt:
        halt(args.halt)
        print(f"HALTED: {args.halt}")

    elif args.resume:
        resume()
        print("RESUMED: HALT file cleared")
        if args.market:
            _clear_market_halt(args.market)
        else:
            print(
                "\nNOTE: --market not specified.\n"
                "  market_state DB and live_*.json halted fields were NOT cleared.\n"
                "  To fully resume a specific market, run:\n"
                "    python3 scripts/halt_trading.py --resume --market <market_id>\n"
                "  (TODO: add --market to cron/runbook for targeted resume)",
                file=sys.stderr,
            )

    else:  # --status
        if is_halted():
            print(f"HALTED: {halt_reason()}")
        else:
            print("ACTIVE — trading enabled")


if __name__ == "__main__":
    main()
