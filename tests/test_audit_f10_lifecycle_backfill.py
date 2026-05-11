"""Tests for audit finding F-10 — strategy_lifecycle backfill + JOIN.

Verifies that:
1. After running backfill_strategy_lifecycle.py, no (strategy, universe) pair
   from configs has a NULL or missing lifecycle state.
2. /api/admin/strategies returns no rows with lifecycle == "UNKNOWN" for pairs
   that have lifecycle rows in the DB.

Audit ref: F-10 (lifecycle hardcoded UNKNOWN in /api/admin/strategies)
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ATLAS_ROOT = Path(__file__).resolve().parents[1]


# ── Fixture: isolated DB with strategy_lifecycle table ────────────────────

@pytest.fixture
def _lifecycle_db(tmp_path):
    """Test DB with strategy_lifecycle populated by our backfill logic."""
    db_path = tmp_path / "test_f10.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    conn.execute("""
        CREATE TABLE strategy_lifecycle (
            strategy TEXT NOT NULL,
            universe TEXT NOT NULL,
            state TEXT NOT NULL,
            entered_state_at TEXT,
            transition_reason TEXT,
            PRIMARY KEY (strategy, universe)
        )
    """)
    conn.execute("""
        CREATE TABLE market_equity_history (
            date TEXT NOT NULL,
            market_id TEXT NOT NULL,
            broker_equity REAL DEFAULT 0,
            allocated_equity REAL DEFAULT 0,
            position_mv REAL DEFAULT 0,
            cash_attributed REAL DEFAULT 0,
            broker_cash REAL DEFAULT 0,
            snapshot_time TEXT,
            PRIMARY KEY (date, market_id)
        )
    """)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            ticker TEXT,
            strategy TEXT,
            universe TEXT,
            entry_date TEXT,
            exit_date TEXT,
            status TEXT,
            pnl REAL
        )
    """)
    conn.execute("""
        CREATE TABLE config_overrides (
            id INTEGER PRIMARY KEY,
            scope TEXT,
            key TEXT,
            state TEXT,
            reason TEXT,
            created_by TEXT,
            created_at TEXT,
            expires_at TEXT,
            prev_state TEXT,
            active INTEGER DEFAULT 1
        )
    """)
    conn.commit()
    yield db_path, conn
    conn.close()


# ── Test: backfill_strategy_lifecycle.py logic ────────────────────────────

class TestBackfillLogic:
    def test_backfill_inserts_research_state(self, _lifecycle_db):
        """backfill() inserts RESEARCH-state rows for all missing (strategy, universe) pairs."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)

            # Insert one pre-existing row (should not be duplicated)
            conn.execute(
                "INSERT INTO strategy_lifecycle VALUES ('momentum_breakout', 'sp500', 'LIVE', datetime('now'), 'existing')"
            )
            conn.commit()

            from scripts.backfill_strategy_lifecycle import backfill, _collect_config_pairs

            config_pairs = _collect_config_pairs()
            assert len(config_pairs) >= 40, f"Expected ≥40 config pairs, got {len(config_pairs)}"

            result = backfill(apply=True)
            assert result["backfilled"] >= 1, "Should have backfilled at least 1 row"
            assert result["missing"] == result["backfilled"], "All missing rows should have been inserted"

        finally:
            _adb._db_path_override = orig

    def test_backfill_idempotent(self, _lifecycle_db):
        """Running backfill twice produces the same result (no duplicates)."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)
            from scripts.backfill_strategy_lifecycle import backfill

            r1 = backfill(apply=True)
            r2 = backfill(apply=True)
            # Second run should find 0 missing
            assert r2["backfilled"] == 0, f"Second backfill should be no-op, inserted {r2['backfilled']}"
            assert r2["missing"] == 0
        finally:
            _adb._db_path_override = orig

    def test_no_null_state_after_backfill(self, _lifecycle_db):
        """After backfill, no row in strategy_lifecycle has NULL state."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)
            from scripts.backfill_strategy_lifecycle import backfill
            backfill(apply=True)

            null_count = conn.execute(
                "SELECT COUNT(*) FROM strategy_lifecycle WHERE state IS NULL"
            ).fetchone()[0]
            assert null_count == 0, f"{null_count} rows have NULL state after backfill"
        finally:
            _adb._db_path_override = orig

    def test_backfill_covers_all_config_pairs(self, _lifecycle_db):
        """After backfill, every (strategy, universe) in config has a lifecycle row."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)
            from scripts.backfill_strategy_lifecycle import backfill, _collect_config_pairs
            backfill(apply=True)

            config_pairs = _collect_config_pairs()
            existing_rows = conn.execute(
                "SELECT strategy, universe FROM strategy_lifecycle"
            ).fetchall()
            existing = {(r[0], r[1]) for r in existing_rows}
            missing_from_db = config_pairs - existing
            assert not missing_from_db, (
                f"After backfill, {len(missing_from_db)} config pairs still lack lifecycle rows: "
                f"{sorted(missing_from_db)[:5]}"
            )
        finally:
            _adb._db_path_override = orig


# ── Test: admin_get_strategies returns real lifecycle states ───────────────

