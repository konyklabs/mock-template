"""Products: the version-cursor list and its overrides, and the three writes.

The five operations, the documented status codes, the two generated members,
inline variants, the soft delete, and the ``product.update`` each write fires.
"""

from __future__ import annotations

import json
from typing import Any

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.entities import COL
from vendorfake.lightspeed.model.product import IMAGE_PLACEHOLDER_THUMB, IMAGE_PLACEHOLDER_URL
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.surface.products import slugify

PRODUCTS = "/products"

SEEDED_IDS = [
    c.SEED_PRODUCT_TRAIL_MIX_ID,
    c.SEED_PRODUCT_SOCKS_ID,
    c.SEED_PRODUCT_BOTTLE_ID,
    c.SEED_PRODUCT_TEE_ID,
    c.SEED_PRODUCT_TEE_SMALL_ID,
    c.SEED_PRODUCT_TEE_LARGE_ID,
]


def _create(h: Harness, **body: Any) -> Any:
    return h.post(h.path(PRODUCTS), json.dumps({"name": "Ridgeline Cap", **body}))


def _ids(h: Harness, **query: str) -> list[str]:
    return [row["id"] for row in h.get(h.path(PRODUCTS), query=query).json()["data"]]


# -- the list ----------------------------------------------------------------


def test_the_list_answers_the_documented_envelope(h: Harness) -> None:
    body = h.get(h.path(PRODUCTS)).json()
    assert set(body) == {"data", "version"}
    assert [row["id"] for row in body["data"]] == SEEDED_IDS
    versions = [row["version"] for row in body["data"]]
    assert body["version"] == {"max": max(versions), "min": min(versions)}
    # Rows come back ascending by version, which is the whole of this API's ordering.
    assert versions == sorted(versions)


def test_a_product_carries_every_required_member(h: Harness) -> None:
    """``Product`` marks 21 members required; every one of them is present."""
    product = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_TRAIL_MIX_ID}")).json()["data"]
    required = {
        "active",
        "attributes",
        "categories",
        "customizations",
        "handle",
        "has_inventory",
        "has_variants",
        "id",
        "images",
        "is_composite",
        "name",
        "packaging",
        "price_excluding_tax",
        "price_including_tax",
        "product_codes",
        "product_suppliers",
        "sku",
        "skuImages",
        "tag_ids",
        "variant_options",
        "version",
    }
    assert required <= set(product)


def test_prices_are_json_numbers_and_not_strings(h: Harness) -> None:
    """The other half of this vendor's surface sends money as a STRING -- the
    register payments summary prints ``"total": "255.00"``. Here
    ``price_excluding_tax`` is ``type: number`` and the examples print ``110``
    and ``2.63158``, so a consumer that assumes one spelling is wrong."""
    product = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_TRAIL_MIX_ID}")).json()["data"]
    assert product["price_including_tax"] == 12.5
    assert product["price_excluding_tax"] == 10.86957
    # An integral amount prints as 110, never 110.0.
    assert isinstance(product["supply_price"], int), f"supply_price came back as {product['supply_price']!r}"


def test_the_walk_over_the_list_repeats_no_row_and_loses_none(h: Harness) -> None:
    """The documented forward sync: page with ``after=<the previous response's
    version.max>`` and stop when ``data`` comes back empty."""
    seen: list[str] = []
    after = 0
    for _ in range(len(SEEDED_IDS) + 2):
        body = h.get(h.path(PRODUCTS), query={"after": str(after), "page_size": "2"}).json()
        if not body["data"]:
            break
        seen.extend(row["id"] for row in body["data"])
        after = body["version"]["max"]
    assert seen == SEEDED_IDS
    assert len(seen) == len(set(seen))


def test_a_page_size_below_one_is_refused(h: Harness) -> None:
    answered = h.get(h.path(PRODUCTS), query={"page_size": "0"})
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "page_size"


def test_the_sku_filter_loads_one_product(h: Harness) -> None:
    assert _ids(h, sku=c.SEED_PRODUCT_TRAIL_MIX_SKU) == [c.SEED_PRODUCT_TRAIL_MIX_ID]
    assert _ids(h, sku="no-such-sku") == []


def test_the_name_filter_loads_a_whole_family(h: Harness) -> None:
    """ "This query typically retrieves all products from the product family
    with the provided name" -- so the parent AND both variants come back."""
    assert _ids(h, name="Ridgeline Tee") == [
        c.SEED_PRODUCT_TEE_ID,
        c.SEED_PRODUCT_TEE_SMALL_ID,
        c.SEED_PRODUCT_TEE_LARGE_ID,
    ]


def test_family_name_is_an_alias_for_name(h: Harness) -> None:
    assert _ids(h, family_name="Ridgeline Tee") == _ids(h, name="Ridgeline Tee")


