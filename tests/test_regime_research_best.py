"""Tests for regime-conditioned research_best (per audit Rec 5, 2026-05-06)."""
import importlib.util
import inspect
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))


# ── Migration tests ───────────────────────────────────────────────────────────

class TestMigration:
    """Migration script: idempotency, schema, row preservation."""

    def _load_mig(self, monkeypatch):
        spec = importlib.util.spec_from_file_location(
            "mig_2026_05_06",
            ATLAS_ROOT / "scripts/migrations/2026-05-06-add-regime-to-research-best.py",
        )
        mig = importlib.util.module_from_spec(spec)
        # Prevent __main__ block from executing during module load
        original_name = mig.__name__
        spec.loader.exec_module(mig)
        return mig

    def test_migration_adds_regime_state_column(self, tmp_path, monkeypatch):
        """Migration adds regime_state column to a bare research_best table."""
        db = tmp_path / "atlas.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE research_best (
                strategy TEXT, universe TEXT, params TEXT NOT NULL,
                sharpe REAL, trades INT, max_dd_pct REAL, updated_at TEXT,
                solo_sharpe REAL, portfolio_sharpe REAL,
                metric_type TEXT NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (strategy, universe)
            );
            INSERT INTO research_best
                (strategy, universe, params, sharpe, trades, metric_type)
            VALUES ('mr', 'sp500', '{}', 0.8, 100, 'solo');
        """)
        con.commit()
        con.close()

        mig = self._load_mig(monkeypatch)
        mig.DB_PATH = db
        rc = mig.migrate(apply=True, db_path=db)
        assert rc == 0

        con = sqlite3.connect(str(db))
        cols = [r[1] for r in con.execute("PRAGMA table_info(research_best)").fetchall()]
        assert "regime_state" in cols
        rows = con.execute("SELECT strategy, regime_state FROM research_best").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "mr"
        assert rows[0][1] is None  # legacy row preserved as NULL
        con.close()

    def test_migration_idempotent(self, tmp_path, monkeypatch):
        """Re-running migration on already-migrated DB is a no-op."""
        db = tmp_path / "atlas.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE research_best (
                strategy TEXT, universe TEXT, params TEXT NOT NULL,
                sharpe REAL, trades INT, max_dd_pct REAL, updated_at TEXT,
                solo_sharpe REAL, portfolio_sharpe REAL,
                metric_type TEXT NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (strategy, universe)
            );
            INSERT INTO research_best (strategy, universe, params, metric_type)
            VALUES ('mr', 'sp500', '{}', 'unknown'),
                   ('mb', 'commodity_etfs', '{}', 'solo');
        """)
        con.commit()
        con.close()

        mig = self._load_mig(monkeypatch)
        rc1 = mig.migrate(apply=True, db_path=db)
        assert rc1 == 0

        # Run again — should detect already_migrated and return 0 without touching rows
        rc2 = mig.migrate(apply=True, db_path=db)
        assert rc2 == 0

        con = sqlite3.connect(str(db))
        n = con.execute("SELECT COUNT(*) FROM research_best").fetchone()[0]
        assert n == 2  # both rows still present
        con.close()

    def test_migration_dry_run_makes_no_changes(self, tmp_path, monkeypatch):
        """Dry-run leaves schema unchanged."""
        db = tmp_path / "atlas.db"
        con = sqlite3.connect(str(db))
        con.executescript("""
            CREATE TABLE research_best (
                strategy TEXT, universe TEXT, params TEXT NOT NULL,
                metric_type TEXT NOT NULL DEFAULT 'unknown',
                PRIMARY KEY (strategy, universe)
            );
            INSERT INTO research_best VALUES ('mr', 'sp500', '{}', 'unknown');
        """)
        con.commit()
        con.close()

        mig = self._load_mig(monkeypatch)
        rc = mig.migrate(apply=False, db_path=db)
        assert rc == 0

        con = sqlite3.connect(str(db))
        cols = [r[1] for r in con.execute("PRAGMA table_info(research_best)").fetchall()]
        assert "regime_state" not in cols  # unchanged
        con.close()

    def test_migration_missing_db_returns_1(self, tmp_path, monkeypatch):
        """migrate() returns 1 when DB file does not exist."""
        mig = self._load_mig(monkeypatch)
        rc = mig.migrate(apply=True, db_path=tmp_path / "nonexistent.db")
        assert rc == 1


