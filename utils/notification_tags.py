"""Shared Telegram notification reason-tag helpers.

Used by:
  - scripts/execute_approved.py
  - services/telegram_bot.py

Keep REASON_TAGS in sync across any callers — this is the single source of truth.
"""
from __future__ import annotations

# Reason code → Telegram display tag.  Keep ASCII-arrow forms compact.
REASON_TAGS: dict[str, str] = {
    "overlay_sizing_zero":   "[overlay→0]",
    "vol_gate_zero":         "[vol-gate→0]",
    "insufficient_cash":     "[no-cash]",
    "below_min_notional":    "[<min-notional]",
    "risk_cap_zero":         "[risk-cap→0]",
    "stop_too_tight":        "[stop-too-tight]",
    "overlay_avoid_tickers": "[overlay-avoid]",
    "buying_power_gate":     "[bp-gate]",
}


def format_reason_tag(entry: dict) -> str:
    """Build the trailing ``[reason]`` tag for a Telegram entry line.

    Order of preference:

    1. ``entry["reason"]`` mapped via :data:`REASON_TAGS`
    2. ``entry["reason"]`` verbatim, truncated to 16 chars
    3. ``entry["status"]`` verbatim (legacy field)
    4. ``"[?]"`` only when nothing meaningful is present
    """
    reason: str = entry.get("reason") or ""
    if reason in REASON_TAGS:
        return REASON_TAGS[reason]
    if reason:
        return f"[{reason[:16]}]"
    status: str = entry.get("status") or ""
    if status:
        return f"[{status[:16]}]"
    return "[?]"
