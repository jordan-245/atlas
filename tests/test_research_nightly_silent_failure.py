"""Tests for the silent-failure detection added to research/autoresearch_nightly.py.

Covers:
  1. _count_rows_added returns correct count for matched universe + timestamp
  2. _count_rows_added filters by universe (not other universes)
  3. _count_rows_added filters by timestamp (old rows are excluded)
  4. MIN_ROWS_PER_UNIVERSE threshold lookup
  5. --dry-run-telegram path prints [TELEGRAM-DRY-RUN] instead of calling Telegram

Per audit 2026-05-06 Recommendation 2.
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch, MagicMock

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))

# Imports pulled at test-function level so _isolate_prod_db autouse fixture
# has a chance to redirect _db_path_override before any DB connection is made.


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _insert_experiment_row(
    universe: str,
    idx: int,
    created_at: str | None = None,
) -> None:
    """Insert a minimal research_experiments row via the isolated DB."""
    from db.atlas_db import get_db

    with get_db() as db:
        ts = created_at or datetime.now(timezone.utc).isoformat()
        db.execute(
            "INSERT INTO research_experiments "
            "(id, strategy, universe, status, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"test-{universe}-{idx}", "mean_reversion", universe, "kept", ts),
        )


# ──────────────────────────────────────────────────────────────────────────────
# Test 1 — correct count returned
# ──────────────────────────────────────────────────────────────────────────────


def test_count_rows_added_returns_correct_count() -> None:
    """_count_rows_added returns the number of rows inserted after session_start_ts."""
    from research.autoresearch_nightly import _count_rows_added

    ts_before = time.time() - 1  # 1 second before inserts
    for i in range(5):
        _insert_experiment_row("sp500", i)

    assert _count_rows_added("sp500", ts_before) == 5


# ──────────────────────────────────────────────────────────────────────────────
# Test 2 — filters by universe
# ──────────────────────────────────────────────────────────────────────────────


def test_count_rows_added_filters_by_universe() -> None:
    """_count_rows_added counts only rows whose universe matches, not other universes."""
    from research.autoresearch_nightly import _count_rows_added

    ts_before = time.time() - 1
    for i in range(3):
        _insert_experiment_row("sp500", i)
    for i in range(2):
        _insert_experiment_row("commodity_etfs", i)

    # sp500 → 3, commodity_etfs → 2
    assert _count_rows_added("sp500", ts_before) == 3
    assert _count_rows_added("commodity_etfs", ts_before) == 2


# ──────────────────────────────────────────────────────────────────────────────
# Test 3 — filters by timestamp
# ──────────────────────────────────────────────────────────────────────────────


def test_count_rows_added_filters_by_timestamp() -> None:
    """Rows with old created_at are NOT counted when cutoff is set to 'now'."""
    from research.autoresearch_nightly import _count_rows_added

    old_ts = "2020-01-01T00:00:00+00:00"
    _insert_experiment_row("sp500", 99, created_at=old_ts)

    # Use a cutoff of "now" — the 2020 row is older, so count must be 0
    ts_now = time.time()
    assert _count_rows_added("sp500", ts_now) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Test 4 — MIN_ROWS_PER_UNIVERSE threshold lookup
# ──────────────────────────────────────────────────────────────────────────────


def test_silent_failure_threshold_lookup() -> None:
    """MIN_ROWS_PER_UNIVERSE has expected floor values; missing keys fall back to DEFAULT_MIN_ROWS."""
    from research.autoresearch_nightly import MIN_ROWS_PER_UNIVERSE, DEFAULT_MIN_ROWS

    assert MIN_ROWS_PER_UNIVERSE["sp500"] == 50
    assert MIN_ROWS_PER_UNIVERSE["commodity_etfs"] == 20
    assert MIN_ROWS_PER_UNIVERSE["sector_etfs"] == 20
    assert MIN_ROWS_PER_UNIVERSE["gold_etfs"] == 10
    assert MIN_ROWS_PER_UNIVERSE["treasury_etfs"] == 10
    assert MIN_ROWS_PER_UNIVERSE["defensive_etfs"] == 10
    assert MIN_ROWS_PER_UNIVERSE["asx"] == 10
    assert DEFAULT_MIN_ROWS == 10

    # Unknown universe falls back to DEFAULT_MIN_ROWS
    unknown_floor = MIN_ROWS_PER_UNIVERSE.get("totally_unknown_universe", DEFAULT_MIN_ROWS)
    assert unknown_floor == DEFAULT_MIN_ROWS


# ──────────────────────────────────────────────────────────────────────────────
# Test 5 — --dry-run-telegram path
# ──────────────────────────────────────────────────────────────────────────────


def test_dry_run_telegram_path(capsys, monkeypatch) -> None:
    """When dry_run_telegram=True, the alert message is printed to stdout
    (prefixed with [TELEGRAM-DRY-RUN]) instead of calling utils.telegram.notify."""
    import research.autoresearch_nightly as _mod

    # Patch _filter_enabled_strategies to pass through unchanged (avoids real config read)
    monkeypatch.setattr(_mod, "_filter_enabled_strategies", lambda strategies, *a, **kw: strategies)

    # Patch _spawn_workers to return nothing (avoids subprocess spawning)
    monkeypatch.setattr(_mod, "_spawn_workers", lambda *a, **kw: [])

    # Patch _run_promotion_sweep to return nothing
    monkeypatch.setattr(_mod, "_run_promotion_sweep", lambda *a, **kw: [])

    # Patch _count_rows_added to return 0 (triggers silent_failure for sp500, threshold=50)
    monkeypatch.setattr(_mod, "_count_rows_added", lambda *a, **kw: 0)

    # Patch _find_latest_snapshot to avoid filesystem lookup
    monkeypatch.setattr(_mod, "_find_latest_snapshot", lambda *a, **kw: "test-snapshot")

    # Patch utils.telegram.notify to assert it is NOT called (dry_run_telegram=True bypasses it)
    mock_tg = MagicMock()
    monkeypatch.setattr("utils.telegram.notify", mock_tg, raising=False)

    result = _mod.run_nightly(
        strategies=["mean_reversion"],
        market="sp500",
        hours=0.01,
        workers=1,
        notify=False,
        universe="sp500",
        dry_run_telegram=True,
    )

    captured = capsys.readouterr()
    assert "[TELEGRAM-DRY-RUN]" in captured.out, (
        f"Expected '[TELEGRAM-DRY-RUN]' in stdout.\nActual stdout:\n{captured.out}"
    )
    assert result.get("silent_failure") is True
    assert result.get("rows_added") == 0

    # notify() should NOT have been called (dry_run_telegram=True routes to print)
    mock_tg.assert_not_called()
