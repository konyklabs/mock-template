"""The Orders surface, against the shapes and rules Square documents.

Three rules this file follows:

* **every assertion names its source.** Where Square publishes the behaviour
  the docstring quotes it; where it does not, the test says the value is this
  unit's convention. A test that pins whatever the code happened to do is not
  evidence of fidelity.
* **absence is asserted as absence.** Several tests check ``"x" not in order``
  rather than ``order["x"] is None``, because the whole point of ``compact()``
  is that Square omits the key and a consumer branches on ``in``.
* **the sparse rules are tested in both directions.** A field the caller did
  not mention must survive, and a field the caller nulled must go. Testing only
  one of those passes for an implementation that ignores the patch entirely.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.square.seed.constants import (
    COLD_BREW_LARGE_VARIATION_ID,
    DEFAULT_SEED_PATH,
    SEED_COMPLETED_ORDER_ID,
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_MERCHANT_ID,
    SEED_OPEN_ORDER_ID,
    TEA_ITEM_ID,
    TEA_MUG_VARIATION_ID,
)
from vendorfake.square.surface.orders import MAX_BATCH_ORDER_IDS, MAX_LOCATION_IDS, SEARCH_MAX_LIMIT

CURSOR_TTL_MS = 5 * 60 * 1000
"""ITALIC: "A cursor has a 5-minute lifetime."
https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination
"""

#: Two order ids whose ICU collation and code-point ordering disagree: ICU puts
#: "a" before "B", Python's `sorted` puts "B" (U+0042) before "a" (U+0061).
#: Square order ids are mixed case, so this is reachable with ordinary data.
LOWER_ID = "CAISaAAAAAAAAAAAAAAAAAAAAAA"
UPPER_ID = "CAISBAAAAAAAAAAAAAAAAAAAAAA"
SAME_INSTANT = "2026-06-01T12:00:00.000Z"


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("orders-only")


@pytest.fixture
def virtual() -> Iterator[Harness]:
    """A unit on a virtual clock, so a cursor expiry is a call rather than a wait."""
    yield from build_harness("orders-only", env={"VENDORFAKE_CLOCK": "virtual"})


@pytest.fixture
def collated(tmp_path: Path) -> Iterator[Harness]:
    """The shipped scenario plus two same-instant orders with colliding ids.

    Built from ``default.seed.json`` rather than written from scratch, so the
    fixture cannot drift away from the document the rest of the suite asserts
    against.
    """
    document: dict[str, Any] = json.loads(DEFAULT_SEED_PATH.read_text(encoding="utf-8"))
    for order_id in (LOWER_ID, UPPER_ID):
        document["orders"].append(
            {
                "id": order_id,
                "location_id": SEED_LOCATION_ID,
                "state": "OPEN",
                "created_at": SAME_INSTANT,
                "updated_at": SAME_INSTANT,
                "version": 1,
                "line_items": [{"uid": f"li_{order_id}", "catalog_object_id": TEA_MUG_VARIATION_ID, "quantity": "1"}],
            }
        )
    path = tmp_path / "collated.seed.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    yield from build_harness("orders-only", env={"VENDORFAKE_SEED": str(path)})


def create(h: Harness, order: dict[str, Any], **body: Any) -> Any:
    return h.api.post("/v2/orders", {"order": order, **body}, headers=h.auth)


def retrieve(h: Harness, order_id: str) -> Any:
    return h.api.get(f"/v2/orders/{order_id}", headers=h.auth)


def update(h: Harness, order_id: str, order: dict[str, Any], **body: Any) -> Any:
    return h.api.put(f"/v2/orders/{order_id}", {"order": order, **body}, headers=h.auth)


def pay(h: Harness, order_id: str, **body: Any) -> Any:
    return h.api.post(f"/v2/orders/{order_id}/pay", body, headers=h.auth)


ALL_SEED_LOCATIONS = [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]
"""Every location the shipped scenario has.

`location_ids` is required on SearchOrders -- "Your request must include one or
more `location_ids`"
(https://developer.squareup.com/docs/orders-api/manage-orders/search-orders) --
so a search that is not about location filtering still has to say something.
Naming both locations is the "no location filter" of a required field.
"""


def search(h: Harness, **body: Any) -> Any:
    """`POST /v2/orders/search`, defaulting `location_ids` to every location.

    The default is here rather than in the surface: a test about sorting or
    paging must not have to restate the scenario's locations, and a test about
    the requirement itself passes its own (or none).
    """
    return h.api.post("/v2/orders/search", {"location_ids": ALL_SEED_LOCATIONS, **body}, headers=h.auth)


def journal_seq(h: Harness) -> int:
    return int(h.api.get("/__unit/journal").json()["seq"])


def digest(h: Harness) -> str:
    return str(h.api.get("/__unit/state").json()["digest"])


# ---------------------------------------------------------------------------
# CreateOrder
# ---------------------------------------------------------------------------


def test_create_defaults_to_open_at_version_one(h: Harness) -> None:
    """The CreateOrder success example shows `"state": "OPEN"` with
    `"version": 1`.
    https://developer.squareup.com/reference/square/orders-api/create-order
    """
    response = create(h, {"location_id": SEED_LOCATION_ID})
    assert response.status == 200
    order = response.json()["order"]
    assert order["state"] == "OPEN"
    assert order["version"] == 1
    assert order["location_id"] == SEED_LOCATION_ID


def test_a_created_order_omits_every_optional_it_has_no_value_for(h: Harness) -> None:
    """`compact()` in one assertion.

    Square's examples carry no key at all for an order with no reference id, no
    customer, no ticket, no source, no line items, no metadata, no tenders and
    no close. A consumer writing `if "closed_at" in order` takes the wrong
    branch on every open order the moment one of these becomes `null`.
    """
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    for absent in (
        "reference_id",
        "customer_id",
        "ticket_name",
        "source",
        "line_items",
        "metadata",
        "tenders",
        "closed_at",
    ):
        assert absent not in order, f"{absent} should be omitted, not null"
    assert order["total_money"] == {"amount": 0, "currency": "USD"}


def test_create_prices_a_line_item_from_the_catalog(h: Harness) -> None:
    """A line naming a catalog variation and no price resolves price, variation
    name and the parent item's name from the catalog -- the behaviour that
    makes seeded catalog data worth having."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "2", "catalog_object_id": TEA_MUG_VARIATION_ID}],
        },
    ).json()["order"]
    line = order["line_items"][0]
    assert line["base_price_money"] == {"amount": 150, "currency": "USD"}
    assert line["variation_name"] == "Mug"
    assert line["name"] == "Tea"
    assert line["total_money"] == {"amount": 300, "currency": "USD"}
    assert order["total_money"] == {"amount": 300, "currency": "USD"}
    assert order["net_amounts"]["total_money"] == {"amount": 300, "currency": "USD"}


