#!/usr/bin/env python3
"""IBKR Client Portal REST API connectivity test.

Tests: gateway health → auth status → account discovery → positions → market data.
Does NOT place any orders.

Prerequisites:
    - IBeam Docker container or Client Portal Gateway running
    - Gateway at https://localhost:5000 (default)

Usage:
    python3 scripts/test_ibkr.py
    python3 scripts/test_ibkr.py --port 5000
    python3 scripts/test_ibkr.py --base-url https://myhost:5000/v1/api
"""
import argparse
import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

print("=" * 60)
print("  Interactive Brokers — Client Portal REST API Test")
print("=" * 60)

# Parse args
parser = argparse.ArgumentParser()
parser.add_argument("--host", default="localhost")
parser.add_argument("--port", type=int, default=5000,
                    help="Gateway port (default: 5000)")
parser.add_argument("--base-url", default=None,
                    help="Full base URL override")
parser.add_argument("--market", default="asx", choices=["asx", "sp500", "hk"])
args = parser.parse_args()

base = args.base_url or f"https://{args.host}:{args.port}/v1/api"
session = requests.Session()
session.verify = False

def get(path, params=None):
    try:
        r = session.get(f"{base}{path}", params=params, timeout=10)
        return r.status_code, r.json() if r.content else None
    except requests.ConnectionError as e:
        return 0, str(e)
    except Exception as e:
        return -1, str(e)

def post(path, data=None):
    try:
        r = session.post(f"{base}{path}", json=data, timeout=10)
        return r.status_code, r.json() if r.content else None
    except requests.ConnectionError as e:
        return 0, str(e)
    except Exception as e:
        return -1, str(e)


# 1. Gateway health
print(f"\n📡 Testing gateway at {base}")
code, data = post("/tickle")
if code == 0:
    print(f"❌ Gateway not reachable at {base}")
    print("\nTroubleshooting:")
    print("  1. Is IBeam or Client Portal Gateway running?")
    print("     Docker: docker run -p 5000:5000 -e IBEAM_ACCOUNT=xxx -e IBEAM_PASSWORD=xxx voyz/ibeam")
    print("  2. Check: curl -k https://localhost:5000/v1/api/tickle")
    print(f"\n  (Error: {str(data)[:100]})")
    print("\n  Gateway is required. Remaining tests show what WILL be checked once running.\n")

    # Show what the adapter does even without gateway
    print("─" * 60)
    print("  Testing IBKRBroker adapter (no gateway)...")
    print("─" * 60)
    from brokers.ibkr.broker import IBKRBroker
    from brokers.ibkr.mapper import strip_suffix, to_atlas, to_conid_lookup

    print(f"\n  Mapper tests:")
    print(f"    strip_suffix('BHP.AX', 'asx')     = {strip_suffix('BHP.AX', 'asx')}")
    print(f"    strip_suffix('AAPL', 'sp500')      = {strip_suffix('AAPL', 'sp500')}")
    print(f"    to_atlas('BHP', 'ASX')             = {to_atlas('BHP', 'ASX')}")
    print(f"    to_atlas('AAPL', 'NASDAQ')         = {to_atlas('AAPL', 'NASDAQ')}")
    print(f"    to_conid_lookup('BHP.AX', 'asx')   = {to_conid_lookup('BHP.AX', 'asx')}")
    print(f"    to_conid_lookup('AAPL', 'sp500')   = {to_conid_lookup('AAPL', 'sp500')}")

    print(f"\n  Broker instantiation:")
    config = {
        "trading": {"broker": "ibkr", "live_enabled": True},
        "ibkr": {"host": args.host, "port": args.port, "currency": "AUD"},
        "risk": {"starting_equity": 5000},
    }
    broker = IBKRBroker(config, live=False)
    print(f"    ✅ Created: {broker}")
    print(f"    market_id: {broker.market_id}")
    print(f"    is_live: {broker.is_live}")

    from brokers.registry import available_brokers
    print(f"\n  Registry: {available_brokers()}")

    print(f"\n{'='*60}")
    print("  Gateway not running — start IBeam then re-run this test")
    print(f"{'='*60}")
    sys.exit(1)

print(f"✅ Gateway responding (tickle)")
if data:
    session_id = data.get("session", "?")
    print(f"   Session: {session_id}")

# 2. Auth status
print(f"\n🔐 Auth status:")
code, data = get("/iserver/auth/status")
if data:
    auth = data.get("authenticated", False)
    competing = data.get("competing", False)
    connected = data.get("connected", False)
    print(f"   Authenticated: {'✅' if auth else '❌'} {auth}")
    print(f"   Connected:     {connected}")
    if competing:
        print(f"   ⚠️  Competing session detected")
    if not auth:
        print("\n   Session not authenticated. Check IBeam logs.")
        print("   You may need to re-authenticate via the gateway.")
        sys.exit(1)
else:
    print(f"   ❌ Auth check failed (HTTP {code})")
    sys.exit(1)

# 3. Accounts
print(f"\n📋 Accounts:")
code, data = get("/portfolio/accounts")
if data and isinstance(data, list):
    for acc in data:
        acc_id = acc.get("id", "?")
        acc_type = acc.get("type", "?")
        print(f"   • {acc_id} (type={acc_type})")
    account_id = data[0].get("id", "")
