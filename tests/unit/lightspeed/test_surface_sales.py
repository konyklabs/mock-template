"""Sales: the five routes, the money, the payments, the return, and the two
events a sale can fire.

Creating a sale is the one route in this package that commits AND announces on
every call, so this file is also where the webhook path is exercised for
``sale.update`` -- the signature included, because a delivery a consumer cannot
verify is a delivery that proves nothing.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION
from vendorfake.lightspeed.model.error import PaymentErrorCode
from vendorfake.lightspeed.model.webhooks import PAYLOAD_FIELD
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.signer import SIGNATURE_HEADER, verify_lightspeed_signature

SALES = "/sales"
SEED_META = {"operation_id": "TestSeed", "seed": True}


def line(product_id: str = c.SEED_PRODUCT_COFFEE_ID, quantity: float = 1, price: float = 4.5, tax: float = 0.68) -> Any:
    return {
        "product": {"id": product_id},
        "quantity": quantity,
        "pricing": {"price": price},
        "tax": {"id": c.SEED_TAX_ID, "amount": tax},
    }


def body(**overrides: Any) -> str:
    document: dict[str, Any] = {
        "state": "parked",
        "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
        "line_items": [line()],
    }
    document.update(overrides)
    return json.dumps(document)


def cash(amount: float, **overrides: Any) -> dict[str, Any]:
    payment: dict[str, Any] = {"amount": amount, "type": {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID}}
    payment.update(overrides)
    return payment


def subscribe(h: Harness, event: str = "sale.update") -> None:
    """A subscription on ``event``. The scenario seeds one on
    ``register_closure.create`` only, so anything watching a sale registers its
    own -- through the vendor's documented route, not by writing the store."""
    created = h.post(h.path("/webhooks"), json.dumps({"active": True, "type": event, "url": "https://sink.example/s"}))
    assert created.status == 201, created.text


def payloads(h: Harness) -> list[dict[str, Any]]:
    from urllib.parse import parse_qsl

    return [json.loads(dict(parse_qsl(delivered.body.decode("utf-8")))[PAYLOAD_FIELD]) for delivered in h.deliveries()]


# -- reads -------------------------------------------------------------------


def test_the_list_answers_every_seeded_sale(h: Harness) -> None:
    ids = [row["id"] for row in h.get(h.path(SALES)).json()["data"]]
    assert set(ids) == {c.SEED_SALE_SAVED_ID, c.SEED_SALE_CLOSED_ID, c.SEED_SALE_LAYBY_ID}


def test_a_sale_carries_the_documented_members(h: Harness) -> None:
    sale = h.get(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}")).json()["data"]
    assert sale["state"] == "closed"
    assert sale["customer_id"] == c.SEED_CUSTOMER_ID
    assert sale["source"]["register_id"] == c.SEED_REGISTER_MAIN_ID
    # DOCUMENTED: `SaleResponseSource.outlet_id` -- derived from the register.
    assert sale["source"]["outlet_id"] == c.SEED_OUTLET_MAIN_ID
    assert sale["source"]["author"] == {"id": c.SEED_USER_ID}
    assert sale["line_items"][0]["product"] == {"id": c.SEED_PRODUCT_COFFEE_ID}
    assert sale["invoice_number"] and sale["receipt_number"]


def test_the_version_is_published_twice_and_agrees(h: Harness) -> None:
    """JUDGMENT: ``_metadata.version`` because the ``Sale`` schema declares it
    only there, and a top-level ``version`` because the documented page walk
    reads the next ``after`` off the rows themselves."""
    sale = h.get(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}")).json()["data"]
    assert isinstance(sale["version"], int)
    assert sale["_metadata"]["version"] == sale["version"]


def test_money_on_a_sale_is_a_json_number_not_a_string(h: Harness) -> None:
    """The vendor's two money shapes, and this is the number one:
    ``SaleTotals``, ``LineItemPricing`` and ``SalePayment.amount`` are all
    ``format: double``, while the register close totals are strings."""
    sale = h.get(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}")).json()["data"]
    for value in (sale["totals"]["price"], sale["line_items"][0]["pricing"]["price"], sale["payments"][0]["amount"]):
        assert isinstance(value, float), value


