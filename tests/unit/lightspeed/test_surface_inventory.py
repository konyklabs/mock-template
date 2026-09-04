"""Inventory: the four reads with their two shapes, and the batch that moves stock.

Two of the reads are POSTs whose query travels in the body, all four answer a
bare array, and the adjustment list is the one that answers the version
envelope.
"""

from __future__ import annotations

import json
from typing import Any

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.seed import constants as c

INVENTORY = "/inventory"
LEVELS = "/inventory_levels"
ADJUSTMENTS = "/stock_adjustments"

TRAIL_MIX_ROWS = 2
#: Five products hold stock at two outlets each. Three of the five are not
#: variants, and `variants` defaults to false, so an unfiltered read answers six.
STOCKED_ROWS = 10
NON_VARIANT_ROWS = 6


def _adjust(h: Harness, *items: dict[str, Any], **kwargs: Any) -> Any:
    return h.post(h.path(ADJUSTMENTS), json.dumps({"stock_adjustments": list(items)}), **kwargs)


def _damage(**overrides: Any) -> dict[str, Any]:
    return {
        "product_id": c.SEED_PRODUCT_TRAIL_MIX_ID,
        "outlet_id": c.SEED_OUTLET_MAIN_ID,
        "quantity": "-2",
        "reason": "DAMAGE",
        **overrides,
    }


def _level(h: Harness, product_id: str, outlet_id: str) -> Any:
    rows = h.get(h.path(f"{INVENTORY}/{product_id}")).json()
    return next(row for row in rows if row["outlet_id"] == outlet_id)


# -- the inventory records ---------------------------------------------------


def test_the_records_read_answers_a_bare_array_and_not_the_envelope(h: Harness) -> None:
    """All four inventory reads declare ``{"items": ..., "type": "array"}`` as
    their 200 body. The adjustment list is the one that declares the
    ``{data, version}`` envelope, and it is not applied here just because most
    of this API uses it."""
    body = h.post(h.path(INVENTORY), "{}").json()
    assert isinstance(body, list)
    assert len(body) == NON_VARIANT_ROWS


def test_a_record_carries_the_documented_members(h: Harness) -> None:
    row = _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)
    assert row["id"] == "1a000000-0000-1000-8000-000000000801"
    assert row["product_id"] == c.SEED_PRODUCT_TRAIL_MIX_ID
    assert row["current_inventory_level"] == 24
    assert row["reorder_method"] == "MIN_MAX"
    assert row["reorder_point"] == 10
    assert row["quantity_to_procure"] == 0


def test_an_unset_reorder_member_is_an_explicit_null_on_the_record(h: Harness) -> None:
    """Each is ``nullable`` on ``Inventory`` and the vendor's own example prints
    ``"reorder_amount": null``, so the null is data rather than absence."""
    row = _level(h, c.SEED_PRODUCT_SOCKS_ID, c.SEED_OUTLET_MAIN_ID)
    assert row["reorder_method"] is None
    assert row["reorder_point"] is None
    assert row["reorder_amount"] is None


def test_the_records_read_filters_by_product(h: Harness) -> None:
    body = h.post(h.path(INVENTORY), json.dumps({"product_id": c.SEED_PRODUCT_TRAIL_MIX_ID})).json()
    assert len(body) == TRAIL_MIX_ROWS
    assert {row["product_id"] for row in body} == {c.SEED_PRODUCT_TRAIL_MIX_ID}


def test_the_records_read_excludes_variants_unless_asked(h: Harness) -> None:
    without = h.post(h.path(INVENTORY), "{}").json()
    with_variants = h.post(h.path(INVENTORY), json.dumps({"variants": True})).json()
    assert len(without) == NON_VARIANT_ROWS
    assert len(with_variants) == STOCKED_ROWS


def test_size_and_sort_direction_page_the_records_read(h: Harness) -> None:
    """``size``, not ``page_size``, and in the BODY -- this operation's
    parameter names are its own."""
    ascending = h.post(h.path(INVENTORY), json.dumps({"variants": True, "size": 3})).json()
    descending = h.post(h.path(INVENTORY), json.dumps({"variants": True, "size": 3, "sort_direction": "desc"})).json()
    assert [row["id"] for row in ascending] != [row["id"] for row in descending]
    assert len(descending) == 3


