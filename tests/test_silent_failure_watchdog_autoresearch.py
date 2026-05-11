"""Regression: silent-failure-watchdog zero-byte autoresearch race-condition guard.

2026-05-11 false alert: autoresearch_connors_rsi2_20260511.log was flagged as
zero-byte by the hourly watchdog because both the autoresearch runner and
the watchdog timer fired at 13:00:05 UTC simultaneously. The log file existed
but hadn't flushed any content yet.

This test suite verifies the min-age guard skips too-fresh logs.
"""
from __future__ import annotations
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class TestAutoresearchMinAgeGuard:

    def test_zero_byte_log_younger_than_min_age_skipped(self, tmp_path, monkeypatch, caplog):
        """A 0-byte autoresearch log that's <15 min old must NOT trigger an alert."""
        import scripts.silent_failure_watchdog as mod
        # Point LOGS_DIR at our tmp path
        monkeypatch.setattr(mod, "LOGS_DIR", tmp_path)

        # Create a zero-byte autoresearch log dated TODAY so _is_rotation_stub skip
        # (signal 2 = past-dated file) does NOT fire.
        from datetime import date
        today = date.today().strftime("%Y%m%d")
        log_file = tmp_path / f"autoresearch_connors_rsi2_{today}.log"
        log_file.touch()

        # mtime is "now" by default — younger than 15 min
        assert log_file.stat().st_size == 0

        with patch.object(mod, "_alert") as mock_alert:
            mod.check_autoresearch_logs(dry_run=True)

        # _alert must NOT have been called — the file is too fresh
        mock_alert.assert_not_called()

    def test_zero_byte_log_older_than_min_age_alerts(self, tmp_path, monkeypatch):
        """A 0-byte autoresearch log >15 min old (but <24h) SHOULD trigger an alert."""
        import scripts.silent_failure_watchdog as mod
        monkeypatch.setattr(mod, "LOGS_DIR", tmp_path)

        from datetime import date
        today = date.today().strftime("%Y%m%d")
        log_file = tmp_path / f"autoresearch_short_term_mr_{today}.log"
        log_file.touch()

        # Set mtime to 30 min ago (older than 15-min guard, younger than 24h cutoff)
        thirty_min_ago = time.time() - 30 * 60
        os.utime(log_file, (thirty_min_ago, thirty_min_ago))

        with patch.object(mod, "_alert") as mock_alert:
            mod.check_autoresearch_logs(dry_run=True)

        # _alert MUST have been called — file is old enough to be a real failure
        mock_alert.assert_called_once()
        # Alert text must mention the file
        args, kwargs = mock_alert.call_args
        alert_text = args[0] if args else kwargs.get("text", "")
        assert log_file.name in alert_text

    def test_zero_byte_paused_strategy_log_skipped_even_when_old(self, tmp_path, monkeypatch):
        """Paused-strategy zero-byte logs continue to be skipped (existing behavior)."""
        import scripts.silent_failure_watchdog as mod
        monkeypatch.setattr(mod, "LOGS_DIR", tmp_path)

        from datetime import date
        today = date.today().strftime("%Y%m%d")
        # mean_reversion IS in PAUSED_AUTORESEARCH_STRATEGIES
        log_file = tmp_path / f"autoresearch_mean_reversion_{today}.log"
        log_file.touch()
        thirty_min_ago = time.time() - 30 * 60
        os.utime(log_file, (thirty_min_ago, thirty_min_ago))

        with patch.object(mod, "_alert") as mock_alert:
            mod.check_autoresearch_logs(dry_run=True)

        # paused strategy — no alert
        mock_alert.assert_not_called()

    def test_non_zero_log_never_alerts(self, tmp_path, monkeypatch):
        """A log with content is fine regardless of age."""
        import scripts.silent_failure_watchdog as mod
        monkeypatch.setattr(mod, "LOGS_DIR", tmp_path)

        from datetime import date
        today = date.today().strftime("%Y%m%d")
        log_file = tmp_path / f"autoresearch_connors_rsi2_{today}.log"
        log_file.write_text("=== Atlas AutoResearch ===\nrunning...\n")
        thirty_min_ago = time.time() - 30 * 60
        os.utime(log_file, (thirty_min_ago, thirty_min_ago))

        with patch.object(mod, "_alert") as mock_alert:
            mod.check_autoresearch_logs(dry_run=True)

        mock_alert.assert_not_called()
