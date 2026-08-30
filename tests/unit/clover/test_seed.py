"""The shipped scenario: it parses, it is what the constants say, it loads
deterministically, and a wrong one is refused by name."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.clover.harness import Harness, harness
from vendorfake.clover.entities import COL, OrderEntity, TokenEntity
from vendorfake.clover.seed import constants as c
from vendorfake.clover.seed.document import parse_seed_document
from vendorfake.core.kernel.types import UnitError, UnitErrorKind


@pytest.fixture
def document() -> dict[str, Any]:
    return dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_the_shipped_scenario_parses(document: dict[str, Any]) -> None:
    doc = parse_seed_document(document)
    assert doc.merchant.id == c.SEED_MERCHANT_ID
    assert len(doc.items) == 3 and len(doc.tokens) == 2 and len(doc.orders) == 1


def test_every_constant_names_something_the_document_contains(document: dict[str, Any]) -> None:
    """The constants and the file agree, or this goes red."""
    ids = {
        "merchant": {document["merchant"]["id"]},
        "employees": {e["id"] for e in document["employees"]},
        "tenders": {t["id"] for t in document["tenders"]},
        "order_types": {o["id"] for o in document["order_types"]},
        "service_charges": {s["id"] for s in document["service_charges"]},
        "tax_rates": {t["id"] for t in document["tax_rates"]},
        "items": {i["id"] for i in document["items"]},
        "modifier_groups": {g["id"] for g in document["modifier_groups"]},
        "modifiers": {m["id"] for m in document["modifiers"]},
        "customers": {x["id"] for x in document["customers"]},
        "orders": {o["id"] for o in document["orders"]},
    }
    assert c.SEED_MERCHANT_ID in ids["merchant"]
    assert {c.EMPLOYEE_OWNER_ID, c.EMPLOYEE_BARISTA_ID} == ids["employees"]
    assert {c.TENDER_CASH_ID, c.TENDER_EXTERNAL_ID} == ids["tenders"]
    assert {c.ORDER_TYPE_DINE_IN_ID, c.ORDER_TYPE_TAKE_OUT_ID} == ids["order_types"]
    assert {c.SERVICE_CHARGE_DEFAULT_ID} == ids["service_charges"]
    assert {c.TAX_DEFAULT_ID, c.TAX_BEVERAGE_ID} == ids["tax_rates"]
    assert {c.ITEM_BEER_ID, c.ITEM_ESPRESSO_ID, c.ITEM_CROISSANT_ID} == ids["items"]
    assert {c.MODIFIER_GROUP_MILK_ID} == ids["modifier_groups"]
    assert {c.MODIFIER_OAT_ID, c.MODIFIER_SOY_ID} == ids["modifiers"]
    assert {c.CUSTOMER_ADA_ID} == ids["customers"]
    assert {c.SEED_OPEN_ORDER_ID} == ids["orders"]
    rates = {t["id"]: t["rate"] for t in document["tax_rates"]}
    assert rates == {c.TAX_DEFAULT_ID: c.TAX_DEFAULT_RATE, c.TAX_BEVERAGE_ID: c.TAX_BEVERAGE_RATE}
    order = document["orders"][0]
    assert order["total"] == c.SEED_OPEN_ORDER_TOTAL
    assert order["lineItems"][0]["id"] == c.SEED_OPEN_ORDER_LINE_ID
    tokens = {t["access_token"]: t for t in document["tokens"]}
    assert tokens[c.SEED_ACCESS_TOKEN]["refresh_token"] == c.SEED_REFRESH_TOKEN
    assert "permissions" not in tokens[c.SEED_ACCESS_TOKEN]  # the app's full set
    assert tokens[c.SEED_READ_ONLY_ACCESS_TOKEN]["refresh_token"] == c.SEED_READ_ONLY_REFRESH_TOKEN
    assert tuple(tokens[c.SEED_READ_ONLY_ACCESS_TOKEN]["permissions"]) == c.SEED_READ_ONLY_PERMISSIONS
    assert all(len(i) == 13 and i.isupper() for group in ids.values() for i in group)


def test_a_misspelled_key_is_a_startup_failure_naming_it(document: dict[str, Any]) -> None:
    broken = copy.deepcopy(document)
    broken["itmes"] = broken.pop("items")
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "seed"
    assert "itmes" in str(caught.value)


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda d: d["items"][0]["taxRates"].append({"id": "NOSUCHTAX0001"}), "items[0].taxRates[1].id"),
        (lambda d: d["items"][1]["modifierGroupIds"].append("NOSUCHGROUP01"), "items[1].modifierGroupIds[1]"),
        (lambda d: d["modifiers"][0].update(modifierGroup={"id": "NOSUCHGROUP01"}), "modifiers[0].modifierGroup.id"),
        (lambda d: d["orders"][0].update(orderType={"id": "NOSUCHTYPE001"}), "orders[0].orderType.id"),
        (lambda d: d["orders"][0].update(employee={"id": "NOSUCHEMPL001"}), "orders[0].employee.id"),
        (
            lambda d: d["orders"][0]["lineItems"].append({"id": "L2", "item": {"id": "NOSUCHITEM001"}}),
            "orders[0].lineItems[1].item.id",
        ),
        (lambda d: d["orders"][0]["lineItems"].append({"id": "L2"}), "orders[0].lineItems[1].price"),
    ],
)
def test_a_reference_that_does_not_resolve_is_refused_by_path(document: dict[str, Any], mutate: Any, path: str) -> None:
    broken = copy.deepcopy(document)
    mutate(broken)
    with pytest.raises(UnitError) as caught:
        parse_seed_document(broken)
    assert caught.value.info is not None and caught.value.info["path"] == path


def test_the_store_holds_what_the_document_describes(h: Harness) -> None:
    store = h.unit.context.store
    assert {e["id"] for e in store.collection(COL.items).all()} == {
        c.ITEM_BEER_ID,
        c.ITEM_ESPRESSO_ID,
        c.ITEM_CROISSANT_ID,
    }
    assert store.collection(COL.employees).size == 2
    assert store.collection(COL.tenders).size == 2
    assert store.collection(COL.order_types).size == 2
    assert store.collection(COL.tax_rates).size == 2
    assert store.collection(COL.modifier_groups).size == 1
    assert store.collection(COL.modifiers).size == 2
    assert store.collection(COL.service_charges).size == 1
    assert store.collection(COL.customers).size == 1
    assert store.collection(COL.orders).size == 1
    assert store.collection(COL.tokens).size == 2


def test_the_seeded_order_inherits_its_line_from_the_item_and_keeps_its_stated_total(h: Harness) -> None:
    order = OrderEntity.from_entity(h.unit.context.store.collection(COL.orders).require(c.SEED_OPEN_ORDER_ID))
    assert order.total == c.SEED_OPEN_ORDER_TOTAL
    assert order.lineItems[0]["price"] == 750 and order.lineItems[0]["name"] == "Craft Beer"
    assert order.createdTime == order.modifiedTime == order.clientCreatedTime == 1755786102000
    assert order.state == "open" and order.paymentState == "OPEN"
    on_wire = h.get(f"/orders/{c.SEED_OPEN_ORDER_ID}", query={"expand": "lineItems,employee"}).json()
    assert on_wire["employee"] == {"id": c.EMPLOYEE_BARISTA_ID}
    assert on_wire["lineItems"][0]["item"] == {"id": c.ITEM_BEER_ID}


def test_seeded_writes_are_marked_as_seeded(h: Harness) -> None:
    entries = h.api.get("/__unit/journal").json()["entries"]
    assert entries and all(e["meta"].get("seed") is True for e in entries)
    assert {e["meta"]["operation_id"] for e in entries} == {"SeedScenario"}


def test_the_seeded_tokens_carry_the_profiles_ttl_and_permissions(h: Harness) -> None:
    tokens = h.unit.context.store.collection(COL.tokens)
    full = TokenEntity.from_entity(tokens.require("tok_seed_full"))
    read_only = TokenEntity.from_entity(tokens.require("tok_seed_readonly"))
    now = h.unit.context.clock.now()
    assert abs(full.access_token_expiration_ms - (now + 30 * 60 * 1000)) < 5000
    assert full.permissions == c.SEED_PERMISSIONS
    assert read_only.permissions == c.SEED_READ_ONLY_PERMISSIONS
    assert h.get("/orders").status == 200
    assert h.api.get(h.path("/orders"), headers=h.read_auth).status == 200
    denied = h.api.post(h.path("/orders"), {"total": 1}, headers=h.read_auth)
    assert denied.status == 401


def test_two_units_seeded_alike_hash_alike_and_reset_rebuilds_the_same_world() -> None:
    digests = []
    for _ in range(2):
        for h in harness():
            digests.append(h.unit.context.store.entity_digest())
    assert digests[0] == digests[1]
    for h in harness():
        before = h.unit.context.store.entity_digest()
        h.create_order()
        assert h.unit.context.store.entity_digest() != before
        assert h.api.post("/__unit/state/reset").status == 200
        assert h.unit.context.store.entity_digest() == before


def test_the_seeded_webhook_subscriber_ships_disabled_and_receives_nothing() -> None:
    """Its callback is a dead `.test` host: enabled, every mutation a consumer
    makes would fire the whole retry cascade into it. It ships as the shape of
    a verified subscription and nothing else."""
    from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION
    from vendorfake.core.webhooks.sink import MemorySink

    sink = MemorySink()
    for h in harness(sink=sink):
        stored = h.unit.context.store.collection(SUBSCRIPTION_COLLECTION).require(c.SEED_WEBHOOK_SUBSCRIPTION_ID)
        assert stored["enabled"] is False
        assert stored["signature_key"] == c.SEED_WEBHOOK_AUTH_CODE
        assert stored["notification_url"] == c.SEED_WEBHOOK_URL
        assert "verified" not in stored  # pre-verified to the dashboard stand-in
        h.create_order()
        h.api.post("/__unit/webhooks/drain", {})
        assert sink.received == []