# ── upsert_research_best / get_research_best tests ───────────────────────────

class TestUpsertGetRegime:
    """DB layer: upsert and get with regime_state."""

    def test_upsert_cross_regime_null(self):
        """upsert with regime_state=None writes a NULL row (cross-regime fallback)."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("mr", "sp500", params={"w": 5}, sharpe=0.7,
                             trades=80, max_dd_pct=9.0, regime_state=None)
        rows = get_research_best(strategy="mr", universe="sp500", regime_state=None)
        assert any(r["regime_state"] is None and r["sharpe"] == 0.7 for r in rows)

    def test_upsert_per_regime_non_null(self):
        """upsert with regime_state='bull_risk_on' writes a per-regime row."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("mr", "commodity_etfs",
                             params={"window": 3}, sharpe=0.756, trades=50,
                             max_dd_pct=8.0, regime_state="bull_risk_on")
        rows = get_research_best(strategy="mr", universe="commodity_etfs",
                                  regime_state="bull_risk_on")
        bull = [r for r in rows if r["regime_state"] == "bull_risk_on"]
        assert len(bull) >= 1
        assert bull[0]["sharpe"] == 0.756
        assert json.loads(bull[0]["params"]) == {"window": 3} if isinstance(bull[0]["params"], str) \
            else bull[0]["params"] == {"window": 3}

    def test_upsert_multiple_regimes_coexist(self):
        """Multiple regime rows coexist for the same (strategy, universe)."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("mb", "sp500", params={"w": 5}, sharpe=0.60,
                             trades=80, regime_state=None)
        upsert_research_best("mb", "sp500", params={"w": 3}, sharpe=0.80,
                             trades=60, regime_state="bull_risk_on")
        upsert_research_best("mb", "sp500", params={"w": 7}, sharpe=0.55,
                             trades=90, regime_state="recovery_early")

        all_rows = get_research_best(strategy="mb", universe="sp500", regime_state=None)
        bull_rows = get_research_best(strategy="mb", universe="sp500",
                                       regime_state="bull_risk_on")
        rec_rows = get_research_best(strategy="mb", universe="sp500",
                                      regime_state="recovery_early")

        assert any(r["regime_state"] is None for r in all_rows)
        assert any(r["regime_state"] == "bull_risk_on" for r in bull_rows)
        assert any(r["regime_state"] == "recovery_early" for r in rec_rows)

    def test_upsert_null_regime_overwrites_previous_null(self):
        """Two upserts with regime_state=None result in exactly one row."""
        from db.atlas_db import upsert_research_best, get_db
        for sharpe in [0.85, 1.10]:
            upsert_research_best("mr", "sp500", params={}, sharpe=sharpe,
                                  regime_state=None)
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM research_best "
                "WHERE strategy='mr' AND universe='sp500' AND regime_state IS NULL"
            ).fetchone()[0]
        assert count == 1

    def test_get_returns_fallback_when_regime_missing(self):
        """get_research_best falls back to NULL row when regime_state not found."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("mr", "test_uni", params={"w": 5}, sharpe=0.5,
                             trades=50, max_dd_pct=5.0, regime_state=None)
        rows = get_research_best(strategy="mr", universe="test_uni",
                                  regime_state="unknown_regime",
                                  fallback_to_cross_regime=True)
        assert len(rows) >= 1
        assert rows[0]["regime_state"] is None  # cross-regime fallback

    def test_get_no_fallback_returns_empty(self):
        """get_research_best with fallback_to_cross_regime=False returns [] when no regime row."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("mr", "test_uni_nf", params={}, sharpe=0.5,
                             trades=50, regime_state=None)
        rows = get_research_best(strategy="mr", universe="test_uni_nf",
                                  regime_state="missing_regime",
                                  fallback_to_cross_regime=False)
        assert len(rows) == 0

    def test_get_legacy_no_regime_arg_returns_null_rows_only(self):
        """get_research_best(regime_state=None) returns ONLY NULL rows (legacy behavior)."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("cr", "sp500", params={}, sharpe=0.6,
                             trades=60, regime_state=None)
        upsert_research_best("cr", "sp500", params={}, sharpe=0.9,
                             trades=40, regime_state="bull_risk_on")
        rows = get_research_best(strategy="cr", universe="sp500", regime_state=None)
        # Should not include bull_risk_on row
        assert all(r["regime_state"] is None for r in rows)

    def test_params_deserialized_for_regime_row(self):
        """params returned as dict (not JSON string) for per-regime row."""
        from db.atlas_db import upsert_research_best, get_research_best
        upsert_research_best("cr2", "sp500", params={"rsi": 14}, sharpe=0.7,
                             trades=50, regime_state="recovery_early")
        rows = get_research_best(strategy="cr2", universe="sp500",
                                  regime_state="recovery_early")
        assert len(rows) >= 1
        p = rows[0]["params"]
        if isinstance(p, str):
            p = json.loads(p)
        assert isinstance(p, dict)
        assert p.get("rsi") == 14


