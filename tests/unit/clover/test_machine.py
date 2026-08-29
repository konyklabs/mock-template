"""The order lifecycle: open -> locked, locked terminal. All JUDGMENT."""

from __future__ import annotations

import pytest

from vendorfake.clover.machine import ORDER_MACHINE, ORDER_MACHINE_NAME, OrderState
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine


@pytest.fixture
def machine() -> StateMachine:
    return StateMachine(ORDER_MACHINE)


def test_the_two_documented_values_and_the_canonical_casing(machine: StateMachine) -> None:
    """Docs mix Open/open across pages; the machine's canonical values are
    lowercase, which is what the seed will use."""
    assert machine.states() == ["open", "locked"]
    assert {s.value for s in OrderState} == set(machine.states())
    assert machine.initial == "open"
    assert machine.field == "state"
    assert ORDER_MACHINE_NAME == "order"


def test_open_reaches_locked_and_locked_goes_nowhere(machine: StateMachine) -> None:
    """JUDGMENT: 'locked is automatically set by Clover' is all the docs give;
    no transition table is published anywhere."""
    assert machine.can_transition("open", "locked")
    assert not machine.can_transition("locked", "open")


def test_locked_is_terminal_and_terminality_is_derived_not_flagged(machine: StateMachine) -> None:
    described = machine.describe()["states"]
    for name, state in described.items():
        assert state["terminal"] == (state["to"] == []), name
    assert machine.is_terminal("locked")
    assert not machine.is_terminal("open")


def test_echoing_open_on_an_open_order_is_legal_but_locked_stays_closed(machine: StateMachine) -> None:
    """The documented update shape POSTs the order back with changed fields, so
    "state": "open" on an open order is ordinary traffic."""
    assert machine.can_transition("open", "open")
    assert not machine.can_transition("locked", "locked")


def test_reopening_a_locked_order_raises_invalid_transition(machine: StateMachine) -> None:
    with pytest.raises(UnitError) as caught:
        machine.assert_transition("locked", "open", "order X")
    assert caught.value.kind is UnitErrorKind.INVALID_TRANSITION
