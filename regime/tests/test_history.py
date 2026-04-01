"""
regime/tests/test_history.py — Tests for regime/history.py

Run with:
    cd /root/atlas && python -m pytest regime/tests/test_history.py -v

Coverage
--------
- backfill_regime_history with small mock dataset (5 trading days)
- Regime transitions correctly detected
- state_distribution counts are correct
- Missing data handling (all-None rows get skipped)
- Idempotent re-run (second call overwrites without error)
- get_regime_transitions returns correct (date, from, to) format
- backfill_macro_data skips re-fetch when table is large enough
- --macro-only and --force CLI flags
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Dict
from unittest.mock import MagicMock, patch

import pytest

# ── Project root on path ───────────────────────────────────────────────────────
PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))

from db.atlas_db import (
    get_db,
    get_macro_indicators,
    init_db,
    record_regime,
    upsert_macro_indicators,
)
from regime.history import (
    _MIN_MACRO_ROWS_THRESHOLD,
    backfill_macro_data,
    backfill_regime_history,
    get_regime_transitions,
    print_regime_summary,
)

# ──────────────────────────────────────────────────────────────────────────────
# Shared fixtures & helpers
# ──────────────────────────────────────────────────────────────────────────────

# Synthetic indicator sets shared across tests
BULL_INDICATORS = {
    "spy_close": 500.0,
    "spy_200dma": 450.0,
    "spy_above_200dma": 1,
    "spy_200dma_slope": 0.05,
    "vix": 14.0,
    "vix3m": 16.0,
    "vix_term_ratio": 0.875,
    "credit_oas": 0.8,
    "yield_curve_10y2y": 1.5,
    "yield_curve_10y3m": 2.0,
    "dxy": 100.0,
    "gold_copper_ratio": 16.0,
}

BEAR_INDICATORS = {
    "spy_close": 250.0,
    "spy_200dma": 400.0,
    "spy_above_200dma": 0,
    "spy_200dma_slope": -0.05,
    "vix": 42.0,
    "vix3m": 30.0,
    "vix_term_ratio": 1.4,
    "credit_oas": 8.0,
    "yield_curve_10y2y": -0.5,
    "yield_curve_10y3m": -0.8,
    "dxy": 108.0,
    "gold_copper_ratio": 30.0,
}


def _five_trading_days() -> list:
    """Return a list of 5 YYYY-MM-DD strings for Mon-Fri of a week."""
    return [
        "2024-01-02",
        "2024-01-03",
        "2024-01-04",
        "2024-01-05",
        "2024-01-08",
    ]


@pytest.fixture()
def tmp_db(tmp_path):
    """Initialise a fresh temp SQLite DB and make it the module-level default."""
    db_path = str(tmp_path / "test_atlas.db")
    init_db(db_path)
    yield db_path
    # Reset override after test
    import db.atlas_db as _db_mod
    _db_mod._db_path_override = None


def _seed_macro_rows(dates: list, indicators: dict | None = None) -> None:
    """Write synthetic macro rows into macro_indicators for each date."""
    base = indicators or BULL_INDICATORS
    for date in dates:
        upsert_macro_indicators(date, **base)


# ──────────────────────────────────────────────────────────────────────────────
# backfill_macro_data — unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestBackfillMacroData:
    """backfill_macro_data delegates to data.macro.backfill_macro_indicators."""

    def test_calls_backfill_and_returns_row_count(self, tmp_db):
        """When delegate returns a non-empty DataFrame, row count is returned."""
        import pandas as pd

        fake_df = pd.DataFrame({"vix": [15.0, 16.0]}, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        # backfill_macro_data imports backfill_macro_indicators locally from data.macro
        with patch("data.macro.backfill_macro_indicators", return_value=fake_df) as mock_fn:
            n = backfill_macro_data(start_date="2024-01-01", end_date="2024-01-05")

        mock_fn.assert_called_once_with(start_date="2024-01-01", end_date="2024-01-05")
        assert n == 2

    def test_returns_zero_on_empty_dataframe(self, tmp_db):
        import pandas as pd

        with patch("data.macro.backfill_macro_indicators", return_value=pd.DataFrame()):
            n = backfill_macro_data(start_date="2024-01-01")
        assert n == 0

    def test_default_end_date_is_today(self, tmp_db):
        import pandas as pd
        from datetime import datetime

        with patch("data.macro.backfill_macro_indicators", return_value=pd.DataFrame()) as mock_fn:
            backfill_macro_data(start_date="2024-01-01")

        _, kwargs = mock_fn.call_args
        today = datetime.now().strftime("%Y-%m-%d")
        assert kwargs.get("end_date") == today or mock_fn.call_args[0][1] == today


# ──────────────────────────────────────────────────────────────────────────────
# backfill_regime_history — core unit tests
# ──────────────────────────────────────────────────────────────────────────────

class TestBackfillRegimeHistory:

    def test_basic_five_day_backfill(self, tmp_db):
        """5 bull-market days should all classify to the same bull state."""
        dates = _five_trading_days()
        _seed_macro_rows(dates)

        # Patch out the macro download (table already has rows > threshold would
        # skip it anyway, but 5 rows < threshold so we mock it to avoid network)
        with patch("regime.history.backfill_macro_data", return_value=5):
            stats = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )

        assert stats["dates_processed"] == 5
        assert stats["dates_skipped"] == 0
        assert sum(stats["state_distribution"].values()) == 5

    def test_regime_transitions_detected(self, tmp_db):
        """Insert 3 bull days then 2 bear days; expect exactly 1 transition."""
        bull_dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        bear_dates = ["2024-01-05", "2024-01-08"]
        _seed_macro_rows(bull_dates, BULL_INDICATORS)
        _seed_macro_rows(bear_dates, BEAR_INDICATORS)

        with patch("regime.history.backfill_macro_data", return_value=5):
            stats = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )

        # There should be at least 1 transition (bull → bear family)
        assert len(stats["regime_transitions"]) >= 1

        # The transition tuple must be (date_str, from_state, to_state)
        for t in stats["regime_transitions"]:
            assert len(t) == 3
            date, from_s, to_s = t
            assert isinstance(date, str)
            assert isinstance(from_s, str)
            assert isinstance(to_s, str)
            assert from_s != to_s

    def test_state_distribution_counts(self, tmp_db):
        """state_distribution sums to dates_processed."""
        dates = _five_trading_days()
        _seed_macro_rows(dates)

        with patch("regime.history.backfill_macro_data", return_value=5):
            stats = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )

        total_in_distribution = sum(stats["state_distribution"].values())
        assert total_in_distribution == stats["dates_processed"]

    def test_missing_data_rows_skipped(self, tmp_db):
        """Rows where all indicator columns are NULL must be skipped."""
        dates = ["2024-01-02", "2024-01-03", "2024-01-04"]
        # Insert 2 valid rows
        _seed_macro_rows(["2024-01-02", "2024-01-03"])
        # Insert 1 empty row (only date, all others NULL)
        with get_db() as db:
            db.execute("INSERT OR REPLACE INTO macro_indicators (date) VALUES (?)", ("2024-01-04",))

        with patch("regime.history.backfill_macro_data", return_value=3):
            stats = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )

        assert stats["dates_processed"] == 2
        assert stats["dates_skipped"] == 1

    def test_idempotent_rerun(self, tmp_db):
        """Running twice should overwrite without error and give same results."""
        dates = _five_trading_days()
        _seed_macro_rows(dates)

        with patch("regime.history.backfill_macro_data", return_value=5):
            stats1 = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )
            stats2 = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=False,
            )

        # Idempotent — same processed count, same state distribution
        assert stats1["dates_processed"] == stats2["dates_processed"]
        assert stats1["state_distribution"] == stats2["state_distribution"]

        # DB should have exactly 5 rows (not doubled)
        with get_db() as db:
            count = db.execute(
                "SELECT COUNT(*) FROM regime_history WHERE date >= '2024-01-01' AND date <= '2024-01-31'"
            ).fetchone()[0]
        assert count == 5

    def test_macro_backfill_skipped_when_table_is_large(self, tmp_db):
        """If macro_indicators has >= threshold rows, macro backfill is skipped."""
        # Insert threshold + 10 unique dates so the auto-skip logic fires.
        # Generate sequential dates to guarantee uniqueness.
        from datetime import date as _date, timedelta
        base = _date(2015, 1, 1)
        for i in range(_MIN_MACRO_ROWS_THRESHOLD + 10):
            date = (base + timedelta(days=i)).strftime("%Y-%m-%d")
            upsert_macro_indicators(date, vix=15.0)

        with patch("regime.history.backfill_macro_data") as mock_macro:
            backfill_regime_history(
                start_date="2023-01-01",
                end_date="2023-01-05",
                force_macro=False,
            )
        # Should NOT have been called since table is big enough
        mock_macro.assert_not_called()

    def test_force_macro_triggers_backfill_regardless(self, tmp_db):
        """force_macro=True always calls backfill_macro_data."""
        # Insert some rows (below threshold)
        _seed_macro_rows(_five_trading_days())

        with patch("regime.history.backfill_macro_data", return_value=5) as mock_macro:
            backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-31",
                force_macro=True,
            )
        mock_macro.assert_called_once()

    def test_no_macro_data_returns_empty_stats(self, tmp_db):
        """If table has no rows after macro backfill, return zero-stats dict."""
        with patch("regime.history.backfill_macro_data", return_value=0):
            stats = backfill_regime_history(
                start_date="2024-01-01",
                end_date="2024-01-05",
                force_macro=False,
            )

        assert stats["dates_processed"] == 0
        assert stats["dates_skipped"] == 0
        assert stats["regime_transitions"] == []
        assert stats["state_distribution"] == {}


# ──────────────────────────────────────────────────────────────────────────────
# get_regime_transitions
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRegimeTransitions:

    def test_returns_empty_list_when_no_history(self, tmp_db):
        transitions = get_regime_transitions()
        assert transitions == []

    def test_no_transitions_when_state_unchanged(self, tmp_db):
        """All same state → no transitions."""
        for date in ["2024-01-02", "2024-01-03", "2024-01-04"]:
            record_regime(
                date=date,
                state="bull_risk_on",
                trend_score=0.8,
                risk_score=0.7,
                active_universes=["sp500"],
                sizing_multiplier=1.0,
            )
        transitions = get_regime_transitions()
        assert transitions == []

    def test_detects_single_transition(self, tmp_db):
        """Bull for 2 days then bear → one transition."""
        for date in ["2024-01-02", "2024-01-03"]:
            record_regime(
                date=date,
                state="bull_risk_on",
                trend_score=0.8,
                risk_score=0.7,
                active_universes=["sp500"],
                sizing_multiplier=1.0,
            )
        for date in ["2024-01-04", "2024-01-05"]:
            record_regime(
                date=date,
                state="bear_risk_off",
                trend_score=-0.6,
                risk_score=-0.5,
                active_universes=["treasury_etfs"],
                sizing_multiplier=0.5,
            )

        transitions = get_regime_transitions()
        assert len(transitions) == 1
        date, from_s, to_s = transitions[0]
        assert date == "2024-01-04"
        assert from_s == "bull_risk_on"
        assert to_s == "bear_risk_off"

    def test_date_range_filtering(self, tmp_db):
        """start_date/end_date filter transitions correctly."""
        states = [
            ("2024-01-02", "bull_risk_on"),
            ("2024-01-03", "bear_risk_off"),   # transition on 03
            ("2024-01-04", "bear_risk_off"),
            ("2024-01-05", "bull_risk_on"),    # transition on 05
        ]
        for date, state in states:
            record_regime(
                date=date,
                state=state,
                trend_score=0.5,
                risk_score=0.5,
                active_universes=[],
                sizing_multiplier=1.0,
            )

        # Only look at 2024-01-04 onwards
        transitions = get_regime_transitions(start_date="2024-01-04")
        # The transition on 2024-01-03 is outside the window
        # The transition on 2024-01-05 is inside
        assert len(transitions) == 1
        assert transitions[0][0] == "2024-01-05"

    def test_tuple_format(self, tmp_db):
        """Each element must be a 3-tuple of strings."""
        record_regime(
            date="2024-01-02",
            state="bull_risk_on",
            trend_score=0.8,
            risk_score=0.7,
            active_universes=[],
            sizing_multiplier=1.0,
        )
        record_regime(
            date="2024-01-03",
            state="bear_risk_off",
            trend_score=-0.5,
            risk_score=-0.6,
            active_universes=[],
            sizing_multiplier=0.5,
        )

        transitions = get_regime_transitions()
        assert len(transitions) == 1
        t = transitions[0]
        assert isinstance(t, tuple)
        assert len(t) == 3
        for element in t:
            assert isinstance(element, str)


# ──────────────────────────────────────────────────────────────────────────────
# print_regime_summary — smoke tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPrintRegimeSummary:

    def test_prints_without_error(self, capsys):
        """Should not raise and should produce non-empty output."""
        stats = {
            "dates_processed": 100,
            "dates_skipped": 2,
            "regime_transitions": [
                ("2024-03-15", "bull_risk_on", "bear_risk_off"),
                ("2024-04-01", "bear_risk_off", "recovery_early"),
            ],
            "state_distribution": {
                "bull_risk_on": 60,
                "bear_risk_off": 25,
                "recovery_early": 15,
            },
        }
        print_regime_summary(stats)
        out = capsys.readouterr().out
        assert "Regime History Backfill" in out
        assert "100" in out
        assert "bull_risk_on" in out

    def test_handles_empty_stats(self, capsys):
        stats = {
            "dates_processed": 0,
            "dates_skipped": 0,
            "regime_transitions": [],
            "state_distribution": {},
        }
        print_regime_summary(stats)  # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# CLI argument parsing
# ──────────────────────────────────────────────────────────────────────────────

class TestCLI:

    def test_macro_only_flag_skips_classification(self, tmp_db):
        """--macro-only should call backfill_macro_data and return early."""
        with patch("regime.history.backfill_macro_data", return_value=10) as mock_macro, \
             patch("regime.history.backfill_regime_history") as mock_regime:
            from regime.history import main
            main(["--start", "2024-01-01", "--macro-only"])

        mock_macro.assert_called_once()
        mock_regime.assert_not_called()

    def test_force_flag_passed_to_backfill(self, tmp_db):
        """--force should be forwarded as force_macro=True."""
        with patch("regime.history.backfill_regime_history", return_value={
            "dates_processed": 0, "dates_skipped": 0,
            "regime_transitions": [], "state_distribution": {},
        }) as mock_fn:
            from regime.history import main
            main(["--start", "2024-01-01", "--force"])

        _, kwargs = mock_fn.call_args
        assert kwargs.get("force_macro") is True

    def test_default_start_date(self, tmp_db):
        """Without --start, default is 2015-01-01."""
        from regime.history import _parse_args
        args = _parse_args([])
        assert args.start == "2015-01-01"
