"""The vendor definition, registry resolution, and a started unit."""

from __future__ import annotations

import pytest

import vendorfake.clover as clover
from tests.unit.clover.conftest import fake_ctx
from vendorfake.clover.machine import ORDER_MACHINE
from vendorfake.clover.retry import CLOVER_RETRY_SCHEDULE_MS
from vendorfake.clover.vendor import CloverVendor, create_clover_vendor
from vendorfake.core.kernel.types import (
    MutableResponse,
    UnitError,
    UnitErrorKind,
    UnitRequest,
    VendorDefinition,
)
from vendorfake.registry import available_vendors, create_unit, resolve_vendor


def request(headers: dict[str, str] | None = None) -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method="GET",
        path="/v3/merchants/M/orders",
        query={},
        headers=headers or {},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-29T00:00:00.000Z",
    )


def test_the_definition_satisfies_the_protocol() -> None:
    """The annotation is the check: mypy verifies the structural conformance of
    CloverVendor at `create_clover_vendor`'s return, and this asserts at run
    time that the registry's target really is one."""
    definition: VendorDefinition = create_clover_vendor()
    assert definition.name == "clover"
    assert definition.display_name == "Clover (REST v3)"
    assert definition.api_version is None  # Clover has no version header


def test_vendor_is_minted_fresh_on_every_access() -> None:
    """A vendor owns a stateful id stream. Two units sharing one would
    interleave their draws and neither would reproduce its own ids."""
    first = clover.VENDOR
    second = clover.VENDOR
    assert first is not second
    assert first.name == second.name == "clover"
    assert first.ids.order() == second.ids.order()  # type: ignore[attr-defined]


def test_a_typo_on_the_module_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        clover.VENDORS  # type: ignore[attr-defined]  # noqa: B018


def test_routes_are_empty_and_the_webhook_seams_are_none_until_their_prs() -> None:
    vendor = create_clover_vendor()
    assert tuple(vendor.routes) == ()
    assert vendor.signer is None
    assert vendor.events is None


def test_the_placeholder_auth_refuses_and_offers_nothing() -> None:
    """Unreachable while no route exists, and fails closed if one lands
    without replacing it (PR B ships the real adapter)."""
    vendor = create_clover_vendor()
    assert vendor.auth.credentials(fake_ctx()) == ()
    with pytest.raises(UnitError) as caught:
        vendor.auth.resolve(None, "bearer")  # type: ignore[arg-type]
    assert caught.value.kind is UnitErrorKind.UNAUTHORIZED


def test_the_order_machine_is_registered_so_the_control_plane_can_publish_it() -> None:
    assert create_clover_vendor().machines == {"order": ORDER_MACHINE}


def test_the_magic_spec_uses_fields_a_real_clover_client_can_set() -> None:
    magic = create_clover_vendor().magic
    assert magic is not None
    assert magic.prefix == "chaos:"
    assert set(magic.body_paths) == {"note", "title", "externalReferenceId"}
    assert tuple(magic.query_params) == ("state",)


def test_the_retry_defaults_carry_the_judgment_schedule() -> None:
    """The core ships no schedule; an unmerged default would present as every
    delivery exhausting on its first attempt. All five values are JUDGMENT --
    Clover documents no retry policy at all."""
    retry = create_clover_vendor().retry_defaults.webhooks.retry
    assert retry.schedule_ms == CLOVER_RETRY_SCHEDULE_MS
    assert retry.schedule_ms == (30_000, 120_000, 600_000, 1_800_000, 7_200_000)
    assert retry.timeout_ms == 10_000


def test_volatile_fields_are_clovers_wall_clock_names() -> None:
    """Clover's names are camelCase, so the core's created_at/updated_at
    auto-exclusion covers none of them; every one is listed."""
    assert set(create_clover_vendor().volatile_fields) == {
        "access_token_expiration",
        "refresh_token_expiration",
        "createdTime",
        "modifiedTime",
        "clientCreatedTime",
        "deletedTime",
    }


def test_decorate_stamps_only_the_unit_vendor_header() -> None:
    vendor = create_clover_vendor()
    res = MutableResponse(status=200, headers={}, body=b"{}")
    vendor.decorate(res, fake_ctx(), request())
    assert res.headers == {"x-unit-vendor": "clover"}


def test_hydrate_resolves_the_profiles_vendor_block_and_reseeds_the_ids() -> None:
    vendor = CloverVendor()
    before = vendor.ids.order()
    vendor.hydrate(fake_ctx(vendor_config={"client_id": "OTHERAPP12345"}, chaos_seed=1), None)
    assert vendor.config.client_id == "OTHERAPP12345"
    assert vendor.ids.order() == before  # reseeded: the stream restarts


def test_the_id_stream_is_reseeded_from_the_unit_seed_not_the_constructor_arg() -> None:
    """A unit that re-hydrates on POST /__unit/state/reset mints the ids it
    minted the first time -- and the seed it restarts from must be the
    *unit's* chaos seed, because reseeding from the constructor argument would
    pass every same-seed test while making profiles with different chaos seeds
    mint identical ids."""
    vendor = CloverVendor(seed=1)
    vendor.hydrate(fake_ctx(chaos_seed=99), None)
    first = [vendor.ids.order() for _ in range(3)]
    vendor.hydrate(fake_ctx(chaos_seed=99), None)
    assert [vendor.ids.order() for _ in range(3)] == first
    # And the seed really is the unit's, not the one the vendor was built with.
    other = CloverVendor(seed=1)
    other.hydrate(fake_ctx(chaos_seed=100), None)
    assert [other.ids.order() for _ in range(3)] != first


def test_hydrate_refuses_a_seed_document_until_pr_e_ships_the_parser() -> None:
    with pytest.raises(UnitError) as caught:
        CloverVendor().hydrate(fake_ctx(), {"merchant": {}})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert "seed" in str(caught.value)


# ---------------------------------------------------------------------------
# Registry and a started unit.
# ---------------------------------------------------------------------------


def test_the_registry_resolves_clover_and_lists_both_vendors() -> None:
    assert resolve_vendor("clover").name == "clover"
    assert set(available_vendors()) >= {"clover", "square"}


def test_a_typo_still_names_the_real_vendors_including_clover() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_vendor("clove")
    assert "no vendor named 'clove'" in str(caught.value)
    assert "clover" in str(caught.value)


def test_a_clover_unit_starts_on_the_full_profile_with_an_empty_surface() -> None:
    """The whole point of PR A: the foundation constructs, hydrates and serves
    its control plane before any vendor route exists."""
    unit = create_unit(vendor="clover", profile="full")
    assert unit.name == "clover"
    assert unit.context.config.profile == "full"
    assert set(unit.context.config.capabilities) == {"oauth", "orders", "inventory", "chaos"}


def test_two_clover_units_in_one_process_do_not_share_an_id_stream() -> None:
    """The per-access VENDOR factory at work through the registry path."""
    first = create_unit(vendor="clover", profile="full")
    second = create_unit(vendor="clover", profile="full")
    assert first.context.vendor is not second.context.vendor
    assert first.context.vendor.ids.order() == second.context.vendor.ids.order()  # type: ignore[attr-defined]
