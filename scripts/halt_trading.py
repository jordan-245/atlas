#!/usr/bin/env python3
"""Kill switch CLI.
  python3 scripts/halt_trading.py --halt "reason"
  python3 scripts/halt_trading.py --resume
  python3 scripts/halt_trading.py --status
"""
import argparse
import sys
sys.path.insert(0, "/root/atlas")
from brokers.kill_switch import halt, resume, is_halted, halt_reason


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--halt", type=str, metavar="REASON", help="Engage kill switch")
    g.add_argument("--resume", action="store_true", help="Release kill switch")
    g.add_argument("--status", action="store_true", help="Show current state")
    args = ap.parse_args()
    if args.halt:
        halt(args.halt)
        print(f"HALTED: {args.halt}")
    elif args.resume:
        resume()
        print("RESUMED")
    else:
        if is_halted():
            print(f"HALTED: {halt_reason()}")
        else:
            print("ACTIVE — trading enabled")


if __name__ == "__main__":
    main()
