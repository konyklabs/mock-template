"""Capability declarations, the (now empty) excuse list, and the informational
not-modelled record."""

from __future__ import annotations

from vendorfake.clover.capabilities import CLOVER_CAPABILITIES, CLOVER_NOT_MODELED, CLOVER_NOT_SUPPORTED
from vendorfake.core.capability.gates import CORE_GATED_CAPABILITIES, check_capability_declarations


def test_every_core_gated_capability_is_declared_or_excused() -> None:
    """The core refuses to start a vendor that gates on a capability it never
    declared. This vendor declares all three core-gated capabilities."""
    report = check_capability_declarations(CLOVER_CAPABILITIES, CLOVER_NOT_SUPPORTED)
    assert report.ok, report.problems
    declared = {decl.name for decl in CLOVER_CAPABILITIES}
    for gate in CORE_GATED_CAPABILITIES:
        assert gate.capability.value in declared or gate.capability.value in CLOVER_NOT_SUPPORTED


def test_the_webhook_gates_are_declared_together_with_their_seams() -> None:
    """Declaring webhooks while signer and events are None would be
    enabled-but-dead: the dispatcher silently no-ops when either seam is
    missing. Both gates are declared, both seams exist, and nothing is
    excused any more."""
    from vendorfake.clover.vendor import create_clover_vendor

    assert CLOVER_NOT_SUPPORTED == {}
    declared = {decl.name for decl in CLOVER_CAPABILITIES}
    assert {"webhooks", "webhooks.chaos"} <= declared
    vendor = create_clover_vendor()
    assert vendor.signer is not None
    assert vendor.events is not None
    chaos = next(decl for decl in CLOVER_CAPABILITIES if decl.name == "webhooks.chaos")
    assert set(chaos.requires) == {"webhooks", "chaos"}


def test_only_surfaces_that_own_routes_are_declared() -> None:
    """Conformance C02 refuses a `surface` capability that owns no route, so
    `orders` and `inventory` are declared in PR C with their routes, not
    before. Every declared surface capability owns at least one route now."""
    from vendorfake.clover.vendor import create_clover_vendor

    surface = {decl.name for decl in CLOVER_CAPABILITIES if decl.kind == "surface"}
    assert surface == {"oauth", "webhooks"}
    owned = {route.capability for route in create_clover_vendor().routes}
    assert surface <= owned, (surface, owned)


def test_a_disabled_capability_answers_explicitly_and_never_with_a_404() -> None:
    """Switching `oauth` off through the environment layer must produce a 501
    naming the capability -- not a 404 that reads as "no such route", which
    is indistinguishable from a consumer's own typo."""
    from tests.unit.clover.harness import CLIENT_ID, Silent
    from vendorfake import create_unit
    from vendorfake.core.transport.inprocess import in_process

    unit = create_unit(vendor="clover", profile="full", logger=Silent(), env={"VENDORFAKE_CAPABILITIES": "-oauth"})
    try:
        api = in_process(unit)
        response = api.call(method="GET", path="/oauth/v2/authorize", query={"client_id": CLIENT_ID})
        assert response.status == 501
        assert response.headers["x-unit-error"] == "capability_disabled"
        assert response.headers["x-unit-capability"] == "oauth"
        body = response.json()
        assert "oauth" in body["message"]
        assert body["unit_error"]["kind"] == "capability_disabled"
        assert body["unit_error"]["capability"] == "oauth"
        # The contrast case, without which the assertion above proves nothing:
        # a path this vendor does not serve is still a 404.
        missing = api.get("/v3/merchants/X/nothing")
        assert missing.status == 404
        assert missing.headers["x-unit-error"] == "not_found"
    finally:
        unit.stop()


def test_the_two_behaviour_capabilities_are_the_core_gated_ones() -> None:
    behaviour = {decl.name: decl for decl in CLOVER_CAPABILITIES if decl.kind == "behavior"}
    assert set(behaviour) == {"chaos", "webhooks.chaos"}
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
