"""Every fault publishes its phase, and the pipeline reads the same table.

konyklabs/roadmap#101, item 17a. A consumer could not tell from a rule
whether the fault would let the handler commit: ``provenance`` is a
different axis (``timeout`` is vendor/request, ``malformed_body`` is
transport/response). ``phase`` is published everywhere the catalogue is, and
the set of faults applied after the handler is derived from it rather than
kept beside it.
"""

from __future__ import annotations

import json

from vendorfake.agent.explain import explain_fault, render_fault
from vendorfake.core.chaos.faults import FAULT_PARAM_KEYS, FAULT_PHASE, FAULT_PROVENANCE, RESPONSE_PHASE_FAULTS
from vendorfake.core.chaos.rules import BUILTIN_FAULTS
from vendorfake.testing import unit

TRANSPORT_FIVE = frozenset({"malformed_body", "body_mutation", "connection_reset", "empty_response", "slow_body"})


def test_every_fault_has_a_phase_and_the_response_set_is_derived_from_it() -> None:
    assert set(FAULT_PHASE) == set(FAULT_PARAM_KEYS)
    assert RESPONSE_PHASE_FAULTS == TRANSPORT_FIVE
    assert {name for name, phase in FAULT_PHASE.items() if phase == "response"} == TRANSPORT_FIVE
    assert {name for name, phase in FAULT_PHASE.items() if phase == "delivery"} == {
        name for name in FAULT_PARAM_KEYS if name.startswith("webhook.")
    }
    assert FAULT_PHASE["timeout"] == "request"


def test_phase_and_provenance_are_independent_axes() -> None:
    """The pair the feedback used to show neither axis implies the other."""
    assert (FAULT_PROVENANCE["timeout"], FAULT_PHASE["timeout"]) == ("vendor", "request")
    assert (FAULT_PROVENANCE["malformed_body"], FAULT_PHASE["malformed_body"]) == ("transport", "response")


def test_the_control_plane_publishes_phase_on_both_listings() -> None:
    with unit("square") as started:
        chaos = started.client.get("/__unit/chaos").json()
        info = started.client.get("/__unit/info").json()
    for listing in (chaos["faults"], info["chaos"]["faults"]):
        by_name = {row["name"]: row["phase"] for row in listing}
        assert by_name == dict(FAULT_PHASE)


def test_explain_fault_reports_phase_in_json_and_prose() -> None:
    data = explain_fault("body_mutation")
    assert data["phase"] == "response"
    assert "phase      : response" in render_fault(data)


def test_the_faults_command_carries_a_phase_column() -> None:
    from tests.unit.test_cli import run

    code, out = run("faults", "--json")
    assert code == 0
    assert {row["name"]: row["phase"] for row in json.loads(out)} == dict(FAULT_PHASE)
    code, table = run("faults")
    assert code == 0
    assert "phase" in table.splitlines()[0]


def test_every_catalogue_entry_serialises_its_phase() -> None:
    for spec in BUILTIN_FAULTS:
        assert spec.as_json()["phase"] == spec.phase