def test_create_takes_the_currency_from_the_location_not_the_request(h: Harness) -> None:
    """An order cannot be denominated in something the seller does not take."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "1", "base_price_money": {"amount": 500, "currency": "GBP"}}],
        },
    ).json()["order"]
    assert order["total_money"]["currency"] == "USD"


def test_create_accepts_draft(h: Harness) -> None:
    """ "Draft orders can be updated, but cannot be paid or fulfilled."
    https://developer.squareup.com/reference/square/enums/OrderState
    """
    assert create(h, {"location_id": SEED_LOCATION_ID, "state": "DRAFT"}).json()["order"]["state"] == "DRAFT"


@pytest.mark.parametrize("state", ["COMPLETED", "CANCELED", "PENDING"])
def test_create_refuses_any_other_state(h: Harness, state: str) -> None:
    """This unit's reading, not a Square document: a terminal state describes an
    order that has already been somewhere, so it cannot be a starting state."""
    response = create(h, {"location_id": SEED_LOCATION_ID, "state": state})
    assert response.status == 400
    error = first_error(response)
    assert error["field"] == "order.state"
    assert response.json()["unit_error"]["allowed"] == ["OPEN", "DRAFT"]


def test_create_refuses_an_unknown_location_and_says_which_exist(h: Harness) -> None:
    response = create(h, {"location_id": "NOPE"})
    assert response.status == 400
    assert first_error(response)["field"] == "order.location_id"
    assert response.json()["unit_error"]["known"] == [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]


def test_create_requires_a_location(h: Harness) -> None:
    response = create(h, {})
    assert response.status == 400
    assert first_error(response)["field"] == "order.location_id"


def test_create_requires_a_quantity_on_every_line_item(h: Harness) -> None:
    response = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [{"name": "Mystery"}]})
    assert response.status == 400
    assert first_error(response)["field"] == "order.line_items[0].quantity"


def test_a_model_rejection_names_the_field_the_way_the_surface_does(h: Harness) -> None:
    """One logical field, one spelling of it in `errors[].field`.

    `{"quantity": 1}` is caught by the strict request model and
    `{"quantity": null}` by a hand-written check in the surface. They used to
    answer `order.line_items.0.quantity` and `order.line_items[0].quantity`
    respectively -- Pydantic's location path versus the surface's -- so a
    consumer keying on `field` could rely on neither. Square's `Error.field` is
    "The name of the field provided in the original request (if any) that the
    error pertains to" and publishes no array notation at all
    (https://developer.squareup.com/reference/square/objects/Error), so the
    brackets are this unit's convention, stated once in
    `vendorfake.square.model.common`.
    """
    from_model = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [{"quantity": 1}]})
    assert from_model.status == 400
    assert first_error(from_model)["field"] == "order.line_items[0].quantity"

    from_surface = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [{"name": "Mystery"}]})
    assert first_error(from_surface)["field"] == "order.line_items[0].quantity"


@pytest.mark.parametrize(
    ("field", "length"),
    [("uid", 61), ("name", 513), ("note", 2001), ("quantity", 13)],
)
def test_a_line_item_string_over_its_documented_maximum_is_refused(h: Harness, field: str, length: int) -> None:
    """ "uid ... Max Length 60", "name ... Max Length 512", "note ... Max Length
    2000", "quantity ... Max Length 12".
    https://developer.squareup.com/reference/square/objects/OrderLineItem

    None of the four were enforced: a 200-character uid and a 41-character
    quantity both came back 200, so a consumer generating ids longer than
    Square accepts learns nothing here and fails on the real API.
    """
    line: dict[str, Any] = {"quantity": "1", "base_price_money": {"amount": 100, "currency": "USD"}}
    line[field] = "9" * length if field == "quantity" else "x" * length
    response = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [line]})
    assert response.status == 400
    assert first_error(response)["field"] == f"order.line_items[0].{field}"


@pytest.mark.parametrize(
    ("field", "length"),
    [("uid", 60), ("name", 512), ("note", 2000), ("quantity", 12)],
)
def test_a_line_item_string_at_its_documented_maximum_is_accepted(h: Harness, field: str, length: int) -> None:
    """The other side of the boundary: "Max Length 60" means 60 is allowed."""
    line: dict[str, Any] = {"quantity": "1", "base_price_money": {"amount": 100, "currency": "USD"}}
    line[field] = "9" * length if field == "quantity" else "x" * length
    response = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [line]})
    assert response.status == 200, response.text


def test_create_requires_a_price_or_a_catalog_reference(h: Harness) -> None:
    response = create(h, {"location_id": SEED_LOCATION_ID, "line_items": [{"quantity": "1"}]})
    assert response.status == 400
    assert first_error(response)["field"] == "order.line_items[0].base_price_money"


def test_create_refuses_a_catalog_id_that_is_not_a_variation(h: Harness) -> None:
    """An ITEM has no price; only an ITEM_VARIATION does."""
    response = create(
        h,
        {"location_id": SEED_LOCATION_ID, "line_items": [{"quantity": "1", "catalog_object_id": TEA_ITEM_ID}]},
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order.line_items[0].catalog_object_id"


def test_quantity_is_a_string_so_junk_in_it_is_a_200_and_not_a_500(h: Harness) -> None:
    """`quantity` is a string on Square's wire, so a consumer may legitimately
    send one that is not a number.

    `Number.parseFloat` consumes the longest numeric *prefix*, so `"2 pieces"`
    is two -- and only a quantity with no numeric prefix at all is worth
    nothing. Both must answer 200; a Python `float()` would raise on either and
    turn expected traffic into an internal error.
    """
    prefixed = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "2 pieces", "base_price_money": {"amount": 150, "currency": "USD"}}],
        },
    )
    assert prefixed.status == 200
    assert prefixed.json()["order"]["total_money"] == {"amount": 300, "currency": "USD"}

    junk = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "pieces", "base_price_money": {"amount": 150, "currency": "USD"}}],
        },
    )
    assert junk.status == 200
    assert junk.json()["order"]["total_money"] == {"amount": 0, "currency": "USD"}


def test_a_numeric_quantity_is_a_type_error_rather_than_a_silent_coercion(h: Harness) -> None:
    """Strict validation, standing in for the reference's `typeof` gates."""
    response = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": 2, "base_price_money": {"amount": 150, "currency": "USD"}}],
        },
    )
    assert response.status == 400


def test_create_is_idempotent_on_idempotency_key(h: Harness) -> None:
    body = {"order": {"location_id": SEED_LOCATION_ID}, "idempotency_key": "same-key"}
    first = h.api.post("/v2/orders", body, headers=h.auth)
    second = h.api.post("/v2/orders", body, headers=h.auth)
    assert second.status == 200
    assert second.header("x-unit-idempotent-replay") == "true"
    assert second.json()["order"]["id"] == first.json()["order"]["id"]


def test_create_refuses_a_reused_key_with_a_different_body(h: Harness) -> None:
    """ "IDEMPOTENCY_KEY_REUSED" is a documented 400.
    https://developer.squareup.com/docs/build-basics/handling-errors
    """
    h.api.post("/v2/orders", {"order": {"location_id": SEED_LOCATION_ID}, "idempotency_key": "k"}, headers=h.auth)
    response = h.api.post(
        "/v2/orders",
        {"order": {"location_id": SEED_KIOSK_LOCATION_ID}, "idempotency_key": "k"},
        headers=h.auth,
    )
    assert response.status == 400
    assert first_error(response)["code"] == "IDEMPOTENCY_KEY_REUSED"


# ---------------------------------------------------------------------------
# RetrieveOrder
# ---------------------------------------------------------------------------


def test_retrieve_reflects_every_committed_mutation(h: Harness) -> None:
    created = create(h, {"location_id": SEED_LOCATION_ID, "reference_id": "ticket-9"}).json()["order"]
    fetched = retrieve(h, created["id"]).json()["order"]
    assert fetched == created