else:
    print(f"   ❌ No accounts found")
    sys.exit(1)

# 4. Account summary
print(f"\n💰 Account summary ({account_id}):")
code, data = get(f"/portfolio/{account_id}/summary")
if data:
    key_fields = [
        "netliquidation", "availablefunds", "grosspositionvalue",
        "buyingpower", "totalcashvalue", "unrealizedpnl", "realizedpnl",
    ]
    for field in key_fields:
        val = data.get(field, {})
        if isinstance(val, dict):
            amount = val.get("amount", "N/A")
            currency = val.get("currency", "")
            print(f"   {field:30s} {str(amount):>15s} {currency}")
        elif val:
            print(f"   {field:30s} {str(val):>15s}")
else:
    print(f"   ❌ Summary failed (HTTP {code})")

# 5. Positions
print(f"\n📊 Positions:")
code, data = get(f"/portfolio/{account_id}/positions/0")
if data and isinstance(data, list):
    positions = [p for p in data if p.get("position", 0) != 0]
    if positions:
        print(f"   {'Ticker':<12s} {'Qty':>8s} {'AvgCost':>10s} {'MktPrice':>10s} {'UnPnL':>10s} {'Exchange':<8s}")
        print("   " + "-" * 62)
        for p in positions:
            desc = p.get("contractDesc", "?")
            sym = desc.split()[0] if desc else "?"
            print(f"   {sym:<12s} {p.get('position', 0):>8.0f} "
                  f"{p.get('avgCost', 0):>10.2f} {p.get('mktPrice', 0):>10.2f} "
                  f"{p.get('unrealizedPnl', 0):>10.2f} {p.get('listingExchange', '?'):<8s}")
    else:
        print("   (no positions)")
else:
    print(f"   (no positions or error)")

# 6. Security search test
print(f"\n🔍 Security search test:")
test_symbols = ["BHP", "CBA", "CSL"] if args.market == "asx" else ["AAPL", "MSFT", "SPY"]
for sym in test_symbols:
    code, data = post("/iserver/secdef/search", {"symbol": sym})
    if data and isinstance(data, list) and data:
        first = data[0]
        conid = first.get("conid", "?")
        desc = first.get("description", "?")
        sections = first.get("sections", [])
        exchanges = [s.get("exchange", "") for s in sections if s.get("secType") == "STK"]
        print(f"   ✅ {sym:8s} conid={conid} desc='{desc}' exchanges={exchanges[:3]}")
    else:
        print(f"   ❌ {sym:8s} — no results")

# 7. Market data snapshot
print(f"\n📉 Market data snapshot:")
# Get conids from the search above
for sym in test_symbols[:2]:
    code, data = post("/iserver/secdef/search", {"symbol": sym})
    if data and isinstance(data, list) and data:
        conid = data[0].get("conid")
        if conid:
            code2, snap = get("/md/snapshot", {"conids": str(conid), "fields": "31,84,86"})
            if snap and isinstance(snap, list) and snap:
                item = snap[0]
                last = item.get("31", "N/A")
                bid = item.get("84", "N/A")
                ask = item.get("86", "N/A")
                print(f"   {sym:8s} last={last} bid={bid} ask={ask}")
            else:
                # IBKR needs a moment to subscribe — retry once
                import time; time.sleep(2)
                code2, snap = get("/md/snapshot", {"conids": str(conid), "fields": "31,84,86"})
                if snap and isinstance(snap, list) and snap:
                    item = snap[0]
                    print(f"   {sym:8s} last={item.get('31', 'N/A')} (delayed)")
                else:
                    print(f"   {sym:8s} — no data (market may be closed)")

# 8. Atlas broker adapter test
print(f"\n🔌 Atlas IBKRBroker adapter test:")
from brokers.ibkr.broker import IBKRBroker

config = {
    "trading": {"broker": "ibkr", "live_enabled": True},
    "ibkr": {"host": args.host, "port": args.port, "currency": "AUD" if args.market == "asx" else "USD"},
    "risk": {"starting_equity": 5000},
}
broker = IBKRBroker(config, live=False)
if broker.connect():
    print(f"   ✅ IBKRBroker connected: {broker}")

    acct = broker.get_account_info()
    print(f"   Account: equity={acct.equity} cash={acct.cash} "
          f"market_value={acct.market_value} currency={acct.currency}")

    positions = broker.get_positions()
    print(f"   Positions: {len(positions)}")
    for p in positions[:5]:
        print(f"     {p.ticker:12s} {p.shares:>6d} @ {p.entry_price:.2f} "
              f"current={p.current_price:.2f} pnl={p.unrealized_pnl:.2f}")

    broker.disconnect()
else:
    print(f"   ❌ IBKRBroker adapter connect failed")

# Done
print(f"\n{'='*60}")
print("  Test complete")
print(f"{'='*60}")
print(f"\nTo use IBKR for ASX trading:")
print('  1. Run gateway: docker run -p 5000:5000 -e IBEAM_ACCOUNT=xxx -e IBEAM_PASSWORD=xxx voyz/ibeam')
print(f'  2. Set config/active/asx.json: "trading": {{ "broker": "ibkr" }}')
print(f'  3. Set config/active/asx.json: "ibkr": {{ "port": {args.port} }}')
