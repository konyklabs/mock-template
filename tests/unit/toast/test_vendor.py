"""The vendor definition, registry resolution, and a started unit."""

from __future__ import annotations

import pytest

import vendorfake.toast as toast
from tests.unit.toast.conftest import fake_ctx
from vendorfake.core.kernel.types import MutableResponse, UnitError, UnitErrorKind, UnitRequest, VendorDefinition
from vendorfake.registry import available_vendors, create_unit, resolve_vendor
from vendorfake.toast.auth import ToastAuth
from vendorfake.toast.entities import COL
from vendorfake.toast.machine import CHECK_MACHINE, GUEST_ORDER_MACHINE
from vendorfake.toast.retry import TOAST_RETRY_SCHEDULE_MS
from vendorfake.toast.seed.constants import SEED_RESTAURANT_GUID
from vendorfake.toast.vendor import ToastVendor, create_toast_vendor


def request() -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method="GET",
        path="/orders/v2/orders",
        query={},
        headers={},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-30T00:00:00.000Z",
    )


def test_the_definition_satisfies_the_protocol() -> None:
    definition: VendorDefinition = create_toast_vendor()
    assert definition.name == "toast"
    assert definition.display_name == "Toast (REST v2/v3)"
    assert definition.api_version is None


def test_vendor_is_minted_fresh_on_every_access() -> None:
    first = toast.VENDOR
    second = toast.VENDOR
    assert first is not second
    assert first.ids.order() == second.ids.order()  # type: ignore[attr-defined]


def test_a_typo_on_the_module_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        toast.VENDORS  # type: ignore[attr-defined]  # noqa: B018


def test_the_login_is_the_first_route_and_the_webhook_seams_are_absent() -> None:
    vendor = create_toast_vendor()
    assert [route.key for route in vendor.routes][:1] == ["POST /authentication/v1/authentication/login"]
    assert vendor.routes is vendor.routes
    assert vendor.signer is None and vendor.events is None
    assert isinstance(vendor.auth, ToastAuth)


def test_both_machines_are_registered_so_the_control_plane_can_publish_them() -> None:
    assert create_toast_vendor().machines == {"check": CHECK_MACHINE, "order": GUEST_ORDER_MACHINE}


def test_the_magic_spec_uses_fields_a_real_toast_client_can_set() -> None:
    magic = create_toast_vendor().magic
    assert magic is not None
    assert magic.prefix == "chaos:"
    assert set(magic.body_paths) == {"externalId", "deliveryInfo.notes"}
    assert tuple(magic.query_params) == ("pageToken",)


def test_the_retry_defaults_carry_the_documented_schedule_and_deadline() -> None:
    """Five minutes then ten (apiRetrySupport.html); 2 seconds (apiTimeouts.html)."""
    retry = create_toast_vendor().retry_defaults.webhooks.retry
    assert retry.schedule_ms == TOAST_RETRY_SCHEDULE_MS == (300_000, 600_000)
    assert retry.timeout_ms == 2_000


def test_volatile_fields_are_toasts_wall_clock_names() -> None:
    volatile = set(create_toast_vendor().volatile_fields)
    assert {"expires_at_ms", "access_token", "openedDate", "modifiedDate", "businessDate", "voidDate"} <= volatile


def test_decorate_stamps_only_the_unit_vendor_header() -> None:
    res = MutableResponse(status=200, headers={}, body=b"{}")
    create_toast_vendor().decorate(res, fake_ctx(), request())
    assert res.headers == {"x-unit-vendor": "toast"}


def test_hydrate_resolves_the_profiles_vendor_block_and_reseeds_both_streams() -> None:
    vendor = ToastVendor()
    before = vendor.ids.order()
    before_request = vendor.request_ids.request_id()
    vendor.hydrate(fake_ctx(vendor_config={"client_id": "other-client"}, chaos_seed=1), None)
    assert vendor.config.client_id == "other-client"
    assert vendor.ids.order() == before
    assert vendor.request_ids.request_id() == before_request


