"""Tests for scripts/auto_promote_paper_to_live.py.

Covers all 8 specified test cases plus idempotency.
All DB operations use the global _isolate_prod_db autouse fixture from conftest.py.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
if str(ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(ATLAS_ROOT))


# ── Helpers ────────────────────────────────────────────────────────────────────

def _iso(days_ago: float = 0) -> str:
    """Return UTC ISO timestamp N days ago."""
    dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return dt.isoformat()


def _insert_lifecycle_row(db, strategy: str, universe: str, days_ago: float = 35) -> None:
    """Seed a PAPER row into strategy_lifecycle."""
    entered_at = _iso(days_ago)
    db.execute(
        "INSERT OR REPLACE INTO strategy_lifecycle "
        "(strategy, universe, state, entered_state_at) "
        "VALUES (?, ?, 'PAPER', ?)",
        (strategy, universe, entered_at),
    )
    db.commit()


def _insert_paper_trades(
    db,
    strategy: str,
    universe: str,
    n: int,
    pnl_pct: float = 1.0,
    days_ago_exit: float = 2.0,
) -> None:
    """Insert N closed paper trades with given pnl_pct."""
    for i in range(n):
        entry_date = _iso(days_ago_exit + 1)[:10]
        exit_date = _iso(days_ago_exit)[:10]
        db.execute(
            "INSERT INTO paper_trades "
            "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
            " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
            "VALUES (?, ?, ?, 'long', ?, 100.0, 10, ?, 101.0, 10.0, ?, 'closed', 0)",
            (f"TICK{i:03d}", strategy, universe, entry_date, exit_date, pnl_pct),
        )
    db.commit()


def _insert_research_best(
    db,
    strategy: str,
    universe: str,
    sharpe: float = 0.7,
    oos_sharpe: float | None = None,
    oos_trades: int | None = None,
    oos_cagr: float | None = None,
    oos_max_dd: float | None = None,
) -> None:
    """Insert a cross-regime research_best row."""
    db.execute(
        "INSERT OR REPLACE INTO research_best "
        "(strategy, universe, regime_state, params, sharpe, trades, metric_type, "
        " oos_sharpe, oos_trades, oos_cagr, oos_max_dd) "
        "VALUES (?, ?, NULL, '{}', ?, 50, 'sharpe', ?, ?, ?, ?)",
        (strategy, universe, sharpe, oos_sharpe, oos_trades, oos_cagr, oos_max_dd),
    )
    db.commit()


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """Isolated SQLite DB for each test (honours _isolate_prod_db if present)."""
    # _isolate_prod_db autouse fixture in conftest already patches _db_path_override,
    # so we just use get_db() directly.
    from db.atlas_db import get_db, init_db
    # init_db() is already called by conftest; just return the get_db context manager
    return get_db


@pytest.fixture()
def promo_log(tmp_path, monkeypatch):
    """Redirect PROMOTION_LOG_PATH to a temp file."""
    import scripts.auto_promote_paper_to_live as mod
    tmp_log = tmp_path / "promotion_log.json"
    monkeypatch.setattr(mod, "PROMOTION_LOG_PATH", tmp_log)
    return tmp_log


@pytest.fixture()
def no_telegram(monkeypatch):
    """Patch utils.telegram.notify to avoid real sends."""
    mock = MagicMock(return_value=True)
    monkeypatch.setattr("utils.telegram.notify", mock, raising=False)
    return mock


# ── Test 1 — skip when trade count below 30 ────────────────────────────────────

def test_skips_when_paper_trade_count_below_30(db, promo_log, no_telegram, caplog):
    """10 trades even though 35d in PAPER — insufficient sample skip."""
    with db() as conn:
        _insert_lifecycle_row(conn, "momentum_breakout", "sp500", days_ago=35)
        _insert_paper_trades(conn, "momentum_breakout", "sp500", n=10)
        _insert_research_best(conn, "momentum_breakout", "sp500")

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    assert not promo_log.exists(), "No promotion log should be written"
    # State should still be PAPER
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("momentum_breakout", "sp500") == PromotionState.PAPER
    # Should log "insufficient sample"
    combined = " ".join(caplog.messages)
    assert "insufficient sample" in combined.lower() or "SKIP" in combined


# ── Test 2 — skip when days in PAPER below 30 ─────────────────────────────────

def test_skips_when_days_in_paper_below_30(db, promo_log, no_telegram, caplog):
    """50 trades but only 10 days in PAPER state — insufficient time skip."""
    with db() as conn:
        _insert_lifecycle_row(conn, "bb_squeeze", "sp500", days_ago=10)
        _insert_paper_trades(conn, "bb_squeeze", "sp500", n=50)
        _insert_research_best(conn, "bb_squeeze", "sp500")

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    assert not promo_log.exists()
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("bb_squeeze", "sp500") == PromotionState.PAPER
    combined = " ".join(caplog.messages)
    assert "SKIP" in combined or "insufficient" in combined.lower()


# ── Test 3 — promotes clean combo ─────────────────────────────────────────────

def test_promotes_clean_combo(db, promo_log, no_telegram, caplog):
    """35d in PAPER, 35 trades with paper Sharpe ~0.62, research Sharpe 0.7 → LIVE.

    Uses deterministic alternating pnl [1.6, -0.4] which gives Sharpe ~0.62.
    Gap = |0.62 - 0.70| / 0.70 ≈ 0.11 < 0.5  → Gate D passes.
    """
    # Deterministic pnl: alternating 1.6 / -0.4 → Sharpe ≈ 0.62 (see conftest math)
    pnl_values = ([1.6, -0.4] * 17) + [1.6]  # 35 values, Sharpe ≈ 0.62
    with db() as conn:
        _insert_lifecycle_row(conn, "connors_rsi2", "commodity_etfs", days_ago=35)
        entry_date = _iso(5)[:10]
        exit_date = _iso(2)[:10]
        for i, pnl in enumerate(pnl_values):
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'connors_rsi2', 'commodity_etfs', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:03d}", entry_date, exit_date, pnl * 10, pnl),
            )
        # research Sharpe 0.7 — close to paper Sharpe (gap ≈ 0.11 < 0.5)
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type, "
            " oos_sharpe, oos_trades, oos_cagr, oos_max_dd) "
            "VALUES ('connors_rsi2', 'commodity_etfs', NULL, '{}', 0.7, 50, 'sharpe', "
            "        0.5, 40, 6.5, 20.0)"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("connors_rsi2", "commodity_etfs") == PromotionState.LIVE, (
        "Expected combo to be LIVE after promotion"
    )
    # promotion_log.json should exist and contain one entry
    assert promo_log.exists(), "promotion_log.json should be created"
    entries = json.loads(promo_log.read_text())
    assert len(entries) == 1
    entry = entries[0]
    assert entry["strategy"] == "connors_rsi2"
    assert entry["universe"] == "commodity_etfs"
    assert entry["from_state"] == "PAPER"
    assert entry["to_state"] == "LIVE"
    assert "auto_promotion_id" in entry
    combined = " ".join(caplog.messages)
    assert "PROMOTED" in combined


# ── Test 4 — rejects high divergence ──────────────────────────────────────────

def test_rejects_high_divergence(db, promo_log, no_telegram, caplog):
    """Paper Sharpe ~0.1, research 0.7 → gap > 0.5 → REJECT."""
    import random; random.seed(99)
    with db() as conn:
        _insert_lifecycle_row(conn, "adx_trend_pullback", "sp500", days_ago=35)
        for i in range(35):
            pnl = 0.1 + random.gauss(0, 1.0)   # low mean → low Sharpe
            entry_date = _iso(5)[:10]
            exit_date = _iso(2)[:10]
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'adx_trend_pullback', 'sp500', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:03d}", entry_date, exit_date, pnl * 10, pnl),
            )
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type) "
            "VALUES ('adx_trend_pullback', 'sp500', NULL, '{}', 0.7, 50, 'sharpe')"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("adx_trend_pullback", "sp500") == PromotionState.PAPER
    assert not promo_log.exists()
    combined = " ".join(caplog.messages)
    assert "REJECT" in combined or "FAIL" in combined


# ── Test 5 — rejects negative paper Sharpe (Gate C) ──────────────────────────

def test_rejects_negative_paper_sharpe(db, promo_log, no_telegram, caplog):
    """Negative paper Sharpe fails Gate C."""
    import random; random.seed(7)
    with db() as conn:
        _insert_lifecycle_row(conn, "demark_sequential", "sp500", days_ago=35)
        for i in range(35):
            pnl = -1.5 + random.gauss(0, 0.5)  # negative mean
            entry_date = _iso(5)[:10]
            exit_date = _iso(2)[:10]
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'demark_sequential', 'sp500', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:03d}", entry_date, exit_date, pnl * 10, pnl),
            )
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type) "
            "VALUES ('demark_sequential', 'sp500', NULL, '{}', 0.6, 50, 'sharpe')"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("demark_sequential", "sp500") == PromotionState.PAPER
    assert not promo_log.exists()
    combined = " ".join(caplog.messages)
    assert "REJECT" in combined or "Gate C" in combined


# ── Test 6 — dry-run does not transition ──────────────────────────────────────

def test_dry_run_does_not_transition(db, promo_log, no_telegram, caplog):
    """All gates pass but --dry-run → no state change, no log file.

    Uses deterministic pnl [1.6, -0.4] * 20 (40 values, Sharpe ≈ 0.62).
    research Sharpe = 0.65, gap ≈ 0.046 < 0.5 — all gates pass.
    """
    pnl_values = [1.6, -0.4] * 20  # 40 values, Sharpe ≈ 0.62
    with db() as conn:
        _insert_lifecycle_row(conn, "mean_reversion", "gold_etfs", days_ago=40)
        entry_date = _iso(5)[:10]
        exit_date = _iso(2)[:10]
        for i, pnl in enumerate(pnl_values):
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'mean_reversion', 'gold_etfs', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:03d}", entry_date, exit_date, pnl * 10, pnl),
            )
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type) "
            "VALUES ('mean_reversion', 'gold_etfs', NULL, '{}', 0.65, 50, 'sharpe')"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=True, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("mean_reversion", "gold_etfs") == PromotionState.PAPER, (
        "dry-run must NOT transition state"
    )
    assert not promo_log.exists(), "dry-run must NOT write promotion_log.json"
    combined = " ".join(caplog.messages)
    assert "DRY" in combined.upper()


# ── Test 7 — --force evaluates only the specified combo ───────────────────────

def test_force_evaluates_single_combo_only(db, promo_log, no_telegram, caplog):
    """Multiple PAPER combos; --force momentum_breakout:sp500 touches only that one.

    Both combos use alternating pnl [1.6, -0.4] → Sharpe ≈ 0.62, research 0.65
    → gap ≈ 0.046 < 0.5 (Gate D passes).  bb_squeeze is NOT evaluated.
    """
    pnl_values_35 = ([1.6, -0.4] * 17) + [1.6]    # 35 values, Sharpe ≈ 0.62
    pnl_values_40 = ([1.6, -0.4] * 20)              # 40 values, Sharpe ≈ 0.62
    with db() as conn:
        # Combo 1 — qualifies (will be forced)
        _insert_lifecycle_row(conn, "momentum_breakout", "sp500", days_ago=35)
        entry_date = _iso(5)[:10]
        exit_date = _iso(2)[:10]
        for i, pnl in enumerate(pnl_values_35):
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'momentum_breakout', 'sp500', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T1{i:02d}", entry_date, exit_date, pnl * 10, pnl),
            )
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type, "
            " oos_sharpe, oos_trades, oos_cagr, oos_max_dd) "
            "VALUES ('momentum_breakout', 'sp500', NULL, '{}', 0.65, 50, 'sharpe', "
            "        0.5, 40, 6.5, 20.0)"
        )
        # Combo 2 — also in PAPER but NOT forced
        _insert_lifecycle_row(conn, "bb_squeeze", "sp500", days_ago=40)
        for i, pnl in enumerate(pnl_values_40):
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'bb_squeeze', 'sp500', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T2{i:02d}", entry_date, exit_date, pnl * 10, pnl),
            )
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type, "
            " oos_sharpe, oos_trades, oos_cagr, oos_max_dd) "
            "VALUES ('bb_squeeze', 'sp500', NULL, '{}', 0.65, 50, 'sharpe', "
            "        0.5, 40, 6.5, 20.0)"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, force="momentum_breakout:sp500", no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("momentum_breakout", "sp500") == PromotionState.LIVE
    # bb_squeeze was NOT forced → must remain PAPER
    assert get_state("bb_squeeze", "sp500") == PromotionState.PAPER

    if promo_log.exists():
        entries = json.loads(promo_log.read_text())
        strategies_promoted = [e["strategy"] for e in entries]
        assert "bb_squeeze" not in strategies_promoted, "bb_squeeze must not appear in log"


# ── Test 8 — idempotent on already-LIVE combo ─────────────────────────────────

def test_idempotent_already_live(db, promo_log, no_telegram, caplog):
    """Combo already in LIVE state — not in PAPER list, not re-promoted."""
    with db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO strategy_lifecycle "
            "(strategy, universe, state, entered_state_at) "
            "VALUES ('connors_rsi2', 'sp500', 'LIVE', ?)",
            (_iso(60),),
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    # Must remain LIVE (was never PAPER — list_state(PAPER) won't include it)
    assert get_state("connors_rsi2", "sp500") == PromotionState.LIVE
    assert not promo_log.exists()
    combined = " ".join(caplog.messages)
    assert "0 PAPER" in combined or "0 paper" in combined.lower() or "Found 0" in combined


# ── Test 9 — research_best.sharpe below Gate F floor ─────────────────────────

def test_rejects_low_research_sharpe_gate_f(db, promo_log, no_telegram, caplog):
    """research_best.sharpe = 0.3 (below 0.5 floor) → Gate F FAIL."""
    import random; random.seed(42)
    with db() as conn:
        _insert_lifecycle_row(conn, "consecutive_down_days", "sp500", days_ago=35)
        for i in range(35):
            pnl = 0.6 + random.gauss(0, 0.5)
            entry_date = _iso(5)[:10]
            exit_date = _iso(2)[:10]
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, 'consecutive_down_days', 'sp500', 'long', ?, 100.0, 10, "
                "?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:03d}", entry_date, exit_date, pnl * 10, pnl),
            )
        # research sharpe below Gate F threshold
        conn.execute(
            "INSERT OR REPLACE INTO research_best "
            "(strategy, universe, regime_state, params, sharpe, trades, metric_type) "
            "VALUES ('consecutive_down_days', 'sp500', NULL, '{}', 0.3, 50, 'sharpe')"
        )
        conn.commit()

    import scripts.auto_promote_paper_to_live as mod
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        rc = mod.run_promotion(dry_run=False, no_telegram=True)

    assert rc == 0
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("consecutive_down_days", "sp500") == PromotionState.PAPER
    assert not promo_log.exists()
    combined = " ".join(caplog.messages)
    assert "Gate F" in combined and "FAIL" in combined


# ── Helpers for OOS gate tests ────────────────────────────────────────────────

def _setup_oos_test_combo(
    db,
    strategy: str = "opening_gap",
    universe: str = "sp500",
    *,
    oos_sharpe: float | None = None,
    oos_trades: int | None = None,
    oos_cagr: float | None = None,
    days_ago: float = 40,
) -> None:
    """Seed lifecycle + 40 deterministic paper trades + research_best with OOS fields.

    Uses alternating pnl [1.6, -0.4] → Sharpe ≈ 0.62, research Sharpe 0.65 →
    gate D gap ≈ 0.046 < 0.5.  Gates A/B/C/D/E/F all pass when research Sharpe = 0.65.
    """
    pnl_values = [1.6, -0.4] * 20  # 40 values, Sharpe ≈ 0.62
    entry_date = _iso(5)[:10]
    exit_date = _iso(2)[:10]
    with db() as conn:
        _insert_lifecycle_row(conn, strategy, universe, days_ago=days_ago)
        for i, pnl in enumerate(pnl_values):
            conn.execute(
                "INSERT INTO paper_trades "
                "(ticker, strategy, universe, direction, entry_date, entry_price, shares, "
                " exit_date, exit_price, pnl, pnl_pct, status, superseded) "
                "VALUES (?, ?, ?, 'long', ?, 100.0, 10, ?, 101.0, ?, ?, 'closed', 0)",
                (f"T{i:02d}", strategy, universe, entry_date, exit_date, pnl * 10, pnl),
            )
        # research Sharpe 0.65 → gap ≈ 0.046; Gate F passes (0.65 ≥ 0.5)
        _insert_research_best(
            conn, strategy, universe,
            sharpe=0.65,
            oos_sharpe=oos_sharpe,
            oos_trades=oos_trades,
            oos_cagr=oos_cagr,
        )
        conn.commit()


# ── Tests: Gate G (OOS Sharpe) ────────────────────────────────────────────────

def test_gate_g_pass_when_oos_sharpe_above_threshold(db, promo_log, no_telegram, caplog):
    """oos_sharpe=0.5 ≥ 0.3 → Gate G PASS (combined with passing H/I)."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=35,
        oos_cagr=6.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    combined_reasons = " ".join(result.get("gates", {}).values())
    assert result["gates"].get("G") == "PASS", (
        f"Expected Gate G=PASS, got {result['gates']}\nReasons: {result}"
    )


