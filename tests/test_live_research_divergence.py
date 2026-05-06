"""Tests for scripts/check_live_research_divergence.py"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

import scripts.check_live_research_divergence as m
from scripts.check_live_research_divergence import (
    _compute_live_sharpe,
    compute_divergences,
    format_telegram,
)


# ─── _compute_live_sharpe ──────────────────────────────────────────────────────

def test_compute_live_sharpe_normal():
    pnls = [1.0, 2.0, 3.0, 4.0, 5.0]
    s = _compute_live_sharpe(pnls)
    assert s is not None
    # mean=3, var=2.5, sd=sqrt(2.5); Sharpe = 3/sqrt(2.5) ≈ 1.897
    assert s > 0
    assert abs(s - 3.0 / math.sqrt(2.5)) < 1e-9


def test_compute_live_sharpe_negative():
    pnls = [-1.0, -2.0, -3.0]
    s = _compute_live_sharpe(pnls)
    assert s is not None
    assert s < 0


def test_compute_live_sharpe_too_few():
    assert _compute_live_sharpe([]) is None
    assert _compute_live_sharpe([1.0]) is None


def test_compute_live_sharpe_zero_stdev():
    assert _compute_live_sharpe([2.0, 2.0, 2.0]) is None


def test_compute_live_sharpe_two_trades():
    """Minimum valid input: exactly 2 trades."""
    s = _compute_live_sharpe([1.0, 3.0])
    assert s is not None
    # mean=2, var=2, sd=sqrt(2); Sharpe = 2/sqrt(2) = sqrt(2)
    assert abs(s - math.sqrt(2)) < 1e-9


# ─── format_telegram ──────────────────────────────────────────────────────────

def test_format_telegram_no_alerts():
    msg = format_telegram(
        [
            {
                "universe": "sp500",
                "strategy": "mr",
                "research_sharpe": 0.5,
                "live_sharpe": 0.4,
                "gap": 0.1,
                "live_trades": 10,
                "trust_score": 0.8,
                "severity": "🟢",
            }
        ],
        gap_threshold=0.5,
    )
    assert "No divergence" in msg
    assert "Healthy: 1" in msg


def test_format_telegram_with_red_alert():
    msg = format_telegram(
        [
            {
                "universe": "commodity_etfs",
                "strategy": "momentum_breakout",
                "research_sharpe": 1.32,
                "live_sharpe": -3.66,
                "gap": 4.98,
                "live_trades": 4,
                "trust_score": 0.0,
                "severity": "🔴",
            }
        ],
        gap_threshold=0.5,
    )
    assert "🔴" in msg
    assert "commodity_etfs/momentum_breakout" in msg
    assert "gap +4.98" in msg
    assert "trust 0.00" in msg
    assert "n=4" in msg


def test_format_telegram_yellow_alert():
    msg = format_telegram(
        [
            {
                "universe": "sp500",
                "strategy": "connors_rsi2",
                "research_sharpe": 0.80,
                "live_sharpe": 0.20,
                "gap": 0.60,
                "live_trades": 8,
                "trust_score": 0.25,
                "severity": "🟡",
            }
        ],
        gap_threshold=0.5,
    )
    assert "🟡" in msg
    assert "gap +0.60" in msg


def test_format_telegram_trust_none():
    """When research_sharpe <= 0, trust_score is None → prints 'trust n/a'."""
    msg = format_telegram(
        [
            {
                "universe": "sp500",
                "strategy": "bb_squeeze",
                "research_sharpe": -0.10,
                "live_sharpe": 0.50,
                "gap": -0.60,
                "live_trades": 6,
                "trust_score": None,
                "severity": "🟡",
            }
        ],
        gap_threshold=0.5,
    )
    assert "trust n/a" in msg


def test_format_telegram_html_escape():
    """'<' in threshold line must be escaped for Telegram HTML."""
    msg = format_telegram(
        [
            {
                "universe": "sp500",
                "strategy": "mr",
                "research_sharpe": 0.5,
                "live_sharpe": 0.4,
                "gap": 0.1,
                "live_trades": 10,
                "trust_score": 0.8,
                "severity": "🟢",
            }
        ],
        gap_threshold=0.5,
    )
    # Healthy line must use HTML entity, not raw '<'
    assert "&lt;" in msg


# ─── compute_divergences (smoking-gun + edge cases) ────────────────────────────

def test_compute_divergences_smoking_gun(monkeypatch):
    """Reproduce the audit smoking-gun row using mocked DB layer."""
    monkeypatch.setattr(
        m,
        "_fetch_research_best_rows",
        lambda: [
            {
                "universe": "commodity_etfs",
                "strategy": "momentum_breakout",
                "sharpe": 1.316,
                "trades": 556,
                "updated_at": "2026-05-05",
            },
            {
                "universe": "sp500",
                "strategy": "momentum_breakout",
                "sharpe": 0.749,
                "trades": 250,
                "updated_at": "2026-05-05",
            },
        ],
    )

    def _fake_trades(u: str, s: str, w: int):
        if u == "commodity_etfs":
            return [-4.99, -1.30, -9.39, -5.33]
        if u == "sp500":
            return [3.0, 2.5, 1.8, -0.5, 4.1, 2.2, 1.0]
        return []

    monkeypatch.setattr(m, "_fetch_live_trades", _fake_trades)
    monkeypatch.setattr(m, "MIN_TRADES_FOR_LIVE_SHARPE", 4)

    divs = compute_divergences()
    assert len(divs) == 2

    cm = next(d for d in divs if d["universe"] == "commodity_etfs")
    assert cm["severity"] == "🔴"
    assert cm["gap"] > 0.5
    assert cm["live_sharpe"] < 0
    assert cm["trust_score"] == 0.0  # clamped to 0 (live < 0)

    sp = next(d for d in divs if d["universe"] == "sp500")
    assert sp["live_sharpe"] > 0
    assert sp["severity"] == "🟢"

    # Sorted by gap descending — commodity_etfs first
    assert divs[0]["universe"] == "commodity_etfs"


def test_compute_divergences_insufficient_trades(monkeypatch):
    """Strategies with fewer than MIN_TRADES are excluded from output."""
    monkeypatch.setattr(
        m,
        "_fetch_research_best_rows",
        lambda: [
            {
                "universe": "sp500",
                "strategy": "trend_following",
                "sharpe": 1.0,
                "trades": 100,
                "updated_at": "2026-05-05",
            }
        ],
    )
    # Only 3 trades — below default MIN_TRADES_FOR_LIVE_SHARPE=5
    monkeypatch.setattr(m, "_fetch_live_trades", lambda u, s, w: [1.0, 2.0, 3.0])
    divs = compute_divergences()
    assert divs == []


def test_compute_divergences_zero_stdev_excluded(monkeypatch):
    """Strategies with zero stdev in live returns are excluded (Sharpe undefined)."""
    monkeypatch.setattr(
        m,
        "_fetch_research_best_rows",
        lambda: [
            {
                "universe": "sp500",
                "strategy": "constant_winner",
                "sharpe": 1.0,
                "trades": 50,
                "updated_at": "2026-05-05",
            }
        ],
    )
    monkeypatch.setattr(
        m, "_fetch_live_trades", lambda u, s, w: [2.0, 2.0, 2.0, 2.0, 2.0]
    )
    divs = compute_divergences()
    assert divs == []


def test_compute_divergences_negative_research_sharpe(monkeypatch):
    """When research Sharpe <= 0, trust_score is None."""
    monkeypatch.setattr(
        m,
        "_fetch_research_best_rows",
        lambda: [
            {
                "universe": "crypto",
                "strategy": "mean_reversion",
                "sharpe": -0.50,
                "trades": 30,
                "updated_at": "2026-05-05",
            }
        ],
    )
    monkeypatch.setattr(
        m,
        "_fetch_live_trades",
        lambda u, s, w: [-1.0, 0.5, -2.0, 1.0, 0.3],
    )
    divs = compute_divergences()
    assert len(divs) == 1
    assert divs[0]["trust_score"] is None


# ─── main() integration ────────────────────────────────────────────────────────

def test_main_dry_run_telegram(capsys, monkeypatch):
    monkeypatch.setattr(m, "_fetch_research_best_rows", lambda: [])
    rc = m.main(["--dry-run-telegram"])
    captured = capsys.readouterr()
    assert rc == 0
    assert "DRY RUN" in captured.out


def test_main_no_telegram_flag(capsys, monkeypatch):
    monkeypatch.setattr(m, "_fetch_research_best_rows", lambda: [])
    rc = m.main(["--no-telegram"])
    captured = capsys.readouterr()
    assert rc == 0
    # Should still print the formatted message
    assert "Divergence" in captured.out


def test_main_custom_window_and_threshold(capsys, monkeypatch):
    """Verify --window-days and --gap-threshold are threaded through."""
    calls = []

    def _fake_divergences(window_days, gap_threshold):
        calls.append((window_days, gap_threshold))
        return []

    monkeypatch.setattr(m, "compute_divergences", _fake_divergences)
    rc = m.main(["--window-days", "60", "--gap-threshold", "0.3", "--no-telegram"])
    assert rc == 0
    assert calls == [(60, 0.3)]