def test_the_id_streams_are_reseeded_from_the_unit_seed_not_the_constructor_arg() -> None:
    vendor = ToastVendor(seed=1)
    vendor.hydrate(fake_ctx(chaos_seed=99), None)
    first = [vendor.ids.order() for _ in range(3)]
    vendor.hydrate(fake_ctx(chaos_seed=99), None)
    assert [vendor.ids.order() for _ in range(3)] == first
    other = ToastVendor(seed=1)
    other.hydrate(fake_ctx(chaos_seed=100), None)
    assert [other.ids.order() for _ in range(3)] != first


def test_hydrate_loads_the_seed_restaurant_and_refuses_a_malformed_document() -> None:
    from vendorfake.toast.entities import RestaurantEntity

    unit = create_unit(vendor="toast", profile="full")
    try:
        ctx = unit.context
        stored = ctx.store.collection(COL.restaurants).require(SEED_RESTAURANT_GUID)
        assert RestaurantEntity.from_entity(stored).name == "Harvest & Rye — Toast"
        vendor = ToastVendor()
        vendor.hydrate(ctx, {"restaurant": {"guid": "second", "general": {"name": "Second"}}})
        assert ctx.store.collection(COL.restaurants).get("second") is not None
        with pytest.raises(UnitError) as caught:
            vendor.hydrate(ctx, {"restaurant": {"guid": "x", "general": {"name": "X"}}, "unknown": 1})
        assert caught.value.kind is UnitErrorKind.INVALID_VALUE
        assert caught.value.field == "seed"
        vendor.hydrate(ctx, None)
    finally:
        unit.stop()


# ---------------------------------------------------------------------------
# Registry and a started unit.
# ---------------------------------------------------------------------------


def test_the_registry_resolves_toast_and_lists_all_three_vendors() -> None:
    assert resolve_vendor("toast").name == "toast"
    assert set(available_vendors()) >= {"clover", "square", "toast"}


def test_a_typo_still_names_the_real_vendors_including_toast() -> None:
    with pytest.raises(ValueError) as caught:
        resolve_vendor("tost")
    assert "no vendor named 'tost'" in str(caught.value)
    assert "toast" in str(caught.value)


def test_a_toast_unit_starts_on_the_full_profile_with_an_empty_surface() -> None:
    unit = create_unit(vendor="toast", profile="full")
    try:
        assert unit.name == "toast"
        assert unit.context.config.profile == "full"
        assert set(unit.context.config.capabilities) == {
            "auth",
            "orders",
            "payments",
            "menus",
            "config",
            "restaurants",
            "partners",
            "stock",
            "chaos",
        }
        assert unit.context.store.collection(COL.restaurants).size == 1
    finally:
        unit.stop()


def test_two_toast_units_seed_identically_and_do_not_share_an_id_stream() -> None:
    first = create_unit(vendor="toast", profile="full")
    second = create_unit(vendor="toast", profile="full")
    try:
        assert first.context.store.entity_digest() == second.context.store.entity_digest()
        assert first.context.vendor is not second.context.vendor
        assert first.context.vendor.ids.order() == second.context.vendor.ids.order()  # type: ignore[attr-defined]
    finally:
        first.stop()
        second.stop()


def test_the_control_plane_publishes_the_two_machines_and_the_documented_errors() -> None:
    from vendorfake.core.transport.inprocess import in_process

    unit = create_unit(vendor="toast", profile="full")
    try:
        api = in_process(unit)
        machines = api.get("/__unit/machines").json()["machines"]
        assert set(machines) == {"check", "order"}
        assert machines["check"]["field"] == "paymentStatus"
        errors = {row["kind"]: row for row in api.get("/__unit/errors").json()["kinds"]}
        assert errors["forbidden_scope"]["status"] == 403
        assert errors["forbidden_scope"]["body"]["status"] == 403
        assert errors["rate_limited"]["headers"]["X-Toast-RateLimit-By"] == "ENDPOINT"
        missing = api.get("/orders/v2/nothing")
        assert missing.status == 404
        assert missing.headers["x-unit-error"] == "not_found"
        assert missing.json()["status"] == 404 and missing.json()["errors"] == []
    finally:
        unit.stop()
