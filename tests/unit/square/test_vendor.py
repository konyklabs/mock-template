"""The vendor definition: what it declares, and the two-phase configuration."""

from __future__ import annotations

import pytest

import vendorfake.square as square
from tests.unit.square.conftest import fake_ctx
from vendorfake.core.capability.gates import CORE_GATED_CAPABILITIES, check_capability_declarations
from vendorfake.core.kernel.types import MutableResponse, UnitError, UnitErrorKind, UnitRequest, VendorDefinition
from vendorfake.square.machine import ORDER_MACHINE
from vendorfake.square.retry import SQUARE_RETRY_SCHEDULE_MS
from vendorfake.square.vendor import SquareVendor, create_square_vendor


def request(headers: dict[str, str] | None = None) -> UnitRequest:
    return UnitRequest(
        id="req_1",
        method="GET",
        path="/v2/locations",
        query={},
        headers=headers or {},
        raw_body=b"",
        transport="inprocess",
        received_at="2026-08-25T00:00:00.000Z",
    )


def test_the_definition_satisfies_the_protocol() -> None:
    """The annotation is the check: mypy verifies the structural conformance of
    SquareVendor at `create_square_vendor`'s return, and this asserts at run
    time that the registry's target really is one."""
    definition: VendorDefinition = create_square_vendor()
    assert definition.name == "square"
    assert definition.display_name == "Square (Connect v2)"
    assert definition.api_version == square.SQUARE_API_VERSION


def test_vendor_is_minted_fresh_on_every_access() -> None:
    """A vendor owns a stateful id stream. Two units sharing one would
    interleave their draws and neither would reproduce its own ids -- and the
    conformance suite builds a fresh unit per check, in one process."""
    first = square.VENDOR
    second = square.VENDOR
    assert first is not second
    assert first.name == second.name == "square"
    assert first.ids.order() == second.ids.order()  # type: ignore[attr-defined]


def test_a_typo_on_the_module_still_raises_attribute_error() -> None:
    with pytest.raises(AttributeError):
        square.VENDORS  # type: ignore[attr-defined]  # noqa: B018


def test_every_core_gated_capability_is_declared_or_excused() -> None:
    """The core refuses to start a vendor that gates on a capability it never
    declared, because "you never told me" is otherwise indistinguishable from
    "switched off"."""
    report = check_capability_declarations(square.SQUARE_CAPABILITIES, square.SQUARE_NOT_SUPPORTED)
    assert report.ok, report.problems
    declared = {decl.name for decl in square.SQUARE_CAPABILITIES}
    for gate in CORE_GATED_CAPABILITIES:
        assert gate.capability.value in declared
    assert square.SQUARE_NOT_SUPPORTED == {}


def test_the_behaviour_capabilities_carry_their_prerequisites() -> None:
    by_name = {decl.name: decl for decl in square.SQUARE_CAPABILITIES}
    assert by_name["chaos"].kind == "behavior"
    assert by_name["webhooks.chaos"].kind == "behavior"
    assert set(by_name["webhooks.chaos"].requires) == {"webhooks", "chaos"}
    assert by_name["webhooks"].kind == "surface"


def test_the_order_machine_is_registered_so_the_control_plane_can_publish_it() -> None:
    machines = create_square_vendor().machines
    assert machines == {"order": ORDER_MACHINE}


def test_the_retry_defaults_carry_squares_documented_schedule() -> None:
    """The core ships no schedule; an unmerged default would present as every
    delivery exhausting on its first attempt."""
    retry = create_square_vendor().retry_defaults.webhooks.retry
    assert retry.schedule_ms == SQUARE_RETRY_SCHEDULE_MS
    assert len(retry.schedule_ms) == 11
    assert retry.schedule_ms[0] == 60_000
    assert retry.timeout_ms == 10_000
    assert retry.time_scale == 1 / 6000


def test_volatile_fields_are_the_wall_clock_ones() -> None:
    assert set(create_square_vendor().volatile_fields) == {
        "expires_at",
        "refresh_token_expires_at",
        "closed_at",
        "used_at",
        "revoked_at",
        "superseded_at",
    }


def test_magic_triggers_name_fields_a_consumer_can_actually_set() -> None:
    magic = create_square_vendor().magic
    assert magic is not None
    assert magic.prefix == "chaos:"
    assert set(magic.body_paths) == {"order.reference_id", "idempotency_key", "subscription.name"}
    assert tuple(magic.query_params) == ("state",)


# ---------------------------------------------------------------------------
# decorate.
# ---------------------------------------------------------------------------


def test_decorate_stamps_the_api_version_it_implements() -> None:
    res = MutableResponse(status=200, headers={}, body=b"{}")
    create_square_vendor().decorate(res, fake_ctx(), request())
    assert res.headers["square-version"] == square.SQUARE_API_VERSION
    assert res.headers["x-unit-vendor"] == "square"


def test_decorate_echoes_the_requested_version() -> None:
    """ "Regardless of whether you explicitly specify a version in the request,
    the response always returns the Square-Version header.\""""
    res = MutableResponse(status=200, headers={}, body=b"{}")
    create_square_vendor().decorate(res, fake_ctx(), request({"square-version": "2021-05-13"}))
    assert res.headers["square-version"] == "2021-05-13"


