"""The shipped scenario: it parses, it is what the constants say, it loads
deterministically, and a wrong one is refused by name."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.entities import COL, RestaurantEntity, TokenEntity
from vendorfake.toast.seed import constants as c
from vendorfake.toast.seed.document import parse_seed_document


@pytest.fixture
def document() -> dict[str, Any]:
    return dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_shipped_scenario_parses_and_matches_the_constants(document: dict[str, Any]) -> None:
    doc = parse_seed_document(document)
    assert doc.restaurant.guid == c.SEED_RESTAURANT_GUID
    assert doc.restaurant.general.name == c.SEED_RESTAURANT_NAME
    assert doc.restaurant.general.managementGroupGuid == c.SEED_MANAGEMENT_GROUP_GUID
    assert doc.restaurant.general.timeZone == "America/New_York"
    assert doc.restaurant.general.closeoutHour == 4
    assert doc.restaurant.general.currencyCode == "USD"
    tokens = {t.access_token: t for t in doc.tokens}
    assert tokens[c.SEED_ACCESS_TOKEN].scopes is None  # the client's full set
    assert tuple(tokens[c.SEED_READ_ONLY_ACCESS_TOKEN].scopes or ()) == c.SEED_READ_ONLY_SCOPES


def test_every_constant_names_something_the_document_contains(document: dict[str, Any]) -> None:
    """The constants and the file agree, or this goes red."""
    guids = {
        "dining": {d["guid"] for d in document["dining_options"]},
        "alt": {a["guid"] for a in document["alternate_payment_types"]},
        "tax": {t["guid"] for t in document["tax_rates"]},
        "revenue": {r["guid"] for r in document["revenue_centers"]},
        "area": {s["guid"] for s in document["service_areas"]},
        "tables": {t["guid"] for t in document["tables"]},
        "services": {s["guid"] for s in document["restaurant_services"]},
        "discounts": {d["guid"] for d in document["discounts"]},
        "charges": {s["guid"] for s in document["service_charges"]},
        "void": {v["guid"] for v in document["void_reasons"]},
    }
    assert {c.DINING_OPTION_DINE_IN_GUID, c.DINING_OPTION_TAKE_OUT_GUID} == guids["dining"]
    assert {c.ALT_PAYMENT_EXTERNAL_GUID} == guids["alt"]
    assert {c.TAX_RATE_DEFAULT_GUID} == guids["tax"]
    assert {c.REVENUE_CENTER_GUID} == guids["revenue"]
    assert {c.SERVICE_AREA_GUID} == guids["area"]
    assert {c.TABLE_1_GUID, c.TABLE_2_GUID} == guids["tables"]
    assert {c.RESTAURANT_SERVICE_DINNER_GUID} == guids["services"]
    assert {c.DISCOUNT_SOUP_GUID} == guids["discounts"]
    assert {c.SERVICE_CHARGE_GRATUITY_GUID} == guids["charges"]
    assert {c.VOID_REASON_GUID} == guids["void"]
    assert document["tax_rates"][0]["rate"] == c.TAX_RATE_DEFAULT_RATE
    assert document["config_modified_ms"] == c.SEED_CONFIG_MODIFIED_MS
    menu = document["menu_v3"]
    assert menu["lastUpdated"] == c.MENU_LAST_UPDATED_MS
    assert menu["menus"][0]["guid"] == c.MENU_GUID
    groups = {g["guid"]: g for g in menu["menus"][0]["menuGroups"]}
    assert set(groups) == {c.GROUP_MAINS_GUID, c.GROUP_DRINKS_GUID}
    items = {i["guid"]: i for g in groups.values() for i in g["menuItems"]}
    assert set(items) == {c.ITEM_SOUP_GUID, c.ITEM_BURGER_GUID, c.ITEM_LEMONADE_GUID}
    assert items[c.ITEM_SOUP_GUID]["price"] == c.ITEM_SOUP_PRICE_CENTS
    assert items[c.ITEM_SOUP_GUID]["multiLocationId"] == c.ITEM_SOUP_MULTI_LOCATION_ID
    assert [g["referenceId"] for g in menu["modifierGroups"]] == [c.MODIFIER_GROUP_SIDES_REF]
    assert menu["modifierGroups"][0]["guid"] == c.MODIFIER_GROUP_SIDES_GUID
    assert {o["referenceId"]: o["guid"] for o in menu["modifierOptions"]} == {
        c.MODIFIER_OPTION_FRIES_REF: c.MODIFIER_OPTION_FRIES_GUID,
        c.MODIFIER_OPTION_SALAD_REF: c.MODIFIER_OPTION_SALAD_GUID,
    }
    assert menu["preModifierGroups"][0]["referenceId"] == c.PRE_MODIFIER_GROUP_REF
    assert {p["guid"] for p in menu["preModifierGroups"][0]["preModifiers"]} == {
        c.PRE_MODIFIER_NO_GUID,
        c.PRE_MODIFIER_EXTRA_GUID,
    }
    every_guid = {document["restaurant"]["guid"], *(g for group in guids.values() for g in group), *items, *groups}
    assert all(len(g) == 36 and g == g.lower() for g in every_guid)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda d: d["service_areas"][0].update(revenueCenter="nope"), "service_areas[0].revenueCenter"),
        (lambda d: d["tables"][0].update(serviceArea="nope"), "tables[0].serviceArea"),
        (
            lambda d: d["menu_v3"]["menus"][0]["menuGroups"][0]["menuItems"][0]["taxInfo"].append("nope"),
            "menu_v3.menus[0].menuGroups[0].menuItems[0].taxInfo[1]",
        ),
        (
            lambda d: d["menu_v3"]["modifierGroups"][0]["modifierOptionReferences"].append(99),
            "menu_v3.modifierGroups[0].modifierOptionReferences[2]",
        ),
        (
            lambda d: d["menu_v3"]["menus"][0]["menuGroups"][0]["menuItems"][1]["modifierGroupReferences"].append(99),
            "menu_v3.menus[0].menuGroups[0].menuItems[1].modifierGroupReferences[1]",
        ),
    ],
)
def test_a_reference_that_does_not_resolve_is_refused_by_path(document: dict[str, Any], mutate: Any, path: str) -> None:
    broken = copy.deepcopy(document)
    mutate(broken)
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.info is not None and caught.value.info["path"] == path


def test_a_misspelled_key_is_a_startup_failure_naming_it(document: dict[str, Any]) -> None:
    broken = copy.deepcopy(document)
    broken["tokns"] = broken.pop("tokens")
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "seed"
    assert "tokns" in str(caught.value)


def test_a_closeout_hour_outside_the_documented_range_is_refused(document: dict[str, Any]) -> None:
    broken = copy.deepcopy(document)
    broken["restaurant"]["general"]["closeoutHour"] = 13
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.info is not None and caught.value.info["path"] == "restaurant.general.closeoutHour"


def test_the_store_holds_the_restaurant_and_the_two_tokens(h: Harness) -> None:
    store = h.unit.context.store
    restaurant = RestaurantEntity.from_entity(store.collection(COL.restaurants).require(c.SEED_RESTAURANT_GUID))
    assert restaurant.name == c.SEED_RESTAURANT_NAME
    assert restaurant.time_zone == "America/New_York"
    assert restaurant.closeout_hour == 4
    assert restaurant.management_group_guid == c.SEED_MANAGEMENT_GROUP_GUID
    assert restaurant.wire()["guid"] == c.SEED_RESTAURANT_GUID
    assert list(restaurant.wire())[:2] == ["guid", "general"]
    assert store.collection(COL.tokens).size == 2
    assert store.collection(COL.menus).size == 1
    assert store.collection(COL.menu_items).size == 3
    assert store.collection(COL.menu_groups).size == 2
    assert store.collection(COL.config_menus).size == 1
    assert store.collection(COL.partners).size == 1
    assert store.collection(COL.tables).size == 2


def test_seeded_writes_are_marked_as_seeded(h: Harness) -> None:
    entries = h.api.get("/__unit/journal").json()["entries"]
    assert entries and all(e["meta"].get("seed") is True for e in entries)
    assert {e["meta"]["operation_id"] for e in entries} == {"SeedScenario"}


def test_the_seeded_tokens_carry_the_documented_ttl_and_their_scopes(h: Harness) -> None:
    tokens = h.unit.context.store.collection(COL.tokens)
    full = TokenEntity.from_entity(tokens.find(lambda t: t.get("access_token") == c.SEED_ACCESS_TOKEN) or {})
    read_only = TokenEntity.from_entity(
        tokens.find(lambda t: t.get("access_token") == c.SEED_READ_ONLY_ACCESS_TOKEN) or {}
    )
    now = h.unit.context.clock.now()
    assert abs(full.expires_at_ms - (now + 19168 * 1000)) < 5000
    assert full.scopes == c.SEED_SCOPES
    assert full.partner_guid == c.SEED_PARTNER_GUID
    assert full.client_id == c.SEED_CLIENT_ID
    assert read_only.scopes == c.SEED_READ_ONLY_SCOPES


def test_two_units_seeded_alike_hash_alike_and_reset_rebuilds_the_same_world() -> None:
    digests = []
    for _ in range(2):
        for h in harness():
            digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1]
    for h in harness():
        before = h.unit.context.store.entity_digest()
        h.unit.context.store.collection(COL.tokens).insert({"id": "extra", "access_token": "x"}, {"operation_id": "T"})
        assert h.unit.context.store.entity_digest() != before
        assert h.api.post("/__unit/state/reset").status == 200
        assert h.unit.context.store.entity_digest() == before