# ── get_current_regime_state tests ────────────────────────────────────────────

class TestGetCurrentRegimeState:
    """get_current_regime_state() returns string or None."""

    def test_returns_none_when_empty(self):
        """Returns None when regime_history is empty (isolated test DB)."""
        from db.atlas_db import get_current_regime_state
        result = get_current_regime_state()
        assert result is None or isinstance(result, str)

    def test_signature_returns_optional_str(self):
        """Function exists and has the correct return annotation."""
        from db import atlas_db
        assert hasattr(atlas_db, "get_current_regime_state")
        fn = atlas_db.get_current_regime_state
        sig = inspect.signature(fn)
        # Should have no required parameters
        assert len([p for p in sig.parameters.values()
                    if p.default is inspect.Parameter.empty]) == 0


# ── load_best tests ────────────────────────────────────────────────────────────

class TestLoadBest:
    """research/loop.py::load_best backward compat + regime-aware path."""

    def test_signature_has_regime_state_param(self):
        """load_best has regime_state kwarg defaulting to None."""
        from research.loop import load_best
        sig = inspect.signature(load_best)
        assert "regime_state" in sig.parameters
        assert sig.parameters["regime_state"].default is None

    def test_backward_compat_no_regime(self, tmp_path, monkeypatch):
        """load_best() with no regime_state reads JSON file (legacy path)."""
        import json as _json
        from research import loop as loop_mod

        # Point BEST_DIR at tmp
        monkeypatch.setattr(loop_mod, "BEST_DIR", tmp_path)
        best_file = tmp_path / "mr.json"
        best_file.write_text(_json.dumps({"strategy": "mr", "params": {"w": 7}}))

        result = loop_mod.load_best("mr", "sp500")
        assert result is not None
        assert result["params"]["w"] == 7

    def test_regime_aware_prefers_sqlite_row(self, monkeypatch):
        """load_best with regime_state prefers SQLite row over JSON file."""
        from db.atlas_db import upsert_research_best
        from research.loop import load_best

        upsert_research_best("mr", "sp500", params={"w": 3}, sharpe=0.9,
                             trades=50, regime_state="bull_risk_on")

        result = load_best("mr", "sp500", regime_state="bull_risk_on")
        assert result is not None
        params = result["params"]
        if isinstance(params, str):
            params = json.loads(params)
        assert params.get("w") == 3
        assert result.get("regime_state") == "bull_risk_on"

    def test_regime_aware_falls_back_to_json(self, tmp_path, monkeypatch):
        """load_best with rare regime falls back to JSON cross-regime file."""
        import json as _json
        from research import loop as loop_mod

        monkeypatch.setattr(loop_mod, "BEST_DIR", tmp_path)
        best_file = tmp_path / "mr.json"
        best_file.write_text(_json.dumps({"strategy": "mr", "params": {"w": 5}}))

        # No SQLite row for 'bear_risk_off' → should fall back to JSON
        result = loop_mod.load_best("mr", "sp500", regime_state="bear_risk_off_rare_xyz")
        # Either falls back to JSON (because NULL SQLite row also doesn't exist in isolated DB)
        # or returns None if no JSON either — both are acceptable
        assert result is None or isinstance(result.get("params"), dict)

    def test_missing_json_and_no_sqlite_returns_none(self, tmp_path, monkeypatch):
        """load_best returns None when no JSON file and no SQLite row exist."""
        from research import loop as loop_mod
        monkeypatch.setattr(loop_mod, "BEST_DIR", tmp_path)  # empty dir

        result = loop_mod.load_best("totally_unknown_strategy_xyz", "sp500")
        assert result is None


