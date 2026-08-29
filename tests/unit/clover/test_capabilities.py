"""Capability declarations, the excused webhook gates, and the informational
not-modelled record."""

from __future__ import annotations

from vendorfake.clover.capabilities import CLOVER_CAPABILITIES, CLOVER_NOT_MODELED, CLOVER_NOT_SUPPORTED
from vendorfake.core.capability.gates import CORE_GATED_CAPABILITIES, check_capability_declarations


def test_every_core_gated_capability_is_declared_or_excused() -> None:
    """The core refuses to start a vendor that gates on a capability it never
    declared. This vendor declares chaos and excuses the two webhook gates
    until PR D ships the seams that would make them deliverable."""
    report = check_capability_declarations(CLOVER_CAPABILITIES, CLOVER_NOT_SUPPORTED)
    assert report.ok, report.problems
    declared = {decl.name for decl in CLOVER_CAPABILITIES}
    for gate in CORE_GATED_CAPABILITIES:
        assert gate.capability.value in declared or gate.capability.value in CLOVER_NOT_SUPPORTED


def test_the_webhook_gates_are_excused_not_declared_until_pr_d() -> None:
    """Declaring webhooks while signer and events are None would be
    enabled-but-dead: the dispatcher silently no-ops when either seam is
    missing, which is exactly the state the declaration system exists to
    prevent."""
    assert set(CLOVER_NOT_SUPPORTED) == {"webhooks", "webhooks.chaos"}
    for name, reason in CLOVER_NOT_SUPPORTED.items():
        assert "PR D" in reason, name
    declared = {decl.name for decl in CLOVER_CAPABILITIES}
    assert not declared & set(CLOVER_NOT_SUPPORTED)


def test_the_surface_capabilities_are_the_three_this_pr_series_declares() -> None:
    surface = {decl.name for decl in CLOVER_CAPABILITIES if decl.kind == "surface"}
    assert surface == {"oauth", "orders", "inventory"}


def test_chaos_is_the_one_behaviour_capability() -> None:
    behaviour = {decl.name: decl for decl in CLOVER_CAPABILITIES if decl.kind == "behavior"}
    assert set(behaviour) == {"chaos"}
    assert behaviour["chaos"].requires == ()


def test_the_not_modelled_record_covers_the_briefed_omissions_with_reasons() -> None:
    """Informational, never handed to the core -- the core would refuse names
    it does not gate on. Every entry carries prose a consumer can read."""
    assert set(CLOVER_NOT_MODELED) == {
        "payments",
        "customers",
        "employees",
        "tax-rates",
        "modifier-groups",
        "token-migration",
        "rate-limit-accounting",
        "90-day-filter-restriction",
    }
    for name, reason in CLOVER_NOT_MODELED.items():
        assert len(reason.strip()) > 20, name


def test_the_not_modelled_names_never_leak_into_the_core_facing_map() -> None:
    """Handing CLOVER_NOT_MODELED to the core would be a startup failure
    ('not_supported names X, which the core does not gate on'); pin that the
    two maps stay disjoint so nobody merges them."""
    assert not set(CLOVER_NOT_MODELED) & set(CLOVER_NOT_SUPPORTED)
    report = check_capability_declarations(CLOVER_CAPABILITIES, {**CLOVER_NOT_SUPPORTED, **CLOVER_NOT_MODELED})
    assert not report.ok  # the core would refuse it, which is why it lives apart