def test_one_products_records_cover_every_outlet(h: Harness) -> None:
    rows = h.get(h.path(f"{INVENTORY}/{c.SEED_PRODUCT_TRAIL_MIX_ID}")).json()
    assert {row["outlet_id"] for row in rows} == {c.SEED_OUTLET_MAIN_ID, c.SEED_OUTLET_SECOND_ID}


def test_a_family_parent_holds_no_stock_until_variants_are_asked_for(h: Harness) -> None:
    alone = h.get(h.path(f"{INVENTORY}/{c.SEED_PRODUCT_TEE_ID}")).json()
    with_variants = h.get(h.path(f"{INVENTORY}/{c.SEED_PRODUCT_TEE_ID}"), query={"variants": "true"}).json()
    assert alone == []
    assert {row["product_id"] for row in with_variants} == {
        c.SEED_PRODUCT_TEE_SMALL_ID,
        c.SEED_PRODUCT_TEE_LARGE_ID,
    }


def test_an_unknown_product_is_a_404(h: Harness) -> None:
    """JUDGMENT: both reads declare only a 200, and an empty array is the same
    answer a real product with no stock gives. Those are different facts."""
    answered = h.get(h.path(f"{INVENTORY}/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "product_id"


def test_the_reads_need_their_documented_scope(h: Harness) -> None:
    answered = h.post(h.path(INVENTORY), "{}", headers=h.restricted_token("products:read"))
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["inventory:read"]


# -- the inventory-level report ----------------------------------------------


def test_the_level_report_is_a_different_shape_from_the_record(h: Harness) -> None:
    """``InventoryLevel`` has no ``id`` and no ``outlet_id``: it has a
    ``location_id``, the product's own ``name``, a ``root_product_id`` and a
    ``total_cost``, and calls the reorder point a ``reorder_threshold``."""
    rows = h.get(h.path(f"{LEVELS}/{c.SEED_PRODUCT_TRAIL_MIX_ID}")).json()
    row = next(item for item in rows if item["location_id"] == c.SEED_OUTLET_MAIN_ID)
    assert "id" not in row
    assert "outlet_id" not in row
    assert row["name"] == "Trail Mix 500g"
    assert row["reorder_threshold"] == 10
    assert row["root_product_id"] == c.SEED_PRODUCT_TRAIL_MIX_ID
    # average_cost 6 x current_inventory_level 24, which is the only reading
    # that makes the vendor's own 10 x 4 = 40 example consistent.
    assert row["total_cost"] == 144


def test_a_variant_reports_its_parent_as_the_root(h: Harness) -> None:
    row = h.get(h.path(f"{LEVELS}/{c.SEED_PRODUCT_TEE_SMALL_ID}")).json()[0]
    assert row["product_id"] == c.SEED_PRODUCT_TEE_SMALL_ID
    assert row["root_product_id"] == c.SEED_PRODUCT_TEE_ID


def test_an_inactive_product_is_absent_from_the_report_unless_asked_for(h: Harness) -> None:
    hidden = h.get(h.path(f"{LEVELS}/{c.SEED_PRODUCT_BOTTLE_ID}")).json()
    shown = h.get(h.path(f"{LEVELS}/{c.SEED_PRODUCT_BOTTLE_ID}"), query={"include_inactive": "true"}).json()
    assert hidden == []
    assert len(shown) == 2


def test_the_report_filters_and_pages_from_its_body(h: Harness) -> None:
    at_quay = h.post(h.path(LEVELS), json.dumps({"location_ids": [c.SEED_OUTLET_SECOND_ID]})).json()
    assert {row["location_id"] for row in at_quay} == {c.SEED_OUTLET_SECOND_ID}
    by_root = h.post(h.path(LEVELS), json.dumps({"root_product_ids": [c.SEED_PRODUCT_TEE_ID]})).json()
    assert {row["root_product_id"] for row in by_root} == {c.SEED_PRODUCT_TEE_ID}
    paged = h.post(h.path(LEVELS), json.dumps({"offset": 1, "size": 2})).json()
    assert len(paged) == 2


# -- the adjustment log ------------------------------------------------------


def test_the_adjustment_list_answers_the_version_envelope(h: Harness) -> None:
    body = h.get(h.path(ADJUSTMENTS)).json()
    assert set(body) == {"data", "version"}
    assert [row["id"] for row in body["data"]] == [
        c.SEED_STOCK_ADJUSTMENT_FIRST_ID,
        c.SEED_STOCK_ADJUSTMENT_SECOND_ID,
    ]


def test_an_adjustment_carries_the_documented_members(h: Harness) -> None:
    row = h.get(h.path(ADJUSTMENTS)).json()["data"][0]
    assert row["reason"] == "DAMAGE"
    # `quantity` is typed `string` on this schema, unlike every other quantity
    # on this surface.
    assert row["quantity"] == "-2"
    assert row["user_id"] == c.SEED_RETAILER_ID


def test_the_adjustment_list_is_gated_on_the_write_scope(h: Harness) -> None:
    """A READ gated on ``inventory:write``: the vendor's own annotation,
    reproduced rather than corrected. So the read-only token cannot see the
    log at all."""
    answered = h.get(h.path(ADJUSTMENTS), headers=h.read_auth)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["inventory:write"]


def test_the_read_only_token_can_still_read_the_records(h: Harness) -> None:
    assert h.post(h.path(INVENTORY), "{}", headers=h.read_auth).status == 200


# -- creating adjustments ----------------------------------------------------


def test_a_batch_answers_the_documented_201_and_moves_the_stock(h: Harness) -> None:
    before = _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)["current_inventory_level"]
    answered = _adjust(h, _damage())
    assert answered.status == 201
    # StockAdjustmentBatchResponse is `data` alone: no version envelope on the create.
    assert list(answered.json()) == ["data"]
    assert answered.json()["data"][0]["quantity"] == "-2"
    after = _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)["current_inventory_level"]
    assert after == before - 2


