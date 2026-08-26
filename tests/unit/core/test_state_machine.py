"""Semantics of the rebuilt state machine.

The two rebuilt behaviours get the most attention, because they are the two
places where doing what the reference does is a defect: a self-transition is
illegal unless declared, and terminality is derived from an empty ``to`` rather
than stored beside it.
"""

from __future__ import annotations

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.machine import MachineDef, StateDef, StateMachine

# A lifecycle with one of each shape: a state that allows a no-op re-send, one
# that does not, and two terminal states.
ORDERS = MachineDef(
    field="state",
    initial="DRAFT",
    states={
        "DRAFT": StateDef(summary="Not yet submitted.", to=("OPEN", "CANCELED"), allow_self=True),
        "OPEN": StateDef(summary="Awaiting payment.", to=("COMPLETED", "CANCELED")),
        "COMPLETED": StateDef(summary="Paid."),
        "CANCELED": StateDef(summary="Abandoned."),
    },
)


def machine() -> StateMachine:
    return StateMachine(ORDERS)


# -- the double-pay defect --------------------------------------------------


def test_self_transition_is_illegal_unless_declared() -> None:
    """The reference returns early on ``from === to``; here COMPLETED->COMPLETED
    is refused, which is what stops an already-paid order being paid twice."""
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("COMPLETED", "COMPLETED", "Order o1")
    assert raised.value.kind is UnitErrorKind.INVALID_TRANSITION


def test_self_transition_on_a_non_terminal_state_is_still_illegal_without_allow_self() -> None:
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("OPEN", "OPEN", "Order o1")
    assert raised.value.kind is UnitErrorKind.INVALID_TRANSITION
    assert raised.value.detail == "Order o1 cannot move from OPEN to OPEN."


def test_a_declared_self_transition_is_a_legal_no_op() -> None:
    machine().assert_transition("DRAFT", "DRAFT", "Order o1")
    assert machine().can_transition("DRAFT", "DRAFT") is True
    assert machine().can_transition("OPEN", "OPEN") is False


def test_an_illegal_self_transition_is_invalid_transition_not_invalid_value() -> None:
    """The target IS a declared value, so the caller does not have a typo. The
    two kinds carry different advice and conformance asserts on the kind."""
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("CANCELED", "CANCELED", "Order o1")
    assert raised.value.kind is UnitErrorKind.INVALID_TRANSITION


# -- derived terminality ----------------------------------------------------


def test_terminal_is_exactly_no_outbound_transitions() -> None:
    for name, state in ORDERS.states.items():
        assert state.terminal == (state.to == ()), name
    assert machine().is_terminal("COMPLETED") is True
    assert machine().is_terminal("OPEN") is False


def test_a_terminal_state_cannot_also_allow_a_self_transition() -> None:
    """The reference can express `{terminal: true, to: ['PAID']}`. This cannot
    express the equivalent contradiction at all."""
    with pytest.raises(ValueError, match="terminal state"):
        StateDef(to=(), allow_self=True)


def test_an_undeclared_state_is_not_terminal() -> None:
    assert machine().is_terminal("NOPE") is False


# -- error shapes -----------------------------------------------------------


def test_unknown_target_is_invalid_value_and_lists_every_state() -> None:
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("OPEN", "PAID", "Order o1")
    err = raised.value
    assert err.kind is UnitErrorKind.INVALID_VALUE
    assert err.detail == "'PAID' is not a valid state for Order o1."
    assert err.field == "state"
    assert err.info == {"allowed": ["DRAFT", "OPEN", "COMPLETED", "CANCELED"]}


def test_allowed_is_in_declaration_order_not_sorted() -> None:
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("OPEN", "PAID", "Order o1")
    assert raised.value.info is not None
    assert raised.value.info["allowed"] != sorted(raised.value.info["allowed"])


def test_forbidden_move_from_a_live_state_names_only_what_is_reachable() -> None:
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("DRAFT", "COMPLETED", "Order o1")
    err = raised.value
    assert err.kind is UnitErrorKind.INVALID_TRANSITION
    assert err.detail == "Order o1 cannot move from DRAFT to COMPLETED."
    assert err.info == {
        "from": "DRAFT",
        "to": "COMPLETED",
        "terminal": False,
        "allowed": ["OPEN", "CANCELED"],
    }


