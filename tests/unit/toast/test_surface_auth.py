"""The login endpoint: the documented request, answer and 401; the JWT it
mints; and the 403 the kernel produces for a scope the token lacks."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import CLIENT_ID, CLIENT_SECRET, RESTAURANT, Harness, Silent, harness
from vendorfake.toast.entities import COL
from vendorfake.toast.jwt import decode_jwt_payload, verify_jwt
from vendorfake.toast.seed.constants import SEED_PARTNER_GUID, SEED_SCOPES
from vendorfake.toast.surface.auth import INVALID_CREDENTIALS_MESSAGE, LOGIN_PATH
from vendorfake.toast.surface.common import RESTAURANT_HEADER

LOGIN = {"clientId": CLIENT_ID, "clientSecret": CLIENT_SECRET, "userAccessType": "TOAST_MACHINE_CLIENT"}


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_login_answers_the_documented_success_document(h: Harness) -> None:
    response = h.api.post(LOGIN_PATH, LOGIN)
    assert response.status == 200, response.text
    body = response.json()
    assert list(body) == ["@class", "token", "status"]
    assert body["@class"] == ".SuccessfulResponse" and body["status"] == "SUCCESS"
    token = body["token"]
    assert list(token) == ["tokenType", "scope", "expiresIn", "accessToken", "idToken", "refreshToken"]
    assert token["tokenType"] == "Bearer"
    assert token["scope"] is None and token["idToken"] is None and token["refreshToken"] is None
    assert token["expiresIn"] == 19168  # the documented example, the configured default


def test_the_minted_token_is_a_jwt_carrying_partner_guid_and_the_lifetime(h: Harness) -> None:
    token = h.api.post(LOGIN_PATH, LOGIN).json()["token"]["accessToken"]
    assert token.count(".") == 2
    claims = decode_jwt_payload(token)
    assert claims["partner_guid"] == SEED_PARTNER_GUID
    assert claims["exp"] - claims["iat"] == 19168
    assert claims["scope"].split(" ") == list(SEED_SCOPES)
    assert verify_jwt(token, "unit-toast-jwt-signing-secret")


def test_the_minted_token_authenticates_in_both_modes_and_is_journalled_as_login(h: Harness) -> None:
    before = h.journal_len()
    token = h.api.post(LOGIN_PATH, LOGIN).json()["token"]["accessToken"]
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [e["collection"] for e in entries] == [COL.tokens]
    assert entries[0]["meta"] == {"operation_id": "Login"}
    auth = h.unit.context.vendor.auth
    from types import SimpleNamespace

    headers = {"authorization": f"Bearer {token}", RESTAURANT_HEADER.lower(): RESTAURANT}
    args = SimpleNamespace(ctx=h.unit.context, header=lambda name: headers.get(name.lower()))
    assert auth.resolve(args, "restaurant").principal_id == SEED_PARTNER_GUID  # type: ignore[arg-type]
    assert auth.resolve(args, "bearer").token_id == decode_jwt_payload(token)["jti"]  # type: ignore[arg-type]


def test_two_logins_mint_two_distinct_deterministic_tokens() -> None:
    minted = []
    for _ in range(2):
        for h in harness("full", env={}):
            h.unit.context.clock  # noqa: B018 - the real clock is per process; only the ids are compared
            first = h.api.post(LOGIN_PATH, LOGIN).json()["token"]["accessToken"]
            second = h.api.post(LOGIN_PATH, LOGIN).json()["token"]["accessToken"]
            assert first != second
            minted.append((decode_jwt_payload(first)["jti"], decode_jwt_payload(second)["jti"]))
    assert minted[0] == minted[1]  # the id stream is seeded: same jti sequence on two units


@pytest.mark.parametrize(
    "body",
    [
        {**LOGIN, "clientSecret": "wrong"},
        {**LOGIN, "clientId": "someone-else"},
    ],
)
def test_bad_credentials_are_the_documented_401_and_journal_nothing(h: Harness, body: dict[str, str]) -> None:
    before = h.journal_len()
    drawn = h.unit.context.vendor.ids.draw_count  # type: ignore[attr-defined]
    response = h.api.post(LOGIN_PATH, body)
    assert response.status == 401
    assert response.headers["x-unit-error"] == "unauthorized"
    assert response.json()["message"] == INVALID_CREDENTIALS_MESSAGE
    assert response.json()["status"] == 401
    assert h.journal_len() == before
    assert h.unit.context.vendor.ids.draw_count == drawn  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({k: v for k, v in LOGIN.items() if k != "clientSecret"}, "clientSecret"),
        ({**LOGIN, "clientId": ""}, "clientId"),
        ({**LOGIN, "userAccessType": "TOAST_HUMAN"}, "userAccessType"),
    ],
)
def test_a_malformed_login_is_a_400_naming_the_field(h: Harness, body: dict[str, str], field: str) -> None:
    response = h.api.post(LOGIN_PATH, body)
    assert response.status == 400, response.text
    assert response.json()["unit_error"]["field"] == field


def test_a_form_encoded_login_reaches_the_handler_as_fields(h: Harness) -> None:
    """The content-type-general body reader: a consumer's client out of habit."""
    response = h.api.call(
        method="POST",
        path=LOGIN_PATH,
        headers={"content-type": "application/x-www-form-urlencoded"},
        raw_body=f"clientId={CLIENT_ID}&clientSecret={CLIENT_SECRET}&userAccessType=TOAST_MACHINE_CLIENT",
    )
    assert response.status == 200, response.text


