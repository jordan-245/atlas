"""Trade state machine — formal state transitions for the trades table.

Status: SCAFFOLD. The Python enforcement is active immediately, but the
SQLite CHECK constraint is documented (in a SQL comment block) and NOT
applied yet — that requires the B.2 cutover to land first.

Backfill helper `classify_existing_trade()` is provided for the migration.

Design: docs/phase-c-trade-state-machine.md
"""
from __future__ import annotations

from enum import Enum
from typing import Optional


class TradeState(Enum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PROTECTED = "PROTECTED"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    SETTLED = "SETTLED"
    ERROR = "ERROR"


# Adjacency map of legal transitions
LEGAL_TRANSITIONS: dict[TradeState, set[TradeState]] = {
    TradeState.PROPOSED: {TradeState.APPROVED, TradeState.ERROR},
    TradeState.APPROVED: {TradeState.SUBMITTED, TradeState.ERROR},
    TradeState.SUBMITTED: {TradeState.FILLED, TradeState.ERROR},
    TradeState.FILLED: {TradeState.PROTECTED, TradeState.CLOSING, TradeState.ERROR},
    TradeState.PROTECTED: {TradeState.CLOSING, TradeState.ERROR},
    TradeState.CLOSING: {TradeState.CLOSED, TradeState.ERROR},
    TradeState.CLOSED: {TradeState.SETTLED},
    TradeState.SETTLED: set(),  # terminal
    TradeState.ERROR: {TradeState.SUBMITTED, TradeState.CLOSING},  # operator-driven
}


class IllegalTransitionError(ValueError):
    """Raised when transition_trade() is called with a disallowed pair."""


def can_transition(from_state: Optional[TradeState], to_state: TradeState) -> bool:
    """Return True iff (from_state → to_state) is legal.

    A None from_state allows transition to PROPOSED only (initial state).
    """
    if from_state is None:
        return to_state == TradeState.PROPOSED
    return to_state in LEGAL_TRANSITIONS.get(from_state, set())


def transition_trade(from_state: Optional[TradeState], to_state: TradeState) -> TradeState:
    """Validate transition, raise on illegal."""
    if not can_transition(from_state, to_state):
        raise IllegalTransitionError(
            f"Illegal trade state transition: {from_state} → {to_state}. "
            f"Legal targets from {from_state}: {LEGAL_TRANSITIONS.get(from_state, set())}"
        )
    return to_state


def classify_existing_trade(trade_row: dict) -> TradeState:
    """Backfill: classify a trades-row dict into a state based on existing fields.

    trade_row should have at least: status, entry_order_id, entry_price,
    stop_order_id, exit_order_id, exit_price, exit_date.
    """
    status = (trade_row.get("status") or "").lower()
    if status == "closed":
        # CLOSED → SETTLED if there's a broker_orders linkage; otherwise CLOSED
        # For now (pre-broker_orders backfill), default to CLOSED.
        return TradeState.CLOSED
    if status == "error":
        return TradeState.ERROR
    # status == "open" — drill in
    if not trade_row.get("entry_order_id"):
        return TradeState.PROPOSED  # never submitted
    if not trade_row.get("entry_price"):
        return TradeState.SUBMITTED  # placed but not filled
    if trade_row.get("exit_order_id"):
        return TradeState.CLOSING  # exit in flight
    if trade_row.get("stop_order_id"):
        return TradeState.PROTECTED
    return TradeState.FILLED  # filled but no protective stop


# ─── DRAFT: SQLite CHECK constraint to enable post-B.2 ─────────────────────
# DO NOT enable this yet — requires migration of all existing rows first.
#
# ALTER TABLE trades ADD COLUMN state TEXT DEFAULT NULL;
# (then in a fresh-table-recreate migration, add:)
#   CHECK (state IS NULL OR state IN (
#       'PROPOSED', 'APPROVED', 'SUBMITTED', 'FILLED',
#       'PROTECTED', 'CLOSING', 'CLOSED', 'SETTLED', 'ERROR'
#   ))
