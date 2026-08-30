"""Locations and catalog: the reference data an order points at.

The rules from the Orders suite apply here too -- every assertion names its
source, and absence is asserted as absence rather than as a null. The two
things this file exists to pin beyond shape are the code-point page ordering
(the reference sorts with ICU collation, which disagrees with Python on
mixed-case data) and the cursor's query fingerprint, which is what makes
"you must use the original query" a refusal rather than a wrong answer.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.unit.square.harness import APPLICATION_SECRET, Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import (
    COLD_BREW_ITEM_ID,
    COLD_BREW_LARGE_VARIATION_ID,
    COLD_BREW_SMALL_VARIATION_ID,
    DEFAULT_SEED_PATH,
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_MERCHANT_ID,
    SEED_OPEN_ORDER_ID,
    TEA_ITEM_ID,
    TEA_MUG_VARIATION_ID,
    TEA_POT_VARIATION_ID,
)
from vendorfake.square.surface.directory import directory_routes

#: Two catalog ids whose ICU collation and code-point ordering disagree: ICU
#: puts "a" before "B"; Python's `sorted` puts "B" (U+0042) before "a" (U+0061).
LOWER_ID = "aTEMMIXEDCASELOWERAAAAAA"
UPPER_ID = "BTEMMIXEDCASEUPPERAAAAAA"


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("orders-only")


@pytest.fixture
def collated(tmp_path: Path) -> Iterator[Harness]:
    """The shipped scenario plus two items whose ids collate differently.

    Built from ``default.seed.json`` rather than written from scratch, so the
    fixture cannot drift from the document the rest of the suite asserts
    against.
    """
    document: dict[str, Any] = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for item_id in (LOWER_ID, UPPER_ID):
        document["catalog"]["items"].append(
            {
                "id": item_id,
                "name": f"Item {item_id}",
                "catalog_version": 1_479_335_124_878,
                "variations": [
                    {"id": f"V{item_id[1:]}", "name": "Only", "price_money": {"amount": 100, "currency": "USD"}}
                ],
            }
        )
    path = tmp_path / "collated.seed.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    yield from build_harness("orders-only", env={"VENDORFAKE_SEED": str(path)})


def catalog(h: Harness, **query: str) -> dict[str, Any]:
    response = h.api.call(method="GET", path="/v2/catalog/list", query=query, headers=h.auth)
    assert response.status == 200, response.text
    return dict(response.json())


# ---------------------------------------------------------------------------
# ListLocations
# ---------------------------------------------------------------------------


def test_list_locations_returns_the_documented_location_shape(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/locations-api/list-locations"""
    body = h.api.get("/v2/locations", headers=h.auth).json()
    assert [location["id"] for location in body["locations"]] == [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]

    grant_park = body["locations"][0]
    assert grant_park["name"] == "Grant Park"
    assert grant_park["merchant_id"] == SEED_MERCHANT_ID
    assert grant_park["status"] == "ACTIVE"
    assert grant_park["type"] == "PHYSICAL"
    assert grant_park["currency"] == "USD"
    assert grant_park["country"] == "US"
    assert grant_park["language_code"] == "en-US"
    assert grant_park["timezone"] == "America/Los_Angeles"
    assert grant_park["capabilities"] == ["CREDIT_CARD_PROCESSING"]
    assert grant_park["address"]["locality"] == "San Francisco"
    assert grant_park["phone_number"] == "+1 650-354-7217"
    # The store's own stamp, not a field this vendor models: it means "when
    # this unit learned about the location", and the seed document pins it.
    assert grant_park["created_at"] == "2016-09-19T17:33:12.000Z"


def test_an_absent_optional_is_absent_rather_than_null(h: Harness) -> None:
    """The kiosk has no phone number. Square omits the key; a `null` would make
    a consumer's `if "phone_number" in location` take the wrong branch."""
    body = h.api.get("/v2/locations", headers=h.auth).json()
    kiosk = next(location for location in body["locations"] if location["id"] == SEED_KIOSK_LOCATION_ID)
    assert "phone_number" not in kiosk
    assert kiosk["type"] == "MOBILE"


def test_each_route_declares_the_scope_square_documents_for_it() -> None:
    """https://developer.squareup.com/docs/oauth-api/square-permissions

    Declared on the route and checked by the kernel, never by the handler --
    which is why a scope is asserted as route data here and enforced once,
    below, for both.
    """
    routes = {route.path: tuple(route.scopes) for route in directory_routes()}
    assert routes == {
        "/v2/merchants": ("MERCHANT_PROFILE_READ",),
        "/v2/merchants/{merchant_id}": ("MERCHANT_PROFILE_READ",),
        "/v2/locations": ("MERCHANT_PROFILE_READ",),
        "/v2/catalog/list": ("ITEMS_READ",),
    }