def test_retrieve_of_an_unknown_order_is_a_404_naming_the_parameter(h: Harness) -> None:
    response = retrieve(h, "CAISnope")
    assert response.status == 404
    error = first_error(response)
    assert error["code"] == "NOT_FOUND"
    assert error["field"] == "order_id"
    assert error["detail"] == "Order CAISnope was not found."


def test_a_shaped_error_still_carries_the_square_version_header(h: Harness) -> None:
    """ "Regardless of whether you explicitly specify a version in the request,
    the response always returns the Square-Version header."
    https://developer.squareup.com/docs/build-basics/versioning-overview
    """
    response = retrieve(h, "CAISnope")
    assert response.status == 404
    assert response.header("square-version")


# ---------------------------------------------------------------------------
# UpdateOrder -- version and concurrency
# ---------------------------------------------------------------------------


def test_update_requires_the_order_version(h: Harness) -> None:
    """ "Your request must include the order.version property set to the
    current version of the order."
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    response = update(h, SEED_OPEN_ORDER_ID, {"reference_id": "x"})
    assert response.status == 400
    error = first_error(response)
    assert error["field"] == "order.version"
    assert error["detail"] == (
        "Your request must include the order.version property set to the current version of the order."
    )


def test_a_stale_version_is_a_version_mismatch(h: Harness) -> None:
    """ "version must be set to the current version of the order or your
    request returns an error." The code is `VERSION_MISMATCH`, which Square
    names in prose on the optimistic-concurrency page but does not list in the
    published ErrorCode enum -- the status is this unit's convention.
    https://developer.squareup.com/docs/working-with-apis/optimistic-concurrency
    """
    response = update(h, SEED_OPEN_ORDER_ID, {"version": 99, "reference_id": "x"})
    assert response.status == 400
    assert first_error(response)["code"] == "VERSION_MISMATCH"


def test_a_string_version_is_refused_rather_than_coerced(h: Harness) -> None:
    """The reference guards this with `typeof version !== 'number'`; strict
    validation is what carries the gate across."""
    assert update(h, SEED_OPEN_ORDER_ID, {"version": "1"}).status == 400


def test_a_rejected_update_leaves_no_journal_entry_and_no_state_change(h: Harness) -> None:
    """The journal is the event source, so an entry for a rejected mutation
    would become a webhook for a change that never happened."""
    before_seq, before_digest = journal_seq(h), digest(h)
    assert update(h, SEED_OPEN_ORDER_ID, {"version": 99, "reference_id": "x"}).status == 400
    assert journal_seq(h) == before_seq
    assert digest(h) == before_digest


def test_the_version_increments_even_when_nothing_changes(h: Harness) -> None:
    """ "On a 200 response, Square has incremented the order version, even if
    all requested property changes are ignored and no changes are actually
    made." The behaviour every naive rebuild drops.
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    after = update(h, SEED_OPEN_ORDER_ID, {"version": before}).json()["order"]
    assert after["version"] == before + 1


def test_an_unapplicable_clear_is_ignored_and_the_version_still_increments(h: Harness) -> None:
    """Same sentence: Square silently ignores clears it cannot apply."""
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    response = update(h, SEED_OPEN_ORDER_ID, {"version": before}, fields_to_clear=["total_money", "nonsense"])
    assert response.status == 200
    assert response.json()["order"]["version"] == before + 1


# ---------------------------------------------------------------------------
# UpdateOrder -- sparse semantics
# ---------------------------------------------------------------------------


def test_an_unmentioned_field_survives_the_update(h: Harness) -> None:
    """The half a `None` test silently deletes.

    "your request should only include the properties that you want to add,
    update, or clear", so everything else must come back unchanged.
    """
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    after = update(h, SEED_OPEN_ORDER_ID, {"version": before["version"], "customer_id": "CUST-1"}).json()["order"]
    assert after["customer_id"] == "CUST-1"
    assert after["reference_id"] == before["reference_id"]
    assert after["ticket_name"] == before["ticket_name"]
    assert after["source"] == before["source"]
    assert after["line_items"] == before["line_items"]


def test_a_null_clears_an_optional_field(h: Harness) -> None:
    """JUDGMENT: Square documents `fields_to_clear` and says nothing about
    null. This unit accepts both, and the cleared key is *absent*, not null."""
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    after = update(h, SEED_OPEN_ORDER_ID, {"version": before["version"], "ticket_name": None}).json()["order"]
    assert "ticket_name" not in after


def test_fields_to_clear_removes_order_level_fields(h: Harness) -> None:
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    after = update(h, SEED_OPEN_ORDER_ID, {"version": before["version"]}, fields_to_clear=["reference_id"]).json()[
        "order"
    ]
    assert "reference_id" not in after


def test_a_null_state_is_refused_because_an_order_must_have_one(h: Harness) -> None:
    """The other half of the null rule: what an order cannot be without cannot
    be cleared, and saying so beats ignoring it."""
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    response = update(h, SEED_OPEN_ORDER_ID, {"version": version, "state": None})
    assert response.status == 400
    assert first_error(response)["field"] == "order.state"


def test_a_line_item_patch_updates_in_place_and_preserves_unmentioned_fields(h: Harness) -> None:
    before = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    after = update(
        h,
        SEED_OPEN_ORDER_ID,
        {"version": before["version"], "line_items": [{"uid": "seed_li_tea_mug", "note": "no ice"}]},
    ).json()["order"]
    line = after["line_items"][0]
    prior = before["line_items"][0]
    assert line["uid"] == "seed_li_tea_mug"
    assert line["note"] == "no ice"
    # Every field the patch did not mention, required and optional alike. The
    # optionals are the ones a `None` test silently wipes, because an absent
    # `name` and an explicit `"name": null` parse to the same value.
    for kept in ("quantity", "base_price_money", "name", "variation_name", "catalog_object_id"):
        assert line[kept] == prior[kept], kept
    # In place means position too: a receipt reads top to bottom.
    assert [item["uid"] for item in after["line_items"]] == [item["uid"] for item in before["line_items"]]


def test_a_null_note_clears_the_note(h: Harness) -> None:
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    noted = update(
        h,
        SEED_OPEN_ORDER_ID,
        {"version": version, "line_items": [{"uid": "seed_li_tea_mug", "note": "no ice"}]},
    ).json()["order"]
    assert noted["line_items"][0]["note"] == "no ice"
    cleared = update(
        h,
        SEED_OPEN_ORDER_ID,
        {"version": noted["version"], "line_items": [{"uid": "seed_li_tea_mug", "note": None}]},
    ).json()["order"]
    assert "note" not in cleared["line_items"][0]


