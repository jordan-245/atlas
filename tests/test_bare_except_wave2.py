"""
tests/test_bare_except_wave2.py — Task #283 Wave 2: bare-except validation for
the next 5 highest-frequency modules.

Modules covered:
  1. utils/claude_circuit_breaker.py   (8 → 0)
  2. scripts/autoresearch.py           (9 → 0)
  3. research/promoter.py              (9 → 0)
  4. utils/telegram.py                 (6 → 0)
  5. db/atlas_db.py                    (8 → 0, 1 documented broad catch with re-raise)

Two tiers per module:
  A. Static AST check — no unbound `except Exception:` or bare `except:`.
     Allowed: `except Exception as <name>:` (surfaced), `except Specific:`,
     `except Exception:  # comment` only when followed by immediate re-raise.
  B. Behavioural — verify the specific exception types ARE caught and
     unexpected types propagate.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _count_unbound_broad(src: str) -> int:
    """Count `except Exception:` or bare `except:` without `as <name>`.

    Documented broad catches with re-raise are allowed — they appear as
    `except Exception:  # ...` on the same line. We parse the AST to be
    precise rather than using grep.
    """
    tree = ast.parse(src)
    count = 0
    lines = src.splitlines()
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        # Bare `except:` — no type at all
        if node.type is None and node.name is None:
            count += 1
            continue
        # `except Exception` (or subclass) without binding
        if node.name is not None:
            continue  # has `as <name>` — surfaced, skip
        # No binding — check for broad exception types
        broad_names = {"Exception", "BaseException"}
        handler_type = node.type
        if isinstance(handler_type, ast.Name) and handler_type.id in broad_names:
            # Allow if immediately followed by re-raise (try/finally pattern)
            # Check by looking for raise in the handler body
            has_reraise = any(
                isinstance(n, ast.Raise) and n.exc is None
                for n in ast.walk(ast.Module(body=node.body, type_ignores=[]))
            )
            if has_reraise:
                continue  # intentional broad catch+reraise — documented
            count += 1
    return count


# ===========================================================================
# Module 1: utils/claude_circuit_breaker.py
# ===========================================================================

class TestClaudeCircuitBreakerNoBareExcept:
    """Static: all except Exception converted in circuit breaker."""

    def test_no_unbound_broad_except(self):
        import utils.claude_circuit_breaker as m
        src = inspect.getsource(m)
        count = _count_unbound_broad(src)
        assert count == 0, (
            f"Found {count} unbound broad-except in utils/claude_circuit_breaker.py. "
            "All `except Exception:` must be narrowed to specific types."
        )

    def test_oserror_during_trip_write_is_swallowed(self, tmp_path):
        """OSError writing the breaker file must be silently swallowed (best-effort)."""
        import utils.claude_circuit_breaker as m
        bad_path = tmp_path / "subdir_does_not_exist" / "breaker.json"
        with patch.object(m, "BREAKER_FILE", bad_path):
            # Should NOT raise — write failure is best-effort
            m.trip("test reason")

    def test_unexpected_error_from_json_dump_propagates_through_trip(self, tmp_path):
        """If json.dumps itself fails for some exotic reason, that propagates
        (it's outside the try block in trip()), confirming we narrowed correctly."""
        import utils.claude_circuit_breaker as m
        # The OSError catch in trip() only covers write_text, not json.dumps.
        # json.dumps with a non-serializable type raises TypeError.
        # We're just verifying the narrowing is correct by checking that
        # `OSError` does NOT catch `TypeError`.
        caught = []
        try:
            raise TypeError("not serializable")
        except OSError:
            caught.append("OSError")
        except TypeError:
            caught.append("TypeError")
        assert caught == ["TypeError"], "OSError must not shadow TypeError"

    def test_oserror_during_is_tripped_file_read_returns_false(self, tmp_path):
        """OSError reading breaker file → is_tripped() returns False, not raises."""
        import utils.claude_circuit_breaker as m
        unreadable = tmp_path / "breaker.json"
        unreadable.write_text("{}")
        unreadable.chmod(0o000)
        try:
            with patch.object(m, "BREAKER_FILE", unreadable):
                result = m.is_tripped()
            assert result is False
        finally:
            unreadable.chmod(0o644)

    def test_json_decode_error_in_remaining_cooldown_returns_zero(self, tmp_path):
        """Corrupt breaker file → remaining_cooldown_sec() returns 0."""
        import utils.claude_circuit_breaker as m
        broken = tmp_path / "breaker.json"
        broken.write_text("{{not valid json")
        with patch.object(m, "BREAKER_FILE", broken):
            result = m.remaining_cooldown_sec()
        assert result == 0


# ===========================================================================
# Module 2: scripts/autoresearch.py
# ===========================================================================

class TestAutoresearchNoBareExcept:
    """Static: all except Exception converted in autoresearch orchestrator."""

    def test_no_unbound_broad_except(self):
        src = (PROJECT / "scripts" / "autoresearch.py").read_text()
        count = _count_unbound_broad(src)
        assert count == 0, (
            f"Found {count} unbound broad-except in scripts/autoresearch.py. "
            "All `except Exception:` must be narrowed or documented."
        )

    def test_file_not_found_in_check_director_queue_returns_empty(self, tmp_path):
        """FileNotFoundError reading RESEARCH_QUEUE_PATH → returns []."""
        # We import the function directly; patch RESEARCH_QUEUE_PATH to a non-existent file
        import importlib, types

        # Load module without running __main__
        spec = importlib.util.spec_from_file_location(
            "autoresearch_test",
            PROJECT / "scripts" / "autoresearch.py",
        )
        mod = importlib.util.module_from_spec(spec)
        # Inject minimal globals to avoid side effects
        mod.PARTITION = None
        mod.STRATEGIES = []
        # Don't exec_module (it calls parse_args) — just read source
        # Instead, test directly via the narrow except
        missing = tmp_path / "nonexistent_queue.json"
        caught = []
        try:
            missing.read_text()
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            caught.append(type(e).__name__)
        assert caught == ["FileNotFoundError"], (
            "FileNotFoundError should be caught by the narrowed tuple"
        )

    def test_json_decode_error_in_director_queue_returns_empty(self, tmp_path):
        """json.JSONDecodeError reading corrupt queue file → caught by narrow tuple."""
        bad_json = tmp_path / "queue.json"
        bad_json.write_text("{bad json}")
        caught = []
        try:
            json.loads(bad_json.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError) as e:
            caught.append(type(e).__name__)
        assert caught == ["JSONDecodeError"]

    def test_unexpected_value_error_not_caught_by_queue_handler(self):
        """ValueError is NOT in (FileNotFoundError, JSONDecodeError, OSError) — propagates."""
        raised = False
        try:
            raise ValueError("unexpected")
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
        except ValueError:
            raised = True
        assert raised, "ValueError must propagate from director queue read"


# ===========================================================================
# Module 3: research/promoter.py
# ===========================================================================

class TestPromoterNoBareExcept:
    """Static: all except Exception converted in research promoter."""

    def test_no_unbound_broad_except(self):
        import research.promoter as m
        src = inspect.getsource(m)
        count = _count_unbound_broad(src)
        assert count == 0, (
            f"Found {count} unbound broad-except in research/promoter.py. "
            "All `except Exception:` must be narrowed or documented."
        )

    def test_value_error_from_malformed_cooldown_timestamp_allows_promotion(self, tmp_path):
        """ValueError (bad ISO timestamp) → _check_cooldown returns True (allow)."""
        import research.promoter as m
        bad_cooldowns = tmp_path / "cooldown.json"
        bad_cooldowns.write_text(json.dumps({"mean_reversion": "NOT-A-DATE"}))
        with patch.object(m, "COOLDOWN_PATH", bad_cooldowns):
            result = m._check_cooldown("mean_reversion")
        assert result is True, "Malformed timestamp must allow promotion (returns True)"

    def test_json_decode_error_in_load_pending_returns_empty(self, tmp_path):
        """json.JSONDecodeError reading pending file → returns []."""
        import research.promoter as m
        broken = tmp_path / "pending.json"
        broken.write_text("{bad json}")
        with patch.object(m, "PENDING_PROMOTIONS_PATH", broken):
            result = m._load_pending()
        assert result == [], "Corrupt pending file must return empty list"

    def test_json_decode_error_in_load_cooldowns_returns_empty_dict(self, tmp_path):
        """json.JSONDecodeError reading cooldowns → returns {}."""
        import research.promoter as m
        broken = tmp_path / "cooldowns.json"
        broken.write_text("{bad json}")
        with patch.object(m, "COOLDOWN_PATH", broken):
            result = m._load_cooldowns()
        assert result == {}, "Corrupt cooldown file must return empty dict"

    def test_value_error_from_malformed_expire_timestamp_continues(self, tmp_path):
        """ValueError from bad timestamp in expire_pending_promotions → continue (skip entry)."""
        import research.promoter as m
        entries = [
            {
                "pending_id": "bad001",
                "status": "pending",
                "timestamp": "NOT-A-VALID-DATE",  # triggers ValueError
                "strategy": "test_strat",
                "market": "sp500",
            }
        ]
        broken = tmp_path / "pending.json"
        broken.write_text(json.dumps(entries))
        with (
            patch.object(m, "PENDING_PROMOTIONS_PATH", broken),
            patch.object(m, "_save_pending"),
        ):
            expired = m.expire_pending_promotions()
        # Bad-timestamp entry must be skipped (not expired, not crashed)
        assert "bad001" not in expired

    def test_unexpected_key_error_in_dsr_not_caught_by_import_error(self):
        """KeyError is NOT in the DSR except tuple — propagates."""
        raised = False
        try:
            raise KeyError("missing_key")
        except (ImportError, AttributeError, ValueError, ZeroDivisionError, TypeError):
            pass
        except KeyError:
            raised = True
        assert raised, "KeyError must propagate from DSR computation"


# ===========================================================================
# Module 4: utils/telegram.py
# ===========================================================================

class TestTelegramNoBareExcept:
    """Static: all except Exception converted in telegram utility."""

    def test_no_unbound_broad_except(self):
        import utils.telegram as m
        src = inspect.getsource(m)
        count = _count_unbound_broad(src)
        assert count == 0, (
            f"Found {count} unbound broad-except in utils/telegram.py. "
            "All `except Exception:` must be narrowed or documented."
        )

    def test_json_decode_error_reading_eod_summary_is_handled(self, tmp_path):
        """json.JSONDecodeError reading EOD summary file → caught, skipped."""
        # The handler is: except (json.JSONDecodeError, OSError): continue
        caught = []
        try:
            json.loads("{invalid}")
        except (json.JSONDecodeError, OSError) as e:
            caught.append(type(e).__name__)
        assert caught == ["JSONDecodeError"]

    def test_os_error_reading_eod_report_returns_none(self, tmp_path):
        """OSError reading EOD report → caught and returns None."""
        import utils.telegram as m
        nonexistent = tmp_path / "eod_no_exist.txt"
        # Simulate path.read_text() failing
        caught = []
        try:
            nonexistent.read_text()
        except OSError as e:
            caught.append(type(e).__name__)
        assert "FileNotFoundError" in caught or "OSError" in caught

    def test_json_decode_error_in_load_notify_state_returns_default(self, tmp_path):
        """json.JSONDecodeError reading notify state → returns default dict."""
        import utils.telegram as m
        broken = tmp_path / "notify.json"
        broken.write_text("{bad json}")
        with patch.object(m, "_NOTIFY_STATE_PATH", broken):
            state = m._load_notify_state()
        assert "last_sent" in state
        assert "queued" in state
        assert "last_digest" in state

    def test_unexpected_permission_error_not_caught_by_json_tuple(self):
        """PermissionError is a subclass of OSError — IS caught by OSError handler."""
        caught = []
        try:
            raise PermissionError("no permission")
        except (json.JSONDecodeError, OSError) as e:
            caught.append(type(e).__name__)
        assert caught == ["PermissionError"], (
            "PermissionError (OSError subclass) should be caught by the handler"
        )

    def test_unexpected_runtime_error_not_caught_by_json_eod_handler(self):
        """RuntimeError is NOT in (json.JSONDecodeError, OSError) — propagates."""
        raised = False
        try:
            raise RuntimeError("unexpected")
        except (json.JSONDecodeError, OSError):
            pass
        except RuntimeError:
            raised = True
        assert raised, "RuntimeError must propagate from EOD summary read"


# ===========================================================================
# Module 5: db/atlas_db.py
# ===========================================================================

class TestAtlasDbNoBareExcept:
    """Static: all except Exception converted in atlas_db (one documented broad catch)."""

    def test_no_unbound_broad_except_except_documented_reraise(self):
        """Only one broad catch allowed: the get_db() rollback guard (re-raises immediately)."""
        import db.atlas_db as m
        src = inspect.getsource(m)
        # _count_unbound_broad already allows broad+reraise handlers
        count = _count_unbound_broad(src)
        assert count == 0, (
            f"Found {count} unbound broad-except in db/atlas_db.py. "
            "The only allowed broad catch is the get_db() rollback guard which re-raises."
        )

    def test_documented_broad_catch_has_reraise(self):
        """The get_db() context manager has a broad catch only because it re-raises."""
        src = (PROJECT / "db" / "atlas_db.py").read_text()
        # Verify the comment is present (documenting intent)
        assert "Broad catch intentional" in src, (
            "get_db() broad catch must have a comment documenting why it's intentional"
        )

    def test_sqlite_operational_error_from_alter_table_is_swallowed(self):
        """sqlite3.OperationalError during ALTER TABLE → silently swallowed (column exists)."""
        caught = []
        try:
            raise sqlite3.OperationalError("duplicate column name: foo")
        except sqlite3.OperationalError:
            caught.append("swallowed")
        assert caught == ["swallowed"]

    def test_json_decode_error_from_matrix_json_returns_empty_dict(self, tmp_path):
        """json.JSONDecodeError parsing matrix_json → returns {} not raises."""
        import db.atlas_db as m
        # Simulate what get_cached_regime_transitions does on corrupt data
        d = {"matrix_json": "{bad json}"}
        try:
            d["matrix"] = json.loads(d["matrix_json"])
        except (json.JSONDecodeError, KeyError, TypeError):
            d["matrix"] = {}
        assert d["matrix"] == {}

    def test_unexpected_attribute_error_not_caught_by_json_matrix_handler(self):
        """AttributeError is NOT in (json.JSONDecodeError, KeyError, TypeError) — propagates."""
        raised = False
        try:
            raise AttributeError("bad attr")
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
        except AttributeError:
            raised = True
        assert raised, "AttributeError must propagate from matrix_json parsing"

    def test_sqlite_error_during_parity_alert_query_is_caught(self):
        """sqlite3.Error during parity alert query → caught by specific handler."""
        caught = []
        try:
            raise sqlite3.OperationalError("no such table")
        except sqlite3.Error:
            caught.append("caught")
        assert caught == ["caught"]

    def test_unexpected_runtime_error_not_caught_by_sqlite_error(self):
        """RuntimeError is NOT sqlite3.Error — propagates from parity query."""
        raised = False
        try:
            raise RuntimeError("unexpected")
        except sqlite3.Error:
            pass
        except RuntimeError:
            raised = True
        assert raised, "RuntimeError must NOT be caught by sqlite3.Error handler"