def test_gate_g_fail_when_oos_sharpe_below_threshold(db, promo_log, no_telegram, caplog):
    """oos_sharpe=0.1 < 0.3 → Gate G FAIL."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.1,
        oos_trades=35,
        oos_cagr=6.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("G") == "FAIL", (
        f"Expected Gate G=FAIL, got {result['gates']}"
    )
    # Gate G must be FAIL; the reason is in the evaluate_gates log not the result dict
    assert result.get("promoted") is False


def test_gate_g_fail_when_oos_sharpe_null(db, promo_log, no_telegram, caplog):
    """oos_sharpe=NULL → Gate G FAIL with 'backfill required' message."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=None,
        oos_trades=None,
        oos_cagr=None,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("G") == "FAIL"
    # The reason text should mention "backfill"
    from monitor.strategy_lifecycle import PromotionState, list_state
    # evaluate_and_promote uses _evaluate_gates internally; verify via run_promotion log
    with caplog.at_level("INFO", logger="auto_promote_paper"):
        import scripts.auto_promote_paper_to_live as m2
        m2.run_promotion(dry_run=True, no_telegram=True)
    combined = " ".join(caplog.messages)
    # Gate G fail reason should mention backfill
    assert "backfill" in combined.lower() or "NULL" in combined or "Gate G" in combined


