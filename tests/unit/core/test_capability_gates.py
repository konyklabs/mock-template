"""Capability-declaration completeness: silence must be a failure.

The defect being closed is quiet: ``is_enabled("webhooks")`` on a vendor that
never declared ``webhooks`` returns False, so delivery is off and nothing
anywhere says why. These tests pin the rule that makes that impossible and the
exact categories a conformance check reads.
"""

from __future__ import annotations

import pytest

from vendorfake.core.capability.gates import (
    CORE_GATED_CAPABILITIES,
    CoreCapability,
    assert_capability_declarations,
    check_capability_declarations,
    core_gated_names,
)
from vendorfake.core.kernel.types import CapabilityDecl, UnitError, UnitErrorKind

COMPLETE = (
    CapabilityDecl(name="webhooks", summary="Signed delivery."),
    CapabilityDecl(name="chaos", summary="Request faults.", kind="behavior"),
    CapabilityDecl(
        name="webhooks.chaos",
        summary="Delivery faults.",
        kind="behavior",
        requires=("webhooks", "chaos"),
    ),
)


def test_the_core_gates_on_exactly_three_capabilities() -> None:
    """A fourth arrives only with its gate site and a conformance check."""
    assert core_gated_names() == ("chaos", "webhooks", "webhooks.chaos")
    assert {member.value for member in CoreCapability} == set(core_gated_names())
    assert len(CORE_GATED_CAPABILITIES) == len(CoreCapability)


def test_every_gate_names_a_call_site_and_an_effect() -> None:
    """`gated_at` is what turns 'what does turning this off do' from a search
    into a lookup, and what the boundary checker reconciles against."""
    for gate in CORE_GATED_CAPABILITIES:
        assert gate.gated_at.startswith("vendorfake.core.")
        assert gate.effect.strip()
        assert gate.as_json()["capability"] == gate.capability.value


def test_a_complete_declaration_reports_no_problems() -> None:
    report = check_capability_declarations(COMPLETE, {})
    assert report.problems == ()
    assert report.ok is True
    assert_capability_declarations(COMPLETE, {})


def test_silence_is_a_failure_naming_the_gate_site() -> None:
    declared = (CapabilityDecl(name="webhooks", summary="Signed delivery."),)
    report = check_capability_declarations(declared, {})
    assert set(report.undeclared) == {"chaos", "webhooks.chaos"}
    joined = " ".join(report.problems)
    assert "vendorfake.core.chaos.selector.FaultSelector.select_request" in joined
    assert "vendorfake.core.chaos.selector.FaultSelector.select_webhook" in joined


def test_an_explicit_not_supported_entry_satisfies_the_rule() -> None:
    declared = (CapabilityDecl(name="chaos", summary="Request faults.", kind="behavior"),)
    report = check_capability_declarations(
        declared,
        {
            "webhooks": "This vendor's API has no outbound event mechanism at all.",
            "webhooks.chaos": "There are no deliveries to disturb.",
        },
    )
    assert report.ok is True


def test_a_blank_reason_is_rejected_because_an_absence_is_recorded_with_its_why() -> None:
    declared = (CapabilityDecl(name="chaos", summary="Request faults.", kind="behavior"),)
    report = check_capability_declarations(
        declared,
        {"webhooks": "   ", "webhooks.chaos": "No deliveries exist."},
    )
    assert report.unreasoned == ("webhooks",)
    assert "has no reason" in " ".join(report.problems)


def test_declaring_and_excusing_the_same_capability_is_a_contradiction() -> None:
    report = check_capability_declarations(COMPLETE, {"webhooks": "Not implemented."})
    assert report.contradictory == ("webhooks",)
    assert "both declared and listed in not_supported" in " ".join(report.problems)


def test_not_supported_may_not_name_a_capability_the_core_does_not_gate_on() -> None:
    """Otherwise the map accumulates names that mean nothing and stop being read."""
    report = check_capability_declarations(COMPLETE, {"telepathy": "We cannot."})
    assert report.ungated == ("telepathy",)
    assert "does not gate on" in " ".join(report.problems)


def test_chaos_must_be_declared_as_a_behavior_capability() -> None:
    declared = (
        CapabilityDecl(name="webhooks", summary="Signed delivery."),
        CapabilityDecl(name="chaos", summary="Request faults."),  # defaults to surface
        CapabilityDecl(
            name="webhooks.chaos", summary="Delivery faults.", kind="behavior", requires=("webhooks", "chaos")
        ),
    )
    report = check_capability_declarations(declared, {})
    assert report.wrong_kind == ("chaos",)


def test_webhook_chaos_must_require_the_chaos_capability() -> None:
    """A unit with fault injection off that nonetheless drops webhooks would be
    lying about itself, so the prerequisite is checked here rather than trusted
    to each vendor's declaration."""
    declared = (
        CapabilityDecl(name="webhooks", summary="Signed delivery."),
        CapabilityDecl(name="chaos", summary="Request faults.", kind="behavior"),
        CapabilityDecl(name="webhooks.chaos", summary="Delivery faults.", kind="behavior", requires=("webhooks",)),
    )
    report = check_capability_declarations(declared, {})
    assert report.missing_prerequisite == ("webhooks.chaos",)
    assert "must list 'chaos' in requires" in " ".join(report.problems)


def test_the_startup_assertion_raises_invalid_value_carrying_every_problem() -> None:
    with pytest.raises(UnitError) as caught:
        assert_capability_declarations((), {})
    err = caught.value
    assert err.kind is UnitErrorKind.INVALID_VALUE
    assert err.field == "capabilities"
    assert err.info is not None
    problems = err.info["problems"]
    assert isinstance(problems, list)
    assert len(problems) == 3
    assert [gate["capability"] for gate in err.info["core_gated"]] == list(core_gated_names())  # type: ignore[index,union-attr]


def test_every_gate_site_names_something_that_actually_exists() -> None:
    """``gated_at`` is a promise, and until now nothing checked it was kept.

    The boundary checker is supposed to reconcile this tuple against the
    literals handed to ``is_enabled`` / ``assert_enabled`` in the core and does
    not yet. This is the half that can be written from here: every path must
    resolve to a real callable, so a rename or a deleted method turns a
    documented gate site into a red test rather than into a lie a reader
    follows and does not find.

    ``webhooks`` names ``WebhookDispatcher.attach`` because the check lives
    inside the listener that method registers -- deliberately, so that
    switching the capability off at runtime stops delivery immediately rather
    than answering a question about the profile.
    """
    import importlib

    for gate in CORE_GATED_CAPABILITIES:
        parts = gate.gated_at.split(".")
        module = None
        index = len(parts)
        while index > 0:
            try:
                module = importlib.import_module(".".join(parts[:index]))
                break
            except ModuleNotFoundError:
                index -= 1
        assert module is not None, f"{gate.gated_at} names no importable module"
        target: object = module
        for attribute in parts[index:]:
            assert hasattr(target, attribute), f"{gate.gated_at} stops resolving at {attribute!r}"
            target = getattr(target, attribute)
        assert callable(target), f"{gate.gated_at} resolves to something that is not callable"
