"""
tests/test_bare_except_latent_bugs_regression.py — Task #286

Regression tests for two latent bugs surfaced by commit dc811e8e
(bare-except conversion in 5 top-frequency modules).

Bug 1 — brokers/live_executor.py  _is_already_protected
────────────────────────────────────────────────────────
  OLD: ``except Exception:``  (bare) silently swallowed ALL exceptions,
       including programmer errors like TypeError and ValueError.
  NEW: ``except (OSError, ConnectionError, TimeoutError, AttributeError, RuntimeError)``
  
  The latent risk: if broker.get_open_orders() raised TypeError (e.g., SDK
  returned wrong type), the function returned False, Atlas believed there was
  no protective stop, and subsequently re-placed a stop → DOUBLE STOP at broker.

  These tests confirm that TypeError and ValueError propagate post-fix.

Bug 2 — regime/model.py  _apply_confirmation_gate (was _apply_confirmation_filter)
───────────────────────────────────────────────────────────────────────────────────
  OLD: ``except Exception:``  (bare) swallowed TypeError and AttributeError from
       wrong argument types, silently returning the current confirmed regime as
       if the DB were merely unavailable — masking programmer errors.
  NEW: ``except (sqlite3.Error, OSError)``

  The latent risk: if get_db() raised TypeError (e.g., wrong config passed
  somewhere upstream), _apply_confirmation_gate returned the wrong regime
  state silently.  This could cause the confirmation gate to be bypassed,
  locking in a regime transition that hadn't actually been confirmed.

  These tests confirm that TypeError and AttributeError propagate post-fix,
  while sqlite3.Error and OSError are still handled gracefully.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ATLAS_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ATLAS_ROOT))


# ────────────────────────────────────────────────────────────────────────────
# Bug 1 — _is_already_protected (brokers/live_executor.py)
# ────────────────────────────────────────────────────────────────────────────

class TestIsAlreadyProtectedPropagatesNarrowExceptions:
    """
    Regression: TypeError and ValueError from get_open_orders() MUST propagate.

    Pre-fix (bare except Exception): any exception → silently return False.
    Post-fix (narrow tuple):         TypeError/ValueError NOT in tuple → propagate.

    Why this matters: returning False causes place_stops_for_plan to add a second
    protective stop, creating a DOUBLE STOP condition at the broker.
    """

    @staticmethod
    def _fn():
        from brokers.live_executor import _is_already_protected  # noqa: PLC0415
        return _is_already_protected

    def test_type_error_from_get_open_orders_propagates(self):
        """
        TypeError is NOT in (OSError, ConnectionError, TimeoutError, AttributeError,
        RuntimeError) → it must propagate, not silently return False.

        Pre-fix behaviour that this guards against:
            except Exception:          # ← bare
                return False           # ← masked TypeError, double-stop risk
        """
        mock_broker = MagicMock()
        mock_broker.get_open_orders.side_effect = TypeError(
            "SDK returned unexpected type (simulated programmer error)"
        )
        with pytest.raises(TypeError, match="unexpected type"):
            self._fn()(mock_broker, "NVDA")

    def test_value_error_from_get_open_orders_propagates(self):
        """
        ValueError is NOT in the narrow tuple → propagates.
        Pre-fix: would have been silently swallowed.
        """
        mock_broker = MagicMock()
        mock_broker.get_open_orders.side_effect = ValueError(
            "invalid enum value from broker SDK"
        )
        with pytest.raises(ValueError):
            self._fn()(mock_broker, "AAPL")

    def test_attribute_error_from_get_open_orders_still_handled(self):
        """
        AttributeError IS explicitly in the tuple (broker SDK attribute issues
        are an expected failure mode, not a programmer bug).
        Confirm this still returns False gracefully (not a regression).
        """
        mock_broker = MagicMock()
        mock_broker.get_open_orders.side_effect = AttributeError(
            "broker object has no attribute get_open_orders"
        )
        result = self._fn()(mock_broker, "MSFT")
        assert result is False, (
            "AttributeError from get_open_orders should still return False "
            "(it is in the catch tuple — broker connectivity issue, not programmer error)"
        )

    def test_connection_error_still_returns_false(self):
        """ConnectionError is in the tuple → still returns False (not a regression)."""
        mock_broker = MagicMock()
        mock_broker.get_open_orders.side_effect = ConnectionError("broker offline")
        assert self._fn()(mock_broker, "GLD") is False

    def test_runtime_error_still_returns_false(self):
        """RuntimeError is in the tuple → still returns False (SDK-level failure)."""
        mock_broker = MagicMock()
        mock_broker.get_open_orders.side_effect = RuntimeError("SDK internal error")
        assert self._fn()(mock_broker, "XLY") is False


# ────────────────────────────────────────────────────────────────────────────
# Bug 2 — _apply_confirmation_gate (regime/model.py)
# ────────────────────────────────────────────────────────────────────────────

class TestApplyConfirmationGatePropagatesNarrowExceptions:
    """
    Regression: TypeError and AttributeError from get_db() MUST propagate.

    Pre-fix (bare except Exception): any exception → treated as 'DB unavailable',
    silently returned raw.state as confirmed — bypassing the confirmation gate.
    Post-fix (except (sqlite3.Error, OSError)): programmer errors propagate.

    Why this matters: a TypeError from a bad config upstream would silently lock
    in an unconfirmed regime change, corrupting the regime_history table.
    """

    @staticmethod
    def _model():
        from regime.model import RegimeModel  # noqa: PLC0415
        return RegimeModel()

    @staticmethod
    def _make_raw():
        """Minimal RegimeClassification instance for testing."""
        from regime.model import RegimeClassification  # noqa: PLC0415
        from regime.states import RegimeState           # noqa: PLC0415
        return RegimeClassification(
            state=RegimeState.BULL_RISK_ON,
            scores={"composite": 0.5},
            active_universes=["sp500"],
            sizing_multiplier=1.0,
            max_positions=8,
            enabled_strategies=["momentum_breakout"],
            reasoning="regression test fixture",
            model_version="v3",
        )

    def test_type_error_from_get_db_propagates(self):
        """
        TypeError is NOT in (sqlite3.Error, OSError) → it must propagate out of
        _apply_confirmation_gate.

        Pre-fix behaviour that this guards against:
            except Exception:           # ← bare
                return raw.state, None  # ← masked TypeError, wrong regime persisted
        """
        model = self._model()
        raw = self._make_raw()

        with patch("db.atlas_db.get_db", side_effect=TypeError("wrong type from upstream")):
            with pytest.raises(TypeError, match="wrong type"):
                model._apply_confirmation_gate(
                    raw=raw,
                    effective_date="2026-04-30",
                    confirmation_days=3,
                )

    def test_attribute_error_from_get_db_propagates(self):
        """
        AttributeError is NOT in (sqlite3.Error, OSError) → propagates.
        Pre-fix: was silently swallowed and regime treated as 'unconfirmed DB'.
        """
        model = self._model()
        raw = self._make_raw()

        with patch("db.atlas_db.get_db", side_effect=AttributeError("bad attribute")):
            with pytest.raises(AttributeError):
                model._apply_confirmation_gate(
                    raw=raw,
                    effective_date="2026-04-30",
                    confirmation_days=3,
                )

    def test_sqlite_operational_error_handled_gracefully(self):
        """
        sqlite3.OperationalError IS in (sqlite3.Error, OSError) → still handled.
        Confirm DB-unavailable path still works (not a regression).
        """
        model = self._model()
        raw = self._make_raw()

        with patch("db.atlas_db.get_db",
                   side_effect=sqlite3.OperationalError("no such table: regime_history")):
            # Should NOT raise — DB unavailable path returns raw state immediately
            confirmed_state, pending_str = model._apply_confirmation_gate(
                raw=raw,
                effective_date="2026-04-30",
                confirmation_days=3,
            )
        # When DB is unavailable (no history), raw state is accepted
        assert confirmed_state is not None

    def test_os_error_from_get_db_handled_gracefully(self):
        """
        OSError IS in (sqlite3.Error, OSError) → still handled.
        """
        model = self._model()
        raw = self._make_raw()

        with patch("db.atlas_db.get_db", side_effect=OSError("db file missing")):
            confirmed_state, _ = model._apply_confirmation_gate(
                raw=raw,
                effective_date="2026-04-30",
                confirmation_days=3,
            )
        assert confirmed_state is not None

    def test_zero_division_error_propagates(self):
        """
        ZeroDivisionError is NOT in the tuple → propagates.
        Belt-and-suspenders: verifies no silent swallow for any programmer error.
        """
        model = self._model()
        raw = self._make_raw()

        with patch("db.atlas_db.get_db", side_effect=ZeroDivisionError("div by zero")):
            with pytest.raises(ZeroDivisionError):
                model._apply_confirmation_gate(
                    raw=raw,
                    effective_date="2026-04-30",
                    confirmation_days=3,
                )
