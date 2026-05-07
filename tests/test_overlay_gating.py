"""tests/test_overlay_gating.py

Tests for:
  #307 — overlay.enabled gate in DB-fallback path of brokers/live_executor.py
  #308 — max(1, …) floor on overlay multiplier to prevent qty=0 truncation

Milestone: overlay-silent-bug-fix
"""
from __future__ import annotations

import logging
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
os.chdir(PROJECT)

# ── constants ──────────────────────────────────────────────────────────────────

TRADE_DATE = "2026-05-08"   # deterministic date; DB row uses this


# ── helpers ────────────────────────────────────────────────────────────────────

def _entry(ticker: str, qty: int = 5, price: float = 100.0, stop: float = 90.0) -> dict:
    return {
        "ticker": ticker,
        "position_size": qty,
        "entry_price": price,
        "stop_price": stop,
        "strategy": "test_strat",
        "confidence": 0.7,
    }


def _make_plan(entries: list, overlay_context=None) -> dict:
    """Build an APPROVED plan. Omit overlay_context key entirely when None
    so the DB-fallback branch fires in execute_plan."""
    plan: dict = {
        "status": "APPROVED",
        "proposed_entries": entries,
        "proposed_exits": [],
    }
    if overlay_context is not None:
        plan["overlay_context"] = overlay_context
    return plan


def _make_executor(config_overlay: dict):
    """Return a dry-run LiveExecutor with given overlay config section."""
    from brokers.live_executor import LiveExecutor

    config = {
        "trading": {
            "mode": "live",
            "live_safety": {"dry_run_first": True},
        },
        "market_id": "sp500",
        "fees": {},
        "strategies": {},
        "overlay": config_overlay,
    }
    ex = LiveExecutor(config)
    ex._connected = True
    ex._halted = False
    ex._halt_reason = ""
    ex._daily_date = TRADE_DATE
    ex._daily_order_count = 0
    return ex


def _patch_side_effects(ex, submitted_calls: list | None = None) -> None:
    """Patch executor internals that require broker / DB infrastructure.

    Includes filter_tradable so synthetic test tickers are not filtered out.
    """
    ex._run_volatility_gate = MagicMock(return_value={
        "action": "none", "size_multiplier": 1.0, "message": "",
        "flags": [], "gate_enabled": False, "triggered_count": 0,
    })
    ex._check_circuit_breaker = MagicMock(return_value=False)
    ex._capture_start_equity = MagicMock()
    ex.check_market_state = MagicMock(return_value={
        "is_tradeable": True, "states": [], "message": "",
    })
    ex.place_stops_for_plan = MagicMock(return_value={})

    if submitted_calls is not None:
        def fake_entry(entry_rec, td):
            submitted_calls.append(dict(entry_rec))
            return {
                "success": True,
                "ticker": entry_rec["ticker"],
                "qty": entry_rec["position_size"],
                "status": "SUBMITTED",
            }
        ex._execute_entry = fake_entry


def _insert_overlay_row(sizing_override: float, trade_date: str = TRADE_DATE) -> None:
    """Insert a tighten overlay_decisions row into the isolated test DB."""
    import sqlite3
    import db.atlas_db as _adb

    db_path = _adb._db_path_override or "data/atlas.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO overlay_decisions
            (timestamp, regime_state, action, sizing_override,
             universes_deactivated, tickers_avoided, reasoning,
             confidence, data_sources)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            f"{trade_date}T12:00:00",
            "transition_uncertain",
            "tighten",
            sizing_override,
            None, None,
            "automated tighten for test",
            0.75,
            None,
        ),
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════════════════════════════════════════
# Test class #307 — DB-fallback respects overlay.enabled
# ══════════════════════════════════════════════════════════════════════════════

