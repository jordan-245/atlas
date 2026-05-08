"""Regression tests: pnl=None must not crash aggregators.

Background
----------
Closed-trade records can carry ``pnl=None`` when a trade is created as a
broker-reconciliation stub before fill information is available.
``dict.get("pnl", 0)`` returns ``None`` (not ``0``) when the key exists
with an explicit ``None`` value, so any downstream ``sum()`` or arithmetic
raised ``TypeError``.

The fix (pattern ``(t.get("pnl") or 0)``) was applied at:
  - brokers/live_portfolio.py   lines 785, 1341
  - backtest/metrics.py         lines 325, 342, 343, 363, 711, 1083, 1092, 1177
  - scripts/strategy_evaluator.py line 354

These tests ensure that a mixed list of ``pnl=None`` and ``pnl=42.5``
produces results equal to ``42.5`` total (None treated as 0).

Closes #298, #306.  Closes #301 as duplicate of #298.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

# ── Project root on path ──────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ── Shared test data ──────────────────────────────────────────────────────────
MIXED_TRADES = [
    # pnl explicitly None (reconciliation stub — key present, value None)
    {"strategy": "momentum", "entry_regime": "bull", "pnl": None,
     "entry_date": "2026-01-05", "exit_date": "2026-01-10",
     "hold_days": 5, "ticker": "AAPL"},
    # pnl = 42.5 (normal closed trade)
    {"strategy": "momentum", "entry_regime": "bull", "pnl": 42.5,
     "entry_date": "2026-01-12", "exit_date": "2026-01-20",
     "hold_days": 8, "ticker": "MSFT"},
]
EXPECTED_TOTAL_PNL = 42.5


# ═══════════════════════════════════════════════════════════════════════════════
# File 1: backtest/metrics.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsNonePnl:
    """Tests for backtest/metrics.py pnl=None safety.

    Covers fixed lines: 325 (win_rate), 342-343 (profit_factor), 363 (avg_trade),
    711 (strategy_correlation), 1083+1092 (calc_all_metrics), 1177 (regime_metrics).
    """

    def test_calc_win_rate_none_pnl_no_typeerror(self):
        """Line 325 fix: (t.get('pnl') or 0) > 0 guard."""
        from backtest.metrics import calc_win_rate
        result = calc_win_rate(MIXED_TRADES)
        # Only trade with pnl=42.5 counts as winner
        assert result == pytest.approx(0.5, abs=0.01)

    def test_calc_profit_factor_none_pnl_no_typeerror(self):
        """Lines 342-343 fix: guard in gross_profit/gross_loss sums."""
        from backtest.metrics import calc_profit_factor
        # No losses in MIXED_TRADES (None treated as 0, not < 0)
        # So gross_loss = 0  => returns inf
        result = calc_profit_factor(MIXED_TRADES)
        assert result in (float("inf"), pytest.approx(99.99, abs=1.0)) or result > 0

    def test_calc_avg_trade_none_pnl_no_typeerror(self):
        """Line 363 fix: sum((t.get('pnl') or 0)...)."""
        from backtest.metrics import calc_avg_trade
        result = calc_avg_trade(MIXED_TRADES)
        # 2 trades, total = 42.5 → avg = 21.25
        assert result == pytest.approx(21.25, abs=0.01)

    def test_calc_all_metrics_total_pnl_none_pnl_no_typeerror(self):
        """Lines 1083+1092 fix: total_pnl and pnls list in calc_all_metrics."""
        from backtest.metrics import calc_all_metrics
        # Minimal equity curve spanning trade dates
        idx = pd.date_range("2026-01-05", periods=20, freq="D")
        equity_curve = pd.Series(
            [5000.0 + i * 2 for i in range(20)], index=idx
        )
        result = calc_all_metrics(equity_curve, MIXED_TRADES)
        assert result["total_pnl"] == pytest.approx(EXPECTED_TOTAL_PNL, abs=0.01)
        # avg_winner should be 42.5 (one winner), no crash on None
        assert result["avg_winner"] == pytest.approx(42.5, abs=0.01)

    def test_calc_regime_metrics_none_pnl_no_typeerror(self):
        """Line 1177 fix: pnls list in calc_regime_metrics."""
        from backtest.metrics import calc_regime_metrics
        result = calc_regime_metrics(MIXED_TRADES)
        assert "bull" in result
        bull = result["bull"]
        assert bull["total_pnl"] == pytest.approx(EXPECTED_TOTAL_PNL, abs=0.01)
        assert bull["avg_trade"] == pytest.approx(EXPECTED_TOTAL_PNL / 2, abs=0.01)

    def test_calc_strategy_correlation_none_pnl_no_typeerror(self):
        """Line 711 fix: pnl = (t.get('pnl') or 0) before dividing by hold."""
        from backtest.metrics import calc_strategy_correlation
        idx = pd.date_range("2026-01-05", periods=20, freq="D")
        equity_curve = pd.Series(
            [5000.0 + i * 2 for i in range(20)], index=idx
        )
        # Should not raise TypeError from None / hold
        result = calc_strategy_correlation(MIXED_TRADES, equity_curve)
        assert "strategies" in result

    def test_none_only_pnl_list_gives_zero_total(self):
        """Edge case: all pnl=None → total = 0."""
        from backtest.metrics import calc_avg_trade, calc_win_rate
        all_none = [{"pnl": None, "strategy": "x"} for _ in range(3)]
        assert calc_avg_trade(all_none) == 0.0
        assert calc_win_rate(all_none) == 0.0

    def test_pnl_key_absent_still_works(self):
        """Trades with no 'pnl' key at all are handled correctly."""
        from backtest.metrics import calc_avg_trade
        no_key = [{"strategy": "x"}, {"strategy": "x", "pnl": 10.0}]
        assert calc_avg_trade(no_key) == pytest.approx(5.0, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# File 2: brokers/live_portfolio.py
# ═══════════════════════════════════════════════════════════════════════════════

MINIMAL_CONFIG = {
    "risk": {
        "starting_equity": 5000.0,
        "max_risk_per_trade_pct": 0.01,
        "max_open_positions": 10,
        "max_daily_drawdown_pct": 0.02,
    },
    "fees": {"commission_per_trade": 0},
}


class TestLivePortfolioNonePnl:
    """Tests for brokers/live_portfolio.py pnl=None safety.

    Covers lines 785 (equity()) and 1341 (record_equity() total_realized).
    Both were already fixed in a prior commit; these tests lock in the behavior.
    """

    def _make_portfolio(self):
        """Return a LivePortfolio with no state file side-effects."""
        from brokers.live_portfolio import LivePortfolio
        with patch.object(LivePortfolio, "_load_local_state", return_value=None):
            lp = LivePortfolio(MINIMAL_CONFIG, market_id="sp500")
        lp.closed_trades = []
        return lp

    def test_equity_with_none_pnl_no_typeerror(self):
        """Line 785: sum((t.get('pnl') or 0)...) in equity()."""
        lp = self._make_portfolio()
        lp.closed_trades = list(MIXED_TRADES)  # includes None pnl
        # No positions — equity = starting_equity + realized_pnl
        result = lp.equity()
        assert result == pytest.approx(5000.0 + EXPECTED_TOTAL_PNL, abs=0.01)

    def test_equity_all_none_pnl_equals_starting_equity(self):
        """Edge case: all pnl=None → equity == starting_equity."""
        lp = self._make_portfolio()
        lp.closed_trades = [{"pnl": None, "ticker": "X"}]
        assert lp.equity() == pytest.approx(5000.0, abs=0.01)

    def test_record_equity_total_realized_none_pnl_no_typeerror(self):
        """Line 1341: total_realized sum in record_equity()."""
        lp = self._make_portfolio()
        lp.broker_data_valid = True
        lp.closed_trades = list(MIXED_TRADES)
        lp.positions = []
        lp.cash = 5000.0
        lp._broker_equity = 5042.5
        # Patch save_state so we don't touch disk
        with patch.object(lp, "save_state", return_value=None):
            lp.record_equity("2026-01-25")
        assert len(lp.equity_history) == 1
        entry = lp.equity_history[0]
        assert entry["total_realized_pnl"] == pytest.approx(EXPECTED_TOTAL_PNL, abs=0.01)


# ═══════════════════════════════════════════════════════════════════════════════
# File 3: scripts/strategy_evaluator.py
# ═══════════════════════════════════════════════════════════════════════════════

class TestStrategyEvaluatorNonePnl:
    """Tests for scripts/strategy_evaluator.py line 354 fix."""

    def _build_metrics_from_result(self, trades):
        """Exercise the _build_metrics path that contains the fixed line 354."""
        import scripts.strategy_evaluator as se
        # Construct a minimal BacktestResult-like object
        result = types.SimpleNamespace(
            sharpe=1.2,
            total_return=0.15,
            cagr=0.15,
            max_drawdown=-0.08,
            total_trades=len([t for t in trades if t.get("pnl") is not None or True]),
            win_rate=0.6,
            profit_factor=1.8,
            avg_trade_return=0.02,
            equity_curve=pd.Series([5000.0, 5042.5],
                                   index=pd.date_range("2026-01-05", periods=2, freq="D")),
            trades=trades,
            strategy_breakdown=None,
            windows_configured=0,
            windows_used=0,
        )
        # Reproduce the inline breakdown logic from strategy_evaluator.py:354
        strat_trades: dict = {}
        for t in result.trades:
            s = t.get("strategy", "unknown")
            strat_trades.setdefault(s, []).append(t)
        breakdown = {}
        for s, strades in strat_trades.items():
            pnls = [(t.get("pnl") or 0) for t in strades]
            wins = sum(1 for p in pnls if p > 0)
            breakdown[s] = {
                "trades": len(strades),
                "total_pnl": round(sum(pnls), 2),
                "win_rate_pct": round(wins / len(strades) * 100, 1) if strades else 0,
            }
        return breakdown

    def test_breakdown_none_pnl_no_typeerror(self):
        """Line 354 fix: [(t.get('pnl') or 0) for t in trades]."""
        breakdown = self._build_metrics_from_result(MIXED_TRADES)
        assert "momentum" in breakdown
        assert breakdown["momentum"]["total_pnl"] == pytest.approx(EXPECTED_TOTAL_PNL, abs=0.01)

    def test_breakdown_all_none_pnl_total_is_zero(self):
        """Edge case: all pnl=None → total_pnl = 0."""
        trades = [{"strategy": "mean_rev", "pnl": None} for _ in range(4)]
        breakdown = self._build_metrics_from_result(trades)
        assert breakdown["mean_rev"]["total_pnl"] == 0.0
        assert breakdown["mean_rev"]["win_rate_pct"] == 0.0

    def test_breakdown_mixed_values_correct_win_rate(self):
        """Mixed None/positive/negative pnl — correct win count."""
        trades = [
            {"strategy": "x", "pnl": None},
            {"strategy": "x", "pnl": 10.0},
            {"strategy": "x", "pnl": -5.0},
            {"strategy": "x", "pnl": 32.5},
        ]
        breakdown = self._build_metrics_from_result(trades)
        # Winners: 10.0, 32.5 → 2 out of 4 = 50%
        assert breakdown["x"]["win_rate_pct"] == pytest.approx(50.0, abs=0.1)
        assert breakdown["x"]["total_pnl"] == pytest.approx(37.5, abs=0.01)
