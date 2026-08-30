"""The vendor definition, registry resolution, and a started unit."""

from __future__ import annotations

import pytest

import vendorfake.clover as clover
from tests.unit.clover.conftest import fake_ctx
from vendorfake.clover.auth import CloverAuth
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


def test_the_three_surfaces_are_live_and_the_webhook_seams_are_none_until_pr_d() -> None:
    vendor = create_clover_vendor()
    keys = [route.key for route in vendor.routes]
    assert keys[:3] == ["GET /oauth/v2/authorize", "POST /oauth/v2/token", "POST /oauth/v2/refresh"]
    assert "POST /v3/merchants/{mId}/orders" in keys
    assert "POST /v3/merchants/{mId}/orders/{orderId}" in keys  # update is POST, not PUT
    assert "DELETE /v3/merchants/{mId}/orders/{orderId}" in keys
    assert "POST /v3/merchants/{mId}/atomic_order/checkouts" in keys
    assert "GET /v3/merchants/{mId}/items/{itemId}" in keys
    assert "POST /v3/merchants/{mId}/items/{itemId}" in keys
    assert "GET /v3/merchants/{mId}" in keys
    assert "POST /v3/merchants/{mId}/orders/{orderId}/payments" in keys
    assert "POST /v3/merchants/{mId}/print_event" in keys
    assert "GET /v3/merchants/{mId}/default_service_charge" in keys
    assert "GET /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}" in keys
    assert "POST /v3/merchants/{mId}/customers" in keys
    assert len(keys) == 27
    assert len(set(keys)) == 27
    # Every merchant-scoped route authenticates and names a permission.
    for route in vendor.routes:
        if route.path.startswith("/v3/"):
            assert route.auth == "bearer", route.key
            assert route.scopes, route.key
    assert vendor.signer is None
    assert vendor.events is None


def test_routes_are_built_once_and_cached() -> None:
    """Route handlers are bound methods; rebuilding per access would make two
    reads of the property produce routes that compare unequal."""
    vendor = create_clover_vendor()
    assert vendor.routes is vendor.routes


def test_the_real_auth_adapter_is_wired_and_fails_closed_with_no_tokens() -> None:
    """On an empty store nothing authenticates and nothing is offered."""
    from types import SimpleNamespace

    unit = create_unit(vendor="clover", profile="full")
    vendor = unit.context.vendor
    assert isinstance(vendor.auth, CloverAuth)
    assert vendor.auth.credentials(unit.context) == ()
    args = SimpleNamespace(ctx=unit.context, header=lambda name: "Bearer never-minted")
    with pytest.raises(UnitError) as caught:
        vendor.auth.resolve(args, "bearer")  # type: ignore[arg-type]
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
    """Every stored instant is a camelCase Clover field or a `_ms`-suffixed
    internal one, so the core's created_at/updated_at auto-exclusion covers
    none of them; every one is listed. The OAuth expirations appear under
    their stored `_ms` names -- the digest hashes entities, and the
    Unix-seconds spellings exist only on the wire."""
    assert set(create_clover_vendor().volatile_fields) == {
        "access_token_expiration_ms",
        "refresh_token_expiration_ms",
        "expires_at_ms",
        "used_at_ms",
        "refresh_used_at_ms",
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


def test_hydrate_loads_the_seed_merchant_and_refuses_a_malformed_document() -> None:
    """The one-merchant seed: a valid document inserts the merchant with the
    seed meta; a wrong one is refused by name at startup; no seed loads
    nothing and is legal."""
    from vendorfake.clover.entities import COL, MerchantEntity

    unit = create_unit(vendor="clover", profile="full")
    ctx = unit.context
    stored = ctx.store.collection(COL.merchants).require("HRVSTRYE12345")
    assert MerchantEntity.from_entity(stored).name == "Harvest & Rye"
    seeded = [e for e in ctx.store.journal() if e.collection == COL.merchants]
    assert seeded and all(e.meta.get("seed") is True for e in seeded)
    vendor = CloverVendor()
    vendor.hydrate(ctx, {"merchant": {"id": "SECONDMERCH01", "name": "Second"}})
    assert ctx.store.collection(COL.merchants).get("SECONDMERCH01") is not None
    with pytest.raises(UnitError) as caught:
        vendor.hydrate(ctx, {"merchant": {"id": "X"}, "unknown": 1})
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "seed"
    with pytest.raises(UnitError):
        vendor.hydrate(ctx, {"merchant": {}})
    vendor.hydrate(ctx, None)  # a profile with no seed


def test_two_fresh_units_seed_identically_and_reset_reseeds() -> None:
    """The determinism the conformance C06 contract asserts, pinned here too:
    identical digests across two units, and after a control-plane reset."""
    from vendorfake.core.transport.inprocess import in_process

    first = create_unit(vendor="clover", profile="full")
    second = create_unit(vendor="clover", profile="full")
    digest = first.context.store.entity_digest()
    assert digest == second.context.store.entity_digest()
    api = in_process(first)
    assert api.post("/__unit/state/reset").status == 200
    assert first.context.store.entity_digest() == digest
    assert first.context.store.collection("merchants").get("HRVSTRYE12345") is not None


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
    assert set(unit.context.config.capabilities) == {
        "oauth",
        "orders",
        "payments",
        "inventory",
        "merchant",
        "customers",
        "chaos",
    }


def test_two_clover_units_in_one_process_do_not_share_an_id_stream() -> None:
    """The per-access VENDOR factory at work through the registry path."""
    first = create_unit(vendor="clover", profile="full")
    second = create_unit(vendor="clover", profile="full")
    assert first.context.vendor is not second.context.vendor
    assert first.context.vendor.ids.order() == second.context.vendor.ids.order()  # type: ignore[attr-defined]
