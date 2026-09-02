"""Menus V3: the documented document shape, money in dollars, the maps keyed
by referenceId, and the documented 404."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.toast.entities import COL
from vendorfake.toast.seed import constants as c
from vendorfake.toast.surface.menus import NO_PUBLISHED_DATA


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_document_has_the_documented_top_level_and_the_maps_are_keyed_by_reference_id(h: Harness) -> None:
    response = h.get("/menus/v3/menus")
    assert response.status == 200, response.text
    body = response.json()
    assert list(body) == [
        "restaurantGuid",
        "lastUpdated",
        "restaurantTimeZone",
        "menus",
        "modifierGroupReferences",
        "modifierOptionReferences",
        "preModifierGroupReferences",
    ]
    assert body["restaurantGuid"] == c.SEED_RESTAURANT_GUID
    assert body["restaurantTimeZone"] == "America/New_York"
    assert body["lastUpdated"].endswith("+0000")
    assert set(body["modifierGroupReferences"]) == {str(c.MODIFIER_GROUP_SIDES_REF)}
    assert set(body["modifierOptionReferences"]) == {str(c.MODIFIER_OPTION_FRIES_REF), str(c.MODIFIER_OPTION_SALAD_REF)}
    assert set(body["preModifierGroupReferences"]) == {str(c.PRE_MODIFIER_GROUP_REF)}
    group = body["modifierGroupReferences"]["2"]
    assert group["modifierOptionReferences"] == [6, 7]
    assert group["preModifierGroupReference"] == 10


def test_prices_are_decimal_dollars_and_the_documented_item_is_8_99(h: Harness) -> None:
    body = h.get("/menus/v3/menus").json()
    (menu,) = body["menus"]
    assert menu["guid"] == c.MENU_GUID
    items = {item["guid"]: item for group in menu["menuGroups"] for item in group["menuItems"]}
    soup = items[c.ITEM_SOUP_GUID]
    assert soup["price"] == 8.99
    assert soup["multiLocationId"] == c.ITEM_SOUP_MULTI_LOCATION_ID
    assert soup["taxInfo"] == [c.TAX_RATE_DEFAULT_GUID]
    assert items[c.ITEM_BURGER_GUID]["modifierGroupReferences"] == [2]
    assert body["modifierOptionReferences"]["7"]["price"] == 1.5
    assert body["modifierOptionReferences"]["6"]["price"] == 0.0
    pre = {p["name"]: p for p in body["preModifierGroupReferences"]["10"]["preModifiers"]}
    assert pre["EXTRA"]["multiplicationFactor"] == 2 and pre["EXTRA"]["chargeAsExtra"] is True
    assert "fixedPrice" not in pre["NO"]  # omitted, not null: the specification types it, unmarked nullable


def test_metadata_is_the_two_documented_fields(h: Harness) -> None:
    body = h.get("/menus/v3/metadata").json()
    assert list(body) == ["restaurantGuid", "lastUpdated"]
    assert body["restaurantGuid"] == c.SEED_RESTAURANT_GUID
    assert body["lastUpdated"] == h.get("/menus/v3/menus").json()["lastUpdated"]


def test_no_published_menu_is_the_documented_404(h: Harness) -> None:
    h.unit.context.store.collection(COL.menus).delete(c.SEED_RESTAURANT_GUID, {"operation_id": "TestUnpublish"})
    for path in ("/menus/v3/menus", "/menus/v3/metadata"):
        response = h.get(path)
        assert response.status == 404
        assert response.json()["message"] == NO_PUBLISHED_DATA


def test_the_restaurant_header_and_the_scope_are_required(h: Harness) -> None:
    assert h.api.get("/menus/v3/menus", headers=h.bearer_only).status == 400
    assert h.api.get("/menus/v3/menus", headers=h.restricted_token("orders:read")).status == 403
    assert h.api.get("/menus/v3/menus", headers=h.read_auth).status == 200