def test_forbidden_move_from_a_terminal_state_reads_differently() -> None:
    """Two different sentences for two different situations, and consumer tests
    assert on the substring 'terminal'."""
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("COMPLETED", "OPEN", "Order o1")
    err = raised.value
    assert err.detail == "Order o1 is in the terminal state COMPLETED and cannot be updated."
    assert err.info == {"from": "COMPLETED", "to": "OPEN", "terminal": True, "allowed": []}


def test_move_from_an_undeclared_state_reports_nothing_reachable() -> None:
    with pytest.raises(UnitError) as raised:
        machine().assert_transition("GHOST", "OPEN", "Order o1")
    assert raised.value.kind is UnitErrorKind.INVALID_TRANSITION
    assert raised.value.info == {"from": "GHOST", "to": "OPEN", "terminal": False, "allowed": []}


# -- assert_mutable ---------------------------------------------------------


def test_assert_mutable_refuses_every_terminal_state() -> None:
    for name in ("COMPLETED", "CANCELED"):
        with pytest.raises(UnitError) as raised:
            machine().assert_mutable(name, "Order o1")
        assert raised.value.kind is UnitErrorKind.INVALID_TRANSITION
        assert raised.value.info == {"from": name, "terminal": True}
        assert raised.value.detail == f"Order o1 is in the terminal state {name} and cannot be updated."


def test_assert_mutable_permits_every_live_state() -> None:
    for name in ("DRAFT", "OPEN"):
        machine().assert_mutable(name, "Order o1")


def test_assert_mutable_refuses_a_mutation_that_is_not_a_state_change() -> None:
    """The whole point of the second guard: paying a CANCELED order fails even
    though a payment says nothing about state."""
    with pytest.raises(UnitError):
        machine().assert_mutable("CANCELED", "Order o1")


# -- construction-time validation -------------------------------------------


def test_an_undeclared_transition_target_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="undeclared transition target 'PAID'"):
        MachineDef(field="state", initial="OPEN", states={"OPEN": StateDef(to=("PAID",))})


def test_an_undeclared_initial_state_is_refused_at_construction() -> None:
    with pytest.raises(ValueError, match="initial state 'NEW'"):
        MachineDef(field="state", initial="NEW", states={"OPEN": StateDef()})


def test_a_machine_with_no_states_is_refused() -> None:
    with pytest.raises(ValueError, match="declares no states"):
        MachineDef(field="state", initial="OPEN", states={})


def test_a_machine_with_no_field_is_refused() -> None:
    with pytest.raises(ValueError, match="entity field"):
        MachineDef(field="", initial="OPEN", states={"OPEN": StateDef()})


def test_the_states_mapping_cannot_be_mutated_after_construction() -> None:
    with pytest.raises(TypeError):
        ORDERS.states["HACKED"] = StateDef()  # type: ignore[index]


def test_a_states_dict_passed_in_is_not_aliased() -> None:
    supplied = {"OPEN": StateDef()}
    built = MachineDef(field="state", initial="OPEN", states=supplied)
    supplied["EXTRA"] = StateDef()
    assert built.states.keys() == {"OPEN"}


# -- the control-plane projection -------------------------------------------


def test_describe_matches_the_published_shape() -> None:
    assert machine().describe() == {
        "field": "state",
        "initial": "DRAFT",
        "states": {
            "DRAFT": {
                "summary": "Not yet submitted.",
                "to": ["OPEN", "CANCELED"],
                "allow_self": True,
                "terminal": False,
            },
            "OPEN": {
                "summary": "Awaiting payment.",
                "to": ["COMPLETED", "CANCELED"],
                "allow_self": False,
                "terminal": False,
            },
            "COMPLETED": {"summary": "Paid.", "to": [], "allow_self": False, "terminal": True},
            "CANCELED": {"summary": "Abandoned.", "to": [], "allow_self": False, "terminal": True},
        },
    }


def test_describe_reports_terminal_as_the_empty_to_list_everywhere() -> None:
    """C13 asserts this over the wire; assert it here too, so a regression is a
    unit failure before it is a conformance failure."""
    states = machine().describe()["states"]
    for name, published in states.items():
        assert published["terminal"] == (published["to"] == []), name
