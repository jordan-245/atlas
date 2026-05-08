"""
tests/test_protective_orders_regression.py — Task #286

Explicit regression guards for two latent bugs surfaced by commit dc811e8e
(bare-except narrowing in 5 modules).

Test 1 — _is_already_protected  (brokers/preflight.py, alias in live_executor.py)
──────────────────────────────────────────────────────────────────────────────────
  OLD bare-except: ``except Exception:`` silently returned False for *any*
  exception raised by ``broker.get_open_orders()``, including AttributeError
  when the broker object was missing the method entirely.  The exception was
  swallowed with zero log visibility.

  POST-FIX: ``except (OSError, ConnectionError, TimeoutError, AttributeError,
  RuntimeError)`` — AttributeError IS still caught (it represents a broker
  SDK / connectivity issue, not a programmer bug), but it is now logged at
  DEBUG level so the anomaly is auditable.

  Regression guard: pass a bare ``types.SimpleNamespace()`` as broker
  (no ``get_open_orders`` attr).  Calling ``broker.get_open_orders()`` raises
  ``AttributeError``.  Post-fix contract: returns False (conservative).

Test 2 — _apply_confirmation_filter  (regime/model.py: _apply_confirmation_gate)
──────────────────────────────────────────────────────────────────────────────────
  OLD bare-except: ``except Exception:`` in ``_apply_confirmation_gate``
  swallowed *any* exception from ``get_db()`` including ``TypeError``.  A
  bad config type upstream would silently be treated as "DB unavailable"
  → raw regime state returned as confirmed → confirmation gate bypassed
  → potential corrupt ``regime_history`` rows.

  POST-FIX: ``except (sqlite3.Error, OSError)`` — TypeError is NOT in the
  tuple, so it propagates.  Programmer errors are now visible instead of
  masking a silent wrong-regime result.

  Regression guard: patch ``get_db()`` to raise ``TypeError``.  Post-fix
  contract: TypeError propagates out of the function.  Pre-fix behaviour
  that this guards against: TypeError swallowed, wrong regime state returned.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATLAS_ROOT))


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — _is_already_protected
# ─────────────────────────────────────────────────────────────────────────────

def test_is_already_protected_handles_position_without_attr() -> None:
    """
    Pass a SimpleNamespace broker (no get_open_orders attribute) — this forces
    AttributeError inside the function's try-block.

    Post-fix contract (dc811e8e):
      AttributeError IS in the catch tuple → returns False (conservative,
      let placement attempt) AND logs at DEBUG.

    Pre-fix contract (bare except Exception):
      Also returned False, but with ZERO log visibility — any programmer
      error from the broker SDK was silently swallowed.

    This test pins the post-fix return value: the function MUST return False
    (not propagate AttributeError) because AttributeError is explicitly in
    the narrowed exception tuple and represents a broker connectivity issue.
    """
    # Import via the back-compat alias exposed from live_executor; same function
    # as brokers.preflight.is_already_protected.
    from brokers.live_executor import _is_already_protected  # noqa: PLC0415

    # SimpleNamespace has no get_open_orders — accessing it raises AttributeError.
    broker_missing_method = types.SimpleNamespace()

    result = _is_already_protected(broker_missing_method, "AAPL")

    assert result is False, (
        "_is_already_protected must return False (not raise AttributeError) "
        "when the broker object has no get_open_orders method.  "
        "AttributeError is explicitly in the narrowed except tuple, so the "
        "function returns the conservative fallback."
    )


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — _apply_confirmation_filter  (the function is _apply_confirmation_gate)
# ─────────────────────────────────────────────────────────────────────────────

def test_apply_confirmation_filter_handles_typeerror() -> None:
    """
    Feed a TypeError from get_db() — simulates wrong config type passed
    upstream — and assert it propagates out of _apply_confirmation_gate.

    Post-fix contract (dc811e8e):
      ``except (sqlite3.Error, OSError)`` — TypeError is NOT in the tuple
      → propagates immediately (programmer error is visible).

    Pre-fix contract (bare except Exception):
      TypeError was swallowed and treated as "DB unavailable" → the raw
      regime state (unconfirmed) was returned as the confirmed state →
      confirmation gate bypassed silently.  This masked bugs in upstream
      config handling.

    Why TypeError specifically?  It is the exception most likely to arise
    from a wrong argument type in the config chain (e.g. ``get_db(None)``
    where a path string is required).  The old bare-except masked exactly
    this class of programmer error.
    """
    from regime.model import RegimeClassification, RegimeModel  # noqa: PLC0415
    from regime.states import RegimeState  # noqa: PLC0415

    model = RegimeModel()

    raw = RegimeClassification(
        state=RegimeState.BULL_RISK_ON,
        scores={"composite": 0.5},
        active_universes=["sp500"],
        sizing_multiplier=1.0,
        max_positions=8,
        enabled_strategies=["momentum_breakout"],
        reasoning="regression test fixture",
        model_version="v3",
    )

    # Patch get_db to raise TypeError (wrong type upstream — programmer error).
    # Pre-fix: would be silently swallowed, returning raw.state as confirmed.
    # Post-fix: propagates — the TypeError is NOT in (sqlite3.Error, OSError).
    with patch("db.atlas_db.get_db", side_effect=TypeError("bad DB config type")):
        with pytest.raises(TypeError, match="bad DB config type"):
            model._apply_confirmation_gate(
                raw=raw,
                effective_date="2026-05-07",
                confirmation_days=3,
            )
