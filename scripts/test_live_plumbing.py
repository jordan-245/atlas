#!/usr/bin/env python3
"""Verify live trading plumbing is correctly wired and safely disabled.

Tests:
    1. Default config → paper broker (live disabled)
    2. broker=moomoo but live_enabled=False → still paper
    3. broker=moomoo + live_enabled=True → LiveExecutor created
    4. LiveExecutor refuses to connect without proper config
    5. Pre-flight checks block bad orders
    6. Dry-run mode logs but doesn't execute
    7. Emergency halt works
    8. Reconciliation logic
    9. Kill switch via .live_halt file
"""

import json
import os
import sys
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

from brokers.registry import get_broker, get_live_executor
from brokers.base import OrderSide, OrderStatus
from brokers.live_executor import (
    LiveExecutor, preflight_check_config, preflight_check_order,
    HALT_FILE, _journal_entry,
)
from brokers.paper import PaperBroker

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print(f"  ✅ {msg}")

def fail(msg):
    global FAIL
    FAIL += 1
    print(f"  ❌ {msg}")

def check(condition, msg):
    if condition:
        ok(msg)
    else:
        fail(msg)


# ─── Load real config ──────────────────────────────────────────
with open(PROJECT / "config" / "active" / "asx.json") as f:
    real_config = json.load(f)


print("═" * 55)
print("  Live Trading Plumbing — Verification")
print("═" * 55)
print()


# ─── Test 1: Default config → paper ───────────────────────────
print("1. Default config uses paper broker")
broker = get_broker("asx", real_config)
check(isinstance(broker, PaperBroker), f"get_broker returns PaperBroker (got {type(broker).__name__})")
check(not broker.is_live, "broker.is_live is False")

executor = get_live_executor(real_config)
check(executor is None, "get_live_executor returns None when disabled")
print()


# ─── Test 2: broker=moomoo but live_enabled=False ─────────────
print("2. broker=moomoo + live_enabled=False → still paper")
cfg2 = json.loads(json.dumps(real_config))
cfg2["trading"]["broker"] = "moomoo"
cfg2["trading"]["live_enabled"] = False
broker2 = get_broker("asx", cfg2)
check(isinstance(broker2, PaperBroker), f"Still PaperBroker (got {type(broker2).__name__})")
executor2 = get_live_executor(cfg2)
check(executor2 is None, "LiveExecutor still None")
print()


# ─── Test 3: broker=moomoo + live_enabled=True → executor ────
print("3. broker=moomoo + live_enabled=True → LiveExecutor created")
cfg3 = json.loads(json.dumps(real_config))
cfg3["trading"]["broker"] = "moomoo"
cfg3["trading"]["live_enabled"] = True
executor3 = get_live_executor(cfg3)
check(executor3 is not None, "LiveExecutor created")
check(isinstance(executor3, LiveExecutor), f"Type is LiveExecutor (got {type(executor3).__name__})")
check(executor3.is_live_enabled, "is_live_enabled = True")
check(executor3.is_dry_run, "is_dry_run = True (default)")
print()


# ─── Test 4: Pre-flight config checks ─────────────────────────
print("4. Pre-flight config validation")
errors_disabled = preflight_check_config(real_config)
check(len(errors_disabled) > 0, f"Disabled config has errors: {errors_disabled[0]}")

errors_enabled = preflight_check_config(cfg3)
check(len(errors_enabled) == 0, "Enabled config passes pre-flight")

bad_cfg = json.loads(json.dumps(cfg3))
del bad_cfg["trading"]["live_safety"]
errors_bad = preflight_check_config(bad_cfg)
check(any("live_safety" in e for e in errors_bad), "Missing safety section caught")
print()


# ─── Test 5: Pre-flight order checks ──────────────────────────
print("5. Pre-flight order validation")
safety = cfg3["trading"]["live_safety"]

errors_ok = preflight_check_order("CBA.AX", OrderSide.BUY, 10, 50.0, safety, 0)
check(len(errors_ok) == 0, "Valid order passes ($500 < $750 max)")

