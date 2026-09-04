"""The vendor definition: identity, the seams, and the invariants around them."""

from __future__ import annotations

from vendorfake.core.kernel.types import VendorDefinition
from vendorfake.lightspeed import VENDOR, LightspeedVendor, create_lightspeed_vendor
from vendorfake.lightspeed.capabilities import LIGHTSPEED_CAPABILITIES
from vendorfake.lightspeed.vendor import API_VERSION, LIGHTSPEED_ROLES
from vendorfake.registry import ROLE_NAMES, available_vendors, resolve_vendor


def test_the_registry_offers_lightspeed() -> None:
    assert "lightspeed" in available_vendors()
    assert resolve_vendor("lightspeed").name == "lightspeed"


def test_vendor_is_a_fresh_definition_on_every_access() -> None:
    """A vendor owns two id streams, a version counter and a rate-limit
    window; two units sharing any of them would interleave."""
    import vendorfake.lightspeed as package

    assert package.VENDOR is not package.VENDOR


def test_the_module_attribute_is_the_protocol() -> None:
    definition: VendorDefinition = VENDOR
    assert definition.name == "lightspeed"
    assert definition.api_version == API_VERSION == "2026-07"
    assert "Lightspeed" in definition.display_name


def test_every_role_maps_to_a_declared_capability() -> None:
    """C34, asserted here as well as in the conformance suite, because a role
    that maps to nothing makes ``create_unit(capabilities=[...])`` silently
    pick nothing."""
    declared = {row.name for row in LIGHTSPEED_CAPABILITIES}
    assert set(LIGHTSPEED_ROLES) == set(ROLE_NAMES)
    assert set(LIGHTSPEED_ROLES.values()) <= declared


def test_the_orders_role_points_at_registers_in_this_slice() -> None:
    """Pinned deliberately: Lightspeed's order-equivalent is a *sale*, and the
    Sales surface arrives in a later slice of konyklabs/roadmap#94. This test is
    what makes re-pointing the role a visible change rather than a silent one."""
    assert LIGHTSPEED_ROLES["orders"] == "registers"


def test_no_state_machine_is_declared() -> None:
    """A register is open or closed -- a boolean, not a lifecycle. The sale
    machine (parked/pending/voided/closed) arrives with the Sales surface."""
    assert dict(VENDOR.machines) == {}


def test_the_signer_and_the_event_mapper_are_both_present() -> None:
    """The dispatcher needs both before it will deliver anything, and a vendor
    that declared ``webhooks`` with only one would be enabled but dead."""
    assert VENDOR.signer is not None
    assert VENDOR.events is not None


def test_the_retry_defaults_carry_a_schedule() -> None:
    """Unit construction refuses to start a vendor that declares ``webhooks``
    with an empty schedule; this asserts the vendor's half of that."""
    assert VENDOR.retry_defaults.webhooks.retry.schedule_ms


def test_the_vendor_config_reaches_the_definition() -> None:
    built = create_lightspeed_vendor(vendor_config={"domain_prefix": "elsewhere"})
    assert isinstance(built, LightspeedVendor)
    assert built.config.domain_prefix == "elsewhere"


def test_two_vendors_on_one_seed_mint_the_same_ids() -> None:
    first = create_lightspeed_vendor()
    second = create_lightspeed_vendor()
    assert isinstance(first, LightspeedVendor) and isinstance(second, LightspeedVendor)
    assert [first.ids.uuid() for _ in range(3)] == [second.ids.uuid() for _ in range(3)]


def test_every_route_is_under_a_documented_prefix() -> None:
    """Two base paths, and nothing else: the resource API under
    ``/api/2026-07`` and the token endpoint under ``/api/1.0``. ``/connect`` is
    the one exception and is the documented authorize path."""
    for route in VENDOR.routes:
        assert route.path.startswith(("/api/2026-07/", "/api/1.0/", "/connect")), route.key