def test_a_resource_filter_ignores_every_other_parameter(h: Harness) -> None:
    """ "Note that all other query params are ignored if this is provided" --
    including ``page_size``, and including one this unit would otherwise refuse
    as a 422."""
    answered = h.get(h.path(PRODUCTS), query={"sku": c.SEED_PRODUCT_TRAIL_MIX_SKU, "page_size": "not-a-number"})
    assert answered.status == 200
    assert [row["id"] for row in answered.json()["data"]] == [c.SEED_PRODUCT_TRAIL_MIX_ID]


def test_include_images_false_drops_the_four_image_members(h: Harness) -> None:
    with_images = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}")).json()["data"]
    assert with_images["image_url"] == IMAGE_PLACEHOLDER_URL
    assert with_images["image_thumbnail_url"] == IMAGE_PLACEHOLDER_THUMB
    without = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}"), query={"include_images": "false"}).json()["data"]
    assert not {"images", "skuImages", "image_url", "image_thumbnail_url"} & set(without)


# -- one product -------------------------------------------------------------


def test_one_product_answers_the_single_record_wrapper(h: Harness) -> None:
    body = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}")).json()
    assert set(body) == {"data"}
    assert body["data"]["id"] == c.SEED_PRODUCT_SOCKS_ID


def test_a_variant_names_its_parent_and_its_options(h: Harness) -> None:
    variant = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_TEE_SMALL_ID}")).json()["data"]
    parent = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_TEE_ID}")).json()["data"]
    assert variant["variant_parent_id"] == c.SEED_PRODUCT_TEE_ID
    assert variant["family_id"] == parent["family_id"]
    assert variant["variant_options"] == [{"name": "Size", "value": "Small"}]
    assert parent["has_variants"] is True
    assert parent["variant_count"] == 2


