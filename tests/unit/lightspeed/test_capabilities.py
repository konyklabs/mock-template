"""What this vendor declares it can do, and what it records it will not."""

from __future__ import annotations

from vendorfake.core.capability.gates import core_gated_names
from vendorfake.lightspeed import VENDOR
from vendorfake.lightspeed.capabilities import (
    LIGHTSPEED_CAPABILITIES,
    LIGHTSPEED_NOT_MODELED,
    LIGHTSPEED_NOT_SUPPORTED,
)


def test_every_core_gated_capability_is_declared_or_excused() -> None:
    """The core cannot tell "switched off" from "you never told me", so a
    capability it gates on has to be one or the other, never neither and never
    both."""
    declared = {row.name for row in LIGHTSPEED_CAPABILITIES}
    gated = set(core_gated_names())
    assert gated <= declared
    assert not (set(LIGHTSPEED_NOT_SUPPORTED) & declared)


def test_not_supported_is_empty_because_everything_is_declared() -> None:
    assert dict(LIGHTSPEED_NOT_SUPPORTED) == {}


def test_every_route_names_a_declared_capability() -> None:
    declared = {row.name for row in LIGHTSPEED_CAPABILITIES}
    for route in VENDOR.routes:
        assert route.capability in declared, route.key


def test_every_surface_capability_owns_at_least_one_route() -> None:
    """C02's rule: a surface capability a consumer cannot meet as endpoints has
    no observable meaning."""
    owned = {route.capability for route in VENDOR.routes}
    for row in LIGHTSPEED_CAPABILITIES:
        if row.kind == "surface":
            assert row.name in owned, row.name
        else:
            assert row.name not in owned, row.name


def test_webhooks_chaos_requires_both_of_its_parents() -> None:
    """Delivery faults are still faults: a unit with fault injection off that
    nonetheless dropped webhooks would be lying about itself."""
    row = next(item for item in LIGHTSPEED_CAPABILITIES if item.name == "webhooks.chaos")
    assert set(row.requires) == {"webhooks", "chaos"}


def test_the_consignment_events_are_recorded_as_never_fired() -> None:
    """Two of the seven documented WebhookType values have no mutation behind
    them in this slice, so nothing fires them and the omission is on the
    record rather than in a reader's head."""
    assert "consignment-events" in LIGHTSPEED_NOT_MODELED
    assert "consignment.send" in LIGHTSPEED_NOT_MODELED["consignment-events"]


def test_every_omission_carries_a_reason() -> None:
    for name, reason in LIGHTSPEED_NOT_MODELED.items():
        assert len(reason) > 40, name


def test_the_second_rate_limit_counter_is_recorded_as_unmodelled() -> None:
    """The page describes two independent counters; this unit has no POS user
    interface and models one."""
    assert "second-rate-limit-counter" in LIGHTSPEED_NOT_MODELED
