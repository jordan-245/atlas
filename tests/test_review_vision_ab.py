"""
Tests for adjust_divergence tracking in scripts/review_vision_ab.py.

Uses synthetic JSONL fixtures written to tmp_path; exercises _build_report
directly with the parsed entry dicts.
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

# Ensure atlas root is on sys.path regardless of how pytest is invoked.
_ATLAS_ROOT = Path(__file__).parent.parent
if str(_ATLAS_ROOT) not in sys.path:
    sys.path.insert(0, str(_ATLAS_ROOT))

from scripts.review_vision_ab import _build_report  # noqa: E402

TODAY = date.today().isoformat()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    text_adjust: bool,
    vision_adjust: bool,
    ticker_divs: int = 0,
    timestamp: str | None = None,
) -> dict:
    """Build a minimal synthetic JSONL entry."""
    ts = timestamp or f"{TODAY}T12:00:00+00:00"
    return {
        "timestamp": ts,
        "universe": "sp500",
        "tickers_analysed": ["SPY"],
        "text_decision": {
            "adjust": text_adjust,
            "sizing_multiplier_override": None,
            "universes_to_deactivate": [],
            "tickers_to_avoid": [],
            "reasoning": "test",
            "confidence": 0.5,
        },
        "vision_decision": {
            "adjust": vision_adjust,
            "reasoning": "test",
            "chart_vision_signals": [
                {
                    "ticker": "SPY",
                    "pattern": "ascending triangle",
                    "tighten_rec": False,
                    "confidence": 0.5,
                }
            ],
        },
        "divergence_flags": [
            {"flag": "tighten_rec_mismatch", "ticker": f"T{i}"}
            for i in range(ticker_divs)
        ],
    }


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    """Write list of dicts to a .jsonl file (one JSON object per line)."""
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


# ---------------------------------------------------------------------------
# Test 1 — no divergence when both sides agree
# ---------------------------------------------------------------------------

def test_no_adjust_divergence(tmp_path: Path) -> None:
    """Both text and vision have adjust=True → adjust_divergence=False, count=0."""
    entries = [_make_entry(text_adjust=True, vision_adjust=True)]
    _write_jsonl(tmp_path / f"{TODAY}.jsonl", entries)

    report = _build_report(entries, days=5)

    assert "Adjust divergences: 0/" in report, (
        f"Expected 'Adjust divergences: 0/' in report:\n{report}"
    )
    assert "adjust_div=True" not in report, (
        "No divergent-cycle line should appear when both sides agree"
    )


# ---------------------------------------------------------------------------
# Test 2 — divergence captured when sides disagree
# ---------------------------------------------------------------------------

def test_adjust_divergence_captured(tmp_path: Path) -> None:
    """text.adjust=True, vision.adjust=False → adjust_divergence=True, count=1."""
    entries = [_make_entry(text_adjust=True, vision_adjust=False)]
    _write_jsonl(tmp_path / f"{TODAY}.jsonl", entries)

    report = _build_report(entries, days=5)

    assert "Adjust divergences: 1/" in report, (
        f"Expected 'Adjust divergences: 1/' in report:\n{report}"
    )
    assert "adjust_div=True" in report, (
        "Per-cycle divergence line missing"
    )
    assert "text=True" in report, "text adjust value not shown in per-cycle line"
    assert "vision=False" in report, "vision adjust value not shown in per-cycle line"


# ---------------------------------------------------------------------------
# Test 3 — aggregate count correct across multiple cycles
# ---------------------------------------------------------------------------

def test_aggregate_count_correct(tmp_path: Path) -> None:
    """5 cycles: 2 with divergence, 3 without → aggregate count=2, pct=40.0%."""
    entries = [
        _make_entry(text_adjust=True,  vision_adjust=False),  # diverge
        _make_entry(text_adjust=False, vision_adjust=True),   # diverge
        _make_entry(text_adjust=False, vision_adjust=False),  # agree
        _make_entry(text_adjust=True,  vision_adjust=True),   # agree
        _make_entry(text_adjust=False, vision_adjust=False),  # agree
    ]
    _write_jsonl(tmp_path / f"{TODAY}.jsonl", entries)

    report = _build_report(entries, days=5)

    assert "Adjust divergences: 2/" in report, (
        f"Expected 'Adjust divergences: 2/' in report:\n{report}"
    )
    assert "40.0%" in report, (
        f"Expected 40.0% in report:\n{report}"
    )
    # Exactly 2 per-cycle divergence lines
    assert report.count("adjust_div=True") == 2, (
        f"Expected exactly 2 divergent-cycle lines, got {report.count('adjust_div=True')}"
    )


# ---------------------------------------------------------------------------
# Test 4 — ticker_divs shown correctly in per-cycle line
# ---------------------------------------------------------------------------

def test_ticker_divs_in_cycle_line(tmp_path: Path) -> None:
    """Divergent cycle shows correct ticker_divs count from divergence_flags."""
    entries = [_make_entry(text_adjust=True, vision_adjust=False, ticker_divs=3)]
    _write_jsonl(tmp_path / f"{TODAY}.jsonl", entries)

    report = _build_report(entries, days=5)

    assert "ticker_divs=3" in report, (
        f"Expected 'ticker_divs=3' in per-cycle line:\n{report}"
    )


# ---------------------------------------------------------------------------
# Test 5 — no vision_decision does NOT raise and is not counted as divergence
# ---------------------------------------------------------------------------

def test_no_vision_decision_not_counted(tmp_path: Path) -> None:
    """Entry with vision_decision=None is skipped for adjust_divergence (not a crash)."""
    entry = _make_entry(text_adjust=True, vision_adjust=False)
    entry["vision_decision"] = None  # simulate missing vision response
    entries = [entry]
    _write_jsonl(tmp_path / f"{TODAY}.jsonl", entries)

    report = _build_report(entries, days=5)

    # vision_decision is None → adjust_div logic should treat as vision_adjust=None
    # → adjust_div=False → count stays 0
    assert "Adjust divergences: 0/" in report, (
        f"Entry without vision_decision should not count as divergence:\n{report}"
    )
    assert "adjust_div=True" not in report