def test_the_totals_are_computed_from_the_line_items(h: Harness) -> None:
    """The seeded closed sale: 2 x 4.50 + 1 x 6.00 = 15.00 excluding tax,
    2 x 0.68 + 1 x 0.90 = 2.26 of tax, 17.26 including."""
    totals = h.get(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}")).json()["data"]["totals"]
    assert totals == {"price": 15.0, "price_incl_tax": 17.26, "tax": 2.26, "loyalty": 0.0, "surcharge": 0.0}


def test_the_taxes_block_groups_the_lines_by_tax_id(h: Harness) -> None:
    taxes = h.get(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}")).json()["data"]["taxes"]
    assert taxes == [{"id": c.SEED_TAX_ID, "tax": 2.26}]


def test_a_layby_is_a_parked_sale_carrying_the_attribute(h: Harness) -> None:
    """There is no ``LAYBY`` state in the 2026-07 schema; ``attributes`` is how
    the schema expresses one (documented example value: ``["onaccount"]``)."""
    sale = h.get(h.path(f"{SALES}/{c.SEED_SALE_LAYBY_ID}")).json()["data"]
    assert sale["state"] == "parked"
    assert sale["attributes"] == ["layby"]
    assert sale["payments"][0]["amount"] == 10.0


def test_an_unknown_sale_is_a_404(h: Harness) -> None:
    answered = h.get(h.path(f"{SALES}/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "sale_id"


def test_the_list_walks_on_the_version_cursor(h: Harness) -> None:
    """The documented walk: ``after`` defaults to 0, each page's
    ``version.max`` is the next ``after``, and an empty ``data`` ends it."""
    seen: list[str] = []
    after = 0
    for _ in range(10):
        page = h.get(h.path(SALES), query={"after": str(after), "page_size": "1"}).json()
        if not page["data"]:
            break
        assert len(page["data"]) == 1
        seen.append(page["data"][0]["id"])
        after = page["version"]["max"]
    assert seen == [c.SEED_SALE_SAVED_ID, c.SEED_SALE_CLOSED_ID, c.SEED_SALE_LAYBY_ID]
    assert h.get(h.path(SALES), query={"after": str(after)}).json()["version"] == {"max": None, "min": None}


# -- create ------------------------------------------------------------------


def test_creating_a_parked_sale_answers_it(h: Harness) -> None:
    answered = h.post(h.path(SALES), body())
    assert answered.status == 200, answered.text
    sale = answered.json()["data"]
    assert sale["state"] == "parked"
    assert sale["totals"]["price_incl_tax"] == 5.18
    assert h.get(h.path(f"{SALES}/{sale['id']}")).status == 200


def test_creating_a_closed_sale_with_a_payment(h: Harness) -> None:
    """The single-request POS flow: ``parked -> closed`` is a legal edge, so a
    sale may be created straight into its end state."""
    answered = h.post(h.path(SALES), body(state="closed", payments=[cash(5.18)]))
    assert answered.status == 200, answered.text
    sale = answered.json()["data"]
    assert sale["state"] == "closed"
    assert sale["payments"][0]["amount"] == 5.18
    # `PaymentTypeDetails` carries the name, which is where a consumer reads it.
    assert sale["payments"][0]["type"] == {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID, "name": "Cash"}
    assert sale["payments"][0]["source"] == {"register_id": c.SEED_REGISTER_MAIN_ID}


def test_a_caller_may_supply_the_id_and_may_not_reuse_it(h: Harness) -> None:
    """DOCUMENTED: "User-provided sale ID. If not included, one will be
    generated". JUDGMENT: re-using one is a 409, never an overwrite."""
    mine = "11111111-2222-1333-8444-555555555555"
    assert h.post(h.path(SALES), body(id=mine)).json()["data"]["id"] == mine
    again = h.post(h.path(SALES), body(id=mine))
    assert again.status == 409
    assert again.json()["unit_error"]["field"] == "id"


def test_the_invoice_number_is_minted_from_the_register(h: Harness) -> None:
    """``{invoice_prefix}{sequence}{invoice_suffix}``: the main register is
    seeded ``MAIN-`` / 1041 / ``-NZ`` and already carries three seeded sales."""
    sale = h.post(h.path(SALES), body()).json()["data"]
    assert sale["invoice_number"] == "MAIN-1044-NZ"
    assert sale["receipt_number"] == sale["invoice_number"]
    assert h.post(h.path(SALES), body()).json()["data"]["invoice_number"] == "MAIN-1045-NZ"


def test_a_supplied_invoice_number_wins(h: Harness) -> None:
    assert h.post(h.path(SALES), body(invoice_number="X-9")).json()["data"]["invoice_number"] == "X-9"


def test_a_sale_may_name_a_customer_that_exists(h: Harness) -> None:
    assert h.post(h.path(SALES), body(customer_id=c.SEED_CUSTOMER_ID)).status == 200


# -- refusals ----------------------------------------------------------------


def test_a_body_without_state_or_source_is_a_422(h: Harness) -> None:
    """``source`` and ``state`` are ``SaleRequestBase``'s only two ``required``
    members."""
    answered = h.post(h.path(SALES), json.dumps({"line_items": []}))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "source"


def test_an_undeclared_state_names_every_state_that_is(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(state="SAVED"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["allowed"] == ["parked", "pending", "voided", "closed"]


def test_a_line_item_naming_an_unknown_product_is_refused(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(line_items=[line(product_id="nope")]))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "line_items[0].product.id"


def test_an_unknown_customer_is_refused(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(customer_id="nope"))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "customer_id"


@pytest.mark.parametrize("quantity", [0, -2])
def test_a_zero_or_negative_quantity_is_refused(h: Harness, quantity: float) -> None:
    """JUDGMENT: negatives belong to a return, which the return route mints."""
    answered = h.post(h.path(SALES), body(line_items=[line(quantity=quantity)]))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "line_items[0].quantity"


def test_a_price_that_is_not_an_amount_is_refused_by_its_dotted_path(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(line_items=[line(price="free")]))  # type: ignore[arg-type]
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "line_items[0].pricing.price"


def test_a_malformed_body_is_a_400_before_the_path_is_resolved(h: Harness) -> None:
    answered = h.put(h.path(f"{SALES}/nope"), "{not json")
    assert answered.status == 400


def test_the_read_only_token_cannot_write(h: Harness) -> None:
    assert h.post(h.path(SALES), body(), headers=h.read_auth).status == 403
    assert h.get(h.path(SALES), headers=h.read_auth).status == 200


def test_the_return_action_needs_both_of_its_documented_scopes(h: Harness) -> None:
    """``initReturnSale``'s description names a pair: ``sales:write``
    ``users:read``."""
    one = h.restricted_token("sales:write")
    answered = h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}", headers=one)
    assert answered.status == 403


# -- payments ----------------------------------------------------------------


def test_a_payment_on_a_closed_register_is_refused_in_the_vendors_payment_shape(h: Harness) -> None:
    """``PaymentErrorResponse`` -- ``{"error": {"code": int, "message": str}}``
    -- exactly, and nothing else beside it but this project's sidecar."""
    answered = h.post(
        h.path(SALES),
        body(payments=[cash(1.0, source={"register_id": c.SEED_REGISTER_SECOND_ID})]),
    )
    assert answered.status == 409
    document = answered.json()
    assert document["error"]["code"] == int(PaymentErrorCode.REGISTER_NOT_OPEN)
    assert isinstance(document["error"]["message"], str)
    assert set(document) == {"error", "unit_error"}


def test_an_unknown_payment_type_is_a_payment_error(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(payments=[{"amount": 1.0, "type": {"config_id": "nope"}}]))
    assert answered.status == 422
    assert answered.json()["error"]["code"] == int(PaymentErrorCode.UNKNOWN_PAYMENT_TYPE)


def test_an_unknown_register_on_a_payment_is_a_payment_error(h: Harness) -> None:
    answered = h.post(h.path(SALES), body(payments=[cash(1.0, source={"register_id": "nope"})]))
    assert answered.status == 422
    assert answered.json()["error"]["code"] == int(PaymentErrorCode.UNKNOWN_REGISTER)


def test_a_payment_with_no_register_anywhere_is_a_payment_error(h: Harness) -> None:
    answered = h.post(
        h.path(SALES),
        json.dumps({"state": "parked", "source": {"author_id": c.SEED_USER_ID}, "payments": [cash(1.0)]}),
    )
    assert answered.status == 422
    assert answered.json()["error"]["code"] == int(PaymentErrorCode.REGISTER_REQUIRED)


def test_the_ordinary_error_shape_is_untouched_by_the_payment_shape(h: Harness) -> None:
    """A refusal that is NOT about a payment keeps the generalised two-member
    body, so the two shapes are distinguishable."""
    answered = h.post(h.path(SALES), body(customer_id="nope"))
    assert set(answered.json()) == {"error", "message", "unit_error"}
    assert isinstance(answered.json()["error"], str)


# -- update ------------------------------------------------------------------


def test_updating_a_parked_sale_replaces_its_line_items(h: Harness) -> None:
    """A PUT states the whole editable document -- there is no PATCH in the
    tag and the arrays are inline -- so this is how a line item is removed."""
    answered = h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body(state="parked", line_items=[line(quantity=3)]))
    assert answered.status == 200, answered.text
    sale = answered.json()["data"]
    assert len(sale["line_items"]) == 1
    assert sale["line_items"][0]["quantity"] == 3.0
    assert sale["totals"]["price"] == 13.5


def test_an_update_keeps_the_id_the_creation_instant_and_the_invoice(h: Harness) -> None:
    before = h.get(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}")).json()["data"]
    after = h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body()).json()["data"]
    assert after["id"] == before["id"]
    assert after["created_at"] == before["created_at"]
    assert after["invoice_number"] == before["invoice_number"]
    assert after["version"] > before["version"]


def test_the_store_owns_created_at_and_updated_at(h: Harness) -> None:
    """Both are the CORE's fields: ``Collection.insert`` stamps them and
    ``Collection.update`` rewrites ``updated_at`` after every mutator, so a
    vendor that wrote them too would have its value replaced on the first
    update and end up with two timestamps spelled to different precisions.

    ``date`` -- an editable member of ``SaleRequestBase`` -- is the vendor's
    and stays the vendor's.
    """
    created = h.post(h.path(SALES), body()).json()["data"]
    assert created["created_at"] == created["updated_at"]
    updated = h.put(h.path(f"{SALES}/{created['id']}"), body()).json()["data"]
    assert updated["created_at"] == created["created_at"]
    assert updated["updated_at"] >= created["updated_at"]
    # The seed states all three, and an insert honours what it is given.
    seeded = h.get(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}")).json()["data"]
    assert seeded["created_at"] == seeded["updated_at"] == seeded["date"] == "2026-09-01T08:40:00Z"


def test_the_sale_date_is_the_callers_when_it_gives_one(h: Harness) -> None:
    """DOCUMENTED: "The date of the sale in RFC3339 format. If not provided
    will be added as the time the sale reached the server"."""
    assert h.post(h.path(SALES), body(date="2009-11-10T23:00:00Z")).json()["data"]["date"] == "2009-11-10T23:00:00Z"
    assert h.post(h.path(SALES), body()).json()["data"]["date"]


def test_closing_a_parked_sale_is_a_legal_move(h: Harness) -> None:
    answered = h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body(state="closed", payments=[cash(5.18)]))
    assert answered.status == 200, answered.text
    assert answered.json()["data"]["state"] == "closed"