def test_a_null_quantity_is_refused(h: Harness) -> None:
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    response = update(
        h, SEED_OPEN_ORDER_ID, {"version": version, "line_items": [{"uid": "seed_li_tea_mug", "quantity": None}]}
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order.line_items[0].quantity"


def test_an_unknown_uid_appends_and_needs_a_quantity_and_a_price(h: Harness) -> None:
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    refused = update(h, SEED_OPEN_ORDER_ID, {"version": version, "line_items": [{"uid": "brand-new", "note": "hi"}]})
    assert refused.status == 400
    assert first_error(refused)["field"] == "order.line_items"

    added = update(
        h,
        SEED_OPEN_ORDER_ID,
        {
            "version": version,
            "line_items": [{"uid": "brand-new", "quantity": "1", "catalog_object_id": COLD_BREW_LARGE_VARIATION_ID}],
        },
    ).json()["order"]
    assert [item["uid"] for item in added["line_items"]][-1] == "brand-new"
    assert added["line_items"][-1]["base_price_money"] == {"amount": 525, "currency": "USD"}


def test_fields_to_clear_removes_one_line_item_and_one_of_its_fields(h: Harness) -> None:
    """Square's bracket notation, e.g. `line_items[coffee_uid]` and
    `line_items[coffee_uid].applied_discounts[discount_uid]`.
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    noted = update(
        h,
        SEED_OPEN_ORDER_ID,
        {"version": version, "line_items": [{"uid": "seed_li_tea_mug", "note": "no ice"}]},
    ).json()["order"]

    sub = update(
        h, SEED_OPEN_ORDER_ID, {"version": noted["version"]}, fields_to_clear=["line_items[seed_li_tea_mug].note"]
    ).json()["order"]
    assert "note" not in sub["line_items"][0]

    whole = update(
        h, SEED_OPEN_ORDER_ID, {"version": sub["version"]}, fields_to_clear=["line_items[seed_li_tea_mug]"]
    ).json()["order"]
    assert [item["uid"] for item in whole["line_items"]] == ["seed_li_coldbrew_lg"]

    emptied = update(h, SEED_OPEN_ORDER_ID, {"version": whole["version"]}, fields_to_clear=["line_items"]).json()[
        "order"
    ]
    assert "line_items" not in emptied
    assert emptied["total_money"] == {"amount": 0, "currency": "USD"}


def test_update_replays_a_reused_key_and_says_it_ignored_the_body(h: Harness) -> None:
    """ "If you don't provide a new idempotency_key with each update request,
    you get a 200 response but the returned order doesn't reflect any of your
    updates." A 200 that silently discarded the change is unobservable without
    the sidecar header.
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    version = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["version"]
    first = update(h, SEED_OPEN_ORDER_ID, {"version": version, "ticket_name": "A"}, idempotency_key="reused")
    assert first.status == 200
    second = update(
        h,
        SEED_OPEN_ORDER_ID,
        {"version": first.json()["order"]["version"], "ticket_name": "B"},
        idempotency_key="reused",
    )
    assert second.status == 200
    assert second.header("x-unit-idempotent-replay") == "true"
    assert second.header("x-unit-idempotent-ignored-body") == "true"
    assert retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]["ticket_name"] == "A"


# ---------------------------------------------------------------------------
# UpdateOrder -- the lifecycle
# ---------------------------------------------------------------------------


def test_a_draft_order_can_be_opened(h: Harness) -> None:
    draft = create(h, {"location_id": SEED_LOCATION_ID, "state": "DRAFT"}).json()["order"]
    opened = update(h, draft["id"], {"version": draft["version"], "state": "OPEN"}).json()["order"]
    assert opened["state"] == "OPEN"
    assert "closed_at" not in opened


def test_cancelling_stamps_closed_at(h: Harness) -> None:
    """ "The timestamp for when the order reached a terminal state, in RFC 3339
    format." https://developer.squareup.com/reference/square/objects/Order
    """
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    canceled = update(h, order["id"], {"version": order["version"], "state": "CANCELED"}).json()["order"]
    assert canceled["state"] == "CANCELED"
    assert canceled["closed_at"].endswith("Z")


def test_a_terminal_order_cannot_be_updated_at_all(h: Harness) -> None:
    """ "Orders with a COMPLETED or CANCELED state cannot be updated."
    Not just the state: `assert_mutable` refuses any mutation, which is why an
    update that mentions no state at all is still refused.
    https://developer.squareup.com/reference/square/orders-api/update-order
    """
    version = retrieve(h, SEED_COMPLETED_ORDER_ID).json()["order"]["version"]
    response = update(h, SEED_COMPLETED_ORDER_ID, {"version": version, "ticket_name": "too late"})
    assert response.status == 400
    assert "terminal" in first_error(response)["detail"]
    assert response.json()["unit_error"]["kind"] == "invalid_transition"


def test_re_declaring_open_on_an_open_order_is_a_legal_no_op(h: Harness) -> None:
    """The documented way to use UpdateOrder is to send back the order you read
    with the fields you want changed, including its state -- so `OPEN -> OPEN`
    has to work. `OPEN` and `DRAFT` declare `allow_self`; the terminal pair
    cannot, which is what closes the double-pay path."""
    order = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    response = update(h, SEED_OPEN_ORDER_ID, {"version": order["version"], "state": "OPEN"})
    assert response.status == 200
    assert response.json()["order"]["state"] == "OPEN"
    assert "closed_at" not in response.json()["order"]


def test_an_undeclared_state_value_is_a_typo_not_a_sequencing_error(h: Harness) -> None:
    order = retrieve(h, SEED_OPEN_ORDER_ID).json()["order"]
    response = update(h, SEED_OPEN_ORDER_ID, {"version": order["version"], "state": "PAID"})
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_value"
    assert response.json()["unit_error"]["allowed"] == ["DRAFT", "OPEN", "COMPLETED", "CANCELED"]


# ---------------------------------------------------------------------------
# PayOrder
# ---------------------------------------------------------------------------


def test_pay_moves_an_open_order_to_completed_and_closes_it(h: Harness) -> None:
    """PayOrder "pays for an order using one or more approved payments" and the
    response example shows a COMPLETED order.
    https://developer.squareup.com/reference/square/orders-api/pay-order
    """
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "2", "catalog_object_id": TEA_MUG_VARIATION_ID}],
        },
    ).json()["order"]
    paid = pay(h, order["id"], idempotency_key="pay-1").json()["order"]
    assert paid["state"] == "COMPLETED"
    assert paid["closed_at"].endswith("Z")
    assert paid["version"] == order["version"] + 1
    assert paid["net_amount_due_money"] == {"amount": 0, "currency": "USD"}
    tender = paid["tenders"][0]
    assert tender["amount_money"] == {"amount": 300, "currency": "USD"}
    assert tender["transaction_id"] == order["id"]
    assert tender["payment_id"] == "unit-payment"


def test_paying_removes_the_line_items_whose_quantity_is_zero(h: Harness) -> None:
    """ "Line items with a quantity of `0` are automatically removed when
    paying for or otherwise completing the order."
    https://developer.squareup.com/reference/square/objects/OrderLineItem

    Documented, and neither the reference nor this file did it: a zeroed line
    survived PayOrder, which is exactly what a cart UI sends when a customer
    sets an item to none.
    """
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [
                {"uid": "keep", "quantity": "1", "base_price_money": {"amount": 500, "currency": "USD"}},
                {"uid": "zero", "quantity": "0", "base_price_money": {"amount": 500, "currency": "USD"}},
            ],
        },
    ).json()["order"]
    assert [item["uid"] for item in order["line_items"]] == ["keep", "zero"]

    paid = pay(h, order["id"], idempotency_key="pay-zero").json()["order"]
    assert [item["uid"] for item in paid["line_items"]] == ["keep"]
    # The total was never the zeroed line's to change: round(500 * 0) is 0.
    assert paid["total_money"] == order["total_money"]


