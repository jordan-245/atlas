#!/usr/bin/env python3
"""Helper: flip live_enabled=false in commodity_etfs.json and sector_etfs.json.

Also bumps version field:
  commodity_etfs: v1.3-consolidation-passive -> v1.4-consolidated-closed
  sector_etfs:    v1.0.3-consolidation-passive -> v1.0.4-consolidated-closed

Idempotent: if already flipped, does nothing.
Run standalone:  python3 scripts/_consolidation_flip_configs.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ATLAS_HOME = Path(__file__).resolve().parent.parent
CONFIG_DIR = ATLAS_HOME / "config" / "active"

TARGETS: list[dict] = [
    {
        "path": CONFIG_DIR / "commodity_etfs.json",
        "new_version": "v1.4-consolidated-closed",
        "old_version": "v1.3-consolidation-passive",
    },
    {
        "path": CONFIG_DIR / "sector_etfs.json",
        "new_version": "v1.0.4-consolidated-closed",
        "old_version": "v1.0.3-consolidation-passive",
    },
]


def flip_config(target: dict) -> None:
    """Flip live_enabled=false and bump version. Idempotent."""
    path: Path = target["path"]
    new_version: str = target["new_version"]

    data = json.loads(path.read_text(encoding="utf-8"))

    # Idempotency: already flipped?
    already_flipped = (
        data.get("trading", {}).get("live_enabled") is False
        and data.get("version") == new_version
    )
    if already_flipped:
        print(f"  {path.name}: already flipped (idempotent)", flush=True)
        return

    changed = False

    # Flip live_enabled
    trading = data.get("trading", {})
    if trading.get("live_enabled") is not False:
        old_val = trading.get("live_enabled")
        data["trading"]["live_enabled"] = False
        changed = True
        print(f"  {path.name}: live_enabled {old_val!r} -> false", flush=True)
    else:
        print(f"  {path.name}: live_enabled already false", flush=True)

    # Bump version
    old_v = data.get("version", "(unknown)")
    if old_v != new_version:
        data["version"] = new_version
        changed = True
        print(f"  {path.name}: version {old_v!r} -> {new_version!r}", flush=True)
    else:
        print(f"  {path.name}: version already {new_version!r}", flush=True)

    if changed:
        # Write back with trailing newline to match repo style
        path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
        print(f"  {path.name}: written", flush=True)
        # Verify parseable
        json.loads(path.read_text(encoding="utf-8"))
        print(f"  {path.name}: re-parse OK", flush=True)


if __name__ == "__main__":
    ok = True
    for t in TARGETS:
        print(f"Processing: {t['path'].name}", flush=True)
        try:
            flip_config(t)
        except Exception as exc:
            print(f"  ERROR: {exc}", flush=True)
            ok = False
    if not ok:
        raise SystemExit(1)
    print("Done.", flush=True)
