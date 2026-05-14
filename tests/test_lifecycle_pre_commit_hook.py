"""Tests for scripts/git-hooks/check_lifecycle_for_enabled.py

Tests the Python logic that enforces strategy_lifecycle state when enabling
strategies in config/active/*.json files.

Each test sets up a minimal git repo in tmp_path, stages the relevant files,
and calls main() directly with CWD set to the tmp repo.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# Import the module under test
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts" / "git-hooks"))
from check_lifecycle_for_enabled import main, _strategies_being_enabled, _latest_lifecycle_state


# ── Helpers ──────────────────────────────────────────────────────────────────

def _init_git_repo(path: Path) -> None:
    """Initialise a throwaway git repo with a generic identity."""
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True)


def _make_initial_commit(repo: Path, config_data: dict, market: str = "sp500") -> None:
    """Write config to config/active/<market>.json and create initial commit."""
    config_dir = repo / "config" / "active"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{market}.json").write_text(json.dumps(config_data, indent=2))
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=repo,
        check=True,
        capture_output=True,
    )


def _stage_config(repo: Path, config_data: dict, market: str = "sp500") -> str:
    """Write config and stage it; return path relative to repo root."""
    rel_path = f"config/active/{market}.json"
    (repo / rel_path).write_text(json.dumps(config_data, indent=2))
    subprocess.run(["git", "add", rel_path], cwd=repo, check=True, capture_output=True)
    return rel_path


def _make_lifecycle_db(path: Path, rows: list[tuple[str, str, str]]) -> Path:
    """Create a minimal strategy_lifecycle SQLite DB at path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(path)) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS strategy_lifecycle "
            "(strategy TEXT, universe TEXT, state TEXT, PRIMARY KEY (strategy, universe))"
        )
        conn.executemany(
            "INSERT OR REPLACE INTO strategy_lifecycle VALUES (?, ?, ?)", rows
        )
    return path


# ── Tests ────────────────────────────────────────────────────────────────────


