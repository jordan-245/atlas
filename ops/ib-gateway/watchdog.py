#!/usr/bin/env python3
"""IB Gateway watchdog — alert on unhealthy container or dead API socket.

Run from a systemd timer (atlas-ib-watchdog.timer, every 10 min). Checks:
  1. container `atlas-ib-ib-gateway-1` is running and docker-health = healthy
  2. the paper API socket (127.0.0.1:4002) accepts a TCP connection

Alerts via atlas.kernel.notify (Telegram) with a 2-strike rule (state file) so a
single transient restart (the daily 05:00 ET auto-restart takes ~2 min) never pages.
Exit 0 always — the timer should not flap on alert failures.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from pathlib import Path

STATE = Path("/root/atlas/data/ib_watchdog_state.json")
CONTAINER = "atlas-ib-ib-gateway-1"
PORT = 4002


def _container_health() -> str:
    try:
        out = subprocess.run(
            ["docker", "inspect", "--format",
             "{{.State.Status}}/{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}",
             CONTAINER],
            capture_output=True, text=True, timeout=15)
        return out.stdout.strip() if out.returncode == 0 else "absent"
    except Exception:
        return "docker-error"


def _socket_ok() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=5):
            return True
    except OSError:
        return False


def main() -> int:
    health = _container_health()
    sock = _socket_ok()
    ok = health.endswith("/healthy") and sock

    state = {"strikes": 0, "alerted": False}
    if STATE.exists():
        try:
            state = json.loads(STATE.read_text())
        except Exception:
            pass

    if ok:
        if state.get("alerted"):
            _notify("✅ IB Gateway recovered — container healthy, API socket up.")
        state = {"strikes": 0, "alerted": False}
    else:
        state["strikes"] = int(state.get("strikes", 0)) + 1
        if state["strikes"] >= 2 and not state.get("alerted"):
            _notify(f"🔴 IB Gateway DOWN (2 checks): container={health} socket_4002={'up' if sock else 'DOWN'}.\n"
                    f"Debug: docker logs {CONTAINER} | VNC localhost:5900")
            state["alerted"] = True

    state["last_check"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    state["last_status"] = f"container={health} socket={'up' if sock else 'down'}"
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=1))
    print(state["last_status"])
    return 0


def _notify(text: str) -> None:
    try:
        sys.path.insert(0, "/root/atlas")
        from atlas.kernel.notify import send_message
        send_message(text)
    except Exception as e:  # alerting must never crash the watchdog
        print(f"notify failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
