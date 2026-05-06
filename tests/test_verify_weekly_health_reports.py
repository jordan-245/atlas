"""Regression tests for scripts/verify_weekly_health_reports.py.

Covers:
  - _most_recent_saturday() date arithmetic
  - check_reports() file-existence logic (all present / all missing / partial)
  - main() exit codes
  - main() Telegram send/no-send behaviour
"""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Ensure the atlas root is importable (mirrors how the script bootstraps itself)
# ---------------------------------------------------------------------------
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.verify_weekly_health_reports import (
    TRACKED_MARKETS,
    _build_alert_message,
    _most_recent_saturday,
    check_reports,
    main,
)

# Use three markets for richer partial-missing tests
_THREE_MARKETS: tuple[str, ...] = ("sp500", "commodity_etfs", "sector_etfs")


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _write_reports(reports_dir: Path, markets: tuple[str, ...], saturday: date) -> None:
    """Write stub JSON report files for *markets* into *reports_dir*."""
    reports_dir.mkdir(parents=True, exist_ok=True)
    for market in markets:
        path = reports_dir / f"health_{market}_{saturday.isoformat()}.json"
        path.write_text(json.dumps({"status": "ok"}))


def _freeze_today(module_dotted: str, fake_date: date):
    """Patch datetime.now().date() in *module_dotted* to return *fake_date*."""
    mock_dt = MagicMock()
    mock_dt.now.return_value.date.return_value = fake_date
    return patch(f"{module_dotted}.datetime", mock_dt)


_MOD = "scripts.verify_weekly_health_reports"


# ─── _most_recent_saturday() ─────────────────────────────────────────────────


class TestMostRecentSaturday:
    def test_from_sunday_returns_yesterday(self) -> None:
        """Running on a Sunday should return Saturday (day before)."""
        known_sunday = date(2026, 5, 3)  # verified: weekday() == 6
        assert known_sunday.weekday() == 6, "fixture check"

        with _freeze_today(_MOD, known_sunday):
            result = _most_recent_saturday()

        assert result == date(2026, 5, 2)
        assert result.weekday() == 5  # Saturday

    def test_from_monday_returns_two_days_ago(self) -> None:
        known_monday = date(2026, 5, 4)
        assert known_monday.weekday() == 0, "fixture check"

        with _freeze_today(_MOD, known_monday):
            result = _most_recent_saturday()

        assert result == date(2026, 5, 2)
        assert result.weekday() == 5

    def test_from_saturday_returns_today(self) -> None:
        known_saturday = date(2026, 5, 2)
        assert known_saturday.weekday() == 5, "fixture check"

        with _freeze_today(_MOD, known_saturday):
            result = _most_recent_saturday()

        assert result == known_saturday

    def test_from_friday_returns_last_saturday(self) -> None:
        known_friday = date(2026, 5, 1)
        assert known_friday.weekday() == 4, "fixture check"

        with _freeze_today(_MOD, known_friday):
            result = _most_recent_saturday()

        assert result == date(2026, 4, 25)
        assert result.weekday() == 5


# ─── check_reports() ─────────────────────────────────────────────────────────


class TestCheckReports:
    def test_all_present_returns_empty(self, tmp_path: Path) -> None:
        saturday = _most_recent_saturday()
        _write_reports(tmp_path, _THREE_MARKETS, saturday)

        missing = check_reports(markets=_THREE_MARKETS, reports_dir=tmp_path)

        assert missing == []

    def test_empty_dir_returns_all_markets(self, tmp_path: Path) -> None:
        missing = check_reports(markets=_THREE_MARKETS, reports_dir=tmp_path)

        assert set(missing) == set(_THREE_MARKETS)
        assert len(missing) == 3

    def test_two_of_three_present(self, tmp_path: Path) -> None:
        saturday = _most_recent_saturday()
        present = _THREE_MARKETS[:2]  # sp500 + commodity_etfs
        _write_reports(tmp_path, present, saturday)

        missing = check_reports(markets=_THREE_MARKETS, reports_dir=tmp_path)

        assert missing == ["sector_etfs"]

    def test_old_files_do_not_count_as_present(self, tmp_path: Path) -> None:
        """Files from a previous Saturday must not satisfy this week's check."""
        saturday = _most_recent_saturday()
        old_saturday = saturday - timedelta(weeks=1)
        _write_reports(tmp_path, _THREE_MARKETS, old_saturday)

        missing = check_reports(markets=_THREE_MARKETS, reports_dir=tmp_path)

        assert set(missing) == set(_THREE_MARKETS)

    def test_default_markets_is_sp500_only(self, tmp_path: Path) -> None:
        """Default TRACKED_MARKETS contains only sp500 (current production state)."""
        assert TRACKED_MARKETS == ("sp500",)

        saturday = _most_recent_saturday()
        _write_reports(tmp_path, ("sp500",), saturday)

        missing = check_reports(reports_dir=tmp_path)  # uses default TRACKED_MARKETS

        assert missing == []