# ── Tests: Gate H (OOS trade count) ──────────────────────────────────────────

def test_gate_h_pass_when_oos_trades_above_threshold(db, promo_log, no_telegram, caplog):
    """oos_trades=50 ≥ 30 → Gate H PASS."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=50,
        oos_cagr=6.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("H") == "PASS", (
        f"Expected Gate H=PASS, got {result['gates']}"
    )


def test_gate_h_fail_when_oos_trades_below_threshold(db, promo_log, no_telegram, caplog):
    """oos_trades=10 < 30 → Gate H FAIL."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=10,
        oos_cagr=6.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("H") == "FAIL", (
        f"Expected Gate H=FAIL, got {result['gates']}"
    )


def test_gate_h_fail_when_oos_trades_null(db, promo_log, no_telegram, caplog):
    """oos_trades=NULL → Gate H FAIL."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=None,
        oos_cagr=6.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("H") == "FAIL"


# ── Tests: Gate I (OOS CAGR) ─────────────────────────────────────────────────

def test_gate_i_pass_when_oos_cagr_above_threshold(db, promo_log, no_telegram, caplog):
    """oos_cagr=7.5 ≥ 5.0 → Gate I PASS."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=35,
        oos_cagr=7.5,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("I") == "PASS", (
        f"Expected Gate I=PASS, got {result['gates']}"
    )


