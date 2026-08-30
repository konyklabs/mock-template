"""Config v2: thirteen resources, the documented shapes, lastModified and the
Toast-Next-Page-Token paging."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.toast.model.config import CONFIG_RESOURCES
from vendorfake.toast.seed import constants as c
from vendorfake.toast.surface.config import NEXT_PAGE_TOKEN_HEADER


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


SEGMENTS = [resource.segment for resource in CONFIG_RESOURCES]


def test_the_thirteen_documented_resources_are_served() -> None:
    assert SEGMENTS == [
        "diningOptions",
        "alternatePaymentTypes",
        "taxRates",
        "revenueCenters",
        "serviceAreas",
        "tables",
        "restaurantServices",
        "discounts",
        "serviceCharges",
        "menuItems",
        "menuGroups",
        "menus",
        "voidReasons",
    ]


@pytest.mark.parametrize("segment", SEGMENTS)
def test_every_list_answers_reference_shaped_entities_and_by_guid_matches(h: Harness, segment: str) -> None:
    listed = h.get(f"/config/v2/{segment}")
    assert listed.status == 200, listed.text
    rows = listed.json()
    assert rows, segment
    assert listed.header(NEXT_PAGE_TOKEN_HEADER) is None  # one page
    for row in rows:
        assert list(row)[:3] == ["guid", "entityType", "externalId"]
        assert "modified_ms" not in row and "id" not in row
        one = h.get(f"/config/v2/{segment}/{row['guid']}")
        assert one.status == 200
        assert one.json() == row
    missing = h.get(f"/config/v2/{segment}/e6a4a8d2-0000-4000-8000-0000000000ff")
    assert missing.status == 404


def test_the_documented_shapes_and_money_in_dollars(h: Harness) -> None:
    dining = {row["guid"]: row for row in h.get("/config/v2/diningOptions").json()}
    assert dining[c.DINING_OPTION_TAKE_OUT_GUID]["behavior"] == "TAKE_OUT"
    assert dining[c.DINING_OPTION_DINE_IN_GUID]["entityType"] == "DiningOption"
    (rate,) = h.get("/config/v2/taxRates").json()
    assert rate["guid"] == c.TAX_RATE_DEFAULT_GUID
    assert rate["rate"] == 0.0625 and rate["isDefault"] is True and rate["roundingType"] == "HALF_UP"
    (table,) = [t for t in h.get("/config/v2/tables").json() if t["guid"] == c.TABLE_1_GUID]
    assert table["serviceArea"] == {"guid": c.SERVICE_AREA_GUID, "entityType": "ServiceArea"}
    assert table["revenueCenter"] == {"guid": c.REVENUE_CENTER_GUID, "entityType": "RevenueCenter"}
    (discount,) = h.get("/config/v2/discounts").json()
    assert (
        discount["name"] == "Enjoy more soup." and discount["percentage"] == 100 and discount["promoCodes"] == ["SOUP"]
    )
    (charge,) = h.get("/config/v2/serviceCharges").json()
    assert charge["amountType"] == "PERCENT" and charge["percent"] == 18 and charge["gratuity"] is True
    (alt,) = h.get("/config/v2/alternatePaymentTypes").json()
    assert alt == {
        "guid": c.ALT_PAYMENT_EXTERNAL_GUID,
        "entityType": "AlternatePaymentType",
        "externalId": None,
        "name": "External",
    }


def test_the_config_menu_views_are_derived_from_the_v3_menu_without_prices(h: Harness) -> None:
    items = {row["guid"]: row for row in h.get("/config/v2/menuItems").json()}
    assert set(items) == {c.ITEM_SOUP_GUID, c.ITEM_BURGER_GUID, c.ITEM_LEMONADE_GUID}
    assert "price" not in items[c.ITEM_SOUP_GUID]
    assert items[c.ITEM_BURGER_GUID]["optionGroups"] == [
        {"guid": c.MODIFIER_GROUP_SIDES_GUID, "entityType": "MenuOptionGroup"}
    ]
    groups = {row["guid"]: row for row in h.get("/config/v2/menuGroups").json()}
    assert groups[c.GROUP_MAINS_GUID]["menu"] == {"guid": c.MENU_GUID, "entityType": "Menu"}
    assert groups[c.GROUP_MAINS_GUID]["items"][0] == {
        "guid": c.ITEM_SOUP_GUID,
        "entityType": "MenuItem",
        "multiLocationId": c.ITEM_SOUP_MULTI_LOCATION_ID,
    }
    (menu,) = h.get("/config/v2/menus").json()
    assert menu["guid"] == c.MENU_GUID
    assert [g["guid"] for g in menu["groups"]] == [c.GROUP_MAINS_GUID, c.GROUP_DRINKS_GUID]


def test_last_modified_filters_inclusively_and_refuses_a_wrong_spelling(h: Harness) -> None:
    before = "2025-08-21T14:21:42.000+0000"  # exactly the seeded modified instant
    after = "2025-08-21T14:21:43.000+0000"
    assert len(h.get("/config/v2/tables", query={"lastModified": before}).json()) == 2
    assert h.get("/config/v2/tables", query={"lastModified": after}).json() == []
    bad = h.get("/config/v2/tables", query={"lastModified": "yesterday"})
    assert bad.status == 400
    assert bad.json()["unit_error"]["field"] == "lastModified"


def test_paging_emits_the_documented_header_and_a_replayed_token_is_refused(h: Harness) -> None:
    """More than 300 rows forces a second page; the token pages its own list
    and a different resource refuses it with the documented-status 400."""
    collection = h.unit.context.store.collection("void_reasons")
    for n in range(301):
        collection.insert({"id": f"void-{n:04d}", "name": f"Reason {n}", "modified_ms": 0}, {"seed": True})
    first = h.get("/config/v2/voidReasons")
    assert first.status == 200 and len(first.json()) == 300
    token = first.header(NEXT_PAGE_TOKEN_HEADER)
    assert token
    second = h.get("/config/v2/voidReasons", query={"pageToken": token})
    assert second.status == 200 and len(second.json()) == 2
    assert second.header(NEXT_PAGE_TOKEN_HEADER) is None
    foreign = h.get("/config/v2/tables", query={"pageToken": token})
    assert foreign.status == 400 and foreign.headers["x-unit-error"] == "invalid_cursor"
    garbage = h.get("/config/v2/voidReasons", query={"pageToken": "nope"})
    assert garbage.status == 400 and garbage.headers["x-unit-error"] == "invalid_cursor"


def test_the_restaurant_header_and_the_scope_are_required(h: Harness) -> None:
    assert h.api.get("/config/v2/tables", headers=h.bearer_only).status == 400
    assert h.api.get("/config/v2/tables", headers=h.restricted_token("orders:read")).status == 403
    assert h.api.get("/config/v2/tables", headers=h.read_auth).status == 200
