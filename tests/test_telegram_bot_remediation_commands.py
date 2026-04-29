"""Regression tests for /halt_remediation, /resume_remediation, /approve_fix.

These tests use synchronous wrappers around async handlers (pytest-asyncio is
not installed in this project). The pattern mirrors existing async tests in the
suite (test_react_screenshots.py, test_dashboard_visual.py).

Authorization: _authorized() is patched to return True/False as required.
The halt file path is redirected to tmp_path via monkeypatch on
core.remediation_kill_switch.PROJECT_ROOT.
The DB path is kept isolated via the global _isolate_prod_db fixture from conftest.
"""
from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(chat_id: int = 12345, username: str = "testuser", args: list | None = None):
    """Build a minimal fake telegram Update object."""
    update = MagicMock()
    update.effective_chat.id = chat_id
    update.effective_user.username = username
    update.message.reply_text = AsyncMock()
    return update


def _make_context(args: list | None = None):
    """Build a minimal fake telegram Context object."""
    ctx = MagicMock()
    ctx.args = args or []
    return ctx


# ---------------------------------------------------------------------------
# /halt_remediation
# ---------------------------------------------------------------------------

def test_halt_remediation_creates_halt_file(tmp_path, monkeypatch):
    """/halt_remediation writes the halt file with correct metadata."""
    import services.telegram_bot as bot_module
    import core.remediation_kill_switch as ks

    monkeypatch.setattr(ks, "PROJECT_ROOT", tmp_path)

    update = _make_update(args=["test", "reason"])
    ctx = _make_context(args=["test", "reason"])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_halt_remediation(update, ctx))

    halt_path = tmp_path / "data" / "AUTO_REMEDIATION_HALT"
    assert halt_path.exists(), "Halt file should have been created"
    content = halt_path.read_text()
    assert "telegram:testuser" in content
    assert "test reason" in content

    # Verify reply was sent
    update.message.reply_text.assert_called_once()
    reply_text = update.message.reply_text.call_args[0][0]
    assert "HALTED" in reply_text


def test_halt_remediation_default_reason(tmp_path, monkeypatch):
    """/halt_remediation with no args uses reason='manual'."""
    import services.telegram_bot as bot_module
    import core.remediation_kill_switch as ks

    monkeypatch.setattr(ks, "PROJECT_ROOT", tmp_path)

    update = _make_update()
    ctx = _make_context(args=[])  # no args

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_halt_remediation(update, ctx))

    halt_path = tmp_path / "data" / "AUTO_REMEDIATION_HALT"
    assert halt_path.exists()
    content = halt_path.read_text()
    assert "manual" in content


def test_halt_remediation_blocks_unauthorized():
    """Unauthorized chat_id gets refused immediately."""
    import services.telegram_bot as bot_module

    update = _make_update(chat_id=99999)
    ctx = _make_context()

    with patch.object(bot_module, "_authorized", return_value=False):
        asyncio.run(bot_module.cmd_halt_remediation(update, ctx))

    # Should reply with "Not authorized", nothing else
    update.message.reply_text.assert_called_once()
    assert "Not authorized" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# /resume_remediation
# ---------------------------------------------------------------------------

def test_resume_remediation_removes_halt_file(tmp_path, monkeypatch):
    """/resume_remediation removes an existing halt file."""
    import services.telegram_bot as bot_module
    import core.remediation_kill_switch as ks

    monkeypatch.setattr(ks, "PROJECT_ROOT", tmp_path)

    # Pre-create the halt file
    halt_path = tmp_path / "data" / "AUTO_REMEDIATION_HALT"
    halt_path.parent.mkdir(parents=True, exist_ok=True)
    halt_path.write_text("halted manually")

    assert halt_path.exists()

    update = _make_update()
    ctx = _make_context()

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_resume_remediation(update, ctx))

    assert not halt_path.exists(), "Halt file should have been removed"

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "RESUMED" in reply


def test_resume_remediation_no_op_when_no_halt(tmp_path, monkeypatch):
    """/resume_remediation is informational (not an error) when no halt file exists."""
    import services.telegram_bot as bot_module
    import core.remediation_kill_switch as ks

    monkeypatch.setattr(ks, "PROJECT_ROOT", tmp_path)

    update = _make_update()
    ctx = _make_context()

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_resume_remediation(update, ctx))

    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    # Should be informational, not an error
    assert "already running" in reply or "already" in reply.lower()


def test_resume_remediation_blocks_unauthorized():
    """Unauthorized chat_id gets refused."""
    import services.telegram_bot as bot_module

    update = _make_update(chat_id=99999)
    ctx = _make_context()

    with patch.object(bot_module, "_authorized", return_value=False):
        asyncio.run(bot_module.cmd_resume_remediation(update, ctx))

    update.message.reply_text.assert_called_once()
    assert "Not authorized" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# /approve_fix
# ---------------------------------------------------------------------------

