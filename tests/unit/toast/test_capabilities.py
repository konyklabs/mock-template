"""Capability declarations, the excuse list, and the not-modelled record."""

from __future__ import annotations

from vendorfake.core.capability.gates import CORE_GATED_CAPABILITIES, check_capability_declarations
from vendorfake.toast.capabilities import TOAST_CAPABILITIES, TOAST_NOT_MODELED, TOAST_NOT_SUPPORTED


def test_every_core_gated_capability_is_declared_or_excused() -> None:
    report = check_capability_declarations(TOAST_CAPABILITIES, TOAST_NOT_SUPPORTED)
    assert report.ok, report.problems
    declared = {decl.name for decl in TOAST_CAPABILITIES}
    for gate in CORE_GATED_CAPABILITIES:
        assert gate.capability.value in declared or gate.capability.value in TOAST_NOT_SUPPORTED


def test_the_webhook_gates_are_declared_together_with_their_seams() -> None:
    """Declaring webhooks with signer and events None would be enabled-but-dead;
    both gates are declared, both seams exist, nothing is excused."""
    from vendorfake.toast.vendor import create_toast_vendor

    vendor = create_toast_vendor()
    declared = {decl.name for decl in TOAST_CAPABILITIES}
    assert vendor.signer is not None and vendor.events is not None
    assert {"webhooks", "webhooks.chaos"} <= declared
    assert TOAST_NOT_SUPPORTED == {}
    chaos = next(decl for decl in TOAST_CAPABILITIES if decl.name == "webhooks.chaos")
    assert set(chaos.requires) == {"webhooks", "chaos"}


def test_every_declared_surface_owns_routes_and_every_route_is_owned() -> None:
    from vendorfake.toast.vendor import create_toast_vendor

    surface = {decl.name for decl in TOAST_CAPABILITIES if decl.kind == "surface"}
    assert surface == {"auth", "orders", "payments", "menus", "config", "restaurants", "partners", "stock", "webhooks"}
    owned = {route.capability for route in create_toast_vendor().routes}
    assert surface == owned, (surface, owned)
    behaviour = {decl.name for decl in TOAST_CAPABILITIES if decl.kind == "behavior"}
    assert behaviour == {"chaos", "webhooks.chaos"}


def test_the_not_modelled_record_carries_reasons_and_never_leaks_into_the_core_facing_map() -> None:
    assert {"hostnames", "token-refresh", "credit-card-authorization", "refunds", "rate-limit-accounting"} <= set(
        TOAST_NOT_MODELED
    )
    for name, reason in TOAST_NOT_MODELED.items():
        assert len(reason.strip()) > 20, name
    assert not set(TOAST_NOT_MODELED) & set(TOAST_NOT_SUPPORTED)
    report = check_capability_declarations(TOAST_CAPABILITIES, {**TOAST_NOT_SUPPORTED, **TOAST_NOT_MODELED})
    assert not report.ok
