"""The Merchants surface: the call an integration makes right after connecting.

Two routes, both reads, and the one documented oddity worth pinning: the list
response's array is named ``merchant`` (singular) and its cursor is an integer.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_MERCHANT_ID


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("orders-only")


def test_retrieve_merchant_returns_the_documented_shape(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/objects/Merchant --
    every field of the object, in its documented order, and nothing else."""
    response = h.api.get(f"/v2/merchants/{SEED_MERCHANT_ID}", headers=h.auth)
    assert response.status == 200, response.text
    merchant = response.json()["merchant"]
    assert list(merchant) == [
        "id",
        "business_name",
        "country",
        "language_code",
        "currency",
        "status",
        "main_location_id",
        "created_at",
    ]
    assert merchant["id"] == SEED_MERCHANT_ID
    assert merchant["business_name"] == "Jet Fuel Coffee"
    assert merchant["status"] == "ACTIVE"
    assert merchant["currency"] == "USD"
    # JUDGMENT, stated in `main_location_of`: the first seeded location.
    assert merchant["main_location_id"] == SEED_LOCATION_ID
    assert merchant["created_at"] == "2016-09-19T17:33:12.000Z"


def test_me_resolves_to_the_merchant_the_token_belongs_to(h: Harness) -> None:
    """ "If the string `me` is supplied as the ID, then the request returns
    the merchant that is currently accessible to this call."
    https://developer.squareup.com/reference/square/merchants-api/retrieve-merchant
    """
    by_alias = h.api.get("/v2/merchants/me", headers=h.auth).json()["merchant"]
    by_id = h.api.get(f"/v2/merchants/{SEED_MERCHANT_ID}", headers=h.auth).json()["merchant"]
    assert by_alias == by_id


def test_an_unknown_merchant_is_404(h: Harness) -> None:
    response = h.api.get("/v2/merchants/NOSUCHMERCHANT", headers=h.auth)
    assert response.status == 404
    assert first_error(response)["code"] == "NOT_FOUND"
    assert first_error(response)["field"] == "merchant_id"


def test_list_merchants_uses_the_singular_key_square_documents(h: Harness) -> None:
    """The array is `merchant`, not `merchants`, and there is no cursor on the
    only page. https://developer.squareup.com/reference/square/merchants-api/list-merchants"""
    body = h.api.get("/v2/merchants", headers=h.auth).json()
    assert list(body) == ["merchant"]
    assert [row["id"] for row in body["merchant"]] == [SEED_MERCHANT_ID]


def test_list_merchants_cursor_is_an_integer_offset(h: Harness) -> None:
    """The documented example prints `"cursor": 1`; an offset past the one
    seeded merchant is an empty page, and a non-integer is refused as a cursor."""
    assert h.api.call(method="GET", path="/v2/merchants", query={"cursor": "1"}, headers=h.auth).json() == {
        "merchant": []
    }
    refused = h.api.call(method="GET", path="/v2/merchants", query={"cursor": "abc"}, headers=h.auth)
    assert refused.status == 400
    assert refused.headers["x-unit-error"] == "invalid_cursor"


def test_both_routes_require_the_profile_scope(h: Harness) -> None:
    """MERCHANT_PROFILE_READ, per
    https://developer.squareup.com/docs/oauth-api/square-permissions. The
    read-only seeded token carries it; a request with no token does not."""
    assert h.api.get("/v2/merchants", headers=h.read_auth).status == 200
    assert h.api.get("/v2/merchants/me", headers=h.read_auth).status == 200
    assert h.api.get("/v2/merchants/me").status == 401


def test_the_surface_is_gone_when_the_capability_is_off() -> None:
    for scoped in build_harness("oauth-only"):
        response = scoped.api.get("/v2/merchants/me", headers=scoped.auth)
        assert response.status == 501
        assert response.headers["x-unit-capability"] == "merchant-directory"
