#!/usr/bin/env python3
"""Atlas Dashboard Server — HTTP Basic Auth protected.

Serves the dashboard static files behind HTTP Basic Auth.
Credentials from ~/.atlas-secrets.json:
    dashboard_user, dashboard_pass

Run:
    python3 services/dashboard_server.py              # foreground
    systemctl start atlas-dashboard                   # systemd
"""

import base64
import hashlib
import http.server
import json
import os
import secrets
import signal
import sys
from functools import partial
from pathlib import Path

signal.signal(signal.SIGHUP, signal.SIG_IGN)

SECRETS_PATH = Path.home() / ".atlas-secrets.json"
SERVE_DIR = Path("/root/atlas/dashboard/data")
BIND = "127.0.0.1"
PORT = 8899


def _load_credentials() -> tuple[str, str]:
    """Load dashboard_user and dashboard_pass from secrets."""
    if not SECRETS_PATH.exists():
        raise ValueError(f"Secrets file not found: {SECRETS_PATH}")
    with open(SECRETS_PATH) as f:
        s = json.load(f)
    user = s.get("dashboard_user", "")
    pw = s.get("dashboard_pass", "")
    if not user or not pw:
        raise ValueError(
            "Set dashboard_user and dashboard_pass in ~/.atlas-secrets.json"
        )
    return user, pw


class AuthHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler with Basic Auth gate."""

    expected_user = ""
    expected_pass = ""

    def do_GET(self):
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Atlas Dashboard"')
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h1>401 Unauthorized</h1>")
            return
        super().do_GET()

    def do_HEAD(self):
        if not self._check_auth():
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="Atlas Dashboard"')
            self.end_headers()
            return
        super().do_HEAD()

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            user, pw = decoded.split(":", 1)
        except Exception:
            return False
        # Constant-time comparison to prevent timing attacks
        user_ok = secrets.compare_digest(user, self.expected_user)
        pw_ok = secrets.compare_digest(pw, self.expected_pass)
        return user_ok and pw_ok

    def log_message(self, fmt, *args):
        # Suppress per-request logs (noisy in systemd journal)
        pass


def main():
    try:
        user, pw = _load_credentials()
    except ValueError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

    AuthHandler.expected_user = user
    AuthHandler.expected_pass = pw

    os.chdir(SERVE_DIR)
    handler = partial(AuthHandler, directory=str(SERVE_DIR))

    class ReusableHTTPServer(http.server.HTTPServer):
        allow_reuse_address = True

    with ReusableHTTPServer((BIND, PORT), handler) as server:
        print(
            f"Atlas dashboard serving on {BIND}:{PORT} "
            f"(auth: {user}) pid={os.getpid()}",
            flush=True,
        )
        server.serve_forever()


if __name__ == "__main__":
    main()