def _create_fix_attempts_db(db_path: Path, fix_id: int = 1, status: str = "reviewing") -> None:
    """Create a minimal fix_attempts table with one row for testing."""
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS errors (
            id INTEGER PRIMARY KEY,
            fingerprint TEXT,
            source TEXT,
            level TEXT,
            last_seen_ts TEXT
        )
    """)
    conn.execute("INSERT INTO errors (id, fingerprint, source, level, last_seen_ts) VALUES (1, 'fp1', 'test', 'ERROR', '2026-01-01T00:00:00')")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS fix_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            error_id INTEGER NOT NULL,
            fingerprint TEXT NOT NULL,
            started_ts TEXT NOT NULL,
            finished_ts TEXT,
            status TEXT NOT NULL DEFAULT 'triaged'
                CHECK(status IN ('triaged','reproducing','diagnosing','fixing','verifying','reviewing','merged','reverted','failed','escalated','blocked','aborted')),
            classification TEXT NOT NULL CHECK(classification IN ('AUTO_FIX','ASSIST','ESCALATE','IGNORE')),
            review_verdict TEXT CHECK(review_verdict IS NULL OR review_verdict IN ('APPROVE','REJECT')),
            review_confidence REAL,
            review_reason TEXT,
            notes TEXT
        )
    """)
    conn.execute(
        "INSERT INTO fix_attempts (id, error_id, fingerprint, started_ts, status, classification) VALUES (?, 1, 'fp1', '2026-04-30T00:00:00', ?, 'ASSIST')",
        (fix_id, status),
    )
    conn.commit()
    conn.close()


def test_approve_fix_updates_db(tmp_path, monkeypatch):
    """/approve_fix sets review_verdict=APPROVE and records approver in notes."""
    import services.telegram_bot as bot_module

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "atlas.db"
    _create_fix_attempts_db(db_path, fix_id=1, status="reviewing")

    # Redirect DB path to our tmp db
    monkeypatch.setattr(bot_module, "PROJECT_ROOT", tmp_path)

    update = _make_update(username="approver_alice")
    ctx = _make_context(args=["1"])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    # Check DB update
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT review_verdict, notes, status FROM fix_attempts WHERE id = 1").fetchone()
    conn.close()

    assert row[0] == "APPROVE", f"review_verdict should be APPROVE, got {row[0]}"
    assert "approver_alice" in (row[1] or ""), f"notes should contain approver, got {row[1]}"
    assert row[2] == "reviewing", "status should remain unchanged"

    # Check reply
    update.message.reply_text.assert_called_once()
    reply = update.message.reply_text.call_args[0][0]
    assert "approved" in reply.lower() or "APPROVE" in reply


def test_approve_fix_not_found(tmp_path, monkeypatch):
    """/approve_fix with unknown fix_id returns a clear error message."""
    import services.telegram_bot as bot_module

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "atlas.db"
    _create_fix_attempts_db(db_path, fix_id=1)

    monkeypatch.setattr(bot_module, "PROJECT_ROOT", tmp_path)

    update = _make_update()
    ctx = _make_context(args=["9999"])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    reply = update.message.reply_text.call_args[0][0]
    assert "not found" in reply.lower()


def test_approve_fix_already_terminal(tmp_path, monkeypatch):
    """/approve_fix on a merged fix is a no-op with informational message."""
    import services.telegram_bot as bot_module

    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    db_path = tmp_path / "data" / "atlas.db"
    _create_fix_attempts_db(db_path, fix_id=1, status="merged")

    monkeypatch.setattr(bot_module, "PROJECT_ROOT", tmp_path)

    update = _make_update()
    ctx = _make_context(args=["1"])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    reply = update.message.reply_text.call_args[0][0]
    assert "terminal" in reply.lower() or "merged" in reply.lower()

    # DB should be unchanged
    conn = sqlite3.connect(str(db_path))
    row = conn.execute("SELECT review_verdict FROM fix_attempts WHERE id = 1").fetchone()
    conn.close()
    assert row[0] is None  # verdict was not changed


def test_approve_fix_invalid_id(tmp_path, monkeypatch):
    """/approve_fix with non-integer fix_id returns usage error."""
    import services.telegram_bot as bot_module

    db_path = tmp_path / "atlas.db"
    monkeypatch.setattr(bot_module, "PROJECT_ROOT", tmp_path)

    update = _make_update()
    ctx = _make_context(args=["abc"])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    reply = update.message.reply_text.call_args[0][0]
    assert "integer" in reply.lower()


def test_approve_fix_missing_id():
    """/approve_fix with no args returns usage hint."""
    import services.telegram_bot as bot_module

    update = _make_update()
    ctx = _make_context(args=[])

    with patch.object(bot_module, "_authorized", return_value=True):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    reply = update.message.reply_text.call_args[0][0]
    assert "Usage" in reply or "usage" in reply.lower() or "approve_fix" in reply


def test_approve_fix_blocks_unauthorized():
    """Unauthorized cannot approve fixes."""
    import services.telegram_bot as bot_module

    update = _make_update(chat_id=99999)
    ctx = _make_context(args=["1"])

    with patch.object(bot_module, "_authorized", return_value=False):
        asyncio.run(bot_module.cmd_approve_fix(update, ctx))

    update.message.reply_text.assert_called_once()
    assert "Not authorized" in update.message.reply_text.call_args[0][0]


# ---------------------------------------------------------------------------
# Handler registration check
# ---------------------------------------------------------------------------

def test_command_handlers_registered():
    """All three new command names are registered in main()."""
    from pathlib import Path
    src = Path("services/telegram_bot.py").read_text()
    assert 'CommandHandler("halt_remediation"' in src
    assert 'CommandHandler("resume_remediation"' in src
    assert 'CommandHandler("approve_fix"' in src
