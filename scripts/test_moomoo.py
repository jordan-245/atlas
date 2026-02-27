#!/usr/bin/env python3
"""Moomoo API connectivity test.

Tests: OpenD connection → account discovery → account info → positions → quote.
Does NOT place any orders or expose credentials.
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from brokers.secrets import get_secret

print("=" * 55)
print("  Moomoo API Connectivity Test")
print("=" * 55)

# 1. Check moomoo-api import
try:
    import moomoo as ft
    print(f"\n✅ moomoo-api v{ft.__version__} imported")
except ImportError:
    print("\n❌ moomoo-api not installed. Run: pip install moomoo-api")
    sys.exit(1)

# 2. Config (non-sensitive)
HOST = "127.0.0.1"
PORT = 11111
SEC_FIRM = ft.SecurityFirm.FUTUAU

print(f"\n── Connecting to OpenD at {HOST}:{PORT} ──\n")

# 3. Quote context
trd_ctx = None
quote_ctx = None
try:
    quote_ctx = ft.OpenQuoteContext(host=HOST, port=PORT)
    ret, data = quote_ctx.get_global_state()
    if ret == ft.RET_OK:
        print(f"✅ OpenD status: {data.get('program_status_type', '?')}")
        print(f"   Quote: {'✅' if data.get('qot_logined') else '❌'}")
        print(f"   Trade: {'✅' if data.get('trd_logined') else '❌'}")
    else:
        print(f"❌ Global state failed: {data}")
        sys.exit(1)

    # 4. Trade context — FUTUAU firm to see real AU account
    trd_ctx = ft.OpenSecTradeContext(
        filter_trdmarket=ft.TrdMarket.HK,
        host=HOST, port=PORT,
        security_firm=SEC_FIRM,
    )
    print("✅ Trade context connected")

    # 5. Account list
    ret, data = trd_ctx.get_acc_list()
    if ret != ft.RET_OK:
        print(f"❌ get_acc_list failed: {data}")
        sys.exit(1)

    real_acc_id = None
    print(f"✅ Accounts found: {len(data)}")
    for _, row in data.iterrows():
        env = row.get("trd_env", "")
        markets = row.get("trdmarket_auth", "")
        acc_id = row["acc_id"]
        print(f"   acc_id={acc_id}  env={env}  markets={markets}")
        if env == "REAL" and "AU" in str(markets):
            real_acc_id = int(acc_id)

    if real_acc_id:
        print(f"   → Real AU account: {real_acc_id}")

        # 6. Account info
        ret, data = trd_ctx.accinfo_query(
            trd_env=ft.TrdEnv.REAL, acc_id=real_acc_id, refresh_cache=True,
        )
        if ret == ft.RET_OK:
            row = data.iloc[0]
            equity = float(row.get("total_assets", 0))
            aud_cash = float(row.get("au_cash", 0))
            print(f"✅ Account — total: ${equity:,.2f}  AUD cash: ${aud_cash:,.2f}")
        else:
            print(f"⚠️  accinfo_query: {data}")

        # 7. Positions
        ret, data = trd_ctx.position_list_query(
            trd_env=ft.TrdEnv.REAL, acc_id=real_acc_id, refresh_cache=True,
        )
        if ret == ft.RET_OK:
            positions = [
                (r.get("code"), int(r.get("qty", 0)))
                for _, r in data.iterrows() if int(r.get("qty", 0)) > 0
            ]
            print(f"✅ Positions: {len(positions)} open")
            for code, qty in positions:
                print(f"   {code}: {qty} shares")
        else:
            print(f"⚠️  positions: {data}")
    else:
        print("   ⚠️  No real AU account found")

    # 8. HK quote test (AU quotes unsupported server-side)
    ret, data = quote_ctx.get_market_snapshot(["HK.00700"])
    if ret == ft.RET_OK:
        price = data.iloc[0].get("last_price", 0)
        name = data.iloc[0].get("name", "")
        print(f"✅ HK quote — {name}: ${price:.2f}")
    else:
        print(f"⚠️  HK quote: {data}")

    print("\n" + "=" * 55)
    print("  ✅ Moomoo API connectivity OK")
    print("=" * 55 + "\n")

except ConnectionRefusedError:
    print(f"❌ Connection REFUSED — OpenD not running on {HOST}:{PORT}")
    print("   Start: python3 scripts/start_opend.py start")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)
finally:
    if trd_ctx:
        trd_ctx.close()
    if quote_ctx:
        quote_ctx.close()