def test_gate_i_fail_when_oos_cagr_below_threshold(db, promo_log, no_telegram, caplog):
    """oos_cagr=2.0 < 5.0 → Gate I FAIL."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=35,
        oos_cagr=2.0,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("I") == "FAIL", (
        f"Expected Gate I=FAIL, got {result['gates']}"
    )


def test_gate_i_fail_when_oos_cagr_null(db, promo_log, no_telegram, caplog):
    """oos_cagr=NULL → Gate I FAIL."""
    _setup_oos_test_combo(
        db, "opening_gap", "sp500",
        oos_sharpe=0.5,
        oos_trades=35,
        oos_cagr=None,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote("opening_gap", "sp500", dry_run=True, no_telegram=True)
    assert result["gates"].get("I") == "FAIL"


# ── Test: promotion blocked when only OOS gates fail ─────────────────────────

def test_promotion_blocked_when_only_oos_gates_fail(db, promo_log, no_telegram, caplog):
    """A-F all pass but G/H/I all NULL → not promoted.

    Uses alternating pnl [1.6, -0.4] (Sharpe ≈ 0.62) with research_sharpe=0.65
    so gates A-F pass; OOS fields are all NULL so G/H/I fail.
    """
    _setup_oos_test_combo(
        db, "keltner_reversion", "sp500",
        oos_sharpe=None,
        oos_trades=None,
        oos_cagr=None,
    )
    import scripts.auto_promote_paper_to_live as mod
    result = mod.evaluate_and_promote(
        "keltner_reversion", "sp500", dry_run=False, no_telegram=True
    )
    assert result["promoted"] is False, (
        "Expected promotion to be BLOCKED when G/H/I are NULL"
    )
    gates = result["gates"]
    assert gates.get("G") == "FAIL"
    assert gates.get("H") == "FAIL"
    assert gates.get("I") == "FAIL"
    # Lifecycle must remain PAPER
    from monitor.strategy_lifecycle import get_state, PromotionState
    assert get_state("keltner_reversion", "sp500") == PromotionState.PAPER
