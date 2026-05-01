"""Regression tests for cross-market ghost position / phantom HALT bug.

Bug class: CROSS-MARKET HWM INCOMPATIBILITY
  A ticker (e.g. FCX) placed in the WRONG market's live state file causes
  the FIX-PMEQ-001 per-market equity formula to omit that position's MV.
  If the HWM was calibrated when the position was counted (via old proportional
  formula or via a correct snapshot), the formula returns a lower equity →
  phantom drawdown → false HALT.

  Root causes confirmed:
    1. FCX was in live_sp500.json instead of live_commodity_etfs.json.
    2. markets/etf_markets.py::CommodityETFsMarket.get_universe_tickers()
       did NOT include FCX, so _refresh_from_broker() filtered it out even
       after moving the state entry.

  Fixes committed 2026-05-01:
    1. Moved FCX from live_sp500.json → live_commodity_etfs.json
    2. Added FCX to CommodityETFsMarket.get_universe_tickers()
    3. Added check_state_file_universes() to universe/membership.py
    4. Added scripts/validate_state_universes.py

Tests:
  1. Phantom drawdown reproduction: FCX missing from positions → per_market_eq
     understated relative to HWM → phantom dd > max_daily_dd threshold.
  2. Fix verification: FCX in positions, formula ≈ HWM, dd ≈ 0, halted=False.
  3. check_state_file_universes() detection function catches the violation.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root is on sys.path
_ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_state_dir(tmp_path: Path) -> Path:
    """Return a temporary directory for state files."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    return state_dir


def _make_state_file(
    state_dir: Path,
    market_id: str,
    positions: list[dict],
    daily_high_water: float = 1297.55,
    daily_high_water_date: str | None = None,
) -> Path:
    """Write a minimal live_{market_id}.json state file."""
    path = state_dir / f"live_{market_id}.json"
    state: dict[str, Any] = {
        "market_id": market_id,
        "mode": "live",
        "positions": positions,
        "closed_trades": [],
        "equity_history": [],
        "daily_high_water": daily_high_water,
        "daily_high_water_date": daily_high_water_date,
        "halted": False,
        "halt_reason": "",
        "last_saved": "2026-04-30T09:36:12.995503",
    }
    path.write_text(json.dumps(state, indent=2))
    return path


_FCX_ENTRY = {
    "ticker": "FCX",
    "strategy": "connors_rsi2",
    "entry_date": "2026-04-30",
    "entry_price": 57.59,
    "shares": 5,
    "stop_price": 55.94,
    "order_id": "",
    "stop_order_id": "cd1225ee-7730-4a34-ae84-356057a28882",
    "tp_order_id": "",
}

