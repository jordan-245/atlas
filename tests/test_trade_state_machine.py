"""Tests for core/trade_state_machine.py — Phase C.1 scaffold.

Covers:
  - Every legal transition succeeds
  - Every illegal pair raises IllegalTransitionError
  - can_transition(None, ...) edge cases
  - classify_existing_trade canonical fixtures
  - Terminal state invariants
  - LEGAL_TRANSITIONS completeness

Run with: python -m pytest tests/test_trade_state_machine.py -v
"""
from __future__ import annotations

import sys
from itertools import product
from pathlib import Path
from typing import Optional

import pytest

ATLAS_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ATLAS_ROOT))

from core.trade_state_machine import (  # noqa: E402
    LEGAL_TRANSITIONS,
    IllegalTransitionError,
    TradeState,
    can_transition,
    classify_existing_trade,
    transition_trade,
)


class TestLegalTransitions:
    """Every legal transition in LEGAL_TRANSITIONS must succeed."""

    def test_proposed_to_approved(self) -> None:
        result = transition_trade(TradeState.PROPOSED, TradeState.APPROVED)
        assert result == TradeState.APPROVED

    def test_proposed_to_error(self) -> None:
        result = transition_trade(TradeState.PROPOSED, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_approved_to_submitted(self) -> None:
        result = transition_trade(TradeState.APPROVED, TradeState.SUBMITTED)
        assert result == TradeState.SUBMITTED

    def test_approved_to_error(self) -> None:
        result = transition_trade(TradeState.APPROVED, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_submitted_to_filled(self) -> None:
        result = transition_trade(TradeState.SUBMITTED, TradeState.FILLED)
        assert result == TradeState.FILLED

    def test_submitted_to_error(self) -> None:
        result = transition_trade(TradeState.SUBMITTED, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_filled_to_protected(self) -> None:
        result = transition_trade(TradeState.FILLED, TradeState.PROTECTED)
        assert result == TradeState.PROTECTED

    def test_filled_to_closing(self) -> None:
        result = transition_trade(TradeState.FILLED, TradeState.CLOSING)
        assert result == TradeState.CLOSING

    def test_filled_to_error(self) -> None:
        result = transition_trade(TradeState.FILLED, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_protected_to_closing(self) -> None:
        result = transition_trade(TradeState.PROTECTED, TradeState.CLOSING)
        assert result == TradeState.CLOSING

    def test_protected_to_error(self) -> None:
        result = transition_trade(TradeState.PROTECTED, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_closing_to_closed(self) -> None:
        result = transition_trade(TradeState.CLOSING, TradeState.CLOSED)
        assert result == TradeState.CLOSED

    def test_closing_to_error(self) -> None:
        result = transition_trade(TradeState.CLOSING, TradeState.ERROR)
        assert result == TradeState.ERROR

    def test_closed_to_settled(self) -> None:
        result = transition_trade(TradeState.CLOSED, TradeState.SETTLED)
        assert result == TradeState.SETTLED

    def test_error_to_submitted(self) -> None:
        """ERROR → SUBMITTED: operator-driven retry after investigation."""
        result = transition_trade(TradeState.ERROR, TradeState.SUBMITTED)
        assert result == TradeState.SUBMITTED

    def test_error_to_closing(self) -> None:
        """ERROR → CLOSING: operator-driven cleanup."""
        result = transition_trade(TradeState.ERROR, TradeState.CLOSING)
        assert result == TradeState.CLOSING

    def test_all_legal_transitions_in_adjacency_map(self) -> None:
        """Exhaustively verify every entry in LEGAL_TRANSITIONS succeeds."""
        for from_state, targets in LEGAL_TRANSITIONS.items():
            for to_state in targets:
                result = transition_trade(from_state, to_state)
                assert result == to_state, (
                    f"transition_trade({from_state}, {to_state}) returned {result}"
                )


class TestIllegalTransitions:
    """Every illegal pair must raise IllegalTransitionError."""

    def _illegal_pairs(self):
        """Generate (from_state, to_state) pairs NOT in LEGAL_TRANSITIONS."""
        all_states = list(TradeState)
        for from_state, to_state in product(all_states, all_states):
            if to_state not in LEGAL_TRANSITIONS.get(from_state, set()):
                yield from_state, to_state

    def test_settled_is_terminal(self) -> None:
        """SETTLED has zero outbound transitions — all targets illegal."""
        for to_state in TradeState:
            with pytest.raises(IllegalTransitionError):
                transition_trade(TradeState.SETTLED, to_state)

    def test_closed_cannot_go_to_protected(self) -> None:
        """Explicit: CLOSED → PROTECTED is forbidden (cannot re-protect a closed trade)."""
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.CLOSED, TradeState.PROTECTED)

    def test_closed_cannot_go_to_filled(self) -> None:
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.CLOSED, TradeState.FILLED)

    def test_proposed_cannot_skip_to_filled(self) -> None:
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.PROPOSED, TradeState.FILLED)

    def test_proposed_cannot_skip_to_closed(self) -> None:
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.PROPOSED, TradeState.CLOSED)

    def test_approved_cannot_go_directly_to_closed(self) -> None:
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.APPROVED, TradeState.CLOSED)

    def test_filled_cannot_go_to_proposed(self) -> None:
        """No backward transitions without an ERROR gateway."""
        with pytest.raises(IllegalTransitionError):
            transition_trade(TradeState.FILLED, TradeState.PROPOSED)

    def test_error_message_is_informative(self) -> None:
        """IllegalTransitionError message names both states."""
        with pytest.raises(IllegalTransitionError, match="SETTLED"):
            transition_trade(TradeState.SETTLED, TradeState.PROPOSED)

    def test_all_illegal_transitions_raise(self) -> None:
        """Exhaustive check: every non-legal pair raises."""
        for from_state, to_state in self._illegal_pairs():
            with pytest.raises(IllegalTransitionError):
                transition_trade(from_state, to_state)


