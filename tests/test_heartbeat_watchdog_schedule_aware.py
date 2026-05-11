"""Regression: heartbeat watchdog config is complete (schedule-aware coverage)."""
from __future__ import annotations
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pytest


class TestHeartbeatConfigCompleteness:

    def test_director_cron_configured_in_heartbeat_json(self):
        """Regression: director_cron must be in heartbeat.json so watchdog uses
        schedule-aware staleness check (weekly cadence), not the 6h fallback.

        Original bug: 2026-05-11 alert fired at 38.2h staleness because the
        global fallback (6h) doesn't know director is weekly.
        """
        import json
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parent.parent / "config" / "heartbeat.json"
        cfg = json.loads(cfg_path.read_text())
        services = cfg.get("services", {})
        assert "director_cron" in services, (
            "director_cron missing from heartbeat.json — watchdog will use "
            "6h default threshold and false-alert at ~6h after each weekly run"
        )
        svc = services["director_cron"]
        assert "expected_cron" in svc
        assert "threshold_hours" in svc
        # Director is WEEKLY — threshold must accommodate at least 7d cadence
        assert svc["threshold_hours"] >= 24, (
            f"director_cron threshold_hours={svc['threshold_hours']} too tight; "
            "must allow at least 24h grace beyond the weekly run"
        )
        # Verify the cron expression is parseable
        try:
            from croniter import croniter
            from datetime import datetime, timezone
            citer = croniter(svc["expected_cron"], datetime.now(timezone.utc))
            citer.get_prev(datetime)  # parse smoke test
        except Exception as exc:
            pytest.fail(f"director_cron expected_cron is invalid: {exc}")