def test_a_batch_moves_several_rows_at_once(h: Harness) -> None:
    answered = _adjust(
        h,
        _damage(),
        {
            "product_id": c.SEED_PRODUCT_SOCKS_ID,
            "outlet_id": c.SEED_OUTLET_SECOND_ID,
            "quantity": "4",
            "reason": "STOCK_FOUND",
        },
    )
    assert answered.status == 201
    assert len(answered.json()["data"]) == 2
    assert _level(h, c.SEED_PRODUCT_SOCKS_ID, c.SEED_OUTLET_SECOND_ID)["current_inventory_level"] == 22


def test_an_adjustment_for_a_product_never_stocked_there_creates_the_row(h: Harness) -> None:
    assert (
        _adjust(
            h,
            {
                "product_id": c.SEED_PRODUCT_TEE_ID,
                "outlet_id": c.SEED_OUTLET_MAIN_ID,
                "quantity": "3",
                "reason": "STOCK_FOUND",
            },
        ).status
        == 201
    )
    assert _level(h, c.SEED_PRODUCT_TEE_ID, c.SEED_OUTLET_MAIN_ID)["current_inventory_level"] == 3


def test_a_negative_reason_requires_a_negative_quantity(h: Harness) -> None:
    """DOCUMENTED on ``StockAdjustmentReason``: "Negative reasons (require
    ``quantity`` < 0): DAMAGE, EXPIRY, INTERNAL_USE, THEFT, DONATION"."""
    answered = _adjust(h, _damage(quantity="2"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].quantity"


def test_a_positive_reason_requires_a_positive_quantity(h: Harness) -> None:
    answered = _adjust(h, _damage(reason="STOCK_FOUND"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].quantity"


def test_an_undocumented_reason_is_refused(h: Harness) -> None:
    answered = _adjust(h, _damage(reason="SHRINKAGE"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].reason"


def test_a_custom_reason_must_name_one_the_scenario_seeds(h: Harness) -> None:
    """The Custom Inventory Adjustment Reasons tag is deferred, so the two
    seeded reasons are the whole vocabulary."""
    good = _adjust(
        h,
        _damage(
            quantity="3",
            reason="CUSTOM",
            custom_inventory_adjustment_reason_id=c.SEED_ADJUSTMENT_REASON_FOUND_ID,
        ),
    )
    assert good.status == 201
    bad = _adjust(h, _damage(reason="CUSTOM", custom_inventory_adjustment_reason_id="nope"))
    assert bad.status == 422
    assert bad.json()["unit_error"]["field"] == "stock_adjustments[0].custom_inventory_adjustment_reason_id"


def test_a_custom_reasons_sign_must_match_its_type(h: Harness) -> None:
    """ "For CUSTOM, the sign must match the referenced custom reason's type"."""
    answered = _adjust(
        h,
        _damage(
            quantity="-1",
            reason="CUSTOM",
            custom_inventory_adjustment_reason_id=c.SEED_ADJUSTMENT_REASON_FOUND_ID,
        ),
    )
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].quantity"


def test_a_custom_reason_id_is_required_for_a_custom_adjustment(h: Harness) -> None:
    answered = _adjust(h, _damage(quantity="1", reason="CUSTOM"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].custom_inventory_adjustment_reason_id"


def test_a_custom_reason_id_on_a_built_in_reason_is_refused(h: Harness) -> None:
    """It would have no effect, and a caller who sent one meant something by
    it. JUDGMENT."""
    answered = _adjust(h, _damage(custom_inventory_adjustment_reason_id=c.SEED_ADJUSTMENT_REASON_SPOILED_ID))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[0].custom_inventory_adjustment_reason_id"


def test_an_unknown_product_or_outlet_in_a_batch_is_refused(h: Harness) -> None:
    assert _adjust(h, _damage(product_id="nope")).status == 422
    assert _adjust(h, _damage(outlet_id="nope")).status == 422


def test_a_batch_is_all_or_nothing(h: Harness) -> None:
    """JUDGMENT: the operation says nothing about partial failure and
    ``StockAdjustmentBatchResponse`` carries no per-item status, so committing
    four of five and then refusing would leave a caller unable to find out
    which four."""
    before = _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)["current_inventory_level"]
    answered = _adjust(h, _damage(), _damage(product_id="nope"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments[1].product_id"
    after = _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)["current_inventory_level"]
    assert after == before
    assert len(h.get(h.path(ADJUSTMENTS)).json()["data"]) == 2


def test_an_empty_batch_is_refused(h: Harness) -> None:
    """``minItems: 1`` on the array."""
    answered = _adjust(h)
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "stock_adjustments"


def test_a_malformed_body_is_a_400(h: Harness) -> None:
    assert h.post(h.path(ADJUSTMENTS), "{not json").status == 400


def test_the_batch_needs_the_write_scope(h: Harness) -> None:
    answered = _adjust(h, _damage(), headers=h.read_auth)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["inventory:write"]


# -- the events ---------------------------------------------------------------


def test_a_level_change_delivers_one_inventory_update_per_row(h: Harness) -> None:
    """Per ROW, never per adjustment: two adjustments against the same product
    and outlet move one row twice, and the journal has two entries for it."""
    assert (
        h.post(
            h.path("/webhooks"),
            json.dumps({"active": True, "type": "inventory.update", "url": "https://consumer.example/hooks/inventory"}),
        ).status
        == 201
    )
    assert (
        _adjust(
            h,
            _damage(),
            {
                "product_id": c.SEED_PRODUCT_SOCKS_ID,
                "outlet_id": c.SEED_OUTLET_MAIN_ID,
                "quantity": "1",
                "reason": "STOCK_FOUND",
            },
        ).status
        == 201
    )
    assert len(h.deliveries()) == 2
    records = h.api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [row["event_type"] for row in records] == ["inventory.update"] * 2


def test_the_delivered_payload_is_the_records_own_wire_shape(h: Harness) -> None:
    from urllib.parse import parse_qsl

    from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD
    from vendorfake.lightspeed.signer import verify_lightspeed_signature

    assert (
        h.post(
            h.path("/webhooks"),
            json.dumps({"active": True, "type": "inventory.update", "url": "https://consumer.example/hooks/inventory"}),
        ).status
        == 201
    )
    assert _adjust(h, _damage()).status == 201
    delivered = h.deliveries()[0]
    assert verify_lightspeed_signature(c.SEED_CLIENT_SECRET, delivered.body, delivered.headers["X-Signature"])
    payload = json.loads(dict(parse_qsl(delivered.body.decode("utf-8")))[PAYLOAD_FIELD])
    assert payload == _level(h, c.SEED_PRODUCT_TRAIL_MIX_ID, c.SEED_OUTLET_MAIN_ID)


def test_the_adjustment_log_itself_announces_nothing(h: Harness) -> None:
    """No value of the documented ``WebhookType`` enum names a stock
    adjustment, so only the inventory row it moved is announced."""
    for event_type in ("product.update", "customer.update", "sale.update"):
        assert (
            h.post(
                h.path("/webhooks"),
                json.dumps({"active": True, "type": event_type, "url": f"https://consumer.example/{event_type}"}),
            ).status
            == 201
        )
    assert _adjust(h, _damage()).status == 201
    assert h.deliveries() == []


def test_a_seeded_adjustment_announces_nothing(h: Harness) -> None:
    assert h.deliveries() == []