def test_a_token_lacking_the_scope_gets_the_documented_403_through_the_kernel() -> None:
    """A test-only guarded route demanding a scope the read-only token lacks:
    the KERNEL's forbidden_scope raise reaches the shaper and is 403 --
    distinct from the 401 an invalid token gets, as Toast documents."""
    from tests.conformance.mutants.seams import VendorOverlay
    from vendorfake import create_unit
    from vendorfake.core.kernel.reply import json_
    from vendorfake.core.kernel.types import Route
    from vendorfake.core.transport.inprocess import in_process
    from vendorfake.toast.vendor import create_toast_vendor

    guarded = Route(
        method="POST",
        path="/orders/v2/prices",
        capability="auth",
        handler=lambda args: json_({"ok": True}),
        auth="restaurant",
        scopes=("orders:read",),
        operation_id="TestGuarded",
        summary="Test-only stand-in for the documented 403 on /prices.",
    )
    overlay = VendorOverlay(create_toast_vendor(), routes=lambda routes: (*routes, guarded))
    unit = create_unit(vendor=overlay, profile="full", logger=Silent())
    try:
        api = in_process(unit)
        p = Harness(unit=unit, api=api, auth={})
        weak = p.restricted_token("menus:read")
        forbidden = api.post("/orders/v2/prices", {}, headers=weak)
        assert forbidden.status == 403
        assert forbidden.headers["x-unit-error"] == "forbidden_scope"
        assert forbidden.json()["status"] == 403
        assert "orders:read" in forbidden.json()["message"]
        bad = api.post("/orders/v2/prices", {}, headers={"authorization": "Bearer nope", RESTAURANT_HEADER: RESTAURANT})
        assert bad.status == 401
        assert api.post("/orders/v2/prices", {}, headers=p.read_auth).status == 200
    finally:
        unit.stop()


def test_the_login_route_is_the_first_and_the_only_route_of_the_auth_capability(h: Harness) -> None:
    routes = h.api.get("/__unit/routes").json()["routes"]
    vendor_routes = [r for r in routes if not r["internal"]]
    assert vendor_routes[0]["path"] == LOGIN_PATH
    assert vendor_routes[0]["capability"] == "auth"
    assert "auth" not in vendor_routes[0]  # the login itself needs no bearer
    assert "example_body" not in vendor_routes[0]