def test_an_unknown_product_is_a_404_naming_the_parameter(h: Harness) -> None:
    answered = h.get(h.path(f"{PRODUCTS}/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "product_id"


def test_reads_need_their_documented_scope(h: Harness) -> None:
    answered = h.get(h.path(PRODUCTS), headers=h.restricted_token("customers:read"))
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["products:read"]


# -- create ------------------------------------------------------------------


def test_a_create_answers_an_array_of_ids(h: Harness) -> None:
    """DOCUMENTED: "An array containing the ID or IDs of the new products", and
    a 200 rather than the 201 the customer create answers."""
    answered = _create(h, price_including_tax=29.5)
    assert answered.status == 200
    assert list(answered.json()) == ["data"]
    ids = answered.json()["data"]
    assert len(ids) == 1
    assert h.get(h.path(f"{PRODUCTS}/{ids[0]}")).json()["data"]["name"] == "Ridgeline Cap"


def test_the_handle_and_sku_default_from_the_name(h: Harness) -> None:
    """``Product`` requires both and ``ProductCreateBody`` requires neither."""
    created = _create(h).json()["data"][0]
    product = h.get(h.path(f"{PRODUCTS}/{created}")).json()["data"]
    assert product["handle"] == slugify("Ridgeline Cap") == "ridgeline-cap"
    assert product["sku"] == product["handle"]


def test_the_price_not_supplied_is_derived_at_the_units_tax_rate(h: Harness) -> None:
    created = _create(h, price_including_tax=115).json()["data"][0]
    product = h.get(h.path(f"{PRODUCTS}/{created}")).json()["data"]
    assert product["price_including_tax"] == 115
    assert product["price_excluding_tax"] == 100


def test_sending_both_prices_is_the_documented_422(h: Harness) -> None:
    """ "**Note**: You cannot include both ``price_including_tax`` and
    ``price_excluding_tax``" -- the create operation's own requestBody
    description, and the one documented 422 on this surface."""
    answered = _create(h, price_including_tax=10, price_excluding_tax=9)
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "price_including_tax"


def test_a_create_without_a_name_is_a_422_naming_it(h: Harness) -> None:
    answered = h.post(h.path(PRODUCTS), json.dumps({"sku": "NO-NAME"}))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "name"


def test_a_malformed_body_is_a_400_before_anything_else(h: Harness) -> None:
    answered = h.post(h.path(PRODUCTS), "{not json")
    assert answered.status == 400


def test_writes_need_their_documented_scope(h: Harness) -> None:
    answered = h.post(h.path(PRODUCTS), "{}", headers=h.read_auth)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["products:write"]


def test_inline_variants_create_one_child_product_each(h: Harness) -> None:
    """This is why the create answers an ARRAY: a body with two variants creates
    a parent and two children, in that order."""
    answered = _create(
        h,
        price_including_tax=29.5,
        variants=[
            {"sku": "CAP-BLK", "variant_definitions": [{"attribute_id": "colour", "value": "Black"}]},
            {"sku": "CAP-GRN", "variant_definitions": [{"attribute_id": "colour", "value": "Green"}]},
        ],
    )
    parent_id, *children = answered.json()["data"]
    parent = h.get(h.path(f"{PRODUCTS}/{parent_id}")).json()["data"]
    assert parent["has_variants"] is True
    assert parent["variant_count"] == 2
    # Stock lives on the variants, not on the family's parent.
    assert parent["has_inventory"] is False
    black = h.get(h.path(f"{PRODUCTS}/{children[0]}")).json()["data"]
    assert black["variant_parent_id"] == parent_id
    assert black["family_id"] == parent["family_id"]
    assert black["sku"] == "CAP-BLK"
    assert black["variant_name"] == "Ridgeline Cap / Black"
    # The Variant Attributes tag is deferred, so an attribute_id cannot be
    # resolved to a display name and travels verbatim as the option's name.
    assert black["variant_options"] == [{"name": "colour", "value": "Black"}]
    # A variant that names no price of its own inherits the family's.
    assert black["price_including_tax"] == 29.5


def test_a_variant_sku_defaults_to_the_parents_with_its_position(h: Harness) -> None:
    answered = _create(h, sku="CAP", variants=[{}, {}])
    _, first, second = answered.json()["data"]
    assert h.get(h.path(f"{PRODUCTS}/{first}")).json()["data"]["sku"] == "CAP-1"
    assert h.get(h.path(f"{PRODUCTS}/{second}")).json()["data"]["sku"] == "CAP-2"


def test_opening_stock_becomes_inventory_rows(h: Harness) -> None:
    created = _create(
        h,
        inventory=[{"outlet_id": c.SEED_OUTLET_MAIN_ID, "current_amount": 5, "reorder_point": 2, "reorder_amount": 10}],
    ).json()["data"][0]
    rows = h.get(h.path(f"/inventory/{created}")).json()
    assert [row["current_inventory_level"] for row in rows] == [5]
    assert rows[0]["outlet_id"] == c.SEED_OUTLET_MAIN_ID
    assert rows[0]["reorder_point"] == 2
    assert rows[0]["reorder_method"] == "FIXED"


def test_opening_stock_at_an_outlet_that_does_not_exist_is_a_422(h: Harness) -> None:
    """The row would be invisible to every read, since both inventory reads are
    scoped by outlet or by product."""
    answered = _create(h, inventory=[{"outlet_id": "nope", "current_amount": 5}])
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "inventory[0].outlet_id"
    # Nothing was created: the refusal happens before the first insert.
    assert [row["id"] for row in h.get(h.path(PRODUCTS)).json()["data"]] == SEEDED_IDS


def test_an_undocumented_product_code_type_is_refused(h: Harness) -> None:
    answered = _create(h, product_codes=[{"code": "123456", "type": "QR"}])
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "product_codes[0].type"


def test_an_attribute_arrives_in_either_documented_spelling(h: Harness) -> None:
    """``ProductCreateBody.attributes`` is a single ``{key, value}``;
    ``Product.attributes`` is an array of ``{name, value}``. Both shapes are
    accepted and the response uses the documented one."""
    from_object = _create(h, attributes={"key": "origin", "value": "New Zealand"}).json()["data"][0]
    from_array = _create(h, attributes=[{"name": "origin", "value": "New Zealand"}]).json()["data"][0]
    expected = [{"name": "origin", "value": "New Zealand"}]
    assert h.get(h.path(f"{PRODUCTS}/{from_object}")).json()["data"]["attributes"] == expected
    assert h.get(h.path(f"{PRODUCTS}/{from_array}")).json()["data"]["attributes"] == expected


# -- update ------------------------------------------------------------------


def test_the_update_takes_the_two_block_body(h: Harness) -> None:
    """``ProductUpdate21Request`` is ``{common, details}`` -- a different shape
    from the flat create body, and neither block is required."""
    answered = h.put(
        h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}"),
        json.dumps({"common": {"name": "Merino Socks (thick)"}, "details": {"price_excluding_tax": 30}}),
    )
    assert answered.status == 200
    product = answered.json()["data"]
    assert product["name"] == "Merino Socks (thick)"
    assert product["price_excluding_tax"] == 30
    assert product["price_including_tax"] == 34.5


def test_an_empty_update_body_is_legal_and_moves_only_the_version(h: Harness) -> None:
    before = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}")).json()["data"]
    after = h.put(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}"), "{}").json()["data"]
    assert after["version"] > before["version"]
    assert {k: v for k, v in after.items() if k not in {"version", "updated_at"}} == {
        k: v for k, v in before.items() if k not in {"version", "updated_at"}
    }


def test_track_inventory_is_the_update_bodys_name_for_has_inventory(h: Harness) -> None:
    answered = h.put(
        h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}"), json.dumps({"common": {"track_inventory": False}})
    )
    assert answered.json()["data"]["has_inventory"] is False


