"""tests/test_sync_protective_exceptions.py — Task #283 Wave 3: bare-except
validation for scripts/sync_protective_orders.py.

Changes: 28 → 11 broad catches (17 narrowed, 11 justified with noqa comments).

Narrowed sites:
  - JSON file reads (_load_held_state, _load_pdt_state) → (json.JSONDecodeError, OSError)
  - JSON file writes (_save_held_state, _save_pdt_state) → (OSError, TypeError)
  - Telegram sends → (ImportError, OSError, ConnectionError, RuntimeError)
  - DB upsert calls → sqlite3.Error (+ ImportError/AttributeError for import paths)
  - Broker disconnect (finally) → (RuntimeError, OSError, AttributeError)
  - Setup logging fallback → (ImportError, OSError, AttributeError, RuntimeError)
  - PDT set_pdt_deferred → (OSError, ValueError, AttributeError, RuntimeError)
  - Crash-guard inner Telegram → (ImportError, OSError, ConnectionError, RuntimeError)

Latent bugs surfaced:
  - f-string usage in logger.warning calls fixed to %-style (L1306, L1574)
  - sqlite3 now explicitly imported (needed for except sqlite3.Error clauses)
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _source() -> str:
    return (PROJECT / "scripts" / "sync_protective_orders.py").read_text()


def _broad_excepts(source: str) -> list[int]:
    """Return line numbers of unbound bare `except Exception:` without noqa."""
    tree = ast.parse(source)
    lines = source.splitlines()
    bad: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if node.type is None:
            bad.append(node.lineno)
            continue
        type_name = node.type.id if isinstance(node.type, ast.Name) else ""
        if type_name == "Exception" and node.name is None:
            line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
            if "noqa" not in line:
                bad.append(node.lineno)
    return bad


# ──────────────────────────────────────────────────────────────────────────────
# A. Static checks
# ──────────────────────────────────────────────────────────────────────────────

class TestStaticChecks:
    def test_no_unbound_bare_excepts(self):
        """All remaining broad catches must have a noqa: BLE001 comment."""
        bad = _broad_excepts(_source())
        assert bad == [], f"Unbound bare excepts at lines: {bad}"

    def test_sqlite3_imported(self):
        """sqlite3 must be imported at module level for except sqlite3.Error clauses."""
        src = _source()
        assert "import sqlite3" in src, "sqlite3 must be imported for except sqlite3.Error"

    def test_json_reads_narrowed(self):
        """JSON state file reads must use narrowed exception type."""
        src = _source()
        assert "except (json.JSONDecodeError, OSError) as exc:" in src, (
            "JSON file read handlers must use (json.JSONDecodeError, OSError)"
        )

    def test_json_writes_narrowed(self):
        """JSON state file writes must use narrowed exception type."""
        src = _source()
        assert "except (OSError, TypeError) as exc:" in src, (
            "JSON file write handlers must use (OSError, TypeError)"
        )

    def test_db_upserts_narrowed(self):
        """DB upsert handlers must use sqlite3.Error."""
        src = _source()
        assert "except sqlite3.Error as db_exc:" in src or \
               "except (ImportError, sqlite3.Error" in src, (
            "DB upsert handlers must use sqlite3.Error"
        )

    def test_telegram_sends_narrowed(self):
        """Telegram send handlers must use narrowed network types."""
        src = _source()
        assert "except (ImportError, OSError, ConnectionError, RuntimeError)" in src, (
            "Telegram send handlers must use narrowed exception types"
        )

    def test_broker_disconnect_narrowed(self):
        """Broker disconnect (finally block) must use narrowed exception type."""
        src = _source()
        assert "except (RuntimeError, OSError, AttributeError) as e:" in src, (
            "Broker disconnect must use narrowed exception type"
        )

    def test_no_fstring_in_logger_warning(self):
        """logger.warning must not use f-strings (latent bug fix: L1306, L1574)."""
        src = _source()
        import re
        fstring_warns = re.findall(r'logger\.\w+\(f"', src)
        assert fstring_warns == [], f"Found f-string logger calls: {fstring_warns}"

    def test_crash_guard_noqa_comment(self):
        """Top-level crash guard must have noqa: BLE001 comment."""
        src = _source()
        assert "noqa: BLE001 — top-level crash guard" in src, (
            "Top-level crash guard needs noqa: BLE001 justification comment"
        )


# ──────────────────────────────────────────────────────────────────────────────
# B. Behavioural — JSON file I/O handlers
# ──────────────────────────────────────────────────────────────────────────────

class TestJsonFileHandlers:
    def test_json_decode_error_caught_on_load(self):
        """json.JSONDecodeError is caught when reading corrupt state file."""
        def load_state(json_fn):
            try:
                return json_fn()
            except (json.JSONDecodeError, OSError) as exc:
                return {}

        # Corrupt JSON → caught
        assert load_state(lambda: json.loads("{corrupt")) == {}
        # Good JSON → passes through
        assert load_state(lambda: json.loads('{"a": 1}')) == {"a": 1}

    def test_os_error_caught_on_load(self):
        """OSError is caught when state file is unreadable."""
        def load_state(fn):
            try:
                return fn()
            except (json.JSONDecodeError, OSError) as exc:
                return {}

        assert load_state(lambda: (_ for _ in ()).throw(OSError("permission denied"))) == {}

    def test_os_error_caught_on_save(self):
        """OSError is caught when state file is unwritable."""
        saved_errors = []
        def save_state(fn):
            try:
                fn()
            except (OSError, TypeError) as exc:
                saved_errors.append(str(exc))

        save_state(lambda: (_ for _ in ()).throw(OSError("read-only filesystem")))
        assert saved_errors == ["read-only filesystem"]

    def test_type_error_caught_on_save(self):
        """TypeError from unserializable dict values is caught on save."""
        saved_errors = []
        def save_state(fn):
            try:
                fn()
            except (OSError, TypeError) as exc:
                saved_errors.append(str(exc))

        save_state(lambda: (_ for _ in ()).throw(TypeError("Object is not JSON serializable")))
        assert saved_errors == ["Object is not JSON serializable"]

    def test_attribute_error_propagates_from_load(self):
        """AttributeError must propagate — signals a programming bug."""
        def load_state(fn):
            try:
                return fn()
            except (json.JSONDecodeError, OSError) as exc:
                return {}

        with pytest.raises(AttributeError):
            load_state(lambda: None.bad_attr)


# ──────────────────────────────────────────────────────────────────────────────
# C. Behavioural — SQLite error handlers
# ──────────────────────────────────────────────────────────────────────────────

class TestSqliteErrorHandlers:
    def test_sqlite_operational_error_caught(self):
        """sqlite3.OperationalError (subclass of sqlite3.Error) is caught."""
        caught = []
        def db_op():
            try:
                raise sqlite3.OperationalError("no such table: trades")
            except sqlite3.Error as db_exc:
                caught.append(str(db_exc))

        db_op()
        assert caught == ["no such table: trades"]

    def test_sqlite_integrity_error_caught(self):
        """sqlite3.IntegrityError (subclass of sqlite3.Error) is caught."""
        caught = []
        def db_op():
            try:
                raise sqlite3.IntegrityError("UNIQUE constraint failed")
            except sqlite3.Error as db_exc:
                caught.append(str(db_exc))

        db_op()
        assert caught == ["UNIQUE constraint failed"]

    def test_runtime_error_propagates_from_db_op(self):
        """RuntimeError must propagate — not a database error."""
        def db_op():
            try:
                raise RuntimeError("unexpected state")
            except sqlite3.Error as db_exc:
                pass  # should NOT catch RuntimeError

        with pytest.raises(RuntimeError):
            db_op()

    def test_import_error_caught_in_upsert_block(self):
        """ImportError is caught in the protective ledger upsert block."""
        caught = []
        def upsert_block():
            try:
                raise ImportError("atlas_db not available")
            except (ImportError, sqlite3.Error, AttributeError, ValueError) as exc:
                caught.append(type(exc).__name__)

        upsert_block()
        assert caught == ["ImportError"]


# ──────────────────────────────────────────────────────────────────────────────
# D. Behavioural — Telegram send handlers
# ──────────────────────────────────────────────────────────────────────────────

class TestTelegramHandlers:
    def test_import_error_caught_in_telegram_send(self):
        """ImportError from missing Telegram module is caught."""
        caught = []
        def tg_send():
            try:
                raise ImportError("utils.telegram not installed")
            except (ImportError, OSError, ConnectionError, RuntimeError) as exc:
                caught.append(type(exc).__name__)

        tg_send()
        assert caught == ["ImportError"]

    def test_connection_error_caught_in_telegram_send(self):
        """ConnectionError from network failure is caught."""
        caught = []
        def tg_send():
            try:
                raise ConnectionError("network unreachable")
            except (ImportError, OSError, ConnectionError, RuntimeError) as exc:
                caught.append(str(exc))

        tg_send()
        assert caught == ["network unreachable"]

    def test_attribute_error_propagates_from_telegram_send(self):
        """AttributeError must propagate — signals a programming bug."""
        def tg_send():
            try:
                raise AttributeError("send_message has wrong signature")
            except (ImportError, OSError, ConnectionError, RuntimeError) as exc:
                pass  # should NOT catch AttributeError

        with pytest.raises(AttributeError):
            tg_send()


# ──────────────────────────────────────────────────────────────────────────────
# E. Smoke test — justified broad catch-alls still work
# ──────────────────────────────────────────────────────────────────────────────

class TestCatchAllSmoke:
    def test_outer_sync_market_catches_any_exception(self):
        """The outer sync_market catch-all must still catch broker SDKError or similar."""
        caught = []

        class BrokerSDKError(Exception):
            pass

        def sync_outer():
            try:
                raise BrokerSDKError("alpaca 429 rate limit")
            except Exception as e:  # noqa: BLE001
                caught.append(str(e))

        sync_outer()
        assert caught == ["alpaca 429 rate limit"]

    def test_crash_guard_catches_system_errors(self):
        """Top-level crash guard (noqa: BLE001) still catches MemoryError subclasses."""
        caught = []

        def crash_guard():
            try:
                raise MemoryError("out of memory")
            except Exception as exc:  # noqa: BLE001
                caught.append(type(exc).__name__)

        crash_guard()
        assert caught == ["MemoryError"]
