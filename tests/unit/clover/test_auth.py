"""The auth adapter, and the documented 401 conflation asserted at its level.

"The API does not distinguish between an unauthorized error (401 -
expired/invalid token) and a permissions error (403 - token has insufficient
permissions) and returns a 401 Unauthorized in either case."
https://docs.clover.com/dev/docs/401-unauthorized
"""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from tests.unit.clover.harness import Harness, harness
from vendorfake.clover.entities import COL, TokenEntity
from vendorfake.clover.errors import CLOVER_ERROR_TABLE
from vendorfake.core.kernel.types import UnitError, UnitErrorKind


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _args_with(h: Harness, authorization: str | None):  # type: ignore[no-untyped-def]
    headers = {} if authorization is None else {"authorization": authorization}
    return SimpleNamespace(
        ctx=h.unit.context,
        header=lambda name: headers.get(name.lower()),
    )


def _insert_token(h: Harness, *, access_expiry_ms: int, permissions: tuple[str, ...] = ("ORDERS_R",)) -> TokenEntity:
    entity = TokenEntity(
        id="tok_manual00001",
        access_token="aaaaaaaa-1111-4222-8333-444444444444",
        refresh_token="bbbbbbbb-1111-4222-8333-444444444444",
        client_id="UNITCLOVERAPP",
        merchant_id="HRVSTRYE12345",
        access_token_expiration_ms=access_expiry_ms,
        refresh_token_expiration_ms=access_expiry_ms,
        permissions=permissions,
    )
    h.unit.context.store.collection(COL.tokens).insert(entity.to_entity(), {"operation_id": "TestSeed", "seed": True})
    return entity


def test_a_minted_token_resolves_with_its_app_inherited_permissions(h: Harness) -> None:
    body = h.exchange()
    auth = h.unit.context.vendor.auth
    result = auth.resolve(_args_with(h, f"Bearer {body['access_token']}"), "bearer")
    assert result.principal_id == "HRVSTRYE12345"
    # The app's fixed set, inherited at mint -- Clover has no per-token scopes.
    from vendorfake.clover.config import DEFAULT_PERMISSIONS

    assert set(result.scopes) == set(DEFAULT_PERMISSIONS)
    assert {"ORDERS_R", "ORDERS_W", "INVENTORY_R", "INVENTORY_W", "MERCHANT_R", "PAYMENTS_W"} <= set(result.scopes)
    assert result.token_id is not None


@pytest.mark.parametrize(
    ("header", "expected_kind"),
    [
        (None, UnitErrorKind.UNAUTHORIZED),
        ("Basic dXNlcg==", UnitErrorKind.UNAUTHORIZED),
        ("Bearer never-minted", UnitErrorKind.UNAUTHORIZED),
    ],
)
def test_bad_credentials_raise_kinds_whose_status_is_401(
    h: Harness, header: str | None, expected_kind: UnitErrorKind
) -> None:
    auth = h.unit.context.vendor.auth
    with pytest.raises(UnitError) as caught:
        auth.resolve(_args_with(h, header), "bearer")
    assert caught.value.kind is expected_kind
    assert CLOVER_ERROR_TABLE[caught.value.kind].status == 401


def test_an_expired_token_raises_token_expired_which_is_also_401(h: Harness) -> None:
    token = _insert_token(h, access_expiry_ms=1)
    auth = h.unit.context.vendor.auth
    with pytest.raises(UnitError) as caught:
        auth.resolve(_args_with(h, f"Bearer {token.access_token}"), "bearer")
    assert caught.value.kind is UnitErrorKind.TOKEN_EXPIRED
    assert CLOVER_ERROR_TABLE[UnitErrorKind.TOKEN_EXPIRED].status == 401


def _protected_unit(*, sidecar: bool) -> Harness:
    """A clover unit with one extra bearer route demanding a permission no
    token carries, so the KERNEL's own forbidden_scope raise -- detail naming
    the permission and all -- is what reaches the shaper."""
    from tests.conformance.mutants.seams import VendorOverlay
    from tests.unit.clover.harness import MERCHANT_ID, Silent
    from vendorfake import create_unit
    from vendorfake.clover.vendor import create_clover_vendor
    from vendorfake.core.kernel.reply import json_
    from vendorfake.core.kernel.types import Route
    from vendorfake.core.transport.inprocess import in_process

    guarded = Route(
        method="GET",
        path="/v3/merchants/{mId}/refunds",
        capability="oauth",
        handler=lambda args: json_({"ok": True}),
        auth="bearer",
        scopes=("REFUNDS_W",),
        operation_id="TestGuarded",
        summary="Test-only: needs REFUNDS_W, a permission the app's set never grants.",
    )
    inner = create_clover_vendor(vendor_config={"error_sidecar": sidecar})
    overlay = VendorOverlay(inner, routes=lambda routes: (*routes, guarded))
    unit = create_unit(vendor=overlay, profile="full", logger=Silent())  # the shipped seed supplies the merchant
    assert unit.context.store.collection(COL.merchants).get(MERCHANT_ID) is not None
    return Harness(unit=unit, api=in_process(unit), auth={})


