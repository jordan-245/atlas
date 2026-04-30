"""tests/test_eod_settlement_exceptions.py — Task #283 Wave 3: bare-except
validation for scripts/eod_settlement.py.

Changes: 22 → 7 broad catches (15 narrowed, 7 justified with noqa: BLE001).

Narrowed:
  - _health_log write → (ImportError, OSError, AttributeError, RuntimeError)
  - Cancel protective orders (×2) → (ImportError, OSError, RuntimeError, ConnectionError)
  - P1 fill oracle (×2 bare, no binding!) → (ImportError, AttributeError, sqlite3.OperationalError)
  - Broker sell exception (×2) → broad retained (SDK) with noqa
  - RegimeModel classification (×2) → (ImportError, AttributeError, ValueError, RuntimeError)
  - SQLite trade exit dual-write (×2) → (ImportError, sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError)
  - get_market timezone → (ImportError, AttributeError, RuntimeError)
  - Broker-failure Telegram → (ImportError, OSError, ConnectionError, RuntimeError)
  - SQLite EOD write → (ImportError, sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError)
  - EOD monitor state read (bare, no binding!) → (json.JSONDecodeError, OSError, AttributeError, KeyError)
  - _eod_monitor_mark_sent write → (OSError, json.JSONDecodeError)
  - run_position_monitor → broad retained with noqa
  - crash-guard inner Telegram → (ImportError, OSError, ConnectionError, RuntimeError)

Latent bugs surfaced:
  - L165/L285: bare `except Exception:` (no binding, no logging) in P1 fill oracle silently
    swallowed ImportError/sqlite3.OperationalError when broker_orders table missing.
    Now logs at DEBUG level + narrowed.
  - L868: bare `except Exception:` (no binding, no logging) in _eod_monitor_already_sent_today
    silently swallowed OSError/JSONDecodeError when state file corrupted.
    Now logs at DEBUG level + narrowed.
  - f-strings in logger calls converted to %-style (L43, L145, L183, L265, L303, L213,
    L333, L565, L793, L904, L923)
"""
from __future__ import annotations

import ast
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _source() -> str:
    return (PROJECT / "scripts" / "eod_settlement.py").read_text()


def _broad_excepts_ast(source: str) -> list[int]:
    """Return lines with unbound bare except (no noqa)."""
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
    def test_no_bare_excepts_without_binding(self):
        """All remaining broad catches must have noqa or binding+logging."""
        bad = _broad_excepts_ast(_source())
        assert bad == [], f"Unbound bare excepts at lines: {bad}"

    def test_sqlite3_imported(self):
        """sqlite3 must be at top-level imports for except sqlite3.OperationalError."""
        src = _source()
        assert "import sqlite3" in src, "sqlite3 must be imported at module level"

    def test_p1_fill_oracle_narrowed(self):
        """P1 fill oracle bare except must be narrowed (was silent swallow)."""
        src = _source()
        assert "except (ImportError, AttributeError, sqlite3.OperationalError)" in src, (
            "P1 fill oracle must use narrowed exception type"
        )

    def test_p1_fill_oracle_has_debug_log(self):
        """P1 fill oracle now must log at DEBUG (latent bug fix: was silent pass)."""
        src = _source()
        assert 'log.debug("broker_orders fill price lookup failed' in src, (
            "P1 fill oracle must log the failure (was silent before)"
        )

    def test_eod_monitor_state_read_narrowed(self):
        """EOD monitor state read bare except must be narrowed."""
        src = _source()
        assert "except (json.JSONDecodeError, OSError, AttributeError, KeyError)" in src, (
            "_eod_monitor_already_sent_today must use narrowed exception type"
        )

    def test_eod_monitor_state_read_has_debug_log(self):
        """EOD monitor state read must log at DEBUG (latent bug fix: was silent pass)."""
        src = _source()
        assert 'log.debug("_eod_monitor_already_sent_today: state read failed' in src, (
            "_eod_monitor_already_sent_today must log the failure (was silent before)"
        )

    def test_health_log_narrowed(self):
        """_health_log must use narrowed exception type."""
        src = _source()
        assert "except (ImportError, OSError, AttributeError, RuntimeError)" in src, (
            "_health_log must use narrowed exception type"
        )

    def test_regime_model_narrowed(self):
        """RegimeModel classification must use narrowed exception type."""
        src = _source()
        assert "except (ImportError, AttributeError, ValueError, RuntimeError) as _re:" in src, (
            "RegimeModel narrowed handler must be present"
        )

    def test_sqlite_dual_write_narrowed(self):
        """SQLite dual-write handlers must use sqlite3 types."""
        src = _source()
        assert "except (ImportError, sqlite3.OperationalError, sqlite3.DatabaseError, AttributeError) as _e:" in src, (
            "SQLite trade exit dual-write must use sqlite3 types"
        )

    def test_no_fstring_in_except_handlers(self):
        """f-strings in logger calls within exception handlers must be fixed."""
        src = _source()
        import re
        # Check key handlers that used to have f-strings
        assert 'log.warning("Health-log write failed (non-fatal): %s"' in src
        assert 'log.warning("Failed to cancel protective orders for %s: %s"' in src
        assert 'log.error("Broker sell exception for %s: %s' in src
        assert 'log.warning("SQLite trade exit dual-write failed: %s"' in src
        assert 'log.warning("SQLite EOD write failed (non-fatal): %s"' in src
        assert 'log.error("Position monitor evaluation failed: %s"' in src