def test_decorate_echoes_even_an_empty_requested_version() -> None:
    """`??` in the reference is nullish, not falsy: a header that was sent is
    echoed, and only an absent one is replaced by the default."""
    res = MutableResponse(status=200, headers={}, body=b"{}")
    create_square_vendor().decorate(res, fake_ctx(), request({"square-version": ""}))
    assert res.headers["square-version"] == ""


# ---------------------------------------------------------------------------
# Configuration, phase two.
# ---------------------------------------------------------------------------


def test_the_profile_vendor_block_wins_over_the_base() -> None:
    vendor = SquareVendor(config=square.resolve_square_config({"api_version": "2020-01-01"}))
    assert vendor.api_version == "2020-01-01"
    vendor._resolve_config(fake_ctx(vendor_config={"api_version": "2030-12-31", "environment": "Production"}))
    assert vendor.api_version == "2030-12-31"
    assert vendor.config.environment == "Production"
    # Untouched keys keep the base's values rather than reverting to defaults.
    assert vendor.config.application_id == "sandbox-sq0idb-unit-square-application"


def test_the_error_shaper_is_rebuilt_when_the_profile_turns_the_sidecar_off() -> None:
    vendor = SquareVendor()
    ctx = fake_ctx(vendor_config={"error_sidecar": False})
    vendor._resolve_config(ctx)
    body = vendor.errors.shape(UnitError(UnitErrorKind.INTERNAL), ctx).body
    assert isinstance(body, dict)
    assert "unit_error" not in body


def test_the_id_stream_is_reseeded_from_the_unit_seed() -> None:
    """A unit that re-hydrates on POST /__unit/state/reset mints the ids it
    minted the first time. That is what makes a scenario reproducible rather
    than merely repeatable."""
    vendor = SquareVendor(seed=1)
    vendor._resolve_config(fake_ctx(chaos_seed=99))
    first = [vendor.ids.order() for _ in range(3)]
    vendor._resolve_config(fake_ctx(chaos_seed=99))
    assert [vendor.ids.order() for _ in range(3)] == first
    # And the seed really is the unit's, not the one the vendor was built with.
    other = SquareVendor(seed=1)
    other._resolve_config(fake_ctx(chaos_seed=100))
    assert [other.ids.order() for _ in range(3)] != first


def test_an_unknown_key_in_the_vendor_block_is_refused_by_name() -> None:
    """Silently ignoring it is how a consumer ends up debugging an OAuth flow
    against the secret they believe they replaced."""
    with pytest.raises(Exception) as caught:
        SquareVendor()._resolve_config(fake_ctx(vendor_config={"aplication_id": "typo"}))
    assert "aplication_id" in str(caught.value)


# ---------------------------------------------------------------------------
# The surfaces, and the seams that are still to come.
# ---------------------------------------------------------------------------


def test_the_shipped_surfaces_are_wired_and_cached() -> None:
    """Cached, not rebuilt: the router, the capability index and the OpenAPI
    document each read this property, and three different route tuples holding
    three different bound methods would be three different surfaces."""
    vendor = create_square_vendor()
    assert vendor.routes is vendor.routes
    assert [(route.method, route.path) for route in vendor.routes] == [
        ("GET", "/oauth2/authorize"),
        ("POST", "/oauth2/token"),
        ("POST", "/oauth2/revoke"),
        ("POST", "/oauth2/token/status"),
        ("POST", "/v2/orders"),
        ("POST", "/v2/orders/search"),
        ("POST", "/v2/orders/batch-retrieve"),
        ("GET", "/v2/orders/{order_id}"),
        ("PUT", "/v2/orders/{order_id}"),
        ("POST", "/v2/orders/{order_id}/pay"),
    ]
    assert {route.capability for route in vendor.routes} == {"oauth", "order-lifecycle"}


def test_every_route_template_uses_braces_never_colons() -> None:
    """`{order_id}`, never `:order_id`. The router, the chaos `match.route`
    key, the capability index and the generated OpenAPI document all read the
    same template, and a colon path would match nothing in any of them."""
    for route in create_square_vendor().routes:
        assert ":" not in route.path


def test_the_webhook_seams_are_still_open() -> None:
    assert create_square_vendor().signer is None
    assert create_square_vendor().events is None


def test_hydrate_refuses_a_missing_scenario_rather_than_leaving_an_empty_store() -> None:
    """An empty store would answer 404 to every read as though the scenario
    were simply small, which is the failure mode this project exists to remove."""
    with pytest.raises(UnitError) as caught:
        create_square_vendor().hydrate(fake_ctx(), None)
    assert caught.value.kind is UnitErrorKind.INTERNAL
    assert "No seed scenario" in str(caught.value)


def test_hydrate_still_applies_the_profile_config_first() -> None:
    """The order matters beyond tidiness: the tokens a scenario seeds are
    stamped with the expiry the *profile's* TTL implies, so resolving the
    config after loading would seed them against the built-in default."""
    vendor = SquareVendor()
    with pytest.raises(UnitError):
        vendor.hydrate(fake_ctx(vendor_config={"api_version": "2030-12-31"}), None)
    assert vendor.api_version == "2030-12-31"


def test_the_auth_adapter_describes_the_documented_schemes() -> None:
    described = create_square_vendor().auth.describe()
    assert "Bearer" in described["bearer"]
    assert "Client" in described["client-secret"]
    assert "ORDERS_WRITE" in described["scopes"]