def test_a_closed_sale_refuses_every_update(h: Harness) -> None:
    answered = h.put(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}"), body(state="closed"))
    assert answered.status == 409
    assert answered.json()["unit_error"]["kind"] == "invalid_transition"


def test_a_pending_sale_cannot_go_back_to_parked(h: Harness) -> None:
    created = h.post(h.path(SALES), body(state="pending")).json()["data"]
    answered = h.put(h.path(f"{SALES}/{created['id']}"), body(state="parked"))
    assert answered.status == 409
    assert answered.json()["unit_error"]["allowed"] == ["closed", "voided"]


def test_voiding_a_parked_sale_and_then_nothing_else(h: Harness) -> None:
    voided = h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body(state="voided"))
    assert voided.status == 200
    assert h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body(state="voided")).status == 409


# -- the return action -------------------------------------------------------


def test_a_return_answers_a_new_parked_sale_with_negated_lines(h: Harness) -> None:
    """DOCUMENTED: "Initializes a return for an existing closed sale and returns
    the newly created SAVED return sale"; the operation's own example prints
    ``"quantity": -1`` and ``"is_return": true`` on every line."""
    answered = h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}")
    assert answered.status == 200, answered.text
    returned = answered.json()["data"]
    assert returned["id"] != c.SEED_SALE_CLOSED_ID
    assert returned["state"] == "parked"
    assert returned["return"] == {"is_return": True, "original_sale_id": c.SEED_SALE_CLOSED_ID}
    assert [item["quantity"] for item in returned["line_items"]] == [-2.0, -1.0]
    assert all(item["return"] == {"is_return": True} for item in returned["line_items"])
    assert returned["totals"]["price_incl_tax"] == -17.26
    assert returned["payments"] == []


