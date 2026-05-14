#!/usr/bin/env python3
"""Pre-commit guard: verify strategy_lifecycle has LIVE/PAPER row for any enabled-true strategy.

Compares staged config/active/*.json against HEAD; for each strategy whose
enabled flag is being set true, queries data/atlas.db for the latest lifecycle
state. Exits non-zero with a clear explanation if any check fails.

Called by:
  - scripts/git-hooks/pre-commit-lifecycle-guard.sh  (raw bash hook path)
  - .pre-commit-config.yaml local hook               (pre-commit framework path)

Usage:
  python3 check_lifecycle_for_enabled.py <db_path> [<file> ...]
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path


def _get_head_version(file_path: str) -> dict | None:
    """Return parsed JSON of HEAD version, or None if file is new."""
    try:
        out = subprocess.check_output(
            ["git", "show", f"HEAD:{file_path}"],
            stderr=subprocess.DEVNULL,
        )
        return json.loads(out)
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        return None


def _get_staged_version(file_path: str) -> dict:
    """Return parsed JSON of staged version."""
    out = subprocess.check_output(
        ["git", "show", f":{file_path}"],
        stderr=subprocess.DEVNULL,
    )
    return json.loads(out)


def _strategies_being_enabled(
    head_cfg: dict | None, staged_cfg: dict
) -> list[tuple[str, str]]:
    """Return list of (strategy, universe) for strategies whose enabled flipped to true."""
    # Universe is the market name; config files use "market" key (e.g. "sp500")
    universe = staged_cfg.get("market", staged_cfg.get("universe", "unknown"))
    flipped: list[tuple[str, str]] = []
    head_strats = (head_cfg or {}).get("strategies", {})
    staged_strats = staged_cfg.get("strategies", {})
    for strat, sconf in staged_strats.items():
        if not isinstance(sconf, dict):
            continue
        if not sconf.get("enabled", False):
            continue  # disabled in staged — no check needed
        head_enabled = head_strats.get(strat, {}).get("enabled", False) if head_cfg else False
        if not head_enabled:
            # Was disabled (or new file), now enabled → must have lifecycle row
            flipped.append((strat, universe))
    return flipped


def _latest_lifecycle_state(db_path: str, strategy: str, universe: str) -> str | None:
    """Return latest lifecycle state for (strategy, universe), or None if no row."""
    db = Path(db_path)
    if not db.exists():
        return None
    try:
        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                # strategy_lifecycle PK is (strategy, universe) — one row per pair
                "SELECT state FROM strategy_lifecycle WHERE strategy = ? AND universe = ?",
                (strategy, universe),
            ).fetchone()
        return row[0] if row else None
    except sqlite3.OperationalError:
        # Table doesn't exist yet (fresh repo) — treat as missing
        return None


def main(argv: list[str]) -> int:
    """Check that any enabled-flipped strategies have a LIVE/PAPER lifecycle row.

    argv: [db_path, file1, file2, ...]
    Returns 0 (pass) or 1 (block).
    """
    if len(argv) < 2:
        return 0  # no files passed — nothing to check

    db_path, *files = argv
    violations: list[str] = []

    for file_path in files:
        try:
            head = _get_head_version(file_path)
            staged = _get_staged_version(file_path)
        except (subprocess.CalledProcessError, json.JSONDecodeError, Exception) as exc:
            # Can't read staged file — skip rather than block (fail-open for unreadable)
            print(f"⚠️  lifecycle-guard: skipping {file_path} (could not parse: {exc})",
                  file=sys.stderr)
            continue

        for strat, universe in _strategies_being_enabled(head, staged):
            state = _latest_lifecycle_state(db_path, strat, universe)
            if state not in ("LIVE", "PAPER"):
                violations.append(
                    f"  • {file_path}: strategy={strat}, universe={universe} — "
                    f"lifecycle state is {state or 'MISSING'} (must be LIVE or PAPER)"
                )

    if violations:
        print(
            "❌ Pre-commit guard: cannot enable strategies without a LIVE/PAPER lifecycle row:\n"
        )
        for v in violations:
            print(v)
        print(
            "\nFix: promote via scripts/promote_strategy_to_paper.py first, OR"
            "\n     insert a strategy_lifecycle row explicitly."
            "\nBypass (use with caution): git commit --no-verify"
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