class TestCanTransition:
    """can_transition() edge cases including None from_state."""

    def test_none_allows_proposed(self) -> None:
        """None from_state is valid only for PROPOSED (initial creation)."""
        assert can_transition(None, TradeState.PROPOSED) is True

    def test_none_denies_filled(self) -> None:
        assert can_transition(None, TradeState.FILLED) is False

    def test_none_denies_every_non_proposed_state(self) -> None:
        for state in TradeState:
            if state != TradeState.PROPOSED:
                assert can_transition(None, state) is False, (
                    f"Expected can_transition(None, {state}) == False"
                )

    def test_none_from_returns_true_for_proposed(self) -> None:
        assert can_transition(None, TradeState.PROPOSED) is True

    def test_legal_pair_returns_true(self) -> None:
        assert can_transition(TradeState.PROPOSED, TradeState.APPROVED) is True

    def test_illegal_pair_returns_false(self) -> None:
        assert can_transition(TradeState.SETTLED, TradeState.PROPOSED) is False

    def test_settled_has_no_outbound(self) -> None:
        """SETTLED is terminal — can_transition returns False for all targets."""
        for state in TradeState:
            assert can_transition(TradeState.SETTLED, state) is False


class TestClassifyExistingTrade:
    """classify_existing_trade() maps existing trade rows to states."""

    def _row(self, **kwargs) -> dict:
        base = {
            "status": "open",
            "entry_order_id": None,
            "entry_price": None,
            "stop_order_id": None,
            "tp_order_id": None,
            "exit_order_id": None,
            "exit_price": None,
            "exit_date": None,
        }
        base.update(kwargs)
        return base

    def test_open_no_entry_order_is_proposed(self) -> None:
        row = self._row(status="open", entry_order_id=None)
        assert classify_existing_trade(row) == TradeState.PROPOSED

    def test_open_entry_order_no_fill_is_submitted(self) -> None:
        row = self._row(status="open", entry_order_id="ord-123", entry_price=None)
        assert classify_existing_trade(row) == TradeState.SUBMITTED

    def test_open_filled_no_stop_is_filled(self) -> None:
        row = self._row(
            status="open",
            entry_order_id="ord-123",
            entry_price=100.0,
            stop_order_id=None,
            exit_order_id=None,
        )
        assert classify_existing_trade(row) == TradeState.FILLED

    def test_open_filled_with_stop_is_protected(self) -> None:
        row = self._row(
            status="open",
            entry_order_id="ord-123",
            entry_price=100.0,
            stop_order_id="stop-456",
            exit_order_id=None,
        )
        assert classify_existing_trade(row) == TradeState.PROTECTED

    def test_open_with_exit_order_is_closing(self) -> None:
        row = self._row(
            status="open",
            entry_order_id="ord-123",
            entry_price=100.0,
            stop_order_id="stop-456",
            exit_order_id="exit-789",
        )
        assert classify_existing_trade(row) == TradeState.CLOSING

    def test_closed_status_is_closed(self) -> None:
        row = self._row(status="closed", exit_price=105.0)
        assert classify_existing_trade(row) == TradeState.CLOSED

    def test_error_status_is_error(self) -> None:
        row = self._row(status="error")
        assert classify_existing_trade(row) == TradeState.ERROR

    def test_status_case_insensitive(self) -> None:
        """Status field matching is case-insensitive."""
        assert classify_existing_trade({"status": "CLOSED"}) == TradeState.CLOSED
        assert classify_existing_trade({"status": "ERROR"}) == TradeState.ERROR

    def test_missing_status_treated_as_open(self) -> None:
        """None/missing status falls through to 'open' logic."""
        row = self._row(status=None, entry_order_id=None)
        assert classify_existing_trade(row) == TradeState.PROPOSED


class TestTerminalStates:
    """SETTLED is terminal — verify zero outbound edges."""

    def test_settled_has_empty_outbound_set(self) -> None:
        assert LEGAL_TRANSITIONS[TradeState.SETTLED] == set()

    def test_settled_outbound_count_is_zero(self) -> None:
        assert len(LEGAL_TRANSITIONS[TradeState.SETTLED]) == 0

    def test_all_states_present_in_legal_transitions(self) -> None:
        """Every TradeState must appear as a key in LEGAL_TRANSITIONS."""
        for state in TradeState:
            assert state in LEGAL_TRANSITIONS, (
                f"TradeState.{state.name} missing from LEGAL_TRANSITIONS"
            )