class TestAdminStrategiesLifecycle:
    def _make_mock_cfg(self, strats: dict | None = None) -> dict:
        strats = strats or {
            "momentum_breakout": {"enabled": True, "weight": 0.5},
            "mean_reversion": {"enabled": True, "weight": 0.3},
        }
        return {
            "market": "sp500",
            "trading": {"mode": "live", "live_enabled": True},
            "risk": {"starting_equity": 5000},
            "strategies": strats,
            "version": "v1.0",
        }

    def test_strategies_returns_real_lifecycle_state(self, _lifecycle_db):
        """admin_get_strategies uses lifecycle_map from DB, not hardcoded UNKNOWN."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)

            # Insert lifecycle rows
            conn.execute(
                "INSERT INTO strategy_lifecycle VALUES "
                "('momentum_breakout', 'sp500', 'LIVE', datetime('now'), 'test')"
            )
            conn.execute(
                "INSERT INTO strategy_lifecycle VALUES "
                "('mean_reversion', 'sp500', 'PAPER', datetime('now'), 'test')"
            )
            conn.commit()

            mock_cfg = self._make_mock_cfg()
            with patch("services.api.admin._list_market_ids", return_value=["sp500"]), \
                 patch("utils.config.get_active_config", return_value=mock_cfg), \
                 patch("utils.config.get_raw_config", return_value=mock_cfg), \
                 patch("services.api.admin._get_active_override", return_value=None), \
                 patch("services.api.admin._open_positions_by_strategy", return_value=0), \
                 patch("services.api.admin._trades_30d_and_pnl", return_value=(0, 0.0)):
                from services.api.admin import admin_get_strategies
                mock_auth = MagicMock()
                result = admin_get_strategies(_auth=mock_auth)

                body = json.loads(result.body)
                strats = body.get("strategies", [])
                by_name = {s["strategy"]: s for s in strats}

                mb = by_name.get("momentum_breakout", {})
                assert mb.get("lifecycle") == "LIVE", (
                    f"momentum_breakout should be LIVE, got {mb.get('lifecycle')}"
                )
                mr = by_name.get("mean_reversion", {})
                assert mr.get("lifecycle") == "PAPER", (
                    f"mean_reversion should be PAPER, got {mr.get('lifecycle')}"
                )
        finally:
            _adb._db_path_override = orig

    def test_strategies_unknown_for_missing_lifecycle_row(self, _lifecycle_db):
        """Strategy with no lifecycle row gets UNKNOWN (fallback, not all are backfilled yet)."""
        db_path, conn = _lifecycle_db
        import db.atlas_db as _adb
        orig = _adb._db_path_override
        try:
            _adb._db_path_override = str(db_path)
            # No lifecycle rows inserted — should fall back to UNKNOWN
            mock_cfg = self._make_mock_cfg({"orphan_strategy": {"enabled": True, "weight": 0.1}})
            with patch("services.api.admin._list_market_ids", return_value=["sp500"]), \
                 patch("utils.config.get_active_config", return_value=mock_cfg), \
                 patch("utils.config.get_raw_config", return_value=mock_cfg), \
                 patch("services.api.admin._get_active_override", return_value=None), \
                 patch("services.api.admin._open_positions_by_strategy", return_value=0), \
                 patch("services.api.admin._trades_30d_and_pnl", return_value=(0, 0.0)):
                from services.api.admin import admin_get_strategies
                mock_auth = MagicMock()
                result = admin_get_strategies(_auth=mock_auth)
                body = json.loads(result.body)
                strats = body.get("strategies", [])
                by_name = {s["strategy"]: s for s in strats}
                orphan = by_name.get("orphan_strategy", {})
                assert orphan.get("lifecycle") == "UNKNOWN", (
                    f"Unknown strategy should fall back to UNKNOWN, got {orphan.get('lifecycle')}"
                )
        finally:
            _adb._db_path_override = orig


# ── Test: live DB state (integration — reads actual atlas.db) ─────────────

class TestLiveDBLifecycleState:
    """Smoke tests against the real atlas.db that verify the backfill ran."""

    def test_live_db_no_null_state(self):
        """Verify production DB has no NULL state rows after backfill."""
        db_path = _ATLAS_ROOT / "data" / "atlas.db"
        if not db_path.exists():
            pytest.skip("atlas.db not found — skipping live DB test")

        # Use raw sqlite3 to bypass test isolation fixture (_db_path_override redirect)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM strategy_lifecycle WHERE state IS NULL"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 0, f"Live DB has {count} NULL state rows in strategy_lifecycle"

    def test_live_db_total_rows_gte_config_pairs(self):
        """Live DB strategy_lifecycle should have ≥ number of config pairs."""
        db_path = _ATLAS_ROOT / "data" / "atlas.db"
        if not db_path.exists():
            pytest.skip("atlas.db not found — skipping live DB test")

        from scripts.backfill_strategy_lifecycle import _collect_config_pairs
        config_pairs = _collect_config_pairs()

        # Use raw sqlite3 to bypass test isolation fixture (_db_path_override redirect)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            total = conn.execute("SELECT COUNT(*) FROM strategy_lifecycle").fetchone()[0]
        finally:
            conn.close()
        assert total >= len(config_pairs), (
            f"strategy_lifecycle has {total} rows but config has {len(config_pairs)} pairs — "
            f"backfill may not have run"
        )

    def test_live_db_asx_equity_seeded(self):
        """Live DB must have ASX equity row in market_equity_history (seed_asx_equity.py ran)."""
        db_path = _ATLAS_ROOT / "data" / "atlas.db"
        if not db_path.exists():
            pytest.skip("atlas.db not found — skipping live DB test")

        # Use raw sqlite3 to bypass test isolation fixture (_db_path_override redirect)
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        try:
            row = conn.execute(
                "SELECT allocated_equity FROM market_equity_history "
                "WHERE market_id='asx' ORDER BY date DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None, "ASX equity not seeded — run scripts/seed_asx_equity.py"
        assert float(row[0]) > 0, f"ASX allocated_equity should be > 0, got {row[0]}"
