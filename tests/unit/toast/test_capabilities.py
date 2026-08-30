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


def test_the_webhook_gates_are_excused_while_their_seams_are_absent() -> None:
    """Declaring webhooks with signer and events None would be enabled-but-dead."""
    from vendorfake.toast.vendor import create_toast_vendor

    vendor = create_toast_vendor()
    declared = {decl.name for decl in TOAST_CAPABILITIES}
    assert vendor.signer is None and vendor.events is None
    assert "webhooks" not in declared and "webhooks.chaos" not in declared
    assert {"webhooks", "webhooks.chaos"} == set(TOAST_NOT_SUPPORTED)
    assert all(len(reason) > 20 for reason in TOAST_NOT_SUPPORTED.values())


def test_the_not_modelled_record_carries_reasons_and_never_leaks_into_the_core_facing_map() -> None:
    assert {"hostnames", "token-refresh", "credit-card-authorization", "refunds", "rate-limit-accounting"} <= set(
        TOAST_NOT_MODELED
    )
    for name, reason in TOAST_NOT_MODELED.items():
        assert len(reason.strip()) > 20, name
    assert not set(TOAST_NOT_MODELED) & set(TOAST_NOT_SUPPORTED)
    report = check_capability_declarations(TOAST_CAPABILITIES, {**TOAST_NOT_SUPPORTED, **TOAST_NOT_MODELED})
    assert not report.ok
