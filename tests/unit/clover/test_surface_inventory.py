"""The inventory surface and the merchant read."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import ITEM_BEER, MERCHANT_ID, SEED_ITEMS, Harness, harness


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


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


def test_update_item_is_post_sparse_and_journalled_as_update_item(h: Harness) -> None:
    """The sold-out reconciliation path: flip `available`, nothing else moves."""
    before = h.journal_len()
    response = h.post(f"/items/{ITEM_BEER}", {"available": False})
    assert response.status == 200
    item = response.json()
    assert item["available"] is False
    assert item["name"] == "Craft Beer" and item["price"] == 750
    assert h.get(f"/items/{ITEM_BEER}").json()["available"] is False
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [(e["collection"], e["op"], e["meta"]["operation_id"]) for e in entries] == [
        ("items", "update", "UpdateItem")
    ]
    assert h.post(f"/items/{ITEM_BEER}", {"name": ""}).status == 400
    assert h.post("/items/NOSUCHITEM001", {"available": True}).status == 404


def test_items_expand_modifier_groups_and_tax_rates(h: Harness) -> None:
    """`GET /items?expand=modifierGroups&limit=1000` is the menu-sync call;
    the expansion is the documented elements shape with `modifierIds` as the
    comma-joined string the example shows."""
    from tests.unit.clover.harness import ITEM_ESPRESSO, MOD_GROUP_MILK, MOD_OAT, MOD_SOY, TAX_BEVERAGE

    body = h.get("/items", query={"expand": "modifierGroups", "limit": "1000"}).json()
    by_id = {e["id"]: e for e in body["elements"]}
    assert by_id[ITEM_ESPRESSO]["modifierGroups"] == {
        "elements": [
            {"id": MOD_GROUP_MILK, "name": "Milk", "showByDefault": True, "modifierIds": f"{MOD_OAT},{MOD_SOY}"}
        ]
    }
    assert by_id[ITEM_BEER]["modifierGroups"] == {"elements": []}
    single = h.get(f"/items/{ITEM_BEER}", query={"expand": "modifierGroups,taxRates"}).json()
    assert single["taxRates"]["elements"][0]["id"] == TAX_BEVERAGE
    assert "modifierGroups" not in h.get(f"/items/{ITEM_BEER}").json()
    assert h.get("/items", query={"expand": "categories"}).status == 400


def test_modifiers_of_a_group_and_the_available_flag(h: Harness) -> None:
    from tests.unit.clover.harness import MOD_GROUP_MILK, MOD_OAT, MOD_SOY

    body = h.get(f"/modifier_groups/{MOD_GROUP_MILK}/modifiers").json()
    assert [(e["id"], e["name"], e["price"], e["available"]) for e in body["elements"]] == [
        (MOD_OAT, "Oat milk", 50, True),
        (MOD_SOY, "Soy milk", 50, False),
    ]
    assert body["elements"][0]["href"].endswith(f"/modifier_groups/{MOD_GROUP_MILK}/modifiers/{MOD_OAT}")
    soy = h.get(f"/modifier_groups/{MOD_GROUP_MILK}/modifiers/{MOD_SOY}").json()
    assert soy["available"] is False
    assert soy["modifierGroup"] == {"id": MOD_GROUP_MILK}
    assert h.get("/modifier_groups/NOSUCHGROUP01/modifiers").status == 404
    assert h.get(f"/modifier_groups/{MOD_GROUP_MILK}/modifiers/NOSUCHMOD0001").status == 404


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
        ("POST", f"/items/{ITEM_BEER}", {"available": False}),
    ):
        response = h.api.call(method=method, path=h.path(suffix), body=body, headers=auth)
        assert response.status == 401, suffix
        assert response.json()["message"] == bad.json()["message"] == "401 Unauthorized"
