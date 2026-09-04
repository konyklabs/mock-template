"""The shipped scenario, and the constants that name it.

The point of the first test is that ``seed/constants.py`` and
``default.seed.json`` cannot drift: a hand-edit to either is a red test rather
than a fixture that quietly stops matching.
"""

from __future__ import annotations

import json

import pytest

from tests.unit.lightspeed.harness import Harness
from vendorfake.core.kernel.types import UnitError
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION, RegisterEntity, TokenEntity
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.seed.document import parse_seed_document
from vendorfake.lightspeed.versioning import FIRST_VERSION


def _document() -> dict[str, object]:
    return dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))


def test_every_constant_names_something_the_document_contains() -> None:
    doc = parse_seed_document(_document())
    assert doc.retailer.id == c.SEED_RETAILER_ID
    assert doc.retailer.name == c.SEED_RETAILER_NAME
    assert doc.retailer.domain_prefix == c.SEED_DOMAIN_PREFIX
    assert [outlet.id for outlet in doc.outlets] == [c.SEED_OUTLET_MAIN_ID, c.SEED_OUTLET_SECOND_ID]
    assert [register.id for register in doc.registers] == [c.SEED_REGISTER_MAIN_ID, c.SEED_REGISTER_SECOND_ID]
    assert [payment_type.id for payment_type in doc.payment_types] == [
        c.SEED_PAYMENT_TYPE_CASH_ID,
        c.SEED_PAYMENT_TYPE_CARD_ID,
        c.SEED_PAYMENT_TYPE_INTERNAL_ID,
    ]
    assert [token.access_token for token in doc.tokens] == [c.SEED_ACCESS_TOKEN, c.SEED_READ_ONLY_ACCESS_TOKEN]
    assert [token.access_token for token in doc.personal_tokens] == [c.SEED_PERSONAL_ACCESS_TOKEN]
    assert [token.refresh_token for token in doc.refresh_tokens] == [c.SEED_REFRESH_TOKEN]
    assert [hook.id for hook in doc.webhooks] == [c.SEED_WEBHOOK_ID]
    assert doc.webhooks[0].type == c.SEED_WEBHOOK_TYPE
    assert doc.webhooks[0].url == c.SEED_WEBHOOK_URL
    assert [product.id for product in doc.products] == [
        c.SEED_PRODUCT_TRAIL_MIX_ID,
        c.SEED_PRODUCT_SOCKS_ID,
        c.SEED_PRODUCT_BOTTLE_ID,
        c.SEED_PRODUCT_TEE_ID,
        c.SEED_PRODUCT_TEE_SMALL_ID,
        c.SEED_PRODUCT_TEE_LARGE_ID,
    ]
    assert doc.products[0].sku == c.SEED_PRODUCT_TRAIL_MIX_SKU
    assert doc.products[2].sku == c.SEED_PRODUCT_BOTTLE_SKU
    assert [group.id for group in doc.customer_groups] == [c.SEED_CUSTOMER_GROUP_ID]
    assert [customer.id for customer in doc.customers] == [
        c.SEED_CUSTOMER_ADA_ID,
        c.SEED_CUSTOMER_BLAKE_ID,
        c.SEED_CUSTOMER_NOOR_ID,
    ]
    assert [reason.id for reason in doc.adjustment_reasons] == [
        c.SEED_ADJUSTMENT_REASON_FOUND_ID,
        c.SEED_ADJUSTMENT_REASON_SPOILED_ID,
    ]
    assert [row.id for row in doc.stock_adjustments] == [
        c.SEED_STOCK_ADJUSTMENT_FIRST_ID,
        c.SEED_STOCK_ADJUSTMENT_SECOND_ID,
    ]


def test_the_family_is_one_parent_and_two_variants() -> None:
    """``?name=`` selects a FAMILY, and ``?variants=true`` on the inventory read
    needs a parent with children -- so the scenario ships one."""
    doc = parse_seed_document(_document())
    by_id = {product.id: product for product in doc.products}
    parent = by_id[c.SEED_PRODUCT_TEE_ID]
    assert parent.has_variants is True
    assert parent.has_inventory is False, "stock lives on the variants, not the family's parent"
    for variant_id in (c.SEED_PRODUCT_TEE_SMALL_ID, c.SEED_PRODUCT_TEE_LARGE_ID):
        assert by_id[variant_id].variant_parent_id == c.SEED_PRODUCT_TEE_ID
        assert by_id[variant_id].family_id == parent.family_id


def test_one_product_is_seeded_inactive() -> None:
    """So that ``include_inactive`` on the inventory-levels report has something
    to include, and excluding it is observable."""
    doc = parse_seed_document(_document())
    inactive = [product.id for product in doc.products if not product.active]
    assert inactive == [c.SEED_PRODUCT_BOTTLE_ID]


def test_every_stock_holding_product_has_a_row_at_both_outlets() -> None:
    doc = parse_seed_document(_document())
    stocked = {product.id for product in doc.products if product.has_inventory}
    outlets = {c.SEED_OUTLET_MAIN_ID, c.SEED_OUTLET_SECOND_ID}
    for product_id in stocked:
        seen = {row.outlet_id for row in doc.inventory if row.product_id == product_id}
        assert seen == outlets, product_id
    assert len(doc.inventory) == len(stocked) * 2


def test_the_scenario_loads_what_it_says_it_loads(h: Harness) -> None:
    stats = h.api.get("/__unit/state").json()["entities"]
    assert stats == {
        "adjustment_reasons": 2,
        "customer_groups": 1,
        "customers": 3,
        "inventory": 10,
        "oauth_apps": 1,
        "outlets": 2,
        "payment_types": 3,
        "products": 6,
        "refresh_tokens": 1,
        "registers": 2,
        "retailer": 1,
        "sales": 3,
        "stock_adjustments": 2,
        "subscriptions": 1,
        "tokens": 3,
    }


