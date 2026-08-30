"""Restaurants v1 and Partners v1: the documented documents, bearer-only."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.toast.seed import constants as c


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_restaurant_document_has_the_documented_blocks(h: Harness) -> None:
    response = h.api.get(f"/restaurants/v1/restaurants/{c.SEED_RESTAURANT_GUID}", headers=h.bearer_only)
    assert response.status == 200, response.text
    body = response.json()
    assert list(body) == ["guid", "general", "urls", "location", "schedules", "delivery", "onlineOrdering", "prepTimes"]
    assert body["general"]["name"] == c.SEED_RESTAURANT_NAME
    assert body["general"]["timeZone"] == "America/New_York"
    assert body["general"]["closeoutHour"] == 4
    assert body["general"]["managementGroupGuid"] == c.SEED_MANAGEMENT_GROUP_GUID
    assert body["location"]["city"] == "Springfield"


def test_an_unknown_restaurant_is_404_and_the_group_lists_its_members(h: Harness) -> None:
    assert (
        h.api.get("/restaurants/v1/restaurants/e6a4a8d2-0000-4000-8000-0000000000ff", headers=h.bearer_only).status
        == 404
    )
    members = h.api.get(f"/restaurants/v1/groups/{c.SEED_MANAGEMENT_GROUP_GUID}/restaurants", headers=h.bearer_only)
    assert members.status == 200
    assert [m["guid"] for m in members.json()] == [c.SEED_RESTAURANT_GUID]
    assert h.api.get("/restaurants/v1/groups/nope/restaurants", headers=h.bearer_only).status == 404


def test_restaurants_take_a_bearer_and_no_restaurant_header(h: Harness) -> None:
    path = f"/restaurants/v1/restaurants/{c.SEED_RESTAURANT_GUID}"
    assert h.api.get(path).status == 401
    assert h.api.get(path, headers=h.bearer_only).status == 200
    assert h.api.get(path, headers=h.auth).status == 200  # the header is simply ignored here
    assert h.api.get(path, headers=h.restricted_token("orders:read")).status == 403


def test_connected_restaurants_is_the_documented_page_envelope(h: Harness) -> None:
    response = h.api.get("/partners/v1/connectedRestaurants", headers=h.bearer_only)
    assert response.status == 200, response.text
    body = response.json()
    assert list(body) == [
        "currentPageNum",
        "results",
        "totalResultCount",
        "pageSize",
        "currentPageToken",
        "nextPageToken",
        "totalCount",
        "nextPageNum",
        "lastPageNum",
        "previousPageNum",
    ]
    assert body["pageSize"] == 100 and body["currentPageNum"] == 1 and body["nextPageToken"] is None
    (row,) = body["results"]
    assert list(row) == [
        "restaurantGuid",
        "managementGroupGuid",
        "restaurantName",
        "locationName",
        "createdByEmailAddress",
        "externalGroupRef",
        "externalRestaurantRef",
        "modifiedDate",
        "createdDate",
        "isoModifiedDate",
        "isoCreatedDate",
        "deleted",
        "scopes",
    ]
    assert row["restaurantGuid"] == c.SEED_RESTAURANT_GUID
    assert row["managementGroupGuid"] == c.SEED_MANAGEMENT_GROUP_GUID
    assert row["modifiedDate"] == 1755786102000 and row["isoModifiedDate"] == "2025-08-21T14:21:42.000+0000"
    assert row["scopes"] == list(c.SEED_SCOPES)
    assert h.api.get("/partners/v1/restaurants", headers=h.bearer_only).json() == [row]


def test_connected_restaurants_pages_and_filters(h: Harness) -> None:
    partners = h.unit.context.store.collection("partners")
    for n in range(3):
        partners.insert(
            {"id": f"extra-{n}", "restaurantName": f"Extra {n}", "modifiedDate": 1_000 * n, "createdDate": 0},
            {"seed": True},
        )
    first = h.api.get("/partners/v1/connectedRestaurants", query={"pageSize": "2"}, headers=h.bearer_only).json()
    assert first["totalCount"] == 4 and first["lastPageNum"] == 2 and first["nextPageNum"] == 2
    second = h.api.get(
        "/partners/v1/connectedRestaurants",
        query={"pageSize": "2", "pageToken": first["nextPageToken"]},
        headers=h.bearer_only,
    ).json()
    assert second["currentPageNum"] == 2 and second["previousPageNum"] == 1 and second["nextPageToken"] is None
    assert {r["restaurantGuid"] for r in first["results"]} | {r["restaurantGuid"] for r in second["results"]} == {
        c.SEED_RESTAURANT_GUID,
        "extra-0",
        "extra-1",
        "extra-2",
    }
    filtered = h.api.get(
        "/partners/v1/restaurants", query={"lastModified": "2025-08-21T14:21:42.000+0000"}, headers=h.bearer_only
    ).json()
    assert [r["restaurantGuid"] for r in filtered] == [c.SEED_RESTAURANT_GUID]
    too_big = h.api.get("/partners/v1/connectedRestaurants", query={"pageSize": "201"}, headers=h.bearer_only)
    assert too_big.status == 400 and too_big.json()["unit_error"]["field"] == "pageSize"