def test_the_original_records_the_return_it_produced(h: Harness) -> None:
    """``SaleReturn.return_sale_ids`` -- "IDs of return sales created from this
    sale". Written even though ``closed`` is terminal: the carve-out is at the
    route's own site."""
    returned = h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}").json()["data"]
    original = h.get(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}")).json()["data"]
    assert original["return"]["return_sale_ids"] == [returned["id"]]
    assert original["state"] == "closed"


def test_a_return_can_take_its_refund_payment_afterwards(h: Harness) -> None:
    """The documented workflow: "start the return workflow before adding refund
    payments or finalizing the returned items"."""
    returned = h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}").json()["data"]
    refund = json.dumps(
        {
            "state": "closed",
            "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
            "line_items": [
                {
                    "product": {"id": item["product"]["id"]},
                    "quantity": item["quantity"],
                    "pricing": {"price": item["pricing"]["price"]},
                    "tax": {"id": item["tax"]["id"], "amount": item["tax"]["amount"]},
                }
                for item in returned["line_items"]
            ],
            "payments": [cash(-17.26)],
        }
    )
    answered = h.put(h.path(f"{SALES}/{returned['id']}"), refund)
    assert answered.status == 200, answered.text
    assert answered.json()["data"]["payments"][0]["amount"] == -17.26


