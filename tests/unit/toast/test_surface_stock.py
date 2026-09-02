"""Stock v1: the documented shape, the IN_STOCK omission, search, update rules,
and the labelled order refusal."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import order_body
from vendorfake.toast.seed import constants as c

DOCUMENTED_KEYS = ["guid", "itemGuidValidity", "status", "quantity", "multiLocationId", "versionId"]


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_inventory_omits_in_stock_and_the_rows_have_the_documented_shape(h: Harness) -> None:
    response = h.get("/stock/v1/inventory")
    assert response.status == 200, response.text
    rows = {row["guid"]: row for row in response.json()}
    assert set(rows) == {c.ITEM_BURGER_GUID, c.ITEM_LEMONADE_GUID}  # the IN_STOCK soup and options are omitted
    burger = rows[c.ITEM_BURGER_GUID]
    assert list(burger) == DOCUMENTED_KEYS
    assert burger["itemGuidValidity"] == "VALID" and burger["status"] == "QUANTITY"
    assert burger["quantity"] == c.STOCK_BURGER_QUANTITY
    assert burger["multiLocationId"] == "100000000171238202"
    assert burger["versionId"]
    only_quantity = h.get("/stock/v1/inventory", query={"status": "QUANTITY"}).json()
    assert {row["guid"] for row in only_quantity} == {c.ITEM_BURGER_GUID, c.ITEM_LEMONADE_GUID}
    assert h.get("/stock/v1/inventory", query={"status": "OUT_OF_STOCK"}).json() == []
    bad = h.get("/stock/v1/inventory", query={"status": "IN_STOCK"})
    assert bad.status == 400 and bad.json()["unit_error"]["field"] == "status"


def test_search_returns_in_stock_rows_and_marks_unknown_guids_invalid(h: Harness) -> None:
    response = h.post(
        "/stock/v1/inventory/search",
        {
            "guids": [c.ITEM_SOUP_GUID, "3c9a1f00-0000-4000-8000-00000000c2ff"],
            "multiLocationIds": [c.ITEM_SOUP_MULTI_LOCATION_ID, "1"],
            "versionIds": [],
        },
    )
    assert response.status == 200, response.text
    soup, unknown, by_multi, unknown_multi = response.json()
    assert soup["guid"] == c.ITEM_SOUP_GUID and soup["status"] == "IN_STOCK" and soup["quantity"] is None
    assert unknown == {
        "guid": "3c9a1f00-0000-4000-8000-00000000c2ff",
        "itemGuidValidity": "INVALID",
        # The guide's own INVALID rows: OUT_OF_STOCK, and the STRING "null"
        # for the identifiers the row does not have.
        "status": "OUT_OF_STOCK",
        "quantity": None,
        "multiLocationId": "null",
        "versionId": "3c9a1f00-0000-4000-8000-00000000c2ff",
    }
    assert by_multi == soup
    assert unknown_multi["itemGuidValidity"] == "INVALID" and unknown_multi["multiLocationId"] == "1"
    assert unknown_multi["guid"] == "null" and unknown_multi["status"] == "OUT_OF_STOCK"


def test_update_follows_the_documented_quantity_rules_and_journals_per_item(h: Harness) -> None:
    before = h.journal_len()
    response = h.put(
        "/stock/v1/inventory/update",
        [
            {"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"},
            {"multiLocationId": "100000000171238202", "status": "QUANTITY", "quantity": 4.0},
            {"guid": c.ITEM_LEMONADE_GUID, "status": "IN_STOCK"},
        ],
    )
    assert response.status == 200, response.text
    soup, burger, lemonade = response.json()
    assert soup["status"] == "OUT_OF_STOCK" and soup["quantity"] is None
    assert burger["guid"] == c.ITEM_BURGER_GUID and burger["quantity"] == 4.0
    assert lemonade["status"] == "IN_STOCK" and lemonade["quantity"] is None
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [(e["collection"], e["id"], e["meta"]["operation_id"]) for e in entries] == [
        ("stock", c.ITEM_SOUP_GUID, "StockInventoryUpdate"),
        ("stock", c.ITEM_BURGER_GUID, "StockInventoryUpdate"),
        ("stock", c.ITEM_LEMONADE_GUID, "StockInventoryUpdate"),
    ]
    listed = {row["guid"]: row["status"] for row in h.get("/stock/v1/inventory").json()}
    assert listed == {c.ITEM_SOUP_GUID: "OUT_OF_STOCK", c.ITEM_BURGER_GUID: "QUANTITY"}


@pytest.mark.parametrize(
    ("item", "field"),
    [
        ({"guid": c.ITEM_SOUP_GUID, "status": "QUANTITY"}, "[0].quantity"),
        ({"guid": c.ITEM_SOUP_GUID, "status": "QUANTITY", "quantity": 0}, "[0].quantity"),
        ({"guid": c.ITEM_SOUP_GUID, "status": "IN_STOCK", "quantity": 3}, "[0].quantity"),
        ({"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK", "quantity": 0}, "[0].quantity"),
        ({"guid": "3c9a1f00-0000-4000-8000-00000000c2ff", "status": "IN_STOCK"}, "[0].guid"),
        ({"status": "IN_STOCK"}, "[0].guid"),
        ({"guid": c.ITEM_SOUP_GUID, "status": "SOLD_OUT"}, "[0].status"),
    ],
)
def test_a_refused_update_writes_nothing(h: Harness, item: dict[str, object], field: str) -> None:
    before = h.journal_len()
    response = h.put("/stock/v1/inventory/update", [item])
    assert response.status == 400, response.text
    assert response.json()["unit_error"]["field"] == field
    assert h.journal_len() == before


def test_a_refusal_in_the_second_item_leaves_the_first_unwritten(h: Harness) -> None:
    before = h.journal_len()
    response = h.put(
        "/stock/v1/inventory/update",
        [{"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"}, {"guid": "nope", "status": "IN_STOCK"}],
    )
    assert response.status == 400 and response.json()["unit_error"]["field"] == "[1].guid"
    assert h.journal_len() == before


def test_ordering_an_out_of_stock_item_is_refused_naming_it_and_orders_do_not_decrement(h: Harness) -> None:
    """JUDGMENT (audit gap 4), both halves."""
    assert h.put("/stock/v1/inventory/update", [{"guid": c.ITEM_SOUP_GUID, "status": "OUT_OF_STOCK"}]).status == 200
    refused = h.post("/orders/v2/orders", order_body())
    assert refused.status == 400, refused.text
    assert "Tomato Soup" in refused.json()["message"] and c.ITEM_SOUP_GUID in refused.json()["message"]
    assert refused.json()["unit_error"]["field"] == "checks[0].selections[0].item.guid"
    assert h.post("/orders/v2/prices", order_body()).status == 400
    burger = {"item": {"guid": c.ITEM_BURGER_GUID}, "quantity": 5}
    assert h.post("/orders/v2/orders", order_body(burger)).status == 200
    (row,) = h.post("/stock/v1/inventory/search", {"guids": [c.ITEM_BURGER_GUID]}).json()
    assert row["quantity"] == c.STOCK_BURGER_QUANTITY  # unchanged


def test_stock_needs_the_restaurant_header_and_its_scopes(h: Harness) -> None:
    assert h.api.get("/stock/v1/inventory", headers=h.bearer_only).status == 400
    assert h.api.get("/stock/v1/inventory", headers=h.read_auth).status == 200
    assert (
        h.api.put(
            "/stock/v1/inventory/update", [{"guid": c.ITEM_SOUP_GUID, "status": "IN_STOCK"}], headers=h.read_auth
        ).status
        == 403
    )
