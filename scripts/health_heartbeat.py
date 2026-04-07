#!/usr/bin/env python3
"""CLI heartbeat writer for cron pipelines.

Usage:
    python3 scripts/health_heartbeat.py <service> <status> [detail_json]

Examples:
    python3 scripts/health_heartbeat.py premarket running '{"stage": "ingest"}'
    python3 scripts/health_heartbeat.py premarket completed '{"plan_generated": true}'
    python3 scripts/health_heartbeat.py postclose failed '{"error": "broker timeout"}'
"""
import json
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from monitor.health_writer import heartbeat


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <service> <status> [detail_json]", file=sys.stderr)
        sys.exit(1)

    service = sys.argv[1]
    status = sys.argv[2]
    detail = None
    if len(sys.argv) > 3:
        try:
            detail = json.loads(sys.argv[3])
        except json.JSONDecodeError:
            detail = {"raw": sys.argv[3]}

    heartbeat(service, status, detail)


if __name__ == "__main__":
    main()