def _three_failures(p: Harness) -> list:  # type: ignore[type-arg]
    """Bad token, expired token, under-permitted token -- the last through the
    kernel's real permission check, not a hand-built error."""
    live = p.exchange()
    expired = _insert_token(p, access_expiry_ms=1)
    path = "/v3/merchants/HRVSTRYE12345/refunds"
    return [
        p.api.get(path, headers={"authorization": "Bearer never-minted"}),
        p.api.get(path, headers={"authorization": f"Bearer {expired.access_token}"}),
        p.api.get(path, headers={"authorization": f"Bearer {live['access_token']}"}),
    ]


def test_the_conflation_makes_every_auth_failure_byte_identical_with_the_sidecar_off() -> None:
    """DOCUMENTED: bad token and insufficient permission both answer 401 and
    Clover does not say which. With the sidecar off the three bodies are the
    same bytes -- including the under-permitted one, whose kernel-raised
    detail names the missing permission and must never reach the wire."""
    p = _protected_unit(sidecar=False)
    try:
        responses = _three_failures(p)
        assert [r.status for r in responses] == [401, 401, 401]
        assert [r.headers["x-unit-error"] for r in responses] == ["unauthorized", "token_expired", "forbidden_scope"]
        bodies = {r.body for r in responses}
        assert bodies == {b'{"message":"401 Unauthorized"}'}, bodies
        assert b"REFUNDS_W" not in responses[2].body
    finally:
        p.unit.stop()


def test_the_conflation_is_still_debuggable_with_the_sidecar_on() -> None:
    """Same three failures, sidecar on: the wire message stays identical while
    unit_error distinguishes them and carries the suppressed detail."""
    p = _protected_unit(sidecar=True)
    try:
        responses = _three_failures(p)
        assert {r.json()["message"] for r in responses} == {"401 Unauthorized"}
        kinds = [r.json()["unit_error"]["kind"] for r in responses]
        assert kinds == ["unauthorized", "token_expired", "forbidden_scope"]
        assert "REFUNDS_W" in responses[2].json()["unit_error"]["detail"]
        assert {r.status for r in responses} == {401}
    finally:
        p.unit.stop()


def test_a_token_without_orders_w_gets_the_byte_identical_401_on_a_real_write_route() -> None:
    """The kernel's permission check on a shipped route, not a test-only one:
    ORDERS_R alone cannot POST /orders, and with the sidecar off the body is
    the same bytes a bad token gets. Nothing is journalled."""
    from tests.unit.clover.harness import Silent, seed
    from vendorfake import create_unit
    from vendorfake.clover.vendor import create_clover_vendor
    from vendorfake.core.transport.inprocess import in_process

    vendor = create_clover_vendor(vendor_config={"error_sidecar": False})
    unit = create_unit(vendor=vendor, profile="full", logger=Silent())
    try:
        seed(unit)
        p = Harness(unit=unit, api=in_process(unit), auth={})
        reader = p.restricted_token("ORDERS_R")
        before = p.journal_len()
        denied = p.api.post(p.path("/orders"), {"total": 1}, headers=reader)
        bad = p.api.post(p.path("/orders"), {"total": 1}, headers={"authorization": "Bearer never-minted"})
        assert denied.status == bad.status == 401
        assert denied.body == bad.body == b'{"message":"401 Unauthorized"}'
        assert denied.headers["x-unit-error"] == "forbidden_scope"
        assert p.journal_len() == before
        # And the same token reads fine: the permission set is real, not a switch.
        assert p.api.get(p.path("/orders"), headers=reader).status == 200
    finally:
        unit.stop()


def test_a_rotated_refresh_does_not_end_the_access_token_here_either(h: Harness) -> None:
    """The adapter never consults refresh_used_at_ms; only the clock ends a
    token. JUDGMENT labelled in surface/oauth.py."""
    first = h.exchange()
    assert h.refresh(refresh_token=first["refresh_token"]).status == 200
    auth = h.unit.context.vendor.auth
    result = auth.resolve(_args_with(h, f"Bearer {first['access_token']}"), "bearer")
    assert result.principal_id == "HRVSTRYE12345"


def test_credentials_offers_live_tokens_and_filters_expired_ones(h: Harness) -> None:
    body = h.exchange()
    _insert_token(h, access_expiry_ms=1)  # expired; must not be offered
    offered = h.unit.context.vendor.auth.credentials(h.unit.context)
    headers = {credential.headers["authorization"] for credential in offered}
    assert f"Bearer {body['access_token']}" in headers
    assert "Bearer aaaaaaaa-1111-4222-8333-444444444444" not in headers
    for credential in offered:
        assert credential.mode == "bearer"
        assert credential.scopes  # the app's permission set, discoverable


def test_describe_names_the_scheme_and_the_conflation(h: Harness) -> None:
    described = h.unit.context.vendor.auth.describe()
    assert "Bearer" in described["bearer"]
    assert "401" in described["conflation"]
