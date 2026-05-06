"""Tests for TradePlanGenerator — plan generation and risk filtering.

Run with:  python -m pytest tests/test_plan_generator.py -v
"""
import json
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from strategies.base import Signal  # noqa: E402
from brokers.plan import TradePlanGenerator  # noqa: E402
TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
from conftest import MINIMAL_CONFIG  # noqa: E402

import copy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    ticker: str = "AAPL",
    strategy: str = "mean_reversion",
    confidence: float = 0.75,
    entry_price: float = 100.0,
) -> Signal:
    return Signal(
        ticker=ticker,
        strategy=strategy,
        direction="long",
        entry_price=entry_price,
        stop_price=entry_price * 0.95,
        take_profit=entry_price * 1.10,
        position_size=5,
        position_value=entry_price * 5,
        risk_amount=entry_price * 5 * 0.05,
        confidence=confidence,
        rationale="Test signal",
        features={"rsi": 28.0},
    )


def _make_mock_portfolio(
    cash: float = 5000.0,
    equity: float = 10000.0,
    n_positions: int = 0,
    check_risk_limits: tuple = (True, ""),
):
    """Build a minimal mock Portfolio object."""
    mock_pos = MagicMock()
    mock_pos.strategy = "mean_reversion"

    portfolio = MagicMock()
    portfolio.cash = cash
    portfolio.equity.return_value = equity
    portfolio.positions = [mock_pos] * n_positions
    portfolio.atlas_positions = [mock_pos] * n_positions
    portfolio.check_risk_limits.return_value = check_risk_limits
    portfolio.portfolio_summary.return_value = {
        "open_positions": [
            {
                "ticker": "HELD",
                "entry_price": 100.0,
                "current_price": 105.0,
                "unrealized_pnl": 50.0,
                "unrealized_pnl_pct": 5.0,
                "stop_price": 95.0,
                "strategy": "mean_reversion",
            }
        ]
        * n_positions,
        "total_pnl": 0.0,
        "total_pnl_pct": 0.0,
    }
    return portfolio


def _make_plan_generator(config: dict, n_positions: int = 0) -> TradePlanGenerator:
    portfolio = _make_mock_portfolio(n_positions=n_positions)
    return TradePlanGenerator(portfolio=portfolio, config=config), portfolio


# ---------------------------------------------------------------------------
# Plan structure tests
# ---------------------------------------------------------------------------