# ---------------------------------------------------------------------------
# ListCatalog
# ---------------------------------------------------------------------------


def test_list_catalog_returns_items_with_their_variations_nested(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/catalog-api/retrieve-catalog-object

    An ITEM nests its ITEM_VARIATION objects rather than returning them as
    siblings, which is the shape Square's own example prints.
    """
    body = catalog(h)
    assert [obj["id"] for obj in body["objects"]] == sorted([TEA_ITEM_ID, COLD_BREW_ITEM_ID])
    tea = next(obj for obj in body["objects"] if obj["id"] == TEA_ITEM_ID)
    assert tea["type"] == "ITEM"
    assert tea["is_deleted"] is False
    assert tea["present_at_all_locations"] is True
    # Square's catalog version, a millisecond-epoch-shaped int64 -- NOT this
    # unit's entity version, which counts mutations of its own copy.
    assert tea["version"] == 1_479_335_124_878
    assert tea["item_data"]["name"] == "Tea"
    assert tea["item_data"]["description"] == "Hot Leaf Juice"
    assert [v["id"] for v in tea["item_data"]["variations"]] == [TEA_MUG_VARIATION_ID, TEA_POT_VARIATION_ID]

    mug = tea["item_data"]["variations"][0]
    assert mug["type"] == "ITEM_VARIATION"
    assert mug["item_variation_data"] == {
        "item_id": TEA_ITEM_ID,
        "name": "Mug",
        "pricing_type": "FIXED_PRICING",
        "price_money": {"amount": 150, "currency": "USD"},
    }


def test_the_last_page_carries_no_cursor(h: Harness) -> None:
    """ "The last page of the result set doesn't include a cursor."
    https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination

    Asserted as absence, so a consumer looping until the key is missing
    terminates.
    """
    assert "cursor" not in catalog(h)


def test_types_defaults_to_item_and_accepts_a_comma_separated_list(h: Harness) -> None:
    """Variations are returned nested inside their item, so ITEM is the only
    top-level type this unit has -- and asking for ITEM_VARIATION explicitly
    returns them flat."""
    assert {obj["type"] for obj in catalog(h)["objects"]} == {"ITEM"}
    assert {obj["type"] for obj in catalog(h, types="ITEM_VARIATION")["objects"]} == {"ITEM_VARIATION"}
    assert {obj["type"] for obj in catalog(h, types="ITEM,ITEM_VARIATION")["objects"]} == {"ITEM", "ITEM_VARIATION"}


def test_types_is_case_insensitive_and_tolerates_whitespace(h: Harness) -> None:
    """A query string is typed by a human as often as it is built by a client."""
    assert catalog(h, types=" item , ITEM_VARIATION ")["objects"] == catalog(h, types="ITEM,ITEM_VARIATION")["objects"]


def test_an_unknown_type_returns_an_empty_page_rather_than_an_error(h: Harness) -> None:
    """Square publishes many `CatalogObjectType` values this unit does not
    model; a consumer syncing its whole catalog asks for all of them, and
    refusing the request would fail on the shrink rather than on anything the
    consumer did wrong."""
    assert catalog(h, types="DISCOUNT")["objects"] == []


def test_the_page_is_ordered_by_code_point_and_not_by_locale(collated: Harness) -> None:
    """ICU puts "a" before "B"; Python's `sorted` puts "B" first. The reference
    sorts with `localeCompare`, so a catalog with mixed-case ids pages
    differently under the two implementations -- and page order is on the wire.

    Code point everywhere is the cross-language contract stated in
    `vendorfake.core.util.json`.
    """
    ids = [obj["id"] for obj in catalog(collated)["objects"]]
    assert ids.index(UPPER_ID) < ids.index(LOWER_ID)
    assert ids == sorted(ids)


def test_a_cursor_walks_the_pages_and_stops(h: Harness) -> None:
    first = catalog(h, types="ITEM_VARIATION", limit="1")
    assert [obj["id"] for obj in first["objects"]] == [
        sorted(
            [
                TEA_MUG_VARIATION_ID,
                TEA_POT_VARIATION_ID,
                COLD_BREW_SMALL_VARIATION_ID,
                COLD_BREW_LARGE_VARIATION_ID,
            ]
        )[0]
    ]
    assert first["cursor"]

    seen = [obj["id"] for obj in first["objects"]]
    cursor = first["cursor"]
    while cursor:
        page = catalog(h, types="ITEM_VARIATION", limit="1", cursor=cursor)
        seen.extend(obj["id"] for obj in page["objects"])
        cursor = page.get("cursor", "")
    assert seen == sorted(
        [TEA_MUG_VARIATION_ID, TEA_POT_VARIATION_ID, COLD_BREW_SMALL_VARIATION_ID, COLD_BREW_LARGE_VARIATION_ID]
    )


def test_types_item_pages_with_a_cursor_and_nests_variations_on_every_page(h: Harness) -> None:
    """The consumer's catalog sync: `GET /v2/catalog/list?types=ITEM` with a
    page size smaller than the catalog. The first page carries a `cursor`,
    the last does not, and each item on each page still nests its
    variations -- paging must not flatten the shape."""
    first = catalog(h, types="ITEM", limit="1")
    assert len(first["objects"]) == 1
    assert first["cursor"]
    assert first["objects"][0]["type"] == "ITEM"
    assert len(first["objects"][0]["item_data"]["variations"]) == 2

    last = catalog(h, types="ITEM", limit="1", cursor=first["cursor"])
    assert len(last["objects"]) == 1
    assert "cursor" not in last
    assert len(last["objects"][0]["item_data"]["variations"]) == 2
    assert {first["objects"][0]["id"], last["objects"][0]["id"]} == {TEA_ITEM_ID, COLD_BREW_ITEM_ID}


def test_a_cursor_from_a_different_query_is_refused(h: Harness) -> None:
    """ "include the cursor along with the same original request body"
    https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination

    Refused rather than silently answered from the wrong result set, which is
    the failure a consumer cannot see.
    """
    cursor = catalog(h, types="ITEM_VARIATION", limit="1")["cursor"]
    response = h.api.call(
        method="GET",
        path="/v2/catalog/list",
        query={"types": "ITEM", "cursor": cursor},
        headers=h.auth,
    )
    assert response.status == 400
    assert response.headers["x-unit-error"] == "invalid_cursor"


def test_changing_only_the_page_size_keeps_the_cursor_valid(h: Harness) -> None:
    """The fingerprint covers the filter and not the paging, so a consumer that
    grows its page size mid-walk is not punished for it."""
    cursor = catalog(h, types="ITEM_VARIATION", limit="1")["cursor"]
    page = catalog(h, types="ITEM_VARIATION", limit="2", cursor=cursor)
    assert len(page["objects"]) == 2


def test_a_malformed_limit_is_refused_rather_than_ignored(h: Harness) -> None:
    """The reference does `Number(query('limit'))`, so `limit=abc` is `NaN`,
    which its pagination reads as "no limit": a consumer who mistyped a page
    size silently receives the default and never learns."""
    for bad in ("abc", "0", "-1", "1.5"):
        response = h.api.call(method="GET", path="/v2/catalog/list", query={"limit": bad}, headers=h.auth)
        assert response.status == 400, bad
        assert response.headers["x-unit-error"] == "invalid_value"
        assert first_error(response)["field"] == "limit"


# ---------------------------------------------------------------------------
# Gating.
# ---------------------------------------------------------------------------


def test_a_token_without_the_scope_gets_403_and_not_404() -> None:
    """403 and not 404: the endpoint exists, and telling a consumer otherwise
    would send them looking for a typo instead of at their scope list.

    The narrow token is minted through the real OAuth dance rather than seeded,
    so the assertion covers the path a consumer actually takes to get one.
    """
    for h in build_harness("full"):
        code = h.code(scope="ORDERS_READ")
        minted = h.token(grant_type="authorization_code", code=code, client_secret=APPLICATION_SECRET)
        assert minted.status == 200, minted.text
        narrow = {"authorization": f"Bearer {minted.json()['access_token']}"}

        for path in ("/v2/locations", "/v2/catalog/list"):
            response = h.api.get(path, headers=narrow)
            assert response.status == 403, path
            assert response.headers["x-unit-error"] == "forbidden_scope"
        # The same token reads orders, so the refusals above are about the
        # scope and not about the token being broken.
        assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=narrow).status == 200


def test_the_surface_is_gone_when_the_capability_is_off_and_says_so() -> None:
    """Not a 404: a consumer must be able to tell "this unit does not serve
    reference data" from "you typed the path wrong"."""
    for h in build_harness("oauth-only"):
        for path in ("/v2/locations", "/v2/catalog/list"):
            response = h.api.get(path, headers=h.auth)
            assert response.status == 501
            assert response.headers["x-unit-error"] == "capability_disabled"
            assert "merchant-directory" in response.text