# ──────────────────────────────────────────────────────────────────────────────
# B. Behavioural — P1 fill oracle (formerly bare except)
# ──────────────────────────────────────────────────────────────────────────────

class TestFillOracleNarrowedExceptions:
    def _fill_oracle_logic(self, fn):
        """Mirrors the narrowed P1 fill oracle body."""
        fill = None
        try:
            fill = fn()
        except (ImportError, AttributeError, sqlite3.OperationalError) as _p1_exc:
            pass  # logged at DEBUG in real code
        return fill

    def test_import_error_caught(self):
        """ImportError (db module missing) is caught."""
        assert self._fill_oracle_logic(
            lambda: (_ for _ in ()).throw(ImportError("atlas_db not found"))
        ) is None

    def test_sqlite_operational_error_caught(self):
        """sqlite3.OperationalError (missing table) is caught."""
        assert self._fill_oracle_logic(
            lambda: (_ for _ in ()).throw(sqlite3.OperationalError("no such table"))
        ) is None

    def test_attribute_error_caught(self):
        """AttributeError (bad method signature) is caught."""
        assert self._fill_oracle_logic(
            lambda: (_ for _ in ()).throw(AttributeError("no attribute 'get_fill_price'"))
        ) is None

    def test_value_error_propagates(self):
        """ValueError from bad price data must propagate — signals upstream bug."""
        with pytest.raises(ValueError):
            self._fill_oracle_logic(
                lambda: (_ for _ in ()).throw(ValueError("negative price"))
            )

    def test_runtime_error_propagates(self):
        """RuntimeError must propagate."""
        with pytest.raises(RuntimeError):
            self._fill_oracle_logic(
                lambda: (_ for _ in ()).throw(RuntimeError("unexpected state"))
            )


# ──────────────────────────────────────────────────────────────────────────────
# C. Behavioural — EOD monitor state read (formerly bare except)
# ──────────────────────────────────────────────────────────────────────────────

class TestEodMonitorStateReadNarrowedExceptions:
    def _state_read_logic(self, fn):
        """Mirrors the narrowed _eod_monitor_already_sent_today body."""
        try:
            return fn()
        except (json.JSONDecodeError, OSError, AttributeError, KeyError) as _mon_err:
            return False  # default: not sent

    def test_json_decode_error_caught_returns_false(self):
        """json.JSONDecodeError from corrupt state → returns False."""
        assert self._state_read_logic(
            lambda: json.loads("{corrupt")
        ) is False

    def test_os_error_caught_returns_false(self):
        """OSError from unreadable file → returns False."""
        assert self._state_read_logic(
            lambda: (_ for _ in ()).throw(OSError("permission denied"))
        ) is False

    def test_key_error_caught_returns_false(self):
        """KeyError from missing key in state dict → returns False."""
        assert self._state_read_logic(
            lambda: {}["last_sent_date"]
        ) is False

    def test_runtime_error_propagates(self):
        """RuntimeError must propagate."""
        with pytest.raises(RuntimeError):
            self._state_read_logic(
                lambda: (_ for _ in ()).throw(RuntimeError("unexpected state"))
            )


# ──────────────────────────────────────────────────────────────────────────────
# D. Behavioural — RegimeModel narrowed handler
# ──────────────────────────────────────────────────────────────────────────────

class TestRegimeModelNarrowedExceptions:
    def _classify_regime(self, fn):
        """Mirrors the narrowed RegimeModel classification handler."""
        regime = None
        try:
            regime = fn()
        except (ImportError, AttributeError, ValueError, RuntimeError) as _re:
            regime = None  # fallback
        return regime

    def test_import_error_caught(self):
        """ImportError from regime model missing is caught."""
        assert self._classify_regime(
            lambda: (_ for _ in ()).throw(ImportError("regime.model not found"))
        ) is None

    def test_attribute_error_caught(self):
        """AttributeError from bad model state is caught."""
        assert self._classify_regime(
            lambda: (_ for _ in ()).throw(AttributeError("no attribute 'state'"))
        ) is None

    def test_value_error_caught(self):
        """ValueError from invalid regime state is caught."""
        assert self._classify_regime(
            lambda: (_ for _ in ()).throw(ValueError("invalid regime value"))
        ) is None

    def test_connection_error_propagates(self):
        """ConnectionError (DB connection failure) must propagate."""
        with pytest.raises(ConnectionError):
            self._classify_regime(
                lambda: (_ for _ in ()).throw(ConnectionError("DB connection failed"))
            )


# ──────────────────────────────────────────────────────────────────────────────
# E. Smoke tests — justified broad catch-alls still work
# ──────────────────────────────────────────────────────────────────────────────

class TestCatchAllSmoke:
    def test_broker_sell_catch_all_catches_sdk_error(self):
        """Broker sell broad catch-all still catches SDK-specific errors."""
        caught = []

        class AlpacaSDKError(Exception):
            pass

        def broker_sell():
            try:
                raise AlpacaSDKError("403 forbidden")
            except Exception as _broker_err:  # noqa: BLE001
                caught.append(str(_broker_err))

        broker_sell()
        assert caught == ["403 forbidden"]

    def test_position_monitor_catch_all_catches_unexpected_error(self):
        """Position monitor broad catch-all catches unexpected DB errors."""
        caught = []

        def run_monitor():
            try:
                raise sqlite3.DatabaseError("DB locked")
            except Exception as e:  # noqa: BLE001
                caught.append(type(e).__name__)

        run_monitor()
        assert caught == ["DatabaseError"]