_GLD_ENTRY = {
    "ticker": "GLD",
    "strategy": "momentum_breakout",
    "entry_date": "2026-04-30",
    "entry_price": 442.8,
    "shares": 2,
    "stop_price": 403.32,
    "order_id": "",
    "stop_order_id": "7444fc32-15da-44f1-89de-e8755d44bc10",
    "tp_order_id": "",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakePosition:
    """Minimal position mock for LivePortfolio.positions."""
    def __init__(self, ticker: str, shares: float, entry_price: float):
        self.ticker = ticker
        self.shares = shares
        self.entry_price = entry_price
        self.current_price = 0.0
        self.stop_price = 0.0
        self.take_profit = None
        self.strategy = "test"
        self.entry_date = "2026-04-30"
        self.sector = "Unknown"
        self.confidence = 1.0
        self.rationale = "test"
        self.entry_value = shares * entry_price


class _DictRow:
    """Sqlite3.Row-like object backed by a plain dict."""
    def __init__(self, data: dict) -> None:
        self._data = data

    def __getitem__(self, key: str):
        return self._data[key]


def _mock_db_context(row_data: dict) -> MagicMock:
    """Return a mock context manager that yields a DB with one fetchone row."""
    mock_db = MagicMock()
    mock_db.execute.return_value.fetchone.return_value = _DictRow(row_data)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = lambda s: mock_db
    mock_ctx.__exit__ = MagicMock(return_value=False)
    return mock_ctx


_SNAP_ROW = {
    "allocated_equity": 1280.8,
    "broker_equity": 5134.19,
    "date": "2026-04-29",
    "position_mv": 1119.47,    # Snapshot included GLD + FCX
    "cash_attributed": 161.33,
    "snapshot_time": "2026-04-29T22:01:06+00:00",
}


# ---------------------------------------------------------------------------
# Test 1: Phantom drawdown reproduction (pre-fix state)
# ---------------------------------------------------------------------------

class TestPhantomDrawdownReproduction:
    """Verify the bug exists when FCX is in the WRONG state file."""

    def test_missing_fcx_causes_understated_per_market_equity(
        self, tmp_state_dir: Path
    ) -> None:
        """FCX missing from self.positions → pos_mv understated vs HWM → phantom dd > 20%."""
        _make_state_file(
            tmp_state_dir,
            "commodity_etfs",
            positions=[_GLD_ENTRY],       # FCX missing — the bug state
            daily_high_water=1297.55,
            daily_high_water_date="2026-05-01",
        )

        from universe.membership import clear_cache
        clear_cache()

        from utils.config import get_active_config
        from brokers.live_portfolio import LivePortfolio

        cfg = get_active_config("commodity_etfs")
        prices = {"GLD": 420.98, "FCX": 57.78}

        with patch("brokers.live_portfolio._STATE_DIR", tmp_state_dir):
            lp = LivePortfolio(cfg, market_id="commodity_etfs")

        # Only GLD in positions — simulating the bug
        lp.positions = [_FakePosition("GLD", 2, 442.8)]
        lp._broker_equity = 5134.0
        lp.broker_data_valid = True
        lp._broker = MagicMock()

        with (
            patch("db.atlas_db.get_db", return_value=_mock_db_context(_SNAP_ROW)),
            patch(
                "portfolio.per_market_cash_flow.compute_realized_cash_flow_since",
                return_value=({"commodity_etfs": 0.0}, False),
            ),
        ):
            eq = lp._get_per_market_equity(lp._broker_equity, prices)

        # Without FCX: GLD 2×420.98 + cash 161.33 = 1003.29 (understated)
        assert eq is not None, "Expected a numeric per_market_eq"
        assert eq < 1297.55 - 150, (
            f"Expected per_market_eq well below HWM $1297.55 when FCX missing, got ${eq:.2f}"
        )
        # Phantom drawdown exceeds max_daily_dd=2% → false HALT
        phantom_dd = (1297.55 - eq) / 1297.55
        assert phantom_dd > 0.18, (
            f"Expected phantom dd > 18% to confirm bug reproduction, got {phantom_dd:.1%}"
        )

    def test_correct_fcx_placement_per_market_eq_near_hwm(
        self, tmp_state_dir: Path
    ) -> None:
        """FCX in self.positions → per_market_eq ≈ HWM, dd ≈ 0%."""
        _make_state_file(
            tmp_state_dir,
            "commodity_etfs",
            positions=[_GLD_ENTRY, _FCX_ENTRY],   # FCX correctly present
            daily_high_water=1297.55,
            daily_high_water_date=None,             # force session reset
        )

        from universe.membership import clear_cache
        clear_cache()

        from utils.config import get_active_config
        from brokers.live_portfolio import LivePortfolio

        cfg = get_active_config("commodity_etfs")
        prices = {"GLD": 420.98, "FCX": 57.78}

        with patch("brokers.live_portfolio._STATE_DIR", tmp_state_dir):
            lp = LivePortfolio(cfg, market_id="commodity_etfs")

        lp.positions = [
            _FakePosition("GLD", 2, 442.8),
            _FakePosition("FCX", 5, 57.59),
        ]
        lp._broker_equity = 5134.0
        lp.broker_data_valid = True
        lp._broker = MagicMock()

        with (
            patch("db.atlas_db.get_db", return_value=_mock_db_context(_SNAP_ROW)),
            patch(
                "portfolio.per_market_cash_flow.compute_realized_cash_flow_since",
                return_value=({"commodity_etfs": 0.0}, False),
            ),
        ):
            eq = lp._get_per_market_equity(lp._broker_equity, prices)

        # With FCX: GLD 2×420.98 + FCX 5×57.78 + cash 161.33 = 1292.19
        assert eq is not None, "Expected numeric per_market_eq"
        assert abs(eq - 1297.55) < 10, (
            f"Expected per_market_eq ≈ HWM $1297.55, got ${eq:.2f} (delta ${eq - 1297.55:+.2f})"
        )

    def test_halted_false_with_correct_fcx_placement(
        self, tmp_state_dir: Path
    ) -> None:
        """Full check_daily_drawdown returns halted=False, dd≈0 when FCX is correct."""
        _make_state_file(
            tmp_state_dir,
            "commodity_etfs",
            positions=[_GLD_ENTRY, _FCX_ENTRY],
            daily_high_water=1297.55,
            daily_high_water_date=None,  # force session reset
        )

        from universe.membership import clear_cache
        clear_cache()

        from utils.config import get_active_config
        from brokers.live_portfolio import LivePortfolio

        cfg = get_active_config("commodity_etfs")
        prices = {"GLD": 420.98, "FCX": 57.78}

        with patch("brokers.live_portfolio._STATE_DIR", tmp_state_dir):
            lp = LivePortfolio(cfg, market_id="commodity_etfs")

        lp.positions = [
            _FakePosition("GLD", 2, 442.8),
            _FakePosition("FCX", 5, 57.59),
        ]
        lp._broker_equity = 5134.0
        lp.broker_data_valid = True
        lp._broker = MagicMock()

        with (
            patch("db.atlas_db.get_db", return_value=_mock_db_context(_SNAP_ROW)),
            patch(
                "portfolio.per_market_cash_flow.compute_realized_cash_flow_since",
                return_value=({"commodity_etfs": 0.0}, False),
            ),
            # Suppress Telegram notification from HWM reset
            patch("utils.telegram.send_message", return_value=None),
            # Suppress save_state write
            patch.object(LivePortfolio, "save_state", return_value=None),
        ):
            halted, dd = lp.check_daily_drawdown(prices)

        assert not halted, f"Expected halted=False, got True (dd={dd:.2%})"
        assert dd < 0.01, f"Expected dd < 1%, got {dd:.2%}"


# ---------------------------------------------------------------------------
# Test 2: Fix verification — markets/etf_markets.py consistency
# ---------------------------------------------------------------------------

class TestMarketsModuleConsistency:
    """CommodityETFsMarket.get_universe_tickers() must include FCX."""

    def test_fcx_in_commodity_etfs_market(self) -> None:
        from markets import get_market

        tickers = get_market("commodity_etfs").get_formatted_tickers()
        assert "FCX" in tickers, (
            f"FCX must be in CommodityETFsMarket.get_universe_tickers(). "
            f"Got: {tickers}"
        )

    def test_commodity_etfs_markets_matches_definitions(self) -> None:
        """markets/etf_markets.py must cover all tickers in universe/definitions.py."""
        from markets import get_market
        from universe.definitions import UNIVERSES

        mkt_tickers = set(get_market("commodity_etfs").get_formatted_tickers())
        def_tickers = set(UNIVERSES.get("commodity_etfs", {}).get("tickers", []))

        missing_from_markets = def_tickers - mkt_tickers
        assert not missing_from_markets, (
            f"Tickers in universe/definitions.py but missing from "
            f"markets/etf_markets.py: {missing_from_markets}. "
            f"This will cause _refresh_from_broker() to silently drop positions."
        )

    def test_derive_universe_fcx_returns_commodity_etfs(self) -> None:
        from universe.membership import derive_universe, clear_cache

        clear_cache()
        universe = derive_universe("FCX")
        assert universe == "commodity_etfs", (
            f"derive_universe('FCX') should return 'commodity_etfs', got {universe!r}"
        )


# ---------------------------------------------------------------------------
# Test 3: check_state_file_universes() detection function
# ---------------------------------------------------------------------------

class TestCheckStateFileUniverses:
    """Tests for universe/membership.py::check_state_file_universes()."""

    def test_clean_state_returns_empty_list(self, tmp_state_dir: Path) -> None:
        """No violations when positions are in their canonical universe."""
        from universe.membership import clear_cache, check_state_file_universes

        clear_cache()
        _make_state_file(tmp_state_dir, "commodity_etfs", [_GLD_ENTRY, _FCX_ENTRY])
        _make_state_file(tmp_state_dir, "sp500", [
            {"ticker": "CAT", "strategy": "momentum_breakout",
             "entry_date": "2026-04-30", "entry_price": 835.24,
             "shares": 1, "stop_price": 861.21, "order_id": "",
             "stop_order_id": "", "tp_order_id": ""}
        ])

        violations = check_state_file_universes(tmp_state_dir)
        assert violations == [], f"Expected no violations, got: {violations}"

    def test_cross_market_fcx_detected(self, tmp_state_dir: Path) -> None:
        """FCX in sp500 state file should be reported as a violation."""
        from universe.membership import clear_cache, check_state_file_universes

        clear_cache()
        _make_state_file(
            tmp_state_dir, "sp500",
            [
                {"ticker": "CAT", "strategy": "momentum_breakout",
                 "entry_date": "2026-04-30", "entry_price": 835.24,
                 "shares": 1, "stop_price": 861.21,
                 "order_id": "", "stop_order_id": "", "tp_order_id": ""},
                _FCX_ENTRY,  # WRONG: FCX should be in commodity_etfs
            ]
        )
        _make_state_file(tmp_state_dir, "commodity_etfs", [_GLD_ENTRY])

        violations = check_state_file_universes(tmp_state_dir)
        tickers_violated = [v["ticker"] for v in violations]
        assert "FCX" in tickers_violated, (
            f"Expected FCX to be flagged as cross-market violation, got: {violations}"
        )
        fcx_violation = next(v for v in violations if v["ticker"] == "FCX")
        assert fcx_violation["market_id"] == "sp500"
        assert fcx_violation["canonical_universe"] == "commodity_etfs"

    def test_violation_contains_expected_fields(self, tmp_state_dir: Path) -> None:
        """Each violation dict must have the required fields."""
        from universe.membership import clear_cache, check_state_file_universes

        clear_cache()
        _make_state_file(tmp_state_dir, "sp500", [_FCX_ENTRY])

        violations = check_state_file_universes(tmp_state_dir)
        assert violations, "Expected at least one violation"

        for v in violations:
            assert "file" in v, f"Missing 'file' key in violation: {v}"
            assert "market_id" in v, f"Missing 'market_id' key in violation: {v}"
            assert "ticker" in v, f"Missing 'ticker' key in violation: {v}"
            assert "canonical_universe" in v, f"Missing 'canonical_universe' in violation: {v}"

    def test_empty_positions_no_violation(self, tmp_state_dir: Path) -> None:
        """State files with empty positions lists should never raise violations."""
        from universe.membership import clear_cache, check_state_file_universes

        clear_cache()
        _make_state_file(tmp_state_dir, "sp500", positions=[])
        _make_state_file(tmp_state_dir, "commodity_etfs", positions=[])

        violations = check_state_file_universes(tmp_state_dir)
        assert violations == []

    def test_multiple_violations_detected(self, tmp_state_dir: Path) -> None:
        """All cross-market positions (not just the first) are reported."""
        from universe.membership import clear_cache, check_state_file_universes

        clear_cache()
        # Both GLD and FCX in sp500 — both are commodity_etfs members
        _make_state_file(tmp_state_dir, "sp500", [_GLD_ENTRY, _FCX_ENTRY])

        violations = check_state_file_universes(tmp_state_dir)
        violated_tickers = {v["ticker"] for v in violations}
        assert "GLD" in violated_tickers, f"Expected GLD violation, got: {violated_tickers}"
        assert "FCX" in violated_tickers, f"Expected FCX violation, got: {violated_tickers}"

    def test_validate_script_exits_zero_on_clean_state(
        self, tmp_path: Path
    ) -> None:
        """scripts/validate_state_universes.py exits 0 when state is clean."""
        import subprocess

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _make_state_file(state_dir, "commodity_etfs", [_GLD_ENTRY, _FCX_ENTRY])
        _make_state_file(state_dir, "sp500", [])

        result = subprocess.run(
            [sys.executable,
             str(_ATLAS_ROOT / "scripts" / "validate_state_universes.py"),
             "--state-dir", str(state_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 for clean state, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "CLEAN" in result.stdout

    def test_validate_script_exits_one_on_violation(
        self, tmp_path: Path
    ) -> None:
        """scripts/validate_state_universes.py exits 1 when cross-market positions exist."""
        import subprocess

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        _make_state_file(state_dir, "sp500", [_FCX_ENTRY])   # FCX in wrong file

        result = subprocess.run(
            [sys.executable,
             str(_ATLAS_ROOT / "scripts" / "validate_state_universes.py"),
             "--state-dir", str(state_dir)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 1, (
            f"Expected exit 1 for violation, got {result.returncode}.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "FCX" in result.stdout or "FCX" in result.stderr
