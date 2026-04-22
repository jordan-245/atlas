"""Tests for scripts/post_sweep_canary_check.py

Run:
    cd /root/atlas && python3 -m pytest tests/test_post_sweep_canary_check.py -v --timeout=30
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

# Bootstrap sys.path
ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

import scripts.post_sweep_canary_check as canary


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_baseline(tmp_path: Path, count: int = 3, hits: list[dict] | None = None) -> Path:
    """Write a synthetic baseline.json into tmp_path."""
    if hits is None:
        hits = [
            {"strategy": "mean_reversion", "s4": 0.8415, "trades": 51,
             "universes": "sector_etfs,gold_etfs,treasury_etfs", "n": 9},
            {"strategy": "momentum_breakout", "s4": 0.479, "trades": 52,
             "universes": "sector_etfs,gold_etfs,treasury_etfs,defensive_etfs", "n": 4},
            {"strategy": "opening_gap", "s4": 0.8962, "trades": 51,
             "universes": "sector_etfs,gold_etfs,treasury_etfs,defensive_etfs", "n": 4},
        ][:count]
    bl = {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "count": len(hits),
        "note": "test baseline",
        "hits": hits,
    }
    p = tmp_path / "baseline.json"
    p.write_text(json.dumps(bl))
    return p


def _make_db(tmp_path: Path) -> Path:
    """Create a minimal research_experiments SQLite DB seeded with corrupt rows."""
    db_path = tmp_path / "test_atlas.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""
            CREATE TABLE research_experiments (
                id INTEGER PRIMARY KEY,
                strategy TEXT,
                universe TEXT,
                sharpe REAL,
                trades INTEGER,
                description TEXT,
                created_at TEXT
            )
        """)
        # Seed 6 corrupt rows (2 universes × 3 strategies) in the corrupt window
        corrupt_rows = [
            ("mean_reversion", "sector_etfs",    0.8415, 51, None,       "2026-04-20"),
            ("mean_reversion", "gold_etfs",       0.8415, 51, None,       "2026-04-20"),
            ("mean_reversion", "treasury_etfs",   0.8415, 51, None,       "2026-04-20"),
            ("opening_gap",    "sector_etfs",     0.8962, 51, "baseline", "2026-04-20"),
            ("opening_gap",    "gold_etfs",       0.8962, 51, "baseline", "2026-04-20"),
            ("opening_gap",    "defensive_etfs",  0.8962, 51, "baseline", "2026-04-20"),
        ]
        conn.executemany(
            "INSERT INTO research_experiments(strategy,universe,sharpe,trades,description,created_at)"
            " VALUES(?,?,?,?,?,?)",
            corrupt_rows,
        )
        conn.commit()
    return db_path


# ─── Test 1: Baseline loading ──────────────────────────────────────────────────

