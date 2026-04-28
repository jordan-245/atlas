"""Audit-style tests for universe.membership.derive_universe.

Confirms:
  1. Every CURRENT sp500 member returns 'sp500' when hint='sp500'.
  2. Every static-universe member (sector_etfs, treasury_etfs, commodity_etfs,
     gold_etfs, defensive_etfs) returns its universe when hint=<that universe>
     (i.e., correct universe wins over wrong hint when unambiguous).
  3. Random / unknown tickers with hint='sp500' return None (NOT 'sp500')
     instead of blind hint fallback. This is the SLV/XLY bug-class regression
     guard.
  4. Cache miss + hint that's a real universe but ticker is NOT in it →
     returns None.
  5. Cache miss + hint='sp500' + ticker IS dynamically a sp500 member →
     returns 'sp500' (live-verify path catches it).
  6. Live verify failure (broken builder) → returns None, never hint.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import universe.builder
import universe.membership as m
from universe.membership import clear_cache, derive_universe


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_cache():
    """Ensure module cache is cleared before AND after every test."""
    clear_cache()
    yield
    clear_cache()


# ---------------------------------------------------------------------------
# Test 1 — full sp500 sweep
# ---------------------------------------------------------------------------

def test_audit_all_current_sp500_tickers_resolve_correctly():
    """Every current sp500 member should resolve to 'sp500' with hint='sp500'."""
    sp500_tickers = universe.builder.get_universe_tickers("sp500")
    assert sp500_tickers, "sp500 universe is empty — check data/processed/sp500/universe.json"

    failures: list[str] = []
    for ticker in sp500_tickers:
        result = derive_universe(ticker, "sp500")
        if result != "sp500":
            failures.append(f"{ticker!r} → {result!r} (expected 'sp500')")

    assert not failures, (
        f"{len(failures)} sp500 tickers resolved incorrectly:\n"
        + "\n".join(failures[:20])
        + ("\n... (truncated)" if len(failures) > 20 else "")
    )


# ---------------------------------------------------------------------------
# Test 2 — full static-ETF sweep
# ---------------------------------------------------------------------------

def test_audit_all_static_etf_tickers_resolve_correctly():
    """Every static-ETF ticker should resolve to its own universe with the matching hint.

    Multi-membership tickers (XLU in sector_etfs+defensive_etfs; GLD in
    commodity_etfs+gold_etfs) are tested against EACH of their universes —
    the hint should always win for ambiguous members.
    """
    from universe.definitions import UNIVERSES

    static_universes = (
        "sector_etfs",
        "treasury_etfs",
        "commodity_etfs",
        "gold_etfs",
        "defensive_etfs",
    )

    failures: list[str] = []
    for uname in static_universes:
        udef = UNIVERSES[uname]
        assert udef.get("method") == "static", f"{uname} is not static"
        for ticker in udef.get("tickers", []):
            result = derive_universe(ticker, uname)
            if result != uname:
                failures.append(
                    f"{ticker!r} in {uname!r} → {result!r} (expected {uname!r})"
                )

    assert not failures, (
        f"{len(failures)} static ETF tickers resolved incorrectly:\n"
        + "\n".join(failures)
    )


# ---------------------------------------------------------------------------
# Test 3 — regression guard: unknown ticker must NOT return sp500 blindly
# ---------------------------------------------------------------------------

def test_unknown_ticker_with_sp500_hint_returns_None_not_sp500():
    """SLV/XLY bug-class regression guard.

    A ticker that is NOT in any universe must return None even when
    hint='sp500' is supplied — the fix must never return hint blindly on
    cache miss.
    """
    result = derive_universe("ZZZZNONEXISTENT", "sp500")
    assert result is None, (
        f"Expected None for unknown ticker with hint='sp500', got {result!r}. "
        f"This is the SLV/XLY bug class — blind hint fallback must be removed."
    )


# ---------------------------------------------------------------------------
# Test 4 — single-membership cache hit ignores wrong hint
# ---------------------------------------------------------------------------

def test_known_static_ticker_with_wrong_sp500_hint_returns_correct_universe():
    """SLV is in commodity_etfs only.

    derive_universe('SLV', 'sp500') must return 'commodity_etfs'
    because the cache has unambiguous single membership — hint is ignored.
    """
    result = derive_universe("SLV", "sp500")
    assert result == "commodity_etfs", (
        f"Expected 'commodity_etfs' for SLV with hint='sp500', got {result!r}."
    )


# ---------------------------------------------------------------------------
# Test 5 — cache miss → live verify recovers sp500 membership
# ---------------------------------------------------------------------------

def test_cache_miss_with_dynamic_sp500_member_live_verifies(monkeypatch):
    """If sp500 cache was built with empty data, live-verify must still succeed.

    Steps:
      1. Patch builder to return [] → cache built without any sp500 members.
      2. Restore real builder.
      3. derive_universe('AAPL', 'sp500') must hit live-verify, confirm AAPL,
         and return 'sp500'.
    """
    real_getter = universe.builder.get_universe_tickers

    # Step 1: build cache with empty sp500
    monkeypatch.setattr(universe.builder, "get_universe_tickers", lambda mid: [])
    clear_cache()
    m._build_membership()  # cache now has no sp500 entries

    # Step 2: restore real builder for live verification
    monkeypatch.setattr(universe.builder, "get_universe_tickers", real_getter)

    # Step 3: AAPL not in cache → live-verify → confirmed → 'sp500'
    result = derive_universe("AAPL", "sp500")
    assert result == "sp500", (
        f"Expected 'sp500' via live-verify for AAPL, got {result!r}."
    )


# ---------------------------------------------------------------------------
# Test 6 — live verify failure → None, never hint
# ---------------------------------------------------------------------------

def test_live_verify_failure_returns_None_never_hint(monkeypatch, caplog):
    """Infrastructure failure in live verify must return None, not hint.

    Steps:
      1. Build cache with empty sp500 (AAPL absent from cache).
      2. Replace builder with a function that raises RuntimeError.
      3. derive_universe must log WARNING and return None.
    """
    # Step 1: build cache with empty sp500
    monkeypatch.setattr(universe.builder, "get_universe_tickers", lambda mid: [])
    clear_cache()
    m._build_membership()

    # Step 2: make builder raise
    def _broken(mid: str) -> list:
        raise RuntimeError("builder broken for test")

    monkeypatch.setattr(universe.builder, "get_universe_tickers", _broken)

    # Step 3: must return None, never 'sp500'
    with caplog.at_level(logging.WARNING):
        result = derive_universe("AAPL", "sp500")

    assert result is None, (
        f"Expected None when live verify raises, got {result!r}."
    )
    assert any(
        "live verify FAILED" in r.message for r in caplog.records
    ), "Expected 'live verify FAILED' warning in log output"


# ---------------------------------------------------------------------------
# Test 7 — empty ticker preserves legacy market_id passthrough
# ---------------------------------------------------------------------------

def test_empty_ticker_returns_hint_or_none():
    """Empty ticker string returns hint if provided, else None.

    This is the unchanged legacy branch — market_id still flows through for
    empty tickers (e.g., placeholder rows).
    """
    assert derive_universe("", "sp500") == "sp500"
    assert derive_universe("", None) is None


# ---------------------------------------------------------------------------
# Test 8 — no hint + unknown ticker → None
# ---------------------------------------------------------------------------

def test_no_hint_unknown_ticker_returns_None(caplog):
    """Unknown ticker with no hint must return None and log WARN."""
    with caplog.at_level(logging.WARNING):
        result = derive_universe("ZZZZ", None)
    assert result is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records), (
        "Expected a WARNING log for unknown ticker with no hint"
    )


# ---------------------------------------------------------------------------
# Test 9 — no hint + known single-membership ticker → correct universe
# ---------------------------------------------------------------------------

def test_no_hint_known_static_ticker_returns_universe():
    """XLE is in sector_etfs only — no hint needed for unambiguous membership."""
    result = derive_universe("XLE", None)
    assert result == "sector_etfs", (
        f"Expected 'sector_etfs' for XLE with no hint, got {result!r}."
    )


# ---------------------------------------------------------------------------
# Test 10 — operational regression: live_sp500.json positions audit
# ---------------------------------------------------------------------------

def test_audit_no_state_file_ticker_returns_sp500_falsely():
    """Operational regression guard for the SLV/XLY/UNG incident class.

    For every ticker currently in live_sp500.json:
      - IF the ticker is in the real sp500 universe → must resolve to 'sp500'
      - IF the ticker is NOT in sp500 → must NOT resolve to 'sp500' blindly
        (should return None or their actual universe)

    Soft-skips if live_sp500.json does not exist.
    """
    state_path = Path(__file__).parent.parent / "brokers" / "state" / "live_sp500.json"
    if not state_path.exists():
        pytest.skip("live_sp500.json not found")

    raw = json.loads(state_path.read_text())
    positions = raw.get("positions", [])
    if isinstance(positions, dict):
        tickers = list(positions.keys())
    else:
        tickers = [p["ticker"] for p in positions if isinstance(p, dict)]

    if not tickers:
        pytest.skip("live_sp500.json has no positions to audit")

    sp500_set = set(universe.builder.get_universe_tickers("sp500"))

    failures_false_sp500: list[str] = []
    failures_missed_sp500: list[str] = []

    for ticker in tickers:
        result = derive_universe(ticker, "sp500")
        in_sp500 = ticker in sp500_set
        if in_sp500 and result != "sp500":
            failures_missed_sp500.append(
                f"{ticker!r}: expected 'sp500' (confirmed member), got {result!r}"
            )
        elif not in_sp500 and result == "sp500":
            failures_false_sp500.append(
                f"{ticker!r}: returned 'sp500' blindly but ticker NOT in sp500 universe"
            )

    errors: list[str] = []
    if failures_false_sp500:
        errors.append(
            "Blind sp500 fallback detected (SLV/XLY bug class):\n"
            + "\n".join(failures_false_sp500)
        )
    if failures_missed_sp500:
        errors.append(
            "Known sp500 member resolved incorrectly:\n"
            + "\n".join(failures_missed_sp500)
        )

    assert not errors, "\n\n".join(errors)
