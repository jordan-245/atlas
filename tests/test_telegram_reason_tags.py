"""Tests for Telegram entry-rejection reason-tag surfacing (Task #309).

Milestone: overlay-silent-bug-fix
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on path
PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

from utils.notification_tags import REASON_TAGS, format_reason_tag


# ---------------------------------------------------------------------------
# 1. Canonical overlay_sizing_zero renders correct tag
# ---------------------------------------------------------------------------

def test_overlay_sizing_zero_renders_tag() -> None:
    entry = {
        "ticker": "LRCX",
        "qty": 0,
        "price": 297.06,
        "success": False,
        "reason": "overlay_sizing_zero",
    }
    result = format_reason_tag(entry)
    assert result == "[overlay\u21920]", f"Got {result!r}"
    assert result != "[?]"


# ---------------------------------------------------------------------------
# 2. Every known reason code maps to its exact tag
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reason,expected_tag", REASON_TAGS.items())
def test_known_codes_each_render_correct_tag(reason: str, expected_tag: str) -> None:
    entry = {"reason": reason, "success": False}
    result = format_reason_tag(entry)
    assert result == expected_tag, (
        f"reason={reason!r}: expected {expected_tag!r}, got {result!r}"
    )


# ---------------------------------------------------------------------------
# 3. Unknown reason is returned verbatim, truncated to 16 chars
# ---------------------------------------------------------------------------

def test_unknown_reason_truncated_verbatim() -> None:
    long_reason = "some_brand_new_failure_mode_too_long"
    entry = {"reason": long_reason}
    result = format_reason_tag(entry)
    assert result == "[some_brand_new_f]"
    assert len(result) == 1 + 16 + 1  # brackets + 16 chars


# ---------------------------------------------------------------------------
# 4. Legacy status field used when reason is absent/empty
# ---------------------------------------------------------------------------

def test_status_legacy_fallback() -> None:
    entry = {"reason": "", "status": "REJECTED"}
    result = format_reason_tag(entry)
    assert result == "[REJECTED]"


def test_status_none_reason_none_returns_question() -> None:
    entry = {"reason": None, "status": None}
    result = format_reason_tag(entry)
    assert result == "[?]"


# ---------------------------------------------------------------------------
# 5. Empty dict / no fields → "[?]"
# ---------------------------------------------------------------------------

def test_neither_field_returns_question() -> None:
    result = format_reason_tag({})
    assert result == "[?]"


# ---------------------------------------------------------------------------
# 6. Full _notify_execution integration: overlay-rejected entry shows [overlay→0]
# ---------------------------------------------------------------------------

def test_full_telegram_line_renders_overlay_tag(monkeypatch: pytest.MonkeyPatch) -> None:
    """_notify_execution must produce [overlay→0] and must NOT produce [?]."""
    # Import the function under test (lazy import, post sys.path setup above)
    import importlib
    import scripts.execute_approved as ea

    report = {
        "entries": [
            {
                "ticker": "LRCX",
                "qty": 0,
                "price": 297.06,
                "success": False,
                "reason": "overlay_sizing_zero",
                # Note: no "status" field — matches real live_executor output
            }
        ],
        "exits": [],
        "ok_entries": 0,
        "total_entries": 1,
        "ok_exits": 0,
        "total_exits": 0,
        "errors": [],
        "volatility_gate": {},
    }

    captured: list[str] = []

    def _fake_send(msg: str) -> None:
        captured.append(msg)

    # Patch send_message inside the execute_approved module's local import
    # The function does `from utils.telegram import send_message, tg_escape as _tge`
    # inside the try block, so we patch at the utils.telegram level.
    with patch("utils.telegram.send_message", side_effect=_fake_send):
        ea._notify_execution(
            report=report,
            market_id="sp500",
            trade_date="2026-05-08",
        )

    assert captured, "send_message was never called"
    msg = captured[0]
    assert "[overlay\u21920]" in msg, f"Missing [overlay→0] in:\n{msg}"
    assert "[?]" not in msg, f"[?] should not appear:\n{msg}"
