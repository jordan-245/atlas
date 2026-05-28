"""Tiny stand-in for /api/finance on port 8899.

Used for visual verification of the new React FinanceTab when the real
Atlas dashboard backend isn't running locally. Returns a hand-built
FinanceData payload that matches the REAL Up Bank /api/finance shape —
no mockup-only fields (no savings_goals, round_ups, joint splits).

Usage:
    python dashboard-ui/finance-mockups/dev-mock-server.py
Then `npm run dev` in dashboard-ui — Vite proxies /api/* → 127.0.0.1:8899.
"""
from __future__ import annotations

import http.server
import json
from pathlib import Path

PORT = 8899
PAYLOAD_FILE = Path(__file__).parent / "dev-finance-payload.json"


class Handler(http.server.BaseHTTPRequestHandler):
    def _send_json(self, body: dict, status: int = 200) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        # Allow any origin so the proxy works regardless of dev port.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802 — stdlib signature
        if self.path.startswith("/api/finance"):
            try:
                payload = json.loads(PAYLOAD_FILE.read_text(encoding="utf-8"))
            except Exception as e:
                self._send_json({"error": f"mock payload load failed: {e}"}, status=500)
                return
            self._send_json(payload)
            return
        # Stub: many other endpoints exist on the real dashboard; for the
        # Finance tab we only need /api/finance. Return 404 for others so
        # the UI's error boundaries get exercised, not silently mocked.
        self._send_json({"error": "not mocked"}, status=404)

    def log_message(self, fmt: str, *args: object) -> None:  # quieter
        print(f"[mock /api/finance] {fmt % args}")


def main() -> None:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Mock /api/finance listening on http://127.0.0.1:{PORT}")
    print(f"Payload: {PAYLOAD_FILE}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")
        server.shutdown()


if __name__ == "__main__":
    main()
