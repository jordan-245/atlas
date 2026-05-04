"""Tests for healthcheck_pipelines.py --quiet heartbeat summary (Fix 4).

Verifies that --quiet mode still emits a WARNING-level summary line,
so the cron log file always has a heartbeat even on healthy days.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).resolve().parent.parent
_SCRIPT = PROJECT / "scripts" / "healthcheck_pipelines.py"


class TestQuietHeartbeat:
    def test_quiet_mode_non_empty_output(self, tmp_path):
        """--quiet --no-alert must produce at least one line of output."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--once", "--quiet", "--no-alert"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT),
        )
        output = result.stdout + result.stderr
        assert output.strip(), (
            f"Expected non-empty output with --quiet, got nothing. exit={result.returncode}"
        )

    def test_quiet_mode_has_summary_line(self, tmp_path):
        """--quiet --no-alert must print a 'healthcheck_pipelines complete:' summary."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--once", "--quiet", "--no-alert"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT),
        )
        output = result.stdout + result.stderr
        assert re.search(r"healthcheck_pipelines complete:", output), (
            f"Expected summary line, got:\n{output}"
        )

    def test_quiet_mode_suppresses_info(self):
        """--quiet must suppress INFO-level lines (only WARNING and above shown)."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--once", "--quiet", "--no-alert"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT),
        )
        output = result.stdout + result.stderr
        # No INFO lines should appear in quiet mode
        info_lines = [l for l in output.splitlines() if " INFO " in l]
        assert not info_lines, (
            f"--quiet should suppress INFO lines, but found: {info_lines}"
        )

    def test_non_quiet_mode_shows_info(self):
        """Without --quiet, INFO-level lines must be visible."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--once", "--no-alert"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT),
        )
        output = result.stdout + result.stderr
        info_or_warning = [l for l in output.splitlines() if " INFO " in l or " WARNING " in l]
        assert info_or_warning, (
            f"Expected INFO or WARNING lines without --quiet, got:\n{output}"
        )

    def test_summary_shows_healthy_count(self):
        """Summary line must contain N/M healthy count format."""
        result = subprocess.run(
            [sys.executable, str(_SCRIPT), "--once", "--quiet", "--no-alert"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(PROJECT),
        )
        output = result.stdout + result.stderr
        # e.g. "healthcheck_pipelines complete: 4/7 healthy, 3 stale"
        assert re.search(r"healthcheck_pipelines complete: \d+/\d+ healthy, \d+ stale", output), (
            f"Summary line format wrong, got:\n{output}"
        )