def test_one_register_starts_open_and_one_closed(h: Harness) -> None:
    """So that a close (and the webhook it fires) and an open each need no
    setup."""
    registers = {
        row["id"]: RegisterEntity.from_entity(row) for row in h.unit.context.store.collection(COL.registers).all()
    }
    assert registers[c.SEED_REGISTER_MAIN_ID].is_open is True
    assert registers[c.SEED_REGISTER_SECOND_ID].is_open is False


def test_the_read_only_token_carries_no_write_scope(h: Harness) -> None:
    stored = h.unit.context.store.collection(COL.tokens).find(
        lambda entity: entity.get("access_token") == c.SEED_READ_ONLY_ACCESS_TOKEN
    )
    assert stored is not None
    scopes = set(TokenEntity.from_entity(stored).scopes)
    assert not scopes & {
        "register:open",
        "register:close",
        "webhooks",
        "products:write",
        "customers:write",
        # `inventory:write` gates GET /stock_adjustments as well as the batch,
        # so a read-only token cannot see the adjustment log either. That is
        # the vendor's own annotation, not a choice made here.
        "inventory:write",
    }


def test_versions_ascend_from_the_first_and_are_unique(h: Harness) -> None:
    """One retailer-global sequence across every resource type, so no two
    seeded entities share a number."""
    store = h.unit.context.store
    versions = [
        row[OBJECT_VERSION]
        for collection in (COL.retailer, COL.outlets, COL.registers, COL.payment_types)
        for row in store.collection(collection).all()
    ]
    assert versions == list(range(FIRST_VERSION, FIRST_VERSION + 8))


def test_two_units_hydrate_to_the_same_digest() -> None:
    """C06's claim, asserted here too, because a uuid4() or a time.time() in
    hydrate is the usual cause and this suite is where it would be introduced."""
    from tests.unit.lightspeed.harness import harness

    digests = []
    for _ in range(2):
        gen = harness()
        started = next(gen)
        digests.append(started.api.get("/__unit/state").json()["digest"])
        gen.close()
    assert digests[0] == digests[1]


def test_a_reset_reproduces_the_scenario(h: Harness) -> None:
    before = h.api.get("/__unit/state").json()["digest"]
    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/close"), "{}").status == 200
    assert h.api.get("/__unit/state").json()["digest"] != before
    assert h.api.post("/__unit/state/reset", {}).status in (200, 204)
    assert h.api.get("/__unit/state").json()["digest"] == before


# -- the schema refuses what it should ---------------------------------------


def test_an_inventory_row_naming_an_absent_product_is_refused() -> None:
    document = _document()
    document["inventory"] = [{"id": "i1", "product_id": "nope", "outlet_id": c.SEED_OUTLET_MAIN_ID}]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "product 'nope' is absent" in str(caught.value)


def test_a_customer_naming_an_absent_group_is_refused() -> None:
    document = _document()
    document["customers"] = [
        {"id": "c1", "first_name": "A", "last_name": "B", "customer_code": "A-1", "customer_group_id": "nope"}
    ]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "customer group 'nope' is absent" in str(caught.value)


def test_a_customer_document_key_CustomerBase_does_not_declare_is_refused() -> None:
    document = _document()
    document["customers"] = [
        {
            "id": "c1",
            "first_name": "A",
            "last_name": "B",
            "customer_code": "A-1",
            "document": {"physical_citty": "Auckland"},
        }
    ]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "physical_citty" in str(caught.value)


def test_a_stock_adjustment_on_an_undocumented_reason_is_refused() -> None:
    document = _document()
    document["stock_adjustments"] = [
        {
            "id": "s1",
            "product_id": c.SEED_PRODUCT_TRAIL_MIX_ID,
            "outlet_id": c.SEED_OUTLET_MAIN_ID,
            "quantity": "-1",
            "reason": "SHRINKAGE",
        }
    ]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "StockAdjustmentReason" in str(caught.value)


def test_a_variant_that_itself_has_variants_is_refused() -> None:
    """Families in this API are one level deep."""
    document = _document()
    products = document["products"]
    assert isinstance(products, list)
    products[4] = {**products[4], "has_variants": True}
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "one level deep" in str(caught.value)


def test_a_register_naming_an_absent_outlet_is_refused() -> None:
    document = _document()
    document["registers"] = [
        {"id": "r1", "name": "R", "outlet_id": "nope", "is_open": False},
    ]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "outlet 'nope' is absent" in str(caught.value)


def test_an_open_register_without_an_open_time_is_refused() -> None:
    document = _document()
    registers = document["registers"]
    assert isinstance(registers, list)
    registers[0] = {**registers[0], "register_open_time": None}
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "register_open_time" in str(caught.value)


def test_a_webhook_on_an_undocumented_event_is_refused() -> None:
    document = _document()
    document["webhooks"] = [{"id": "w1", "type": "sale.deleted", "url": "https://x.example/h"}]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "WebhookType" in str(caught.value)


def test_a_token_claiming_a_scope_the_application_lacks_is_refused() -> None:
    document = _document()
    # A documented scope this application never carries -- consignments are
    # outside issue #94's scoped surface. See test_auth.py's twin.
    document["personal_tokens"] = [{"id": "t1", "access_token": "x", "scopes": ["consignments:read"]}]
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "consignments:read" in str(caught.value)


def test_an_unknown_key_is_refused_by_name() -> None:
    document = _document()
    document["outlets_typo"] = []
    with pytest.raises(UnitError) as caught:
        parse_seed_document(document)
    assert "outlets_typo" in str(caught.value)
