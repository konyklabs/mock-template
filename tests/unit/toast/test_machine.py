"""The two lifecycles: what is documented, what is labelled."""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.toast.machine import (
    CHECK_MACHINE,
    CHECK_MACHINE_NAME,
    GUEST_ORDER_MACHINE,
    GUEST_ORDER_MACHINE_NAME,
    CheckPaymentStatus,
    GuestOrderStatus,
)


@pytest.fixture
def check() -> StateMachine:
    return StateMachine(CHECK_MACHINE)


@pytest.fixture
def order() -> StateMachine:
    return StateMachine(GUEST_ORDER_MACHINE)


def test_the_check_machine_carries_the_three_enum_values_and_the_voided_one(check: StateMachine) -> None:
    """OPEN|PAID|CLOSED from the schema; VOIDED from the void walkthrough."""
    assert check.states() == ["OPEN", "PAID", "CLOSED", "VOIDED"]
    assert {s.value for s in CheckPaymentStatus} == set(check.states())
    assert check.initial == "OPEN"
    assert check.field == "paymentStatus"
    assert CHECK_MACHINE_NAME == "check"


def test_check_transitions_and_terminals(check: StateMachine) -> None:
    assert check.can_transition("OPEN", "PAID")
    assert check.can_transition("OPEN", "VOIDED")
    assert check.can_transition("PAID", "VOIDED")
    assert check.can_transition("PAID", "CLOSED")
    assert not check.can_transition("PAID", "OPEN")
    assert not check.can_transition("OPEN", "CLOSED")
    assert check.is_terminal("VOIDED") and check.is_terminal("CLOSED")
    assert not check.can_transition("OPEN", "OPEN")  # no self-transitions declared


def test_a_voided_check_refuses_any_mutation(check: StateMachine) -> None:
    """'Once an order has been voided, it can not be updated.'"""
    with pytest.raises(UnitError) as caught:
        check.assert_mutable("VOIDED", "check X")
    assert caught.value.kind is UnitErrorKind.INVALID_TRANSITION


def test_the_guest_order_machine_has_the_documented_first_row(order: StateMachine) -> None:
    """RECEIVED -> IN_PREPARATION | READY_FOR_PICKUP | CLOSED | VOIDED is the
    documented list; the rest is forward-only JUDGMENT."""
    assert order.states() == ["RECEIVED", "IN_PREPARATION", "READY_FOR_PICKUP", "CLOSED", "VOIDED"]
    assert {s.value for s in GuestOrderStatus} == set(order.states())
    assert order.initial == "RECEIVED"
    assert order.field == "guestOrderStatus"
    assert GUEST_ORDER_MACHINE_NAME == "order"
    for target in ("IN_PREPARATION", "READY_FOR_PICKUP", "CLOSED", "VOIDED"):
        assert order.can_transition("RECEIVED", target), target
    assert order.can_transition("IN_PREPARATION", "READY_FOR_PICKUP")
    assert not order.can_transition("READY_FOR_PICKUP", "IN_PREPARATION")
    assert not order.can_transition("IN_PREPARATION", "RECEIVED")
    assert order.is_terminal("CLOSED") and order.is_terminal("VOIDED")


def test_terminality_is_derived_not_flagged_on_both_machines(check: StateMachine, order: StateMachine) -> None:
    for machine in (check, order):
        for name, state in machine.describe()["states"].items():
            assert state["terminal"] == (state["to"] == []), name