def test_completing_through_update_removes_them_too(h: Harness) -> None:
    """ "or otherwise completing the order" -- the UpdateOrder transition into
    COMPLETED is the other completion path."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [
                {"uid": "keep", "quantity": "2", "base_price_money": {"amount": 100, "currency": "USD"}},
                {"uid": "zero", "quantity": "0.00", "base_price_money": {"amount": 100, "currency": "USD"}},
            ],
        },
    ).json()["order"]
    updated = update(h, order["id"], {"version": order["version"], "state": "COMPLETED"}).json()["order"]
    assert updated["state"] == "COMPLETED"
    assert [item["uid"] for item in updated["line_items"]] == ["keep"]


def test_cancelling_removes_nothing_because_it_is_not_completing(h: Harness) -> None:
    """The sentence says *completing*. A canceled order's lines are the record
    of what was not sold, so they stay -- and this unit says so rather than
    leaving the reader to infer it from the absence of code."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"uid": "zero", "quantity": "0", "base_price_money": {"amount": 100, "currency": "USD"}}],
        },
    ).json()["order"]
    canceled = update(h, order["id"], {"version": order["version"], "state": "CANCELED"}).json()["order"]
    assert canceled["state"] == "CANCELED"
    assert [item["uid"] for item in canceled["line_items"]] == ["zero"]


