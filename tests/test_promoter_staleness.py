"""tests/test_promoter_staleness.py — Staleness guard tests for research/promoter.py

Tests covering:
1. Recent research_best row (5d old) → proceeds past staleness guard to gates
2. Stale research_best row (35d old) → returns status='blocked_stale', NOT promoted
3. NULL updated_at → permissive (proceeds past guard)

Run with: python3 -m pytest tests/test_promoter_staleness.py -v --timeout=30
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_research_best_db(tmp_path: Path, updated_at: str | None) -> Path:
    """Create a minimal SQLite DB with research_best table."""
    db_path = tmp_path / "test_atlas.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS research_best (
            strategy TEXT,
            universe TEXT,
            params TEXT,
            sharpe REAL,
            trades INTEGER,
            max_dd_pct REAL,
            updated_at TEXT,
            PRIMARY KEY (strategy, universe)
        )
    """)
    conn.execute(
        "INSERT INTO research_best (strategy, universe, sharpe, updated_at) VALUES (?,?,?,?)",
        ("mean_reversion", "sp500", 0.85, updated_at),
    )
    conn.commit()
    conn.close()
    return db_path


def _make_fake_get_db(db_path: Path):
    """Return a context-manager factory that uses the given SQLite path."""
    from contextlib import contextmanager

    @contextmanager
    def fake_get_db():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    return fake_get_db


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestPromoterStalenessGuard:
    """auto_promote() staleness guard: blocks promotions for old research_best rows."""

    def _call_auto_promote_with_db(
        self,
        tmp_path: Path,
        updated_at: str | None,
        *,
        expect_stale: bool,
    ) -> dict:
        """Run auto_promote() with a patched DB and verify staleness behavior.

        All downstream gates (cooldown, regression, OOS) are mocked to PASS
        so any blocking must come from the staleness guard.
        """
        db_path = _make_research_best_db(tmp_path, updated_at)
        fake_get_db = _make_fake_get_db(db_path)

        with (
            patch("db.atlas_db.get_db", fake_get_db),
            patch("research.promoter._check_cooldown", return_value=True),
            patch("research.promoter._regression_check", return_value={
                "pass": True,
                "baseline_metrics": {},
                "candidate_metrics": {"sharpe": 1.0, "cagr_pct": 15.0, "total_trades": 50},
                "comparisons": {},
            }),
            patch("research.promoter._sanity_check", return_value={"pass": True, "reason": "OK"}),
            patch("research.promoter._run_oos_validation", return_value={"pass": True, "reason": "OK"}),
            patch("research.promoter._get_cached_oos", return_value=None),
            patch("research.promoter._save_oos_cache"),
            patch("research.promoter._add_pending", return_value="test-pending-id"),
            patch("research.promoter._notify_approval_request"),
            patch("research.promoter._notify"),
            patch("utils.config.get_active_config", return_value={
                "market": "sp500",
                "version": "v1.0",
                "strategies": {},
            }),
        ):
            from research.promoter import auto_promote
            result = auto_promote(
                strategy="mean_reversion",
                improved_params={"window": 20},
                initial_sharpe=0.7,
                final_sharpe=0.9,
                improvements=["window tuned"],
                market="sp500",
            )

        if expect_stale:
            assert result.get("status") == "blocked_stale", (
                f"Expected status='blocked_stale' for stale row, got: {result}"
            )
            assert result.get("promoted") is False, (
                f"Expected promoted=False for stale row, got: {result}"
            )
            assert "stale" in result.get("reason", "").lower() or (
                "old" in result.get("reason", "").lower()
            ), f"Expected stale reason, got: {result.get('reason')}"
        else:
            assert result.get("status") != "blocked_stale", (
                f"Expected NOT blocked_stale for fresh row, got: {result}"
            )

        return result

    def test_recent_row_promotes(self, tmp_path: Path) -> None:
        """Row updated 5 days ago → proceeds past staleness guard to gates."""
        five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=5)
        ).isoformat()

        result = self._call_auto_promote_with_db(
            tmp_path, five_days_ago, expect_stale=False
        )
        # Should reach _add_pending (mock) since all gates pass
        # result may have "pending": True or "promoted": False/True depending on mocks
        assert result.get("status") != "blocked_stale", (
            f"5-day-old row should NOT be blocked as stale, got: {result}"
        )

    def test_stale_row_blocked(self, tmp_path: Path) -> None:
        """Row updated 35 days ago → returns status='blocked_stale', NOT promoted."""
        thirty_five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=35)
        ).isoformat()

        result = self._call_auto_promote_with_db(
            tmp_path, thirty_five_days_ago, expect_stale=True
        )

        assert result["status"] == "blocked_stale"
        assert result["promoted"] is False
        # Should include the strategy and market
        assert result.get("strategy") == "mean_reversion"
        assert result.get("market") == "sp500"

    def test_missing_updated_at_permissive(self, tmp_path: Path) -> None:
        """Row with NULL updated_at → proceeds (permissive on schema gaps)."""
        # updated_at = None → NULL in SQLite
        result = self._call_auto_promote_with_db(
            tmp_path, None, expect_stale=False
        )
        assert result.get("status") != "blocked_stale", (
            f"NULL updated_at should be permissive (not blocked), got: {result}"
        )

    def test_at_threshold_boundary_not_blocked(self, tmp_path: Path) -> None:
        """Row at 29d old → NOT blocked (well within 30d threshold)."""
        # Using 29 days (not exactly 30) to avoid timing sensitivity from test
        # execution time pushing a "30d exactly" row over the threshold.
        twenty_nine_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=29)
        ).isoformat()

        result = self._call_auto_promote_with_db(
            tmp_path, twenty_nine_days_ago, expect_stale=False
        )
        assert result.get("status") != "blocked_stale", (
            f"29d-old row should NOT be blocked (threshold is >30d), got: {result}"
        )

    def test_one_day_past_threshold_blocked(self, tmp_path: Path) -> None:
        """Row at 31d old → blocked (just past threshold)."""
        thirty_one_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=31)
        ).isoformat()

        result = self._call_auto_promote_with_db(
            tmp_path, thirty_one_days_ago, expect_stale=True
        )
        assert result["status"] == "blocked_stale"

    def test_db_failure_permissive(self, tmp_path: Path) -> None:
        """DB lookup failure → permissive (proceeds past guard)."""
        # Simulate DB failure by using a non-existent db
        with (
            patch("db.atlas_db.get_db", side_effect=Exception("DB connection failed")),
            patch("research.promoter._check_cooldown", return_value=True),
            patch("research.promoter._regression_check", return_value={
                "pass": True,
                "baseline_metrics": {},
                "candidate_metrics": {"sharpe": 1.0, "cagr_pct": 15.0, "total_trades": 50},
                "comparisons": {},
            }),
            patch("research.promoter._sanity_check", return_value={"pass": True, "reason": "OK"}),
            patch("research.promoter._run_oos_validation", return_value={"pass": True, "reason": "OK"}),
            patch("research.promoter._get_cached_oos", return_value=None),
            patch("research.promoter._save_oos_cache"),
            patch("research.promoter._add_pending", return_value="test-pending-id"),
            patch("research.promoter._notify_approval_request"),
            patch("research.promoter._notify"),
            patch("utils.config.get_active_config", return_value={
                "market": "sp500",
                "version": "v1.0",
                "strategies": {},
            }),
        ):
            from research.promoter import auto_promote
            result = auto_promote(
                strategy="mean_reversion",
                improved_params={"window": 20},
                initial_sharpe=0.7,
                final_sharpe=0.9,
                improvements=["window tuned"],
                market="sp500",
            )

        # Should not be blocked_stale (DB failure is permissive)
        assert result.get("status") != "blocked_stale", (
            f"DB failure should not block with 'blocked_stale', got: {result}"
        )

    def test_stale_guard_logs_warning(self, tmp_path: Path, caplog) -> None:
        """Stale guard emits a WARNING log when blocking."""
        import logging

        thirty_five_days_ago = (
            datetime.now(timezone.utc) - timedelta(days=35)
        ).isoformat()

        db_path = _make_research_best_db(tmp_path, thirty_five_days_ago)
        fake_get_db = _make_fake_get_db(db_path)

        with caplog.at_level(logging.WARNING):
            with (
                patch("db.atlas_db.get_db", fake_get_db),
                patch("research.promoter._check_cooldown", return_value=True),
                patch("research.promoter._regression_check", return_value={"pass": True,
                    "baseline_metrics": {}, "candidate_metrics": {}, "comparisons": {}}),
                patch("research.promoter._sanity_check", return_value={"pass": True, "reason": "OK"}),
                patch("research.promoter._notify"),
                patch("utils.config.get_active_config", return_value={
                    "market": "sp500", "version": "v1.0", "strategies": {},
                }),
            ):
                from research.promoter import auto_promote
                result = auto_promote(
                    strategy="mean_reversion",
                    improved_params={"window": 20},
                    initial_sharpe=0.7,
                    final_sharpe=0.9,
                    improvements=[],
                    market="sp500",
                )

        assert result["status"] == "blocked_stale"

        # Find warning log — message contains "old" and "threshold" (not "stale")
        stale_warnings = [
            r for r in caplog.records
            if r.levelno >= logging.WARNING and (
                "old" in r.message.lower()
                or "stale" in r.message.lower()
                or "threshold" in r.message.lower()
                or "re-sweep" in r.message.lower()
            )
        ]
        assert stale_warnings, (
            f"Expected staleness WARNING log (containing 'old'/'threshold'/'re-sweep'), "
            f"got records: {[(r.levelname, r.message) for r in caplog.records]}"
        )
