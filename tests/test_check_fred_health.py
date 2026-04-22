"""Tests for scripts/check_fred_health.py.

Mocks FREDClient.fetch_series to cover:
    - fresh data → exit 0
    - empty series → exit 1 + Telegram
    - stale data → exit 1 + Telegram
    - exception during fetch → exit 1 + Telegram

All tests are standalone (no live API calls, no DB writes).
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pandas as pd
import pytest

# ── Path bootstrap ─────────────────────────────────────────────────────────────
_ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

from scripts.check_fred_health import _check_key, _check_series, main, run_checks


# ── Helpers ────────────────────────────────────────────────────────────────────

def _fresh_series(lag_days: int = 1) -> pd.Series:
    """Return a small Series whose latest index is ``lag_days`` ago."""
    today = datetime.now(tz=timezone.utc).date()
    latest = today - timedelta(days=lag_days)
    dates = pd.date_range(end=latest, periods=10, freq="D")
    return pd.Series([1.0] * 10, index=dates)


def _stale_series(lag_days: int = 10) -> pd.Series:
    """Return a Series whose latest index is ``lag_days`` ago (beyond daily threshold)."""
    today = datetime.now(tz=timezone.utc).date()
    latest = today - timedelta(days=lag_days)
    dates = pd.date_range(end=latest, periods=10, freq="D")
    return pd.Series([0.5] * 10, index=dates)


# ── _check_key ─────────────────────────────────────────────────────────────────

class TestCheckKey:
    def test_key_present(self, tmp_path):
        """_check_key returns True when FREDClient.api_key is non-empty."""
        mock_client = MagicMock()
        mock_client.api_key = "fake-key-abc123"
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            assert _check_key(MagicMock()) is True

    def test_key_absent(self, tmp_path):
        """_check_key returns False when FREDClient.api_key is None/empty."""
        mock_client = MagicMock()
        mock_client.api_key = None
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            assert _check_key(MagicMock()) is False

    def test_import_error(self):
        """_check_key returns False if FREDClient cannot be imported."""
        with patch("scripts.check_fred_health.FREDClient", side_effect=ImportError("no module")):
            assert _check_key(MagicMock()) is False


# ── _check_series ──────────────────────────────────────────────────────────────

class TestCheckSeries:
    def _make_client(self, return_value):
        mock_client = MagicMock()
        mock_client.get_yield_curve_slope.return_value = return_value
        mock_client.api_key = "key"
        return mock_client

    def test_fresh_data_passes(self):
        """Fresh non-empty series → ok=True."""
        mock_client = self._make_client(_fresh_series(lag_days=1))
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            result = _check_series(
                "get_yield_curve_slope", "Yield Curve", max_lag_days=5,
                logger=MagicMock(),
            )
        assert result["ok"] is True
        assert result["n_obs"] == 10
        assert result["latest_date"] is not None

    def test_empty_series_fails(self):
        """Empty series → ok=False with reason."""
        mock_client = self._make_client(pd.Series(dtype=float))
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            result = _check_series(
                "get_yield_curve_slope", "Yield Curve", max_lag_days=5,
                logger=MagicMock(),
            )
        assert result["ok"] is False
        assert "empty" in result["reason"]

    def test_stale_data_fails(self):
        """Series with latest point > max_lag_days old → ok=False."""
        mock_client = self._make_client(_stale_series(lag_days=10))
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            result = _check_series(
                "get_yield_curve_slope", "Yield Curve", max_lag_days=5,
                logger=MagicMock(),
            )
        assert result["ok"] is False
        assert "stale" in result["reason"]

    def test_all_nan_fails(self):
        """Series where all values are NaN → ok=False."""
        import numpy as np
        dates = pd.date_range(end=date.today(), periods=5)
        s = pd.Series([np.nan] * 5, index=dates)
        mock_client = self._make_client(s)
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            result = _check_series(
                "get_yield_curve_slope", "Yield Curve", max_lag_days=5,
                logger=MagicMock(),
            )
        assert result["ok"] is False

    def test_exception_fails(self):
        """Exception in fetch → ok=False with exception detail."""
        mock_client = MagicMock()
        mock_client.get_yield_curve_slope.side_effect = RuntimeError("network error")
        mock_client.api_key = "key"
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            result = _check_series(
                "get_yield_curve_slope", "Yield Curve", max_lag_days=5,
                logger=MagicMock(),
            )
        assert result["ok"] is False
        assert "exception" in result["reason"]


# ── run_checks (integration of key + series) ──────────────────────────────────

class TestRunChecks:
    def _patch_all_fresh(self):
        """Return a mock FREDClient whose methods all return fresh series."""
        mock_client = MagicMock()
        mock_client.api_key = "test-key"
        mock_client.get_yield_curve_slope.return_value = _fresh_series(1)
        mock_client.get_credit_oas.return_value = _fresh_series(1)
        mock_client.get_fed_funds_rate.return_value = _fresh_series(55)  # monthly, 60d threshold
        return mock_client

    def test_all_healthy_exit_0(self, tmp_path):
        """All series fresh → all_ok=True."""
        mock_client = self._patch_all_fresh()
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            all_ok, results = run_checks(tmp_path)
        assert all_ok is True
        assert all(r["ok"] for r in results)

    def test_one_series_stale_exit_1(self, tmp_path):
        """One stale series → all_ok=False."""
        mock_client = self._patch_all_fresh()
        mock_client.get_credit_oas.return_value = _stale_series(lag_days=10)
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            all_ok, results = run_checks(tmp_path)
        assert all_ok is False
        failed = [r for r in results if not r["ok"]]
        assert len(failed) == 1
        assert "Credit" in failed[0]["name"]

    def test_missing_key_exit_1(self, tmp_path):
        """Missing API key → all_ok=False, no series checks run."""
        mock_client = MagicMock()
        mock_client.api_key = None
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            all_ok, results = run_checks(tmp_path)
        assert all_ok is False
        # Only the key result present (series checks skipped)
        assert results[0]["name"] == "API Key"
        assert len(results) == 1


# ── main() with Telegram alert ─────────────────────────────────────────────────

class TestMainAlerts:
    def _patch_all_fresh(self):
        mock_client = MagicMock()
        mock_client.api_key = "test-key"
        mock_client.get_yield_curve_slope.return_value = _fresh_series(1)
        mock_client.get_credit_oas.return_value = _fresh_series(1)
        mock_client.get_fed_funds_rate.return_value = _fresh_series(30)
        return mock_client

    def test_healthy_no_telegram(self, tmp_path):
        """All healthy → exit 0 and no Telegram call."""
        mock_client = self._patch_all_fresh()
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client), \
             patch("scripts.check_fred_health._send_telegram") as mock_tg:
            rc = main(["--log-dir", str(tmp_path)])
        assert rc == 0
        mock_tg.assert_not_called()

    def test_stale_sends_telegram_exits_1(self, tmp_path):
        """Stale series → exit 1 and Telegram called with series name."""
        mock_client = self._patch_all_fresh()
        mock_client.get_yield_curve_slope.return_value = _stale_series(10)
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client), \
             patch("scripts.check_fred_health._send_telegram") as mock_tg:
            rc = main(["--log-dir", str(tmp_path)])
        assert rc == 1
        assert mock_tg.called
        alert_text = mock_tg.call_args[0][0]
        assert "⚠️" in alert_text
        assert "Yield Curve" in alert_text

    def test_exception_sends_telegram_exits_1(self, tmp_path):
        """Exception during fetch → exit 1 and Telegram called."""
        mock_client = self._patch_all_fresh()
        mock_client.get_credit_oas.side_effect = ConnectionError("timeout")
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client), \
             patch("scripts.check_fred_health._send_telegram") as mock_tg:
            rc = main(["--log-dir", str(tmp_path)])
        assert rc == 1
        assert mock_tg.called

    def test_json_output(self, tmp_path, capsys):
        """--json flag produces valid JSON on stdout."""
        import json
        mock_client = self._patch_all_fresh()
        with patch("scripts.check_fred_health.FREDClient", return_value=mock_client):
            rc = main(["--log-dir", str(tmp_path), "--json"])
        assert rc == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert "ok" in payload
        assert "results" in payload
        assert payload["ok"] is True