def test_a_quantity_that_is_not_a_number_is_not_swept_up_as_a_zero(h: Harness) -> None:
    """`"pieces"` has a line total of 0 -- `parseFloat` finds no number -- but
    it is not a quantity *of* zero, and the documented rule is about the
    quantity, not about the total."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [
                {"uid": "junk", "quantity": "pieces", "base_price_money": {"amount": 100, "currency": "USD"}}
            ],
        },
    ).json()["order"]
    paid = pay(h, order["id"], idempotency_key="pay-junk").json()["order"]
    assert [item["uid"] for item in paid["line_items"]] == ["junk"]


def test_paying_a_completed_order_a_second_time_is_refused(h: Harness) -> None:
    """The reference answered 200 here, replaced the tenders and bumped the
    version -- a second payment against a closed order, because its
    `assertTransition` returned early on `from === to`. The core's machine
    forbids a self-transition unless the state declares `allow_self`, and a
    terminal state cannot.
    """
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    first = pay(h, order["id"], idempotency_key="pay-1")
    assert first.status == 200

    second = pay(h, order["id"], idempotency_key="pay-2")
    assert second.status == 400
    assert second.json()["unit_error"]["kind"] == "invalid_transition"
    assert second.json()["unit_error"]["terminal"] is True
    assert retrieve(h, order["id"]).json()["order"]["version"] == first.json()["order"]["version"]


def test_paying_a_draft_order_is_refused_with_the_documented_reason(h: Harness) -> None:
    """ "Draft orders can be updated, but cannot be paid or fulfilled."
    https://developer.squareup.com/reference/square/enums/OrderState
    """
    order = create(h, {"location_id": SEED_LOCATION_ID, "state": "DRAFT"}).json()["order"]
    response = pay(h, order["id"], idempotency_key="pay-draft")
    assert response.status == 400
    assert "DRAFT" in first_error(response)["detail"]


def test_paying_a_canceled_order_is_refused(h: Harness) -> None:
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    update(h, order["id"], {"version": order["version"], "state": "CANCELED"})
    assert pay(h, order["id"], idempotency_key="pay-canceled").status == 400


def test_pay_requires_an_idempotency_key(h: Harness) -> None:
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    response = h.api.post(f"/v2/orders/{order['id']}/pay", {}, headers=h.auth)
    assert response.status == 400
    assert first_error(response)["field"] == "idempotency_key"


def test_pay_honours_order_version_as_optimistic_concurrency(h: Harness) -> None:
    """ "The version of the order being paid. If not supplied, the latest
    version will be paid."
    https://developer.squareup.com/reference/square/orders-api/pay-order
    """
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    stale = pay(h, order["id"], idempotency_key="pay-stale", order_version=99)
    assert stale.status == 400
    assert first_error(stale)["code"] == "VERSION_MISMATCH"
    assert pay(h, order["id"], idempotency_key="pay-ok", order_version=order["version"]).status == 200


def test_pay_spreads_the_total_across_the_supplied_payment_ids(h: Harness) -> None:
    """SHRINK: there is no Payments API here, so the payment ids are opaque and
    the first tender carries the whole total. Square requires the payments to
    sum to the order total, which is trivially true when the total is all there
    is to divide."""
    order = create(
        h,
        {
            "location_id": SEED_LOCATION_ID,
            "line_items": [{"quantity": "1", "catalog_object_id": TEA_MUG_VARIATION_ID}],
        },
    ).json()["order"]
    paid = pay(h, order["id"], idempotency_key="pay-many", payment_ids=["pay_A", "pay_B"]).json()["order"]
    assert [tender["payment_id"] for tender in paid["tenders"]] == ["pay_A", "pay_B"]
    assert [tender["amount_money"]["amount"] for tender in paid["tenders"]] == [150, 0]


def test_a_rejected_payment_draws_no_tender_ids(h: Harness) -> None:
    """Ids are minted inside the mutator, which `Collection.update` reaches only
    after the version check. Two runs of one scenario must number their tenders
    the same whether or not a stale request was tried in between."""
    order = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    assert pay(h, order["id"], idempotency_key="rejected", order_version=99).status == 400
    paid = pay(h, order["id"], idempotency_key="accepted").json()["order"]

    for other in build_harness("orders-only"):
        twin = create(other, {"location_id": SEED_LOCATION_ID}).json()["order"]
        twin_paid = pay(other, twin["id"], idempotency_key="accepted").json()["order"]
        assert twin_paid["tenders"][0]["id"] == paid["tenders"][0]["id"]


# ---------------------------------------------------------------------------
# SearchOrders
# ---------------------------------------------------------------------------


def test_search_returns_orders_newest_first_by_default(h: Harness) -> None:
    """ "sort_field ... Default: CREATED_AT" and "sort_order ... Default: DESC".
    https://developer.squareup.com/reference/square/orders-api/search-orders
    """
    ids = [order["id"] for order in search(h).json()["orders"]]
    assert ids == [SEED_OPEN_ORDER_ID, SEED_COMPLETED_ORDER_ID]


def test_search_returns_order_entries_when_asked(h: Harness) -> None:
    """ "If set to true, returns the OrderEntry objects instead of Order
    objects." An OrderEntry is exactly three keys.
    https://developer.squareup.com/reference/square/objects/OrderEntry
    """
    body = search(h, return_entries=True).json()
    assert "orders" not in body
    assert body["order_entries"] == [
        {"order_id": SEED_OPEN_ORDER_ID, "version": 1, "location_id": SEED_LOCATION_ID},
        {"order_id": SEED_COMPLETED_ORDER_ID, "version": 3, "location_id": SEED_KIOSK_LOCATION_ID},
    ]


def test_return_entries_must_be_a_real_boolean(h: Harness) -> None:
    """The reference tests `body.return_entries === true`, so the string
    `"true"` read as false there and a consumer asking for entries silently got
    whole orders. Strict validation says so instead."""
    assert search(h, return_entries="true").status == 400


def test_search_filters_by_location(h: Harness) -> None:
    ids = [order["id"] for order in search(h, location_ids=[SEED_KIOSK_LOCATION_ID]).json()["orders"]]
    assert ids == [SEED_COMPLETED_ORDER_ID]


def test_search_requires_location_ids(h: Harness) -> None:
    """ "Your request must include one or more `location_ids`. `SearchOrders`
    only returns the orders for those locations."
    https://developer.squareup.com/docs/orders-api/manage-orders/search-orders

    The reference typed the field optional and answered 200 with every
    location's orders, which is the one shape Square will not answer: a
    consumer whose query is missing the field builds a page of results here and
    gets an error in production. The 400 is this unit's convention -- Square
    publishes no error code for the omission -- but the refusal is not.
    """
    omitted = h.api.post("/v2/orders/search", {}, headers=h.auth)
    assert omitted.status == 400
    assert first_error(omitted)["field"] == "location_ids"
    assert omitted.json()["unit_error"]["kind"] == "missing_field"

    # An empty list is the same failure: it names no location either.
    empty = h.api.post("/v2/orders/search", {"location_ids": []}, headers=h.auth)
    assert empty.status == 400
    assert first_error(empty)["field"] == "location_ids"


def test_search_caps_location_ids_at_ten(h: Harness) -> None:
    """ "location_ids ... Max: 10"
    https://developer.squareup.com/reference/square/orders-api/search-orders
    """
    response = search(h, location_ids=[f"L{n}" for n in range(MAX_LOCATION_IDS + 1)])
    assert response.status == 400
    assert first_error(response)["field"] == "location_ids"
    assert first_error(response)["detail"] == "Max: 10 location IDs."


def test_search_filters_by_state(h: Harness) -> None:
    ids = [
        order["id"]
        for order in search(h, query={"filter": {"state_filter": {"states": ["COMPLETED"]}}}).json()["orders"]
    ]
    assert ids == [SEED_COMPLETED_ORDER_ID]


def test_a_date_time_filter_must_match_the_sort_field(h: Harness) -> None:
    """ "If you use the DateTimeFilter in a SearchOrders query, you must set the
    sort_field in OrdersSort to the same field you filter for."
    https://developer.squareup.com/reference/square/objects/SearchOrdersDateTimeFilter
    """
    response = search(
        h,
        query={
            "filter": {"date_time_filter": {"closed_at": {"start_at": "2026-01-01T00:00:00Z"}}},
            "sort": {"sort_field": "CREATED_AT"},
        },
    )
    assert response.status == 400
    assert first_error(response)["field"] == "query.sort.sort_field"


def test_a_date_range_is_start_inclusive_and_end_exclusive(h: Harness) -> None:
    """The seeded open order was created at 2026-08-01T10:15:00.000Z."""
    inclusive = search(
        h,
        query={
            "filter": {"date_time_filter": {"created_at": {"start_at": "2026-08-01T10:15:00.000Z"}}},
            "sort": {"sort_field": "CREATED_AT"},
        },
    ).json()["orders"]
    assert [order["id"] for order in inclusive] == [SEED_OPEN_ORDER_ID]

    exclusive = search(
        h,
        query={
            "filter": {"date_time_filter": {"created_at": {"end_at": "2026-08-01T10:15:00.000Z"}}},
            "sort": {"sort_field": "CREATED_AT"},
        },
    ).json()["orders"]
    assert [order["id"] for order in exclusive] == [SEED_COMPLETED_ORDER_ID]


def test_an_order_with_no_value_for_the_filtered_field_is_excluded(h: Harness) -> None:
    """ "closed between Monday and Tuesday" must not match an order that is
    still open, and the seeded open order has no `closed_at` at all.

    The COMPLETED order does have one, so this asserts the exclusion by naming
    what came back rather than by expecting nothing at all -- which is the
    stronger assertion: an empty page passes a filter that excludes every
    order, and this one must exclude exactly the open order.
    """
    found = search(
        h,
        query={
            "filter": {"date_time_filter": {"closed_at": {"start_at": "2000-01-01T00:00:00Z"}}},
            "sort": {"sort_field": "CLOSED_AT"},
        },
    ).json()["orders"]
    assert [order["id"] for order in found] == [SEED_COMPLETED_ORDER_ID]


def test_a_search_that_matches_nothing_still_carries_an_orders_array(h: Harness) -> None:
    """Half of the one empty-array rule; the other half is asserted in
    `test_model_order.py`, where an order with no line items carries no
    `line_items` key.

    Square settles neither case -- it publishes no sentence about empty arrays
    -- so this is stated as convention in `vendorfake.square.model.order`: an
    optional array inside an ENTITY is absent when empty, and the collection an
    OPERATION returns is always present. The package previously did both
    without saying so, which meant neither could be relied on.
    """
    body = search(h, query={"filter": {"state_filter": {"states": ["CANCELED"]}}}).json()
    assert body["orders"] == []
    assert "cursor" not in body

    entries = search(
        h,
        return_entries=True,
        query={"filter": {"state_filter": {"states": ["CANCELED"]}}},
    ).json()
    assert entries["order_entries"] == []
    assert "orders" not in entries


@pytest.mark.parametrize(
    ("field", "value"),
    [("sort_field", "PRICE"), ("sort_order", "SIDEWAYS")],
)
def test_search_refuses_an_unknown_sort(h: Harness, field: str, value: str) -> None:
    response = search(h, query={"sort": {field: value}})
    assert response.status == 400
    assert first_error(response)["field"] == f"query.sort.{field}"


def test_search_orders_same_instant_orders_by_code_point_not_by_collation(collated: Harness) -> None:
    """Four sort orders in this project are code point and never locale
    collation, and this is the one that reaches the wire.

    ICU puts "a" before "B"; Python's `sorted` puts "B" (U+0042) before "a"
    (U+0061). Square order ids are mixed case, so two orders created in the
    same millisecond page in a different order under the two rules. The
    reference sorts with `localeCompare`, which takes no locale argument and is
    therefore environment-dependent -- it is the non-deterministic side here.
    """
    assert len(LOWER_ID) == len(UPPER_ID) == 27
    ids = [
        order["order_id"]
        for order in collated.api.post(
            "/v2/orders/search",
            {
                "location_ids": ALL_SEED_LOCATIONS,
                "return_entries": True,
                "query": {
                    "filter": {
                        "date_time_filter": {"created_at": {"start_at": SAME_INSTANT, "end_at": "2026-06-02T00:00:00Z"}}
                    },
                    "sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"},
                },
            },
            headers=collated.auth,
        ).json()["order_entries"]
    ]
    assert ids == [UPPER_ID, LOWER_ID]
    assert sorted([LOWER_ID, UPPER_ID]) == [UPPER_ID, LOWER_ID]


# ---------------------------------------------------------------------------
# SearchOrders -- pagination
# ---------------------------------------------------------------------------


def test_the_last_page_carries_no_cursor(h: Harness) -> None:
    """ "The last page of the result set doesn't include a cursor" and "The
    pagination cursor ... If unset, this is the final response."
    https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination
    """
    first = search(h, limit=1).json()
    assert "cursor" in first
    second = search(h, limit=1, cursor=first["cursor"]).json()
    assert "cursor" not in second
    assert [order["id"] for order in second["orders"]] == [SEED_COMPLETED_ORDER_ID]


def test_paging_with_a_changed_query_is_refused(h: Harness) -> None:
    """ "include the cursor along with the same original request body" and "you
    must use the original query". `INVALID_CURSOR` is a documented ErrorCode.
    """
    cursor = search(h, limit=1).json()["cursor"]
    response = search(h, limit=1, cursor=cursor, location_ids=[SEED_LOCATION_ID])
    assert response.status == 400
    assert first_error(response)["code"] == "INVALID_CURSOR"
    assert first_error(response)["field"] == "cursor"


def test_changing_only_the_page_size_keeps_the_cursor_valid(h: Harness) -> None:
    """The fingerprint covers the whole body *except* `cursor` and `limit`,
    which is what lets a consumer page more slowly without re-running the
    query."""
    cursor = search(h, limit=1).json()["cursor"]
    assert search(h, limit=500, cursor=cursor).status == 200


def test_a_cursor_expires_after_five_minutes(virtual: Harness) -> None:
    """ "A cursor has a 5-minute lifetime ... after a cursor expires, it can no
    longer be used."
    https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination
    """
    cursor = search(virtual, limit=1).json()["cursor"]
    assert search(virtual, limit=1, cursor=cursor).status == 200
    virtual.api.post("/__unit/clock/advance", {"ms": CURSOR_TTL_MS + 1})
    response = search(virtual, limit=1, cursor=cursor)
    assert response.status == 400
    assert first_error(response)["code"] == "INVALID_CURSOR"
    assert "expired" in first_error(response)["detail"]


def test_a_forged_cursor_is_refused_rather_than_decoded(h: Harness) -> None:
    response = search(h, cursor="not-a-cursor")
    assert response.status == 400
    assert first_error(response)["code"] == "INVALID_CURSOR"


def test_the_limit_is_clamped_to_the_documented_maximum(h: Harness) -> None:
    """ "limit ... Default: 500. Max: 1000." A limit above the maximum is
    clamped rather than refused, which is what the core's paginator does for
    every vendor."""
    assert search(h, limit=SEARCH_MAX_LIMIT + 5000).status == 200
    assert search(h, limit=0).status == 200


def test_a_string_limit_is_refused_rather_than_ignored(h: Harness) -> None:
    """The reference's `typeof body.limit === 'number'` read `"5"` as "no
    limit", so a consumer's page size silently became 500."""
    assert search(h, limit="5").status == 400