class TestNoConfigChange:
    """test_no_config_change_passes — no staged config/active files → silent pass."""

    def test_no_config_change_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Staging a non-config file should not trigger any check (exit 0)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Initial commit with a plain text file
        (repo / "README.md").write_text("hello\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        # Stage a non-config file
        (repo / "some_script.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "some_script.py"], cwd=repo, check=True, capture_output=True)

        monkeypatch.chdir(repo)
        # No config files passed → exits 0 immediately
        assert main([str(repo / "data" / "atlas.db")]) == 0

    def test_no_args_passes(self) -> None:
        """main() with only db_path (no files) exits 0."""
        assert main(["data/atlas.db"]) == 0


class TestEnablingWithLifecycleRow:
    """test_enabling_with_live_row_passes — strategy enabled, lifecycle=LIVE → pass."""

    def test_enabling_with_live_row_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Config enables a strategy that has LIVE in strategy_lifecycle → exit 0."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # HEAD: strategy disabled
        head_cfg = {"market": "sp500", "strategies": {"momentum_breakout": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        # DB: LIVE row exists
        db_path = _make_lifecycle_db(
            repo / "data" / "atlas.db",
            [("momentum_breakout", "sp500", "LIVE")],
        )

        # Stage: strategy now enabled
        staged_cfg = {"market": "sp500", "strategies": {"momentum_breakout": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        assert main([str(db_path), rel_path]) == 0

    def test_enabling_with_paper_row_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Strategy has PAPER lifecycle state → also a valid pass."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        head_cfg = {"market": "sp500", "strategies": {"mean_reversion": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        db_path = _make_lifecycle_db(
            repo / "data" / "atlas.db",
            [("mean_reversion", "sp500", "PAPER")],
        )

        staged_cfg = {"market": "sp500", "strategies": {"mean_reversion": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        assert main([str(db_path), rel_path]) == 0


class TestEnablingWithoutLifecycleRow:
    """test_enabling_without_lifecycle_row_blocks — no row → exit 1 with message."""

    def test_enabling_without_lifecycle_row_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Enabling a strategy with no lifecycle row → exit 1, message mentions strategy."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        head_cfg = {"market": "sp500", "strategies": {"my_strategy": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        # DB exists but has NO row for my_strategy/sp500
        db_path = _make_lifecycle_db(repo / "data" / "atlas.db", [])

        staged_cfg = {"market": "sp500", "strategies": {"my_strategy": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        result = main([str(db_path), rel_path])

        assert result == 1
        captured = capsys.readouterr()
        assert "my_strategy" in captured.out
        assert "sp500" in captured.out
        assert "MISSING" in captured.out
        assert "LIVE or PAPER" in captured.out

    def test_error_message_contains_bypass_instruction(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Block message must include --no-verify bypass instruction."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        head_cfg = {"market": "sp500", "strategies": {"new_strat": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        db_path = _make_lifecycle_db(repo / "data" / "atlas.db", [])

        staged_cfg = {"market": "sp500", "strategies": {"new_strat": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        main([str(db_path), rel_path])

        captured = capsys.readouterr()
        assert "--no-verify" in captured.out


class TestEnablingWithRetiredRow:
    """test_enabling_with_retired_row_blocks — latest=RETIRED → exit 1."""

    def test_enabling_with_retired_row_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Strategy has RETIRED lifecycle state → blocked (not LIVE or PAPER)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        head_cfg = {"market": "sp500", "strategies": {"old_strat": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        db_path = _make_lifecycle_db(
            repo / "data" / "atlas.db",
            [("old_strat", "sp500", "RETIRED")],
        )

        staged_cfg = {"market": "sp500", "strategies": {"old_strat": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        result = main([str(db_path), rel_path])

        assert result == 1
        captured = capsys.readouterr()
        assert "old_strat" in captured.out
        assert "RETIRED" in captured.out

    def test_enabling_with_research_row_blocks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
    ) -> None:
        """Strategy has RESEARCH lifecycle state → also blocked."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        head_cfg = {"market": "sp500", "strategies": {"research_strat": {"enabled": False}}}
        _make_initial_commit(repo, head_cfg)

        db_path = _make_lifecycle_db(
            repo / "data" / "atlas.db",
            [("research_strat", "sp500", "RESEARCH")],
        )

        staged_cfg = {"market": "sp500", "strategies": {"research_strat": {"enabled": True}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        result = main([str(db_path), rel_path])

        assert result == 1
        captured = capsys.readouterr()
        assert "research_strat" in captured.out


class TestDisablingPasses:
    """test_disabling_with_no_lifecycle_passes — disabling a strategy always passes."""

    def test_disabling_with_no_lifecycle_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Disabling a strategy (enabled: true → false) passes even with no lifecycle row."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # HEAD: strategy was ENABLED
        head_cfg = {"market": "sp500", "strategies": {"active_strat": {"enabled": True}}}
        _make_initial_commit(repo, head_cfg)

        # DB: no row at all
        db_path = _make_lifecycle_db(repo / "data" / "atlas.db", [])

        # Stage: strategy now DISABLED
        staged_cfg = {"market": "sp500", "strategies": {"active_strat": {"enabled": False}}}
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        assert main([str(db_path), rel_path]) == 0

    def test_already_enabled_no_flip_passes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Strategy already enabled in HEAD and stays enabled → no flip → passes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # HEAD: already enabled
        head_cfg = {"market": "sp500", "strategies": {"stable_strat": {"enabled": True}}}
        _make_initial_commit(repo, head_cfg)

        # DB: no row (strategy was enabled before lifecycle table existed)
        db_path = _make_lifecycle_db(repo / "data" / "atlas.db", [])

        # Stage: enabled stays true, but other param changed
        staged_cfg = {
            "market": "sp500",
            "strategies": {"stable_strat": {"enabled": True, "weight": 0.3}},
        }
        rel_path = _stage_config(repo, staged_cfg)

        monkeypatch.chdir(repo)
        # No flip (was True → still True) → passes silently
        assert main([str(db_path), rel_path]) == 0


# ── Unit tests for helper functions ──────────────────────────────────────────


class TestStrategiesBeingEnabled:
    """Unit tests for the _strategies_being_enabled helper."""

    def test_new_file_enabled_strategy_detected(self) -> None:
        """New file (head=None) with enabled=true counts as a flip."""
        staged = {"market": "sp500", "strategies": {"new_strat": {"enabled": True}}}
        result = _strategies_being_enabled(None, staged)
        assert ("new_strat", "sp500") in result

    def test_new_file_disabled_not_detected(self) -> None:
        """New file with enabled=false → no flip."""
        staged = {"market": "sp500", "strategies": {"new_strat": {"enabled": False}}}
        result = _strategies_being_enabled(None, staged)
        assert result == []

    def test_already_enabled_no_flip(self) -> None:
        """Strategy enabled in HEAD and staged → not a flip."""
        head = {"market": "sp500", "strategies": {"s": {"enabled": True}}}
        staged = {"market": "sp500", "strategies": {"s": {"enabled": True, "weight": 0.5}}}
        result = _strategies_being_enabled(head, staged)
        assert result == []

    def test_uses_market_key_for_universe(self) -> None:
        """Universe derived from 'market' key."""
        staged = {"market": "commodity_etfs", "strategies": {"s": {"enabled": True}}}
        result = _strategies_being_enabled(None, staged)
        assert ("s", "commodity_etfs") in result

    def test_falls_back_to_universe_key(self) -> None:
        """Falls back to 'universe' key if 'market' missing."""
        staged = {"universe": "sector_etfs", "strategies": {"s": {"enabled": True}}}
        result = _strategies_being_enabled(None, staged)
        assert ("s", "sector_etfs") in result


class TestLatestLifecycleState:
    """Unit tests for _latest_lifecycle_state helper."""

    def test_returns_none_if_db_missing(self, tmp_path: Path) -> None:
        state = _latest_lifecycle_state(str(tmp_path / "noexist.db"), "s", "sp500")
        assert state is None

    def test_returns_none_if_no_row(self, tmp_path: Path) -> None:
        db = _make_lifecycle_db(tmp_path / "atlas.db", [])
        state = _latest_lifecycle_state(str(db), "missing_strat", "sp500")
        assert state is None

    def test_returns_state_when_row_exists(self, tmp_path: Path) -> None:
        db = _make_lifecycle_db(tmp_path / "atlas.db", [("s", "sp500", "LIVE")])
        assert _latest_lifecycle_state(str(db), "s", "sp500") == "LIVE"

    def test_retired_state_returned_correctly(self, tmp_path: Path) -> None:
        db = _make_lifecycle_db(tmp_path / "atlas.db", [("s", "sp500", "RETIRED")])
        assert _latest_lifecycle_state(str(db), "s", "sp500") == "RETIRED"

    def test_no_table_returns_none(self, tmp_path: Path) -> None:
        """DB exists but table is missing → treated as None (fail-open)."""
        db_path = tmp_path / "empty.db"
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("CREATE TABLE dummy (x INTEGER)")
        state = _latest_lifecycle_state(str(db_path), "s", "sp500")
        assert state is None