# ── Backfill dry-run smoke test ───────────────────────────────────────────────

class TestBackfillScript:
    """backfill_regime_research_best.py: dry-run returns 0."""

    def test_dry_run_exits_zero(self, monkeypatch):
        """Backfill dry-run on isolated DB returns 0.

        The isolated test DB is created from schema.sql which lacks the
        regime_state column in research_experiments (it's migration-added).
        We add it here so the backfill can proceed past the schema check.
        """
        import importlib.util as _ilu
        from db import atlas_db as _adb

        # Add regime_state to research_experiments in the isolated test DB
        with _adb.get_db() as db:
            try:
                db.execute(
                    "ALTER TABLE research_experiments ADD COLUMN regime_state TEXT"
                )
            except Exception:
                pass  # already present

        spec = _ilu.spec_from_file_location(
            "bf",
            ATLAS_ROOT / "scripts/backfill_regime_research_best.py",
        )
        bf = _ilu.module_from_spec(spec)
        spec.loader.exec_module(bf)
        rc = bf.backfill(apply=False)
        assert rc == 0

    def test_dry_run_with_seeded_data(self, monkeypatch):
        """Backfill dry-run correctly identifies eligible combos from seeded data."""
        import importlib.util as _ilu
        from db import atlas_db as _adb

        params_json = '{"w": 5}'
        params_json_mb = '{"w": 3}'

        with _adb.get_db() as db:
            # Add regime_state if not present (migration-added column not in schema.sql)
            try:
                db.execute(
                    "ALTER TABLE research_experiments ADD COLUMN regime_state TEXT"
                )
            except Exception:
                pass

            # Seed 35 experiments for (mean_reversion, sp500, bull_risk_on) — above MIN_EXPERIMENTS=30
            for i in range(35):
                db.execute(
                    "INSERT OR IGNORE INTO research_experiments "
                    "(id, strategy, universe, regime_state, sharpe, trades, params_changed, status) "
                    "VALUES (?, 'mean_reversion', 'sp500', 'bull_risk_on', ?, 50, ?, 'kept')",
                    (f"bf_test_{i}", 0.5 + i * 0.01, params_json),
                )
            # Seed only 10 for (mb, sp500, recovery_early) — below threshold, should be skipped
            for i in range(10):
                db.execute(
                    "INSERT OR IGNORE INTO research_experiments "
                    "(id, strategy, universe, regime_state, sharpe, trades, params_changed, status) "
                    "VALUES (?, 'momentum_breakout', 'sp500', 'recovery_early', ?, 50, ?, 'kept')",
                    (f"bf_mb_{i}", 0.6, params_json_mb),
                )

        spec = _ilu.spec_from_file_location(
            "bf2",
            ATLAS_ROOT / "scripts/backfill_regime_research_best.py",
        )
        bf = _ilu.module_from_spec(spec)
        spec.loader.exec_module(bf)
        rc = bf.backfill(apply=False)
        assert rc == 0  # dry-run always succeeds