def test_updating_a_product_that_does_not_exist_is_a_404(h: Harness) -> None:
    assert h.put(h.path(f"{PRODUCTS}/nope"), "{}").status == 404


def test_a_malformed_update_body_is_a_400_whichever_product_it_named(h: Harness) -> None:
    assert h.put(h.path(f"{PRODUCTS}/nope"), "{not json").status == 400


# -- delete ------------------------------------------------------------------


def test_a_delete_is_soft_and_answers_an_empty_200(h: Harness) -> None:
    answered = h.delete(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_BOTTLE_ID}"))
    assert answered.status == 200
    assert answered.body == b""
    # Gone from the list, still readable by id, and carrying its tombstone.
    assert c.SEED_PRODUCT_BOTTLE_ID not in _ids(h)
    deleted = h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_BOTTLE_ID}")).json()["data"]
    assert deleted["deleted_at"].endswith("Z")


def test_deleted_true_puts_it_back_in_the_list(h: Harness) -> None:
    """Which is what "Indicates whether deleted items should be included in the
    response" means -- a hard delete would leave nothing to include.

    It comes back LAST, not in its old position: a delete is a mutation, it
    drew a new version, and the list is ascending by version. That is what lets
    a consumer's forward sync learn about the deletion at all.
    """
    assert h.delete(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_BOTTLE_ID}")).status == 200
    with_deleted = _ids(h, deleted="true")
    assert sorted(with_deleted) == sorted(SEEDED_IDS)
    assert with_deleted[-1] == c.SEED_PRODUCT_BOTTLE_ID


def test_deleting_a_variant_leaves_the_rest_of_the_family(h: Harness) -> None:
    """ "If a variant ID is provided, that single variant is removed" -- the
    family delete is ``DELETE /products/{id}/all``, which this unit does not
    serve."""
    assert h.delete(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_TEE_SMALL_ID}")).status == 200
    assert _ids(h, name="Ridgeline Tee") == [c.SEED_PRODUCT_TEE_ID, c.SEED_PRODUCT_TEE_LARGE_ID]


def test_deleting_a_product_that_does_not_exist_is_a_404(h: Harness) -> None:
    assert h.delete(h.path(f"{PRODUCTS}/nope")).status == 404


# -- the events ---------------------------------------------------------------


def _subscribe(h: Harness, event_type: str, url: str) -> None:
    created = h.post(h.path("/webhooks"), json.dumps({"active": True, "type": event_type, "url": url}))
    assert created.status == 201


def test_each_write_delivers_exactly_one_product_update(h: Harness) -> None:
    _subscribe(h, "product.update", "https://consumer.example/hooks/products")
    created = _create(h).json()["data"][0]
    assert h.put(h.path(f"{PRODUCTS}/{created}"), json.dumps({"common": {"name": "Cap v2"}})).status == 200
    assert h.delete(h.path(f"{PRODUCTS}/{created}")).status == 200
    # Settle first: the dispatcher hands off to a worker, so the delivery log
    # is written after the request has been answered.
    assert len(h.deliveries()) == 3
    records = h.api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [row["event_type"] for row in records] == ["product.update"] * 3


def test_the_delivered_payload_is_the_products_own_wire_shape(h: Harness) -> None:
    """The webhook and the REST route answer through ONE projection, so they
    cannot drift."""
    from urllib.parse import parse_qsl

    from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD
    from vendorfake.lightspeed.signer import verify_lightspeed_signature

    _subscribe(h, "product.update", "https://consumer.example/hooks/products")
    assert h.put(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}"), "{}").status == 200
    delivered = h.deliveries()[0]
    assert verify_lightspeed_signature(c.SEED_CLIENT_SECRET, delivered.body, delivered.headers["X-Signature"])
    payload = json.loads(dict(parse_qsl(delivered.body.decode("utf-8")))[PAYLOAD_FIELD])
    assert payload == h.get(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_SOCKS_ID}")).json()["data"]


def test_a_delete_still_carries_the_entity_because_the_delete_is_soft(h: Harness) -> None:
    from urllib.parse import parse_qsl

    from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD

    _subscribe(h, "product.update", "https://consumer.example/hooks/products")
    assert h.delete(h.path(f"{PRODUCTS}/{c.SEED_PRODUCT_BOTTLE_ID}")).status == 200
    payload = json.loads(dict(parse_qsl(h.deliveries()[0].body.decode("utf-8")))[PAYLOAD_FIELD])
    assert payload["id"] == c.SEED_PRODUCT_BOTTLE_ID
    assert "deleted_at" in payload


def test_a_seeded_product_announces_nothing(h: Harness) -> None:
    assert h.deliveries() == []
    assert h.unit.context.store.collection(COL.products).size == len(SEEDED_IDS)
