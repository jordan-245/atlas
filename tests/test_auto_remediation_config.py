"""tests/test_auto_remediation_config.py — Config consistency checks for
auto-remediation safety rules.

Task #291 (2026-04-30):
  monitor/lifecycle.py  → NEVER (auto_fix_deny.yaml + auto_remediation.never_fix)
  monitor/evaluator.py  → permanent_assist (auto_remediation.permanent_assist)
  No file may appear in BOTH the NEVER deny list AND permanent_assist.
"""
from __future__ import annotations

from pathlib import Path
import yaml
import pytest

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
DENY_PATH = CONFIG_DIR / "auto_fix_deny.yaml"
REMEDIATION_PATH = CONFIG_DIR / "auto_remediation.yaml"


def _load_deny() -> dict:
    with open(DENY_PATH) as f:
        return yaml.safe_load(f) or {}


def _load_remediation() -> dict:
    with open(REMEDIATION_PATH) as f:
        return yaml.safe_load(f) or {}


def _extract_deny_paths(deny: dict) -> set[str]:
    """Extract all path-like strings from auto_fix_deny.yaml.

    Walks file_globs (top-level list) as well as any nested 'paths' or
    'files' keys, returning only string values (skips comments and scalars
    that don't look like file paths).
    """
    paths: set[str] = set()

    # Primary source: file_globs flat list
    for item in deny.get("file_globs", []):
        if isinstance(item, str):
            paths.add(item)

    # Secondary: walk any nested dict for 'paths' / 'files' keys
    def _walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("paths", "files"):
                    if isinstance(v, list):
                        for entry in v:
                            if isinstance(entry, str):
                                paths.add(entry)
                else:
                    _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(deny)
    return paths


def _extract_assist_paths(rem: dict) -> set[str]:
    """Extract explicit path strings from auto_remediation.yaml permanent_assist.paths."""
    return set(rem.get("permanent_assist", {}).get("paths", []))


# ---------------------------------------------------------------------------

class TestConfigConsistency:

    def test_no_monitor_file_in_both_deny_and_assist(self) -> None:
        """monitor/ files cannot appear in BOTH the NEVER deny list and permanent_assist.

        Task #291 scope: the monitor/ inconsistency between lifecycle.py (NEVER)
        and evaluator.py (permanent_assist only).

        Pre-existing overlaps for **/*.cron, **/*.sql, db/migrations/** are
        intentional belt-and-suspenders (deny = never classify; permanent_assist =
        never auto-merge if somehow reached). Those are out of scope here.
        """
        deny = _load_deny()
        rem = _load_remediation()

        deny_paths = _extract_deny_paths(deny)
        assist_paths = _extract_assist_paths(rem)

        # Scope to monitor/ paths only (task #291)
        monitor_deny = {p for p in deny_paths if p.startswith("monitor/")}
        monitor_assist = {p for p in assist_paths if p.startswith("monitor/")}

        overlap = monitor_deny & monitor_assist
        assert not overlap, (
            f"monitor/ files in BOTH deny list and permanent_assist: {sorted(overlap)}\n"
            f"  monitor deny entries: {sorted(monitor_deny)}\n"
            f"  monitor assist entries: {sorted(monitor_assist)}"
        )

    def test_lifecycle_is_in_deny_list(self) -> None:
        """monitor/lifecycle.py must be explicitly in auto_fix_deny.yaml file_globs."""
        deny = _load_deny()
        deny_paths = _extract_deny_paths(deny)
        assert "monitor/lifecycle.py" in deny_paths, (
            "monitor/lifecycle.py missing from auto_fix_deny.yaml file_globs — "
            "it is trading-path-adjacent and must be NEVER"
        )

    def test_evaluator_not_in_deny_list(self) -> None:
        """monitor/evaluator.py must NOT be in auto_fix_deny.yaml file_globs.

        It is observability (permanent_assist), not trading-path-adjacent (NEVER).
        """
        deny = _load_deny()
        deny_paths = _extract_deny_paths(deny)
        assert "monitor/evaluator.py" not in deny_paths, (
            "monitor/evaluator.py should NOT be in auto_fix_deny.yaml — "
            "it is observability (permanent_assist), not NEVER"
        )

    def test_lifecycle_is_in_never_fix(self) -> None:
        """monitor/lifecycle.py must be in auto_remediation.yaml never_fix.paths."""
        rem = _load_remediation()
        never_paths = set(rem.get("never_fix", {}).get("paths", []))
        assert "monitor/lifecycle.py" in never_paths, (
            "monitor/lifecycle.py missing from auto_remediation.yaml never_fix.paths — "
            "it is trading-path-adjacent and must be NEVER"
        )

    def test_lifecycle_not_in_permanent_assist_explicitly(self) -> None:
        """monitor/lifecycle.py must NOT appear as an EXPLICIT entry in permanent_assist.paths.

        It may be semantically covered by monitor/** (the glob), but the never_fix
        rule takes precedence in the enforcement layer. This test guards against
        accidental explicit listing.
        """
        rem = _load_remediation()
        assist_paths = _extract_assist_paths(rem)
        assert "monitor/lifecycle.py" not in assist_paths, (
            "monitor/lifecycle.py must not be explicitly listed in permanent_assist.paths"
        )

    def test_yaml_files_are_valid(self) -> None:
        """Both YAML config files must be parseable (no syntax errors)."""
        deny = _load_deny()
        rem = _load_remediation()
        assert isinstance(deny, dict)
        assert isinstance(rem, dict)
        assert "file_globs" in deny, "auto_fix_deny.yaml missing 'file_globs' key"
        assert "permanent_assist" in rem, "auto_remediation.yaml missing 'permanent_assist' key"
        assert "never_fix" in rem, "auto_remediation.yaml missing 'never_fix' key"