@pytest.mark.parametrize("sale_id", [c.SEED_SALE_SAVED_ID, c.SEED_SALE_LAYBY_ID])
def test_only_a_closed_sale_can_be_returned(h: Harness, sale_id: str) -> None:
    answered = h.post(h.path(f"{SALES}/{sale_id}/actions/return"), "{}")
    assert answered.status == 409
    assert answered.json()["unit_error"]["field"] == "sale_id"


def test_returning_an_unknown_sale_is_a_404(h: Harness) -> None:
    assert h.post(h.path(f"{SALES}/nope/actions/return"), "{}").status == 404


# -- events ------------------------------------------------------------------


def test_creating_a_sale_delivers_exactly_one_verifiable_sale_update(h: Harness) -> None:
    subscribe(h)
    created = h.post(h.path(SALES), body()).json()["data"]
    delivered = h.deliveries()
    assert len(delivered) == 1
    header = delivered[0].headers[SIGNATURE_HEADER]
    assert header.startswith("signature=") and header.endswith(",algorithm=HMAC-SHA256")
    assert verify_lightspeed_signature(c.SEED_CLIENT_SECRET, delivered[0].body, header)
    payload = payloads(h)[0]
    assert payload["id"] == created["id"]
    assert payload["state"] == "parked"
    assert payload["totals"] == created["totals"]