class TestOverlayEnabledGate:
    """#307: DB-fallback must be skipped when overlay.enabled=False."""

    # ── test 1 ────────────────────────────────────────────────────────────────
    def test_db_fallback_respects_enabled_false(self, caplog):
        """Insert a tighten row; executor must NOT apply it when enabled=False."""
        _insert_overlay_row(sizing_override=0.5)

        submitted = []
        ex = _make_executor({"enabled": False, "shadow_mode": False})
        _patch_side_effects(ex, submitted)

        # Plan WITHOUT overlay_context → DB-fallback branch
        plan = _make_plan([_entry("LRCX", qty=5)])

        with (
            patch(
                "brokers.alpaca.tradable_assets.filter_tradable",
                return_value=(["LRCX"], []),
            ),
            caplog.at_level(logging.DEBUG, logger="atlas.live_executor"),
        ):
            ex.execute_plan(plan, TRADE_DATE)

        # Entry must reach _execute_entry with ORIGINAL qty=5 (not 2 = int(5*0.5))
        assert len(submitted) == 1, (
            f"Expected 1 entry call, got {len(submitted)}: {submitted}"
        )
        assert submitted[0]["position_size"] == 5, (
            f"Expected position_size=5 (no overlay), got {submitted[0]['position_size']}"
        )

        msgs = [r.getMessage() for r in caplog.records]

        # No overlay_applied sizing log emitted
        overlay_applied = [
            m for m in msgs
            if "overlay_applied" in m and "sizing" in m and "LRCX" in m
        ]
        assert not overlay_applied, (
            f"overlay_applied must NOT fire when enabled=False; got: {overlay_applied}"
        )

        # The skip log must appear
        skip_logs = [
            m for m in msgs
            if "overlay.enabled=false" in m or "skipping DB-fallback" in m
        ]
        assert skip_logs, (
            f"Expected 'skipping DB-fallback' log, none found. All messages:\n"
            + "\n".join(msgs)
        )

    # ── test 2 ────────────────────────────────────────────────────────────────
    def test_db_fallback_honored_when_enabled_true(self, caplog):
        """Same setup with enabled=True — DB row must apply and halve the qty."""
        _insert_overlay_row(sizing_override=0.5)

        submitted = []
        # enabled=True, shadow_mode=False → enforce
        ex = _make_executor({"enabled": True, "shadow_mode": False})
        _patch_side_effects(ex, submitted)

        # qty=10 → int(10*0.5)=5 (clear; no floor edge case here)
        plan = _make_plan([_entry("LRCX", qty=10)])

        with (
            patch(
                "brokers.alpaca.tradable_assets.filter_tradable",
                return_value=(["LRCX"], []),
            ),
            caplog.at_level(logging.DEBUG, logger="atlas.live_executor"),
        ):
            ex.execute_plan(plan, TRADE_DATE)

        assert len(submitted) == 1, (
            f"Expected 1 entry call, got {len(submitted)}: {submitted}"
        )
        assert submitted[0]["position_size"] == 5, (
            f"Expected position_size=5 (halved by overlay 0.5), "
            f"got {submitted[0]['position_size']}"
        )

        msgs = [r.getMessage() for r in caplog.records]
        applied_logs = [m for m in msgs if "overlay_applied" in m and "LRCX" in m]
        assert applied_logs, (
            f"Expected overlay_applied log for LRCX, not found. All:\n"
            + "\n".join(msgs)
        )


# ══════════════════════════════════════════════════════════════════════════════
# Test class #308 — max(1, …) floor on overlay multiplier
# ══════════════════════════════════════════════════════════════════════════════

class TestOverlayMultiplierFloor:
    """#308: int(qty * multiplier) must floor at 1 when multiplier > 0."""

    @pytest.mark.parametrize("original_qty,multiplier,expected_qty", [
        (1, 0.8, 1),    # floor: int(1*0.8)=0 → max(1,…)=1
        (2, 0.5, 1),    # floor: int(2*0.5)=1 → max(1,1)=1
        (1, 0.0, 0),    # explicit block (multiplier==0.0) → 0
        (0, 0.5, 0),    # zero input → 0 (no fractional revival)
        (10, 0.3, 3),   # normal int truncation: int(10*0.3)=3
        (3, 0.8, 2),    # normal: int(3*0.8)=2
    ])
    def test_max1_floor_overlay_multiplier(
        self,
        original_qty: int,
        multiplier: float,
        expected_qty: int,
        caplog,
    ) -> None:
        """Exercise the enforce branch via overlay_context.sizing_override."""
        submitted = []
        ex = _make_executor({"enabled": True, "shadow_mode": False})
        _patch_side_effects(ex, submitted)

        ticker = "AAPL"   # real Alpaca ticker — avoid filter_tradable rejection
        plan = _make_plan(
            [_entry(ticker, qty=original_qty)],
            overlay_context={
                "action": "tighten",
                "sizing_override": multiplier,
                "tickers_to_avoid": [],
            },
        )

        with (
            patch(
                "brokers.alpaca.tradable_assets.filter_tradable",
                return_value=([ticker], []),
            ),
            caplog.at_level(logging.DEBUG, logger="atlas.live_executor"),
        ):
            report = ex.execute_plan(plan, TRADE_DATE)

        if expected_qty == 0:
            # qty→0 path: entry is blocked (reason=overlay_sizing_zero)
            assert len(submitted) == 0, (
                f"Expected no _execute_entry call when qty→0, "
                f"got {submitted}"
            )
            blocked = [
                e for e in report["entries"]
                if e.get("reason") == "overlay_sizing_zero"
            ]
            assert blocked, (
                f"Expected overlay_sizing_zero in report entries; got {report['entries']}"
            )
        else:
            assert len(submitted) == 1, (
                f"Expected 1 entry call, got {len(submitted)}: {submitted}"
            )
            got_qty = submitted[0]["position_size"]
            assert got_qty == expected_qty, (
                f"original_qty={original_qty} * multiplier={multiplier}: "
                f"expected {expected_qty}, got {got_qty}"
            )
