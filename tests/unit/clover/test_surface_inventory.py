"""The inventory surface and the merchant read."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import ITEM_BEER, MERCHANT_ID, SEED_ITEMS, Harness, harness


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_merchant_record_carries_id_name_owner_and_address(h: Harness) -> None:
    body = h.get("").json()
    assert body["id"] == MERCHANT_ID
    assert body["name"] == "Harvest & Rye"
    assert body["owner"]["id"] == "OWNERHRVST001"
    assert body["address"]["city"] == "Springfield"
    assert "currency" not in body  # internal to this unit, not a documented merchant field


def test_create_item_matches_the_documented_example_shape(h: Harness) -> None:
    """The verbatim create-item response: id, hidden false, available true,
    name, price, priceType FIXED, defaultTaxRates true, isRevenue (JUDGMENT
    default False), modifiedTime in ms."""
    response = h.post("/items", {"name": "Craft Beer", "price": 750})
    assert response.status == 200
    item = response.json()
    assert len(item["id"]) == 13
    assert item["hidden"] is False
    assert item["available"] is True
    assert item["name"] == "Craft Beer"
    assert item["price"] == 750
    assert item["priceType"] == "FIXED"
    assert item["defaultTaxRates"] is True
    assert item["isRevenue"] is False
    assert item["modifiedTime"] > 10**12
    explicit = h.post("/items", {"name": "Tip", "price": 0, "priceType": "VARIABLE", "isRevenue": True}).json()
    assert explicit["priceType"] == "VARIABLE"
    assert explicit["isRevenue"] is True


def test_create_item_requires_name_and_price(h: Harness) -> None:
    """DOCUMENTED: name and price are required; a missing one is a 400
    naming the field, and nothing is journalled."""
    before = h.journal_len()
    no_price = h.post("/items", {"name": "Free"})
    assert no_price.status == 400
    assert no_price.json()["unit_error"]["field"] == "price"
    no_name = h.post("/items", {"price": 100})
    assert no_name.status == 400
    assert no_name.json()["unit_error"]["field"] == "name"
    empty_name = h.post("/items", {"name": "", "price": 100})
    assert empty_name.status == 400
    assert empty_name.json()["unit_error"]["field"] == "name"
    fractional = h.post("/items", {"name": "x", "price": 7.5})
    assert fractional.status == 400
    assert h.journal_len() == before


def test_list_items_is_the_elements_envelope_with_hrefs_and_paging(h: Harness) -> None:
    body = h.get("/items").json()
    assert [e["id"] for e in body["elements"]] == [item_id for item_id, _, _ in SEED_ITEMS]
    assert (
        body["elements"][0]["href"] == f"https://apisandbox.dev.clover.com/v3/merchants/{MERCHANT_ID}/items/{ITEM_BEER}"
    )
    assert body["elements"][0]["price"] == 750
    first = h.get("/items", query={"limit": "2"}).json()["elements"]
    rest = h.get("/items", query={"limit": "2", "offset": "2"}).json()["elements"]
    assert len(first) == 2 and len(rest) == 1
    assert {e["id"] for e in first}.isdisjoint({e["id"] for e in rest})
    assert h.get("/items", query={"limit": "x"}).status == 400


def test_get_item_and_its_404(h: Harness) -> None:
    assert h.get(f"/items/{ITEM_BEER}").json()["name"] == "Craft Beer"
    missing = h.get("/items/NOSUCHITEM001")
    assert missing.status == 404
    assert missing.json()["unit_error"]["field"] == "itemId"


def test_inventory_routes_need_their_own_permissions(h: Harness) -> None:
    """A token with only ORDERS_R reaches no inventory route, and the refusal
    is the documented conflated 401 -- byte-identical to a bad token."""
    auth = h.restricted_token("ORDERS_R")
    bad = h.api.get(h.path("/items"), headers={"authorization": "Bearer never-minted"})
    for method, suffix, body in (
        ("GET", "/items", None),
        ("POST", "/items", {"name": "x", "price": 1}),
        ("GET", "", None),
    ):
        response = h.api.call(method=method, path=h.path(suffix), body=body, headers=auth)
        assert response.status == 401, suffix
        assert response.json()["message"] == bad.json()["message"] == "401 Unauthorized"