# ---------------------------------------------------------------------------
# BatchRetrieveOrders
# ---------------------------------------------------------------------------


def test_batch_retrieve_returns_the_orders_in_the_order_asked_for(h: Harness) -> None:
    body = h.api.post(
        "/v2/orders/batch-retrieve",
        {"order_ids": [SEED_COMPLETED_ORDER_ID, SEED_OPEN_ORDER_ID]},
        headers=h.auth,
    ).json()
    assert [order["id"] for order in body["orders"]] == [SEED_COMPLETED_ORDER_ID, SEED_OPEN_ORDER_ID]
    assert "errors" not in body


def test_batch_retrieve_ignores_an_id_that_does_not_exist(h: Harness) -> None:
    """ "If a given order ID does not exist, the ID is ignored instead of
    generating an error", and `orders` holds "the requested orders, omitting
    any that don't exist".
    https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders
    """
    response = h.api.post("/v2/orders/batch-retrieve", {"order_ids": ["CAISnope", SEED_OPEN_ORDER_ID]}, headers=h.auth)
    assert response.status == 200
    assert [order["id"] for order in response.json()["orders"]] == [SEED_OPEN_ORDER_ID]


def test_batch_retrieve_scopes_to_location_id_when_one_is_given(h: Harness) -> None:
    """JUDGMENT: `location_id` is deprecated on Square's request object and
    documented only as "omit it to retrieve orders within the scope of the
    current authorization's merchant ID". Scoping to it is this unit's reading
    of the field's name."""
    body = h.api.post(
        "/v2/orders/batch-retrieve",
        {"order_ids": [SEED_OPEN_ORDER_ID, SEED_COMPLETED_ORDER_ID], "location_id": SEED_KIOSK_LOCATION_ID},
        headers=h.auth,
    ).json()
    assert [order["id"] for order in body["orders"]] == [SEED_COMPLETED_ORDER_ID]


def test_batch_retrieve_caps_the_request_at_a_hundred_ids(h: Harness) -> None:
    """ "A maximum of 100 orders can be retrieved per request." """
    response = h.api.post(
        "/v2/orders/batch-retrieve",
        {"order_ids": [f"CAIS{n:023d}" for n in range(MAX_BATCH_ORDER_IDS + 1)]},
        headers=h.auth,
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order_ids"


def test_batch_retrieve_requires_order_ids(h: Harness) -> None:
    response = h.api.post("/v2/orders/batch-retrieve", {}, headers=h.auth)
    assert response.status == 400
    assert first_error(response)["field"] == "order_ids"


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


def test_a_read_only_token_cannot_create_an_order(h: Harness) -> None:
    """Scopes are declared on the route and checked by the kernel, so this is
    the same 403 for every vendor. `INSUFFICIENT_SCOPES` is a documented code
    with a documented 403.
    https://developer.squareup.com/docs/build-basics/handling-errors
    """
    response = h.api.post("/v2/orders", {"order": {"location_id": SEED_LOCATION_ID}}, headers=h.read_auth)
    assert response.status == 403
    assert first_error(response)["code"] == "INSUFFICIENT_SCOPES"
    assert "ORDERS_WRITE" in first_error(response)["detail"]


def test_a_read_only_token_can_read_an_order(h: Harness) -> None:
    assert h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}", headers=h.read_auth).status == 200


def test_a_read_only_token_cannot_pay(h: Harness) -> None:
    response = h.api.post(f"/v2/orders/{SEED_OPEN_ORDER_ID}/pay", {"idempotency_key": "k"}, headers=h.read_auth)
    assert response.status == 403
    detail = first_error(response)["detail"]
    assert "ORDERS_WRITE" in detail and "PAYMENTS_WRITE" in detail


def test_an_unauthenticated_call_is_a_401(h: Harness) -> None:
    response = h.api.get(f"/v2/orders/{SEED_OPEN_ORDER_ID}")
    assert response.status == 401
    assert first_error(response)["code"] == "UNAUTHORIZED"


def test_the_orders_surface_belongs_to_one_capability_and_one_merchant(h: Harness) -> None:
    routes = h.api.get("/__unit/routes").json()["routes"]
    orders = [route for route in routes if route["path"].startswith("/v2/orders")]
    assert len(orders) == 6
    assert {route["capability"] for route in orders} == {"order-lifecycle"}
    created = create(h, {"location_id": SEED_LOCATION_ID}).json()["order"]
    stored = h.api.get("/__unit/state").json()
    assert stored["entities"]["orders"] == 3
    assert created["id"].startswith("CAIS")
    assert SEED_MERCHANT_ID


# ---------------------------------------------------------------------------
# What this surface unblocks
# ---------------------------------------------------------------------------


def test_the_route_keys_a_chaos_rule_can_name_are_the_brace_form(h: Harness) -> None:
    """`chaos-demo` names `POST /v2/orders` and `GET /v2/orders/{order_id}`.

    Every shipped profile sets `chaos.strict_rules`, so a rule naming a route
    that does not exist is a startup failure rather than a silent no-op -- and
    a colon template (`GET /v2/orders/:order_id`, which is what the reference's
    own profile shipped) matches nothing, forever. This test is what says the
    keys are there and are spelled with braces, so the profile can land.
    """
    keys = {f"{route['method']} {route['path']}" for route in h.api.get("/__unit/routes").json()["routes"]}
    assert {"POST /v2/orders", "GET /v2/orders/{order_id}"} <= keys
    assert not any(":" in key.split(" ", 1)[1] for key in keys)


