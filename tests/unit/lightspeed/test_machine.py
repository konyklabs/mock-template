"""The sale lifecycle: the four documented values, and the edges this project
chose between them.

The values are pinned because they are the vendor's and a change to them would
be a change to the specification's own enum. The edges are pinned because they
are NOT the vendor's -- ``machine.py`` says so at length -- and a table of
JUDGMENT calls that nothing asserts is a table that drifts.

The control-plane half matters as much as the definition half: a machine the
core does not enforce is documentation, and ``GET /__unit/machines`` is where a
consumer reads the lifecycle without importing anything.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.lightspeed.harness import Harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.lightspeed.machine import SALE_MACHINE, SALE_MACHINE_NAME, SALE_STATE_FIELD, SaleState
from vendorfake.lightspeed.seed import constants as c

MACHINE = StateMachine(SALE_MACHINE)

#: Every (from, to) pair the module docstring's reading declares legal.
LEGAL = {
    ("parked", "parked"),
    ("parked", "pending"),
    ("parked", "closed"),
    ("parked", "voided"),
    ("pending", "pending"),
    ("pending", "closed"),
    ("pending", "voided"),
}


def test_the_states_are_the_schemas_own_enum() -> None:
    """``SaleRequestBase.state``: ``["parked", "pending", "voided", "closed"]``,
    lower-case, four values, on a field spelled ``state``."""
    assert SALE_STATE_FIELD == "state"
    assert SALE_MACHINE.field == "state"
    assert [value.value for value in SaleState] == ["parked", "pending", "voided", "closed"]
    assert set(SALE_MACHINE.states) == {"parked", "pending", "voided", "closed"}


def test_the_api_1_0_status_vocabulary_is_absent() -> None:
    """No ``SAVED``, ``LAYBY``, ``ONACCOUNT`` or upper-case ``CLOSED``.

    Those belong to API 1.0 and survive in this document only inside the
    ``initReturnSale`` response EXAMPLE. Asserting their absence is what stops
    a future edit quietly reintroducing a vocabulary the 2026-07 schemas do not
    declare -- see ``capabilities.py``'s ``sale-status-vocabulary``.
    """
    declared = set(SALE_MACHINE.states)
    assert declared.isdisjoint({"SAVED", "CLOSED", "LAYBY", "ONACCOUNT", "VOIDED"})


@pytest.mark.parametrize("state", ["closed", "voided"])
def test_the_two_end_states_are_terminal(state: str) -> None:
    """Terminality is DERIVED from an empty ``to``, never stored, so this also
    pins that neither state kept an outgoing edge by accident."""
    assert MACHINE.is_terminal(state)
    assert SALE_MACHINE.states[state].to == ()


def test_every_edge_is_exactly_the_declared_set() -> None:
    """All sixteen ordered pairs, so an edge silently added or removed fails
    here by name rather than somewhere downstream."""
    states = list(SALE_MACHINE.states)
    actual = {(a, b) for a in states for b in states if MACHINE.can_transition(a, b)}
    assert actual == LEGAL


def test_pending_cannot_go_back_to_parked() -> None:
    """The one edge whose absence is a decision rather than an omission: a
    pending sale's line items may be ``CONFIRMED``, which the schema documents
    as "added as read-only"."""
    assert not MACHINE.can_transition("pending", "parked")


def test_a_typo_and_a_mistimed_move_are_different_failures() -> None:
    """``invalid_value`` carries every state (the caller has a typo);
    ``invalid_transition`` carries only what is reachable (the caller has a
    sequencing bug). The core keeps them apart and this vendor relies on it."""
    with pytest.raises(UnitError) as typo:
        MACHINE.assert_transition("parked", "CLOSED", "Sale s1")
    assert typo.value.kind is UnitErrorKind.INVALID_VALUE
    assert (typo.value.info or {})["allowed"] == ["parked", "pending", "voided", "closed"]

    with pytest.raises(UnitError) as mistimed:
        MACHINE.assert_transition("closed", "parked", "Sale s1")
    assert mistimed.value.kind is UnitErrorKind.INVALID_TRANSITION
    assert (mistimed.value.info or {})["terminal"] is True


# -- the control plane -------------------------------------------------------


def test_the_unit_publishes_the_machine(h: Harness) -> None:
    published = h.api.get("/__unit/machines").json()
    assert set(published["machines"]) == {SALE_MACHINE_NAME}
    sale = published["machines"][SALE_MACHINE_NAME]
    assert sale["field"] == "state"
    assert sale["initial"] == "parked"
    assert sale["states"]["closed"]["terminal"] is True
    assert sale["states"]["parked"]["allow_self"] is True


def test_the_published_edges_are_the_enforced_edges(h: Harness) -> None:
    """What ``GET /__unit/machines`` prints and what ``POST
    /__unit/machines/probe`` answers are the same lifecycle, which is the whole
    point of publishing it."""
    sale = h.api.get("/__unit/machines").json()["machines"][SALE_MACHINE_NAME]
    allowed: set[tuple[str, str]] = set()
    for state in sale["states"]:
        for target in sale["states"]:
            probed = h.api.post(
                "/__unit/machines/probe",
                json.dumps({"machine": SALE_MACHINE_NAME, "from": state, "to": target}),
            )
            # The probe answers 200 with ``ok`` for a legal move and this
            # vendor's shaped refusal for anything else, so the set of moves it
            # accepts is the set of moves the unit enforces.
            if probed.status == 200:
                assert probed.json()["ok"] is True
                allowed.add((state, target))
    assert allowed == LEGAL


def test_the_route_enforces_what_the_machine_declares(h: Harness) -> None:
    """The seeded closed sale is terminal, so the update route refuses it with
    the 409 the shaper maps ``invalid_transition`` to."""
    body = json.dumps(
        {"state": "parked", "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID}}
    )
    answered = h.put(h.path(f"/sales/{c.SEED_SALE_CLOSED_ID}"), body)
    assert answered.status == 409
    assert answered.json()["unit_error"]["kind"] == "invalid_transition"