errors_too_big = preflight_check_order("CBA.AX", OrderSide.BUY, 100, 50.0, safety, 0)
check(any("exceeds" in e for e in errors_too_big), f"Oversized order blocked ($5000 > $750)")

errors_daily = preflight_check_order("CBA.AX", OrderSide.BUY, 1, 10.0, safety, 5)
check(any("Daily" in e for e in errors_daily), "Daily order limit enforced")

errors_zero = preflight_check_order("CBA.AX", OrderSide.BUY, 0, 10.0, safety, 0)
check(any("quantity" in e.lower() for e in errors_zero), "Zero qty blocked")
print()


# ─── Test 6: LiveExecutor refuses connect without live_enabled ─
print("6. LiveExecutor.connect() blocked when not configured")
exec_disabled = LiveExecutor(real_config)
result = exec_disabled.connect()
check(not result, "connect() returns False for disabled config")
print()


# ─── Test 7: Plan execution requires APPROVED status ──────────
print("7. Plan must be APPROVED")
exec7 = LiveExecutor(cfg3)
# Don't connect — just test plan validation
exec7._connected = True  # fake for testing
exec7._broker = None

plan_pending = {"status": "PENDING_APPROVAL", "proposed_entries": [], "proposed_exits": []}
report = exec7.execute_plan(plan_pending, "2026-01-01")
check("error" in report, f"PENDING plan rejected: {report.get('error', '')[:50]}")

plan_approved = {"status": "APPROVED", "proposed_entries": [], "proposed_exits": []}
report2 = exec7.execute_plan(plan_approved, "2026-01-01")
check("error" not in report2, "APPROVED plan accepted")
print()


# ─── Test 8: Emergency halt ───────────────────────────────────
print("8. Emergency halt and kill switch")
exec8 = LiveExecutor(cfg3)
exec8.emergency_halt("Test halt")
check(exec8._halted, "Executor is halted")
check(HALT_FILE.exists(), ".live_halt file created")

exec8_new = LiveExecutor(cfg3)
result8 = exec8_new.connect()
check(not result8, "New executor can't connect while halt file exists")

exec8.clear_halt()
check(not HALT_FILE.exists(), ".live_halt file removed after clear")
print()


# ─── Test 9: Dry-run flag ────────────────────────────────────
print("9. Dry-run configuration")
cfg9_dry = json.loads(json.dumps(cfg3))
cfg9_dry["trading"]["live_safety"]["dry_run_first"] = True
exec9 = LiveExecutor(cfg9_dry)
check(exec9.is_dry_run, "dry_run_first=True → is_dry_run=True")

cfg9_live = json.loads(json.dumps(cfg3))
cfg9_live["trading"]["live_safety"]["dry_run_first"] = False
exec9b = LiveExecutor(cfg9_live)
check(not exec9b.is_dry_run, "dry_run_first=False → is_dry_run=False")
print()


# ─── Test 10: Active config is safe ──────────────────────────
print("10. Active config safety verification")
check(real_config["trading"]["broker"] == "paper", "Active broker is 'paper'")
check(not real_config["trading"].get("live_enabled", False), "Active live_enabled is False")
check(real_config["trading"]["live_safety"]["dry_run_first"], "Active dry_run_first is True")
max_val = real_config["trading"]["live_safety"]["max_order_value"]
check(max_val <= 1000, f"Active max_order_value is conservative (${max_val})")
print()


# ─── Summary ──────────────────────────────────────────────────
print("═" * 55)
total = PASS + FAIL
print(f"  Results: {PASS}/{total} passed, {FAIL} failed")
if FAIL == 0:
    print("  ✅ All plumbing verified — live trading safely disabled")
else:
    print("  ⚠️  Fix failures above")
print("═" * 55)

# Clean up test artifacts
HALT_FILE.unlink(missing_ok=True)

sys.exit(FAIL)