def test_a_chaos_rule_naming_an_orders_route_is_live_rather_than_dead(h: Harness) -> None:
    """The count the control plane echoes back is how a consumer learns a rule
    is dead immediately rather than from a transcript that never faulted."""
    response = h.api.post(
        "/__unit/chaos/rules",
        {
            "rules": [
                {
                    "id": "rate-limit-every-third-create",
                    "scope": "request",
                    "fault": "rate_limit",
                    "match": {"route": "POST /v2/orders"},
                    "when": {"every": 3},
                },
                {
                    "id": "token-expires-on-fourth-read",
                    "scope": "request",
                    "fault": "token_expiry",
                    "match": {"route": "GET /v2/orders/{order_id}"},
                    "when": {"nth": [4]},
                },
            ]
        },
    )
    assert response.status == 200
    matched = {rule["id"]: rule["matched_routes"] for rule in response.json()["rules"]}
    assert matched["rate-limit-every-third-create"] == ["POST /v2/orders"]
    assert matched["token-expires-on-fourth-read"] == ["GET /v2/orders/{order_id}"]


# ---------------------------------------------------------------------------
# CreateOrder on its legacy path
# ---------------------------------------------------------------------------


def test_the_legacy_path_creates_the_same_order_as_the_current_one(h: Harness) -> None:
    """`POST /v2/locations/{location_id}/orders` takes the location from the
    URL and delegates everything else to CreateOrder: same pricing from the
    catalog, same state, same shape back."""
    response = h.api.post(
        f"/v2/locations/{SEED_LOCATION_ID}/orders",
        {
            "idempotency_key": "legacy-1",
            "order": {"line_items": [{"catalog_object_id": TEA_MUG_VARIATION_ID, "quantity": "2"}]},
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    order = response.json()["order"]
    assert order["location_id"] == SEED_LOCATION_ID
    assert order["state"] == "OPEN"
    assert order["version"] == 1
    assert order["total_money"] == {"amount": 300, "currency": "USD"}
    assert order["line_items"][0]["name"] == "Tea"
    assert retrieve(h, order["id"]).json()["order"] == order


def test_the_legacy_path_refuses_a_body_naming_a_different_location(h: Harness) -> None:
    """JUDGMENT, stated on the route: the two places a location can be named
    must agree, and neither silently wins."""
    response = h.api.post(
        f"/v2/locations/{SEED_LOCATION_ID}/orders",
        {"idempotency_key": "legacy-2", "order": {"location_id": SEED_KIOSK_LOCATION_ID}},
        headers=h.auth,
    )
    assert response.status == 400
    assert first_error(response)["field"] == "order.location_id"
    agreed = h.api.post(
        f"/v2/locations/{SEED_LOCATION_ID}/orders",
        {"idempotency_key": "legacy-3", "order": {"location_id": SEED_LOCATION_ID}},
        headers=h.auth,
    )
    assert agreed.status == 200, agreed.text


def test_the_legacy_path_refuses_an_unknown_location_like_create_does(h: Harness) -> None:
    response = h.api.post("/v2/locations/NOSUCH/orders", {"idempotency_key": "legacy-4", "order": {}}, headers=h.auth)
    assert response.status == 400
    assert first_error(response)["field"] == "order.location_id"
    assert "known" in response.json()["unit_error"]


def test_the_two_create_paths_share_one_idempotency_scope(h: Harness) -> None:
    """One key, one order: a client that retried the same key on the other
    path gets the replay when the body matches and IDEMPOTENCY_KEY_REUSED when
    it does not, exactly as it would on one path."""
    body = {"idempotency_key": "legacy-shared", "order": {"location_id": SEED_LOCATION_ID}}
    first = h.api.post(f"/v2/locations/{SEED_LOCATION_ID}/orders", body, headers=h.auth)
    second = h.api.post("/v2/orders", body, headers=h.auth)
    assert first.status == second.status == 200
    assert first.json() == second.json()
    different = h.api.post(
        "/v2/orders",
        {"idempotency_key": "legacy-shared", "order": {"location_id": SEED_LOCATION_ID, "ticket_name": "x"}},
        headers=h.auth,
    )
    assert different.status == 400
    assert first_error(different)["code"] == "IDEMPOTENCY_KEY_REUSED"


def test_the_legacy_path_requires_the_order_object(h: Harness) -> None:
    response = h.api.post(f"/v2/locations/{SEED_LOCATION_ID}/orders", {"idempotency_key": "legacy-5"}, headers=h.auth)
    assert response.status == 400
    assert first_error(response)["field"] == "order"


# ---------------------------------------------------------------------------
# SearchOrders -- explicit sort, the "recover after a gateway timeout" query
# ---------------------------------------------------------------------------


def test_search_sorts_ascending_when_asked(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/objects/SearchOrdersSort
    -- `sort_order` ASC reverses the default DESC, tie-break included."""
    desc = [
        o["id"] for o in search(h, query={"sort": {"sort_field": "CREATED_AT", "sort_order": "DESC"}}).json()["orders"]
    ]
    asc = [
        o["id"] for o in search(h, query={"sort": {"sort_field": "CREATED_AT", "sort_order": "ASC"}}).json()["orders"]
    ]
    assert asc == list(reversed(desc))
    assert asc == [SEED_COMPLETED_ORDER_ID, SEED_OPEN_ORDER_ID]


def test_search_sorts_by_updated_at_and_puts_the_freshest_mutation_first(h: Harness) -> None:
    """The recovery query: after a timeout, ask for the most recently updated
    order and read its `version` and `state` back."""
    created = create(h, {"location_id": SEED_LOCATION_ID}, idempotency_key="sort-1").json()["order"]
    update(h, SEED_OPEN_ORDER_ID, {"version": 1, "ticket_name": "Bar"}, idempotency_key="sort-2")
    page = search(h, query={"sort": {"sort_field": "UPDATED_AT", "sort_order": "DESC"}}, limit=1).json()
    (latest,) = page["orders"]
    assert latest["id"] == SEED_OPEN_ORDER_ID
    assert latest["version"] == 2
    assert latest["ticket_name"] == "Bar"
    assert page["cursor"]
    ids = [
        o["id"] for o in search(h, query={"sort": {"sort_field": "UPDATED_AT", "sort_order": "DESC"}}).json()["orders"]
    ]
    assert ids[:2] == [SEED_OPEN_ORDER_ID, created["id"]]


def test_search_sorts_by_closed_at_and_excludes_open_orders_only_when_filtering(h: Harness) -> None:
    """Sorting by CLOSED_AT alone keeps open orders -- an empty key sorts
    first ascending, last descending; a `closed_at` filter is what excludes
    them (see `_within`)."""
    ids = [
        o["id"] for o in search(h, query={"sort": {"sort_field": "CLOSED_AT", "sort_order": "DESC"}}).json()["orders"]
    ]
    assert ids == [SEED_COMPLETED_ORDER_ID, SEED_OPEN_ORDER_ID]
    filtered = search(
        h,
        query={
            "sort": {"sort_field": "CLOSED_AT", "sort_order": "DESC"},
            "filter": {"date_time_filter": {"closed_at": {"start_at": "2026-01-01T00:00:00Z"}}},
        },
    ).json()
    assert [o["id"] for o in filtered["orders"]] == [SEED_COMPLETED_ORDER_ID]


def test_sort_field_and_order_are_case_insensitive(h: Harness) -> None:
    """JUDGMENT: upper-cased before comparison, so an SDK that emits
    `created_at` lower-case is not refused."""
    response = search(h, query={"sort": {"sort_field": "created_at", "sort_order": "asc"}})
    assert response.status == 200, response.text
