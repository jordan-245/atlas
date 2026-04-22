"""Tests for the paused-strategy filtering in silent_failure_watchdog.py."""
from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.silent_failure_watchdog as wdog
from scripts.silent_failure_watchdog import (
    PAUSED_AUTORESEARCH_STRATEGIES,
    check_autoresearch_logs,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_zero_log(directory: Path, name: str, age_secs: float = 0) -> Path:
    """Create a zero-byte file in *directory* with mtime in the last 24 h."""
    p = directory / name
    p.write_bytes(b"")
    # Set mtime to now minus age_secs (default = just now, well within 24 h)
    now = time.time()
    import os
    os.utime(p, (now - age_secs, now - age_secs))
    return p


# ---------------------------------------------------------------------------
# Test A — paused strategy zero-byte log is SKIPPED (no alert)
# ---------------------------------------------------------------------------

class TestPausedStrategySkipped:
    """Zero-byte logs for paused strategies must be silently ignored."""

    @pytest.mark.parametrize("strat", sorted(PAUSED_AUTORESEARCH_STRATEGIES))
    def test_single_paused_strategy_no_alert(self, tmp_path: Path, strat: str) -> None:
        """A single paused-strategy zero-byte log produces no alert."""
        _make_zero_log(tmp_path, f"autoresearch_{strat}_20260423.log")

        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)

        assert alerts_sent == [], (
            f"Expected no alert for paused strategy '{strat}', got: {alerts_sent}"
        )

    def test_all_three_paused_no_alert(self, tmp_path: Path) -> None:
        """All three paused strategies together produce no alert."""
        for strat in PAUSED_AUTORESEARCH_STRATEGIES:
            _make_zero_log(tmp_path, f"autoresearch_{strat}_20260423.log")

        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)

        assert alerts_sent == [], f"Expected no alert, got: {alerts_sent}"

    def test_paused_strategy_logged_as_info(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """When all zero-byte logs are paused, the INFO message mentions 'skipped'."""
        _make_zero_log(tmp_path, "autoresearch_mean_reversion_20260423.log")

        import logging
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", return_value=None),
            caplog.at_level(logging.INFO, logger="silent_failure_watchdog"),
        ):
            check_autoresearch_logs(dry_run=True)

        assert any("skipped" in r.message and "paused" in r.message for r in caplog.records), (
            f"Expected 'skipped N paused-strategy stub(s)' in logs; got: {[r.message for r in caplog.records]}"
        )


# ---------------------------------------------------------------------------
# Test B — non-paused strategy zero-byte log STILL triggers alert
# ---------------------------------------------------------------------------

class TestNonPausedStrategyAlerts:
    """Zero-byte logs for non-paused strategies must still fire an alert."""

    def test_sector_rotation_triggers_alert(self, tmp_path: Path) -> None:
        """sector_rotation is NOT paused — its zero-byte log must alert."""
        _make_zero_log(tmp_path, "autoresearch_sector_rotation_20260423.log")

        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)

        assert len(alerts_sent) == 1, f"Expected exactly 1 alert, got: {alerts_sent}"
        assert "sector_rotation" in alerts_sent[0]

    def test_trend_following_triggers_alert(self, tmp_path: Path) -> None:
        """trend_following is NOT paused — its zero-byte log must alert."""
        _make_zero_log(tmp_path, "autoresearch_trend_following_20260423.log")

        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)

        assert len(alerts_sent) == 1
        assert "trend_following" in alerts_sent[0]

    def test_mixed_paused_and_active_only_alerts_active(self, tmp_path: Path) -> None:
        """Paused logs are silently skipped; active logs still fire the alert."""
        _make_zero_log(tmp_path, "autoresearch_mean_reversion_20260423.log")   # paused
        _make_zero_log(tmp_path, "autoresearch_momentum_breakout_20260423.log") # paused
        _make_zero_log(tmp_path, "autoresearch_sector_rotation_20260423.log")   # NOT paused

        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)

        assert len(alerts_sent) == 1, f"Expected 1 alert (for sector_rotation), got: {alerts_sent}"
        assert "sector_rotation" in alerts_sent[0]
        assert "mean_reversion" not in alerts_sent[0]
        assert "momentum_breakout" not in alerts_sent[0]


# ---------------------------------------------------------------------------
# Test C — sanity: no zero-byte logs → no alert
# ---------------------------------------------------------------------------

class TestNoLogsNoAlert:
    def test_empty_logs_dir_ok(self, tmp_path: Path) -> None:
        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)
        assert alerts_sent == []

    def test_nonzero_log_not_alerted(self, tmp_path: Path) -> None:
        p = tmp_path / "autoresearch_sector_rotation_20260423.log"
        p.write_text("some content")
        alerts_sent: list[str] = []
        with (
            patch.object(wdog, "LOGS_DIR", tmp_path),
            patch.object(wdog, "_alert", side_effect=lambda msg, **kw: alerts_sent.append(msg)),
        ):
            check_autoresearch_logs(dry_run=True)
        assert alerts_sent == []