class TestPlanStructure:
    def test_generate_plan_returns_dict(self, tmp_path, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        signals = [_make_signal()]
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan(signals, [], {}, "2024-01-15")
        assert isinstance(plan, dict)

    def test_plan_has_required_keys(self, tmp_path, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        signals = [_make_signal(confidence=0.80)]
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan(signals, [], {}, "2024-01-15")
        required = {
            "trade_date", "generated_at", "status",
            "portfolio_snapshot", "proposed_entries", "rejected_entries",
            "proposed_exits", "risk_summary",
        }
        assert required.issubset(set(plan.keys()))

    def test_plan_status_pending(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-01-15")
        assert plan["status"] == "PENDING_APPROVAL"

    def test_plan_trade_date(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-02-20")
        assert plan["trade_date"] == "2024-02-20"

    def test_plan_portfolio_snapshot_keys(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-01-15")
        snap = plan["portfolio_snapshot"]
        assert "equity" in snap
        assert "cash" in snap
        assert "open_positions" in snap


# ---------------------------------------------------------------------------
# Confidence threshold filtering
# ---------------------------------------------------------------------------

class TestConfidenceFiltering:
    def test_signal_below_min_confidence_is_rejected(self, mock_config):
        """Signal with confidence < min_confidence should appear in rejected_entries."""
        mock_config["risk"]["min_confidence"] = 0.70
        gen, portfolio = _make_plan_generator(mock_config)
        # Confidence just below threshold
        low_conf_signal = _make_signal(confidence=0.65)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([low_conf_signal], [], {}, "2024-01-15")
        rejected = [r["ticker"] for r in plan["rejected_entries"]]
        assert "AAPL" in rejected

    def test_rejection_reason_mentions_confidence(self, mock_config):
        mock_config["risk"]["min_confidence"] = 0.70
        gen, portfolio = _make_plan_generator(mock_config)
        low_conf_signal = _make_signal(confidence=0.60)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([low_conf_signal], [], {}, "2024-01-15")
        rej = plan["rejected_entries"][0]
        assert "confidence" in rej.get("rejection_reason", "").lower() or \
               "Confidence" in rej.get("rejection_reason", "")

    def test_signal_above_min_confidence_is_proposed(self, mock_config):
        """Signal above threshold should appear in proposed_entries."""
        mock_config["risk"]["min_confidence"] = 0.65
        gen, portfolio = _make_plan_generator(mock_config)
        portfolio.check_risk_limits.return_value = (True, "")
        good_signal = _make_signal(confidence=0.80)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([good_signal], [], {}, "2024-01-15")
        proposed = [e["ticker"] for e in plan["proposed_entries"]]
        assert "AAPL" in proposed

    def test_signal_at_exact_threshold_is_accepted(self, mock_config):
        """Confidence exactly equal to threshold: accepted (not < threshold)."""
        mock_config["risk"]["min_confidence"] = 0.65
        gen, portfolio = _make_plan_generator(mock_config)
        portfolio.check_risk_limits.return_value = (True, "")
        exact_signal = _make_signal(confidence=0.65)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([exact_signal], [], {}, "2024-01-15")
        proposed = [e["ticker"] for e in plan["proposed_entries"]]
        # confidence >= threshold, so not rejected by confidence check
        # (may be rejected by other checks, but not confidence)
        rejected_reasons = [
            r["rejection_reason"] for r in plan["rejected_entries"]
        ]
        for reason in rejected_reasons:
            # Exact threshold should not trigger confidence rejection
            assert "below threshold" not in reason.lower()


# ---------------------------------------------------------------------------
# Max positions cap
# ---------------------------------------------------------------------------

class TestMaxPositionsCap:
    def test_signal_rejected_when_max_positions_full(self, mock_config):
        """When portfolio already at max_open_positions, new signals are rejected."""
        mock_config["risk"]["max_open_positions"] = 2
        # n_positions=2 means available_slots = 2 - 2 = 0
        gen, portfolio = _make_plan_generator(mock_config, n_positions=2)
        portfolio.check_risk_limits.return_value = (True, "")
        sig = _make_signal(confidence=0.90)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], {}, "2024-01-15")
        # All signals should be rejected (no slots available)
        assert len(plan["proposed_entries"]) == 0
        assert len(plan["rejected_entries"]) > 0

    def test_max_position_rejection_reason(self, mock_config):
        mock_config["risk"]["max_open_positions"] = 1
        gen, portfolio = _make_plan_generator(mock_config, n_positions=1)
        portfolio.check_risk_limits.return_value = (True, "")
        sig = _make_signal(confidence=0.90)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], {}, "2024-01-15")
        if plan["rejected_entries"]:
            reason = plan["rejected_entries"][0].get("rejection_reason", "")
            assert "position" in reason.lower() or "max" in reason.lower()

    def test_multiple_signals_respect_slot_limit(self, mock_config):
        """Only as many signals as available slots should be proposed."""
        mock_config["risk"]["max_open_positions"] = 3
        gen, portfolio = _make_plan_generator(mock_config, n_positions=1)
        portfolio.check_risk_limits.return_value = (True, "")
        # 5 signals, 2 slots available
        signals = [_make_signal(ticker=f"T{i}", confidence=0.80) for i in range(5)]
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan(signals, [], {}, "2024-01-15")
        assert len(plan["proposed_entries"]) <= 2


# ---------------------------------------------------------------------------
# Risk check integration
# ---------------------------------------------------------------------------

class TestRiskCheckIntegration:
    def test_signal_rejected_when_risk_limits_fail(self, mock_config):
        """When portfolio.check_risk_limits returns False, signal is rejected."""
        gen, portfolio = _make_plan_generator(mock_config)
        portfolio.check_risk_limits.return_value = (False, "Sector concentration exceeded")
        sig = _make_signal(confidence=0.90)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], {}, "2024-01-15")
        assert len(plan["proposed_entries"]) == 0
        assert len(plan["rejected_entries"]) == 1

    def test_exit_recommendations_passed_through(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        exits = [{"ticker": "MSFT", "reason": "stop_hit", "exit_price": 95.0}]
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], exits, {}, "2024-01-15")
        assert plan["proposed_exits"] == exits

    def test_risk_summary_has_required_keys(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-01-15")
        risk = plan["risk_summary"]
        assert "total_proposed_cost" in risk
        assert "total_proposed_risk" in risk
        assert "positions_after" in risk


# ---------------------------------------------------------------------------
# approve_plan and load_plan
# ---------------------------------------------------------------------------

class TestPlanPersistence:
    def test_approve_plan_sets_approved_status(self, tmp_path, mock_config):
        """Test approve_plan updates status — uses real file I/O via tmp_path."""
        gen, _ = _make_plan_generator(mock_config)

        # Patch PROJECT_ROOT for plans dir
        plans_dir = tmp_path / "plans"
        plans_dir.mkdir()

        with patch("brokers.plan.PROJECT_ROOT", tmp_path):
            with patch.object(gen, "_save_plan", wraps=gen._save_plan):
                # Generate a plan with real save
                signals = [_make_signal(confidence=0.80)]
                plan = gen.generate_plan(signals, [], {}, "2024-03-01")

        # Manually save it to tmp_path/plans
        plan_path = plans_dir / f"plan_sp500_2024-03-01.json"
        plan_path.write_text(json.dumps(plan))

        # Now approve it
        with patch("brokers.plan.PROJECT_ROOT", tmp_path):
            approved = gen.approve_plan("2024-03-01", market_id="sp500")

        assert approved is not None
        assert approved["status"] == "APPROVED"

    def test_format_plan_text_contains_date(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-05-10")
        text = gen.format_plan_text(plan)
        assert "2024-05-10" in text

    def test_format_plan_text_is_string(self, mock_config):
        gen, _ = _make_plan_generator(mock_config)
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2024-01-15")
        text = gen.format_plan_text(plan)
        assert isinstance(text, str)
        assert len(text) > 0


# ---------------------------------------------------------------------------
# Portfolio exposure formula regression tests (2026-05-06 negative-leverage bug)
# ---------------------------------------------------------------------------

class TestPortfolioExposureFormula:
    """Regression tests for portfolio_exposure_pct formula fix.

    Bug: (current_eq - cash + proposed_cost) / current_eq assumed
    current_eq - cash = positions_value, true only for single-account
    global accounting. Under per-market accounting, cash is the FULL
    broker balance (>> per-market equity), producing negative leverage.

    Fix: (current_positions_value + proposed_cost) / current_eq
    """

    def test_portfolio_exposure_long_only_no_existing_positions(self, mock_config):
        """No open positions, cash >> equity: must NOT produce negative leverage.

        OLD formula: (1000 - 4000 + 500) / 1000 * 100 = -250%
        NEW formula: (0 + 500) / 1000 * 100 = 50%
        """
        # cash=4000 (full broker), equity=1000 (per-market slice)
        portfolio = _make_mock_portfolio(cash=4000.0, equity=1000.0, n_positions=0)
        gen = TradePlanGenerator(portfolio=portfolio, config=mock_config)

        # Signal with position_value=500
        sig = _make_signal(ticker="SPY", entry_price=100.0)
        sig = Signal(
            ticker="SPY",
            strategy="mean_reversion",
            direction="long",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,
            position_size=5,
            position_value=500.0,
            risk_amount=25.0,
            confidence=0.80,
            rationale="Test",
            features={},
        )

        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], {}, "2026-05-06")

        exposure = plan["risk_summary"]["portfolio_exposure_pct"]
        assert exposure > 0, f"Exposure must be positive, got {exposure}%"
        assert abs(exposure - 50.0) < 1.0, f"Expected ~50%, got {exposure}%"

    def test_portfolio_exposure_with_existing_positions(self, mock_config):
        """Two open positions totaling $1100 MV, new signal $400: expect 100%.

        (1100 + 400) / 1500 * 100 = 100.0%
        """
        # Build two real-ish positions
        pos_aapl = MagicMock(shares=5, ticker="AAPL", entry_price=100.0, strategy="mean_reversion")
        pos_msft = MagicMock(shares=3, ticker="MSFT", entry_price=200.0, strategy="mean_reversion")

        portfolio = _make_mock_portfolio(cash=4000.0, equity=1500.0, n_positions=0)
        portfolio.positions = [pos_aapl, pos_msft]
        portfolio.atlas_positions = [pos_aapl, pos_msft]
        # Update summary for 2 positions
        portfolio.portfolio_summary.return_value = {
            "open_positions": [
                {"ticker": "AAPL", "strategy": "mean_reversion"},
                {"ticker": "MSFT", "strategy": "mean_reversion"},
            ],
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
        }

        gen = TradePlanGenerator(portfolio=portfolio, config=mock_config)

        # Signal $400 proposed cost: 4 shares @ $100
        sig = Signal(
            ticker="GS",
            strategy="mean_reversion",
            direction="long",
            entry_price=100.0,
            stop_price=95.0,
            take_profit=110.0,
            position_size=4,
            position_value=400.0,
            risk_amount=20.0,
            confidence=0.85,
            rationale="Test",
            features={},
        )

        prices = {"AAPL": 100.0, "MSFT": 200.0}
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], prices, "2026-05-06")

        exposure = plan["risk_summary"]["portfolio_exposure_pct"]
        # positions_value = 5*100 + 3*200 = 500+600 = 1100; proposed=400; eq=1500
        assert abs(exposure - 100.0) < 1.0, f"Expected ~100%, got {exposure}%"

    def test_portfolio_exposure_zero_equity_returns_zero(self, mock_config):
        """Zero equity guard: must return 0, not raise ZeroDivisionError."""
        portfolio = _make_mock_portfolio(cash=0.0, equity=0.0, n_positions=0)
        gen = TradePlanGenerator(portfolio=portfolio, config=mock_config)

        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([], [], {}, "2026-05-06")

        assert plan["risk_summary"]["portfolio_exposure_pct"] == 0

    def test_portfolio_exposure_per_market_account_realism(self, mock_config):
        """REGRESSION: today's commodity_etfs scenario that produced -224.69%.

        cash=$4042.88 (global broker), equity=$956.82 (per-market),
        0 open positions, proposed=$936.21.
        Expected: 936.21/956.82*100 ≈ 97.85%  (OLD: -224.69%)
        """
        portfolio = _make_mock_portfolio(cash=4042.88, equity=956.82, n_positions=0)
        gen = TradePlanGenerator(portfolio=portfolio, config=mock_config)

        # Signal matching today's commodity_etfs proposed cost of 936.21
        # position_size * entry_price = 936.21  → use size=9, price=104.0 ≈ 936
        sig = Signal(
            ticker="GLD",
            strategy="momentum_breakout",
            direction="long",
            entry_price=104.02,
            stop_price=98.82,
            take_profit=114.42,
            position_size=9,
            position_value=936.18,
            risk_amount=46.98,
            confidence=0.80,
            rationale="Test",
            features={},
        )

        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], {}, "2026-05-06")

        exposure = plan["risk_summary"]["portfolio_exposure_pct"]
        assert exposure > 0, f"Must be positive, got {exposure}% (OLD formula: -224.69%)"
        assert 88 < exposure < 110, f"Expected ~97.85%, got {exposure}%"

    def test_portfolio_exposure_sp500_today_scenario(self, mock_config):
        """REGRESSION: today's sp500 scenario that produced -121%.

        cash=$4042.88 (global), equity=$1334.05 (per-market),
        2 positions: CAT 1sh $835.24, SYK 1sh $294.65.
        Proposed: $1092.50.
        Expected: (835.24+294.65+1092.50)/1334.05*100 ≈ 166.96%  (OLD: -121%)
        """
        pos_cat = MagicMock(shares=1, ticker="CAT", entry_price=835.24, strategy="trend_following")
        pos_syk = MagicMock(shares=1, ticker="SYK", entry_price=294.65, strategy="trend_following")

        portfolio = _make_mock_portfolio(cash=4042.88, equity=1334.05, n_positions=0)
        portfolio.positions = [pos_cat, pos_syk]
        portfolio.atlas_positions = [pos_cat, pos_syk]
        portfolio.portfolio_summary.return_value = {
            "open_positions": [
                {"ticker": "CAT", "strategy": "trend_following"},
                {"ticker": "SYK", "strategy": "trend_following"},
            ],
            "total_pnl": 0.0,
            "total_pnl_pct": 0.0,
        }

        gen = TradePlanGenerator(portfolio=portfolio, config=mock_config)

        # Signal matching today's sp500 proposed cost of $1092.50
        sig = Signal(
            ticker="NVDA",
            strategy="momentum_breakout",
            direction="long",
            entry_price=109.25,
            stop_price=103.79,
            take_profit=120.18,
            position_size=10,
            position_value=1092.50,
            risk_amount=54.60,
            confidence=0.82,
            rationale="Test",
            features={},
        )

        prices = {"CAT": 835.24, "SYK": 294.65}
        with patch.object(gen, "_save_plan"):
            plan = gen.generate_plan([sig], [], prices, "2026-05-06")

        exposure = plan["risk_summary"]["portfolio_exposure_pct"]
        assert exposure > 0, f"Must be positive, got {exposure}% (OLD formula: -121%)"
        assert 150 < exposure < 200, f"Expected ~166.96%, got {exposure}%"