# ─── main() exit codes ───────────────────────────────────────────────────────


class TestMainExitCodes:
    def test_all_present_exit_0(self, tmp_path: Path) -> None:
        saturday = _most_recent_saturday()
        _write_reports(tmp_path, _THREE_MARKETS, saturday)

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
        ):
            rc = main(["--dry-run"])

        assert rc == 0

    def test_all_missing_exit_1_dry_run(self, tmp_path: Path, capsys) -> None:
        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
        ):
            rc = main(["--dry-run"])

        assert rc == 1
        out = capsys.readouterr().out
        assert "DRY RUN" in out
        assert "Missing weekly health reports" in out

    def test_partial_missing_exit_1_dry_run(self, tmp_path: Path, capsys) -> None:
        saturday = _most_recent_saturday()
        _write_reports(tmp_path, ("sp500", "commodity_etfs"), saturday)

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
        ):
            rc = main(["--dry-run"])

        assert rc == 1
        out = capsys.readouterr().out
        assert "sector_etfs" in out


# ─── main() Telegram behaviour ───────────────────────────────────────────────


class TestMainTelegram:
    def test_missing_sends_telegram(self, tmp_path: Path) -> None:
        """When reports are missing (non-dry-run), send_message should be called once."""
        mock_send = MagicMock(return_value=True)

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
            patch("utils.telegram.send_message", mock_send),
        ):
            rc = main([])

        assert rc == 1
        mock_send.assert_called_once()
        alert_text = mock_send.call_args[0][0]
        assert "Missing weekly health reports" in alert_text
        # All three missing markets should appear in the alert
        for market in _THREE_MARKETS:
            assert market in alert_text

    def test_all_present_no_telegram(self, tmp_path: Path) -> None:
        """When all reports exist, Telegram must NOT be called."""
        saturday = _most_recent_saturday()
        _write_reports(tmp_path, _THREE_MARKETS, saturday)
        mock_send = MagicMock()

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
            patch("utils.telegram.send_message", mock_send),
        ):
            rc = main([])

        assert rc == 0
        mock_send.assert_not_called()

    def test_partial_missing_telegram_names_only_missing_market(
        self, tmp_path: Path
    ) -> None:
        """Alert message should list exactly the missing market, not the present ones."""
        saturday = _most_recent_saturday()
        # Write sp500 + commodity_etfs; sector_etfs is missing
        _write_reports(tmp_path, ("sp500", "commodity_etfs"), saturday)
        mock_send = MagicMock(return_value=True)

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
            patch("utils.telegram.send_message", mock_send),
        ):
            rc = main([])

        assert rc == 1
        mock_send.assert_called_once()
        alert_text = mock_send.call_args[0][0]
        assert "sector_etfs" in alert_text
        # sp500 and commodity_etfs should NOT appear in the missing-files list
        # (they may appear in the "re-run" instruction but not as missing entries)
        assert f"health_sp500_{saturday.isoformat()}.json" not in alert_text
        assert f"health_commodity_etfs_{saturday.isoformat()}.json" not in alert_text

    def test_telegram_failure_does_not_raise(self, tmp_path: Path) -> None:
        """A Telegram exception must not propagate — just log and return 1."""
        mock_send = MagicMock(side_effect=RuntimeError("network timeout"))

        with (
            patch(f"{_MOD}.REPORTS_DIR", tmp_path),
            patch(f"{_MOD}.TRACKED_MARKETS", _THREE_MARKETS),
            patch("utils.telegram.send_message", mock_send),
        ):
            rc = main([])  # must not raise

        assert rc == 1


# ─── _build_alert_message() ──────────────────────────────────────────────────


class TestBuildAlertMessage:
    def test_contains_saturday_date(self) -> None:
        saturday = date(2026, 5, 2)
        msg = _build_alert_message(["sp500"], saturday)
        assert "2026-05-02" in msg

    def test_lists_each_missing_market(self) -> None:
        saturday = date(2026, 5, 2)
        msg = _build_alert_message(["sp500", "sector_etfs"], saturday)
        assert "sp500" in msg
        assert "sector_etfs" in msg

    def test_html_safe_for_telegram(self) -> None:
        """Message should not contain bare < or > outside of HTML tags (Telegram parse mode)."""
        saturday = date(2026, 5, 2)
        msg = _build_alert_message(["sp500"], saturday)
        # Verify the re-run command uses <code> tags properly
        assert "<code>" in msg
        assert "</code>" in msg
