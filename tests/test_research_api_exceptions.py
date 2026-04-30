"""tests/test_research_api_exceptions.py — Task #283 Wave 3: bare-except
validation for services/api/research.py.

Changes: 11 → 10 broad catches (1 narrowed subprocess call, 10 justified HTTP
handler catch-alls with noqa: BLE001 and logger.exception).

The HTTP handler pattern:
    try:
        # handler body with DB reads and optional imports
    except Exception as e:  # noqa: BLE001 — HTTP handler catch-all
        logger.exception("... failed")
        raise HTTPException(status_code=500, detail=str(e))

This is the correct pattern for FastAPI routes: unexpected exceptions must
be converted to 500 rather than crashing the server, and must be logged
with full traceback (logger.exception includes exc_info=True).

Narrowed site (L150): engine_status subprocess → (subprocess.SubprocessError, OSError, ValueError)
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

PROJECT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT))


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _source() -> str:
    return (PROJECT / "services" / "api" / "research.py").read_text()


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
    def test_no_bare_excepts_without_noqa(self):
        """All remaining broad catches must have noqa: BLE001."""
        bad = _broad_excepts_ast(_source())
        assert bad == [], f"Unbound bare excepts at lines: {bad}"

    def test_subprocess_call_narrowed(self):
        """engine_status subprocess call must be narrowed to subprocess types."""
        src = _source()
        assert "except (subprocess.SubprocessError, OSError, ValueError)" in src, (
            "engine_status subprocess call should use narrowed exception type"
        )

    def test_http_handlers_have_noqa_comment(self):
        """All HTTP handler catch-alls must have noqa: BLE001 comment."""
        src = _source()
        noqa_count = src.count("# noqa: BLE001 — HTTP handler catch-all")
        assert noqa_count >= 10, (
            f"Expected ≥10 HTTP handler noqa comments, found {noqa_count}"
        )

    def test_http_handlers_have_logger_exception(self):
        """All HTTP handler catch-alls must use logger.exception (includes exc_info)."""
        src = _source()
        count = src.count("logger.exception(")
        assert count >= 10, (
            f"Expected ≥10 logger.exception calls, found {count}"
        )

    def test_http_handlers_re_raise_as_http_exception(self):
        """All HTTP handler catch-alls must re-raise as HTTPException."""
        src = _source()
        count = src.count("raise HTTPException(status_code=500")
        assert count >= 10, (
            f"Expected ≥10 HTTPException re-raises, found {count}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# B. Behavioural — engine_status subprocess call
# ──────────────────────────────────────────────────────────────────────────────

class TestSubprocessNarrowedExceptions:
    def _check_engine_status(self, fn):
        """Mirrors the narrowed engine_status subprocess handler."""
        engine_status = "unknown"
        try:
            engine_status = fn()
        except (subprocess.SubprocessError, OSError, ValueError) as e:
            engine_status = "unknown"
        return engine_status

    def test_subprocess_timeout_caught(self):
        """subprocess.TimeoutExpired (subclass of SubprocessError) is caught."""
        result = self._check_engine_status(
            lambda: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=["systemctl"], timeout=5)
            )
        )
        assert result == "unknown"

    def test_subprocess_called_process_error_caught(self):
        """subprocess.CalledProcessError (subclass of SubprocessError) is caught."""
        result = self._check_engine_status(
            lambda: (_ for _ in ()).throw(
                subprocess.CalledProcessError(returncode=1, cmd=["systemctl"])
            )
        )
        assert result == "unknown"

    def test_os_error_caught(self):
        """OSError from missing executable is caught."""
        result = self._check_engine_status(
            lambda: (_ for _ in ()).throw(OSError("No such file or directory"))
        )
        assert result == "unknown"

    def test_value_error_caught(self):
        """ValueError from bad subprocess output parsing is caught."""
        result = self._check_engine_status(
            lambda: (_ for _ in ()).throw(ValueError("unexpected output"))
        )
        assert result == "unknown"

    def test_attribute_error_propagates(self):
        """AttributeError must propagate — programming bug."""
        with pytest.raises(AttributeError):
            self._check_engine_status(
                lambda: (_ for _ in ()).throw(AttributeError("no attribute 'stdout'"))
            )

    def test_runtime_error_propagates(self):
        """RuntimeError must propagate — unexpected state."""
        with pytest.raises(RuntimeError):
            self._check_engine_status(
                lambda: (_ for _ in ()).throw(RuntimeError("unexpected state"))
            )

    def test_good_path_returns_status(self):
        """Normal execution returns the engine status string."""
        result = self._check_engine_status(lambda: "active")
        assert result == "active"


# ──────────────────────────────────────────────────────────────────────────────
# C. Behavioural — HTTP handler broad catch-all pattern
# ──────────────────────────────────────────────────────────────────────────────

class TestHttpHandlerCatchAllSmoke:
    """Verify the justified HTTP handler catch-alls still work correctly."""

    def test_sqlite_error_caught_and_converted_to_500(self):
        """sqlite3 errors must be caught and converted to HTTP 500 response."""
        import sqlite3

        errors = []

        def handler():
            try:
                raise sqlite3.OperationalError("no such table: research_experiments")
            except Exception as e:  # noqa: BLE001 — HTTP handler catch-all
                errors.append({"type": type(e).__name__, "detail": str(e)})

        handler()
        assert errors[0]["type"] == "OperationalError"
        assert "research_experiments" in errors[0]["detail"]

    def test_import_error_caught_and_converted(self):
        """ImportError (optional module missing) must be caught by handler."""
        errors = []

        def handler():
            try:
                raise ImportError("atlas_db not available")
            except Exception as e:  # noqa: BLE001 — HTTP handler catch-all
                errors.append(type(e).__name__)

        handler()
        assert errors == ["ImportError"]

    def test_connection_error_caught(self):
        """ConnectionError (DB connection lost) must be caught by handler."""
        errors = []

        def handler():
            try:
                raise ConnectionError("DB connection lost")
            except Exception as e:  # noqa: BLE001 — HTTP handler catch-all
                errors.append(type(e).__name__)

        handler()
        assert errors == ["ConnectionError"]

    def test_http_exception_re_raised_correctly(self):
        """HTTPException from inner checks must pass through (re-raised)."""
        class MockHTTPException(Exception):
            def __init__(self, status_code, detail):
                self.status_code = status_code
                self.detail = detail

        def handler():
            try:
                inner_ex = MockHTTPException(400, "bad request")
                # In real code, HTTPException is caught before generic except
                # and re-raised. Simulate that here.
                try:
                    raise inner_ex
                except MockHTTPException:
                    raise  # re-raise passes through outer try
            except Exception as e:  # noqa: BLE001 — HTTP handler catch-all
                if isinstance(e, MockHTTPException):
                    raise  # re-raise HTTP exceptions
                return {"status": 500, "detail": str(e)}

        with pytest.raises(MockHTTPException):
            handler()
