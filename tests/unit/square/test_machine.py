"""The order lifecycle, including the defect the core's rebuild closes."""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.square.machine import ORDER_MACHINE, ORDER_MACHINE_NAME, OrderState


@pytest.fixture
def machine() -> StateMachine:
    return StateMachine(ORDER_MACHINE)


def test_the_four_documented_states_and_no_others(machine: StateMachine) -> None:
    assert machine.states() == ["DRAFT", "OPEN", "COMPLETED", "CANCELED"]
    assert {s.value for s in OrderState} == set(machine.states())
    assert machine.initial == "OPEN"
    assert machine.field == "state"
    assert ORDER_MACHINE_NAME == "order"


def test_terminal_is_exactly_the_states_with_no_outgoing_edges(machine: StateMachine) -> None:
    """'Orders with a COMPLETED or CANCELED state cannot be updated.'"""
    described = machine.describe()["states"]
    for name, state in described.items():
        assert state["terminal"] == (state["to"] == []), name
    assert described["COMPLETED"]["terminal"] is True
    assert described["CANCELED"]["terminal"] is True
    assert described["OPEN"]["terminal"] is False
    assert described["DRAFT"]["terminal"] is False


def test_the_documented_transitions(machine: StateMachine) -> None:
    assert machine.can_transition("DRAFT", "OPEN")
    assert machine.can_transition("OPEN", "COMPLETED")
    assert machine.can_transition("OPEN", "CANCELED")
    # JUDGMENT: an unpaid draft can be abandoned. Square publishes no matrix.
    assert machine.can_transition("DRAFT", "CANCELED")
    # An order does not go back to draft, and a terminal order goes nowhere.
    assert not machine.can_transition("OPEN", "DRAFT")
    assert not machine.can_transition("COMPLETED", "CANCELED")
    assert not machine.can_transition("CANCELED", "OPEN")


def test_a_self_transition_is_legal_only_where_square_needs_it(machine: StateMachine) -> None:
    """UpdateOrder takes the order object, so echoing "state": "OPEN" on an
    order that is already OPEN is a documented, legal request. Paying an order
    that is already COMPLETED is not, and that is the double-payment the
    reference's identity short-circuit allowed."""
    assert machine.can_transition("OPEN", "OPEN")
    assert machine.can_transition("DRAFT", "DRAFT")
    assert not machine.can_transition("COMPLETED", "COMPLETED")
    assert not machine.can_transition("CANCELED", "CANCELED")


def test_paying_a_completed_order_is_refused(machine: StateMachine) -> None:
    with pytest.raises(UnitError) as caught:
        machine.assert_transition("COMPLETED", "COMPLETED", "Order CAIS1")
    assert caught.value.kind is UnitErrorKind.INVALID_TRANSITION
    assert "terminal" in str(caught.value)


def test_an_unknown_state_is_a_typo_not_a_sequencing_error(machine: StateMachine) -> None:
    with pytest.raises(UnitError) as caught:
        machine.assert_transition("OPEN", "PAID", "Order CAIS1")
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.info is not None
    assert caught.value.info["allowed"] == ["DRAFT", "OPEN", "COMPLETED", "CANCELED"]


def test_any_mutation_of_a_terminal_order_is_refused(machine: StateMachine) -> None:
    """Not just a state change: a completed order does not get new line items."""
    for state in ("COMPLETED", "CANCELED"):
        with pytest.raises(UnitError) as caught:
            machine.assert_mutable(state, "Order CAIS1")
        assert caught.value.kind is UnitErrorKind.INVALID_TRANSITION
    machine.assert_mutable("OPEN", "Order CAIS1")
    machine.assert_mutable("DRAFT", "Order CAIS1")