class TestBaselineLoading:
    def test_loads_valid_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bl_path = _make_baseline(tmp_path)
        monkeypatch.setattr(canary, "BASELINE_FILE", bl_path)
        result = canary.load_baseline()
        assert result is not None
        assert result["count"] == 3
        assert len(result["hits"]) == 3

    def test_missing_baseline_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(canary, "BASELINE_FILE", tmp_path / "nonexistent.json")
        assert canary.load_baseline() is None

    def test_missing_baseline_causes_exit_1(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(canary, "BASELINE_FILE", tmp_path / "nonexistent.json")
        monkeypatch.setattr(canary, "CANARY_RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(canary, "LOG_FILE", tmp_path / "canary.log")
        with patch("scripts.post_sweep_canary_check._telegram", return_value=False):
            rc = canary.main(["--dry-run", "--db", str(tmp_path / "fake.db")])
        assert rc == 1


# ─── Test 2: Purge logic ───────────────────────────────────────────────────────

class TestPurgeLogic:
    def test_purge_deletes_corrupt_rows(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        db_path = _make_db(tmp_path)
        bl_path = _make_baseline(tmp_path)

        monkeypatch.setattr(canary, "BASELINE_FILE", bl_path)
        monkeypatch.setattr(canary, "PURGE_DONE_FILE", tmp_path / "purge_done.json")
        monkeypatch.setattr(canary, "CANARY_STATE_DIR", tmp_path)
        monkeypatch.setattr(canary, "CANARY_RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(canary, "LOG_FILE", tmp_path / "canary.log")

        baseline = json.loads(bl_path.read_text())
        baseline_hits = baseline["hits"]

        # Dry-run: no rows deleted
        n = canary._purge_corrupt_rows(db_path, baseline_hits, dry_run=True)
        assert n == 0

        # Pre-purge row count
        with sqlite3.connect(str(db_path)) as conn:
            before = conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0]
        assert before == 6

        # Real purge
        n = canary._purge_corrupt_rows(db_path, baseline_hits, dry_run=False)
        assert n > 0

        with sqlite3.connect(str(db_path)) as conn:
            after = conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0]
        assert after < before


# ─── Test 3: Idempotency ──────────────────────────────────────────────────────

class TestIdempotency:
    def test_second_run_skips_purge(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        db_path = _make_db(tmp_path)
        bl_path = _make_baseline(tmp_path)
        done_path = tmp_path / "purge_done.json"
        done_path.write_text(json.dumps({
            "purged_at": "2026-04-22T07:00:00+00:00",
            "rows_deleted": 42,
            "canary_count_at_purge": 0,
        }))

        monkeypatch.setattr(canary, "BASELINE_FILE", bl_path)
        monkeypatch.setattr(canary, "PURGE_DONE_FILE", done_path)
        monkeypatch.setattr(canary, "CANARY_STATE_DIR", tmp_path)
        monkeypatch.setattr(canary, "CANARY_RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(canary, "LOG_FILE", tmp_path / "canary.log")

        # Stub query_suspicious to return 0 hits (≤ baseline 3)
        with patch("scripts.post_sweep_canary_check.query_suspicious", return_value=[]):
            with patch("scripts.post_sweep_canary_check._telegram", return_value=True):
                rc = canary.main(["--db", str(db_path)])
        assert rc == 0

        # DB should be unchanged (purge sentinel prevented delete)
        with sqlite3.connect(str(db_path)) as conn:
            remaining = conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0]
        assert remaining == 6


# ─── Test 4: Regression detection ────────────────────────────────────────────

class TestRegressionDetection:
    def test_count_increase_triggers_alert_not_delete(self, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
        db_path = _make_db(tmp_path)
        bl_path = _make_baseline(tmp_path)

        monkeypatch.setattr(canary, "BASELINE_FILE", bl_path)
        monkeypatch.setattr(canary, "PURGE_DONE_FILE", tmp_path / "purge_done.json")
        monkeypatch.setattr(canary, "CANARY_STATE_DIR", tmp_path)
        monkeypatch.setattr(canary, "CANARY_RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(canary, "LOG_FILE", tmp_path / "canary.log")

        # Simulate 5 hits vs baseline of 3 → regression
        fake_hits = [{"strategy": f"s{i}", "s4": 0.5, "trades": 10,
                      "universes": "a,b,c", "n": 3} for i in range(5)]

        telegram_calls: list[str] = []

        def fake_telegram(msg: str) -> bool:
            telegram_calls.append(msg)
            return True

        with patch("scripts.post_sweep_canary_check.query_suspicious", return_value=fake_hits):
            with patch("scripts.post_sweep_canary_check._telegram", side_effect=fake_telegram):
                rc = canary.main(["--db", str(db_path)])

        assert rc == 1, "Should exit 1 on regression"
        assert len(telegram_calls) == 1
        assert "regression" in telegram_calls[0].lower() or "🚨" in telegram_calls[0]

        # Sentinel must NOT exist (no purge attempted)
        assert not (tmp_path / "purge_done.json").exists()

        # DB unchanged
        with sqlite3.connect(str(db_path)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM research_experiments").fetchone()[0]
        assert count == 6


# ─── Test 5: Telegram mocking ─────────────────────────────────────────────────

class TestTelegramMocking:
    def test_successful_purge_sends_confirmation(self, tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch) -> None:
        db_path = _make_db(tmp_path)
        bl_path = _make_baseline(tmp_path)

        monkeypatch.setattr(canary, "BASELINE_FILE", bl_path)
        monkeypatch.setattr(canary, "PURGE_DONE_FILE", tmp_path / "purge_done.json")
        monkeypatch.setattr(canary, "CANARY_STATE_DIR", tmp_path)
        monkeypatch.setattr(canary, "CANARY_RUNS_DIR", tmp_path / "runs")
        monkeypatch.setattr(canary, "LOG_FILE", tmp_path / "canary.log")

        sent: list[str] = []

        def capture_telegram(msg: str) -> bool:
            sent.append(msg)
            return True

        # 0 current hits → fix held, purge fires
        with patch("scripts.post_sweep_canary_check.query_suspicious", return_value=[]):
            with patch("scripts.post_sweep_canary_check._telegram", side_effect=capture_telegram):
                rc = canary.main(["--db", str(db_path)])

        assert rc == 0
        assert len(sent) == 1
        assert "✅" in sent[0] or "purge complete" in sent[0].lower()
        assert (tmp_path / "purge_done.json").exists()