def test_updating_a_sale_delivers_one_more(h: Harness) -> None:
    subscribe(h)
    h.put(h.path(f"{SALES}/{c.SEED_SALE_SAVED_ID}"), body())
    assert len(h.deliveries()) == 1


def test_a_return_fires_sale_update_twice(h: Harness) -> None:
    """DOCUMENTED shape: ``sale.update`` "may fire multiple times". A return is
    two committed writes -- the new sale, and the original that now links it."""
    subscribe(h)
    h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}")
    assert len(h.deliveries()) == 2
    assert [payload["id"] for payload in payloads(h)][1] == c.SEED_SALE_CLOSED_ID


def test_a_refused_sale_delivers_nothing(h: Harness) -> None:
    """A mutation that did not commit is not a mutation, so the journal never
    sees it and no event exists to map."""
    subscribe(h)
    assert h.post(h.path(SALES), body(customer_id="nope")).status == 422
    assert h.deliveries() == []


# -- the inventory effect ----------------------------------------------------


def _seed_inventory(h: Harness, level: float = 10) -> str:
    """One inventory record, inserted the way the sibling slice's seed will.

    The ``inventory`` collection belongs to that slice; this surface only ever
    moves a record that already exists, and the effect is unreachable until one
    does -- which is exactly the guard being exercised here.
    """
    record = {
        "id": "1a000000-0000-1000-8000-000000000d01",
        "product_id": c.SEED_PRODUCT_COFFEE_ID,
        "outlet_id": c.SEED_OUTLET_MAIN_ID,
        "current_inventory_level": level,
        OBJECT_VERSION: 999_000,
    }
    h.unit.context.store.collection(COL.inventory).insert(record, SEED_META)
    return str(record["id"])


def level_of(h: Harness, record_id: str) -> Any:
    stored = h.unit.context.store.collection(COL.inventory).get(record_id)
    assert stored is not None
    return stored["current_inventory_level"]


def test_closing_a_sale_draws_the_outlets_stock(h: Harness) -> None:
    record = _seed_inventory(h, level=10)
    assert h.post(h.path(SALES), body(state="closed", line_items=[line(quantity=3)])).status == 200
    assert level_of(h, record) == 7


def test_a_parked_sale_draws_nothing_until_it_closes(h: Harness) -> None:
    record = _seed_inventory(h, level=10)
    created = h.post(h.path(SALES), body(line_items=[line(quantity=3)])).json()["data"]
    assert level_of(h, record) == 10
    h.put(h.path(f"{SALES}/{created['id']}"), body(state="closed", line_items=[line(quantity=3)]))
    assert level_of(h, record) == 7


def test_a_return_gives_the_stock_back(h: Harness) -> None:
    """Its line quantities are negative, so the same subtraction adds."""
    record = _seed_inventory(h, level=10)
    returned = h.post(h.path(f"{SALES}/{c.SEED_SALE_CLOSED_ID}/actions/return"), "{}").json()["data"]
    refund = body(
        state="closed",
        line_items=[
            {
                "product": {"id": item["product"]["id"]},
                "quantity": item["quantity"],
                "pricing": {"price": item["pricing"]["price"]},
                "tax": {"id": item["tax"]["id"], "amount": item["tax"]["amount"]},
            }
            for item in returned["line_items"]
        ],
    )
    assert h.put(h.path(f"{SALES}/{returned['id']}"), refund).status == 200
    assert level_of(h, record) == 12


def test_the_stock_move_fires_inventory_update(h: Harness) -> None:
    subscribe(h, "inventory.update")
    _seed_inventory(h)
    assert h.post(h.path(SALES), body(state="closed", line_items=[line(quantity=3)])).status == 200
    assert len(h.deliveries()) == 1


def test_a_unit_with_no_inventory_still_closes_a_sale(h: Harness) -> None:
    """The guard. The shipped scenario in this branch carries no inventory at
    all, so this is the ordinary path here and the decrement is the exception.
    """
    assert h.unit.context.store.collection(COL.inventory).all() == []
    assert h.post(h.path(SALES), body(state="closed", line_items=[line(quantity=3)])).status == 200
