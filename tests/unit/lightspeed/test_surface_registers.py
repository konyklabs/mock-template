"""Registers: the reads, the two actions, the closure, and the webhook it fires.

Closing is the one mutation in this slice that both commits and announces, so
the delivery half of this file is where the whole webhook path -- journal to
mapper to form encoding to signature -- is exercised end to end.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

from tests.unit.lightspeed.harness import Harness
from vendorfake.lightspeed.entities import COL, RegisterClosureEntity
from vendorfake.lightspeed.model.webhooks import DOMAIN_PREFIX_FIELD, ENVIRONMENT_FIELD, PAYLOAD_FIELD
from vendorfake.lightspeed.seed import constants as c
from vendorfake.lightspeed.signer import SIGNATURE_HEADER, verify_lightspeed_signature

OPEN_REGISTER = f"/registers/{c.SEED_REGISTER_SECOND_ID}/actions/open"
CLOSE_REGISTER = f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/close"
SUMMARY = f"/registers/{c.SEED_REGISTER_MAIN_ID}/payments_summary"


# -- reads -------------------------------------------------------------------


def test_the_list_answers_every_register(h: Harness) -> None:
    ids = [row["id"] for row in h.get(h.path("/registers")).json()["data"]]
    assert set(ids) == {c.SEED_REGISTER_MAIN_ID, c.SEED_REGISTER_SECOND_ID}


def test_a_register_carries_the_documented_members(h: Harness) -> None:
    register = h.get(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}")).json()["data"]
    assert register["id"] == c.SEED_REGISTER_MAIN_ID
    assert register["outlet_id"] == c.SEED_OUTLET_MAIN_ID
    assert register["is_open"] is True
    assert register["ask_for_note_on_save"] == 1
    assert isinstance(register["invoice_sequence"], int)
    assert isinstance(register["version"], int)


def test_register_close_time_is_an_explicit_null_while_open(h: Harness) -> None:
    """The schema documents it as "Null if currently open"; dropping the key
    would leave a consumer unable to tell an open register from an old
    response."""
    register = h.get(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}")).json()["data"]
    assert "register_close_time" in register
    assert register["register_close_time"] is None


def test_an_unknown_register_is_a_404(h: Harness) -> None:
    answered = h.get(h.path("/registers/nope"))
    assert answered.status == 404
    assert answered.json()["unit_error"]["field"] == "register_id"


# -- the two actions ---------------------------------------------------------


def test_opening_a_closed_register_sets_the_open_time_and_a_sequence(h: Harness) -> None:
    answered = h.put(h.path(OPEN_REGISTER), "{}")
    assert answered.status == 200, answered.text
    register = answered.json()["data"]
    assert register["is_open"] is True
    assert register["register_open_time"]
    assert register["register_open_sequence_id"]
    assert register["register_close_time"] is None


def test_opening_honours_a_supplied_open_time(h: Harness) -> None:
    answered = h.put(h.path(OPEN_REGISTER), json.dumps({"register_open_time": "2026-01-02T03:04:05Z"}))
    assert answered.json()["data"]["register_open_time"] == "2026-01-02T03:04:05Z"


def test_opening_an_open_register_is_a_409(h: Harness) -> None:
    """JUDGMENT: the schema says nothing about repeating an action. Answering
    200 would let a consumer's end-of-day run twice and report success both
    times."""
    answered = h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/open"), "{}")
    assert answered.status == 409
    assert answered.json()["unit_error"]["is_open"] is True


def test_closing_a_closed_register_is_a_409(h: Harness) -> None:
    answered = h.put(h.path(f"/registers/{c.SEED_REGISTER_SECOND_ID}/actions/close"), "{}")
    assert answered.status == 409


def test_an_empty_close_body_is_legal(h: Harness) -> None:
    """``RegisterCloseRequest`` declares no ``required`` list, so ``{}`` closes
    the register with no declared totals."""
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200


def test_a_declared_total_is_validated_and_not_added_to_the_observed_one(h: Harness) -> None:
    """The close request's declared totals are the cashier's till count, which
    is the SAME money the register already rang up -- so adding them would
    report the till twice over (see the module docstring).

    The scenario's layby put $10.00 of cash through this register. Declaring
    $255 of counted cash does not change what the summary reports, and the
    wire shape is still the documented one: ``total`` is a ``string`` with two
    decimal places.
    """
    answered = h.put(
        h.path(CLOSE_REGISTER),
        json.dumps({"payments": [{"payment_type_id": c.SEED_PAYMENT_TYPE_CASH_ID, "total": "255"}]}),
    )
    assert answered.status == 200
    summary = h.get(h.path(SUMMARY)).json()["data"]
    totals = {row["payment_type_id"]: row for row in summary["payments"]}
    assert totals[c.SEED_PAYMENT_TYPE_CASH_ID] == {
        "payment_type_id": c.SEED_PAYMENT_TYPE_CASH_ID,
        "payment_type_name": "Cash",
        "total": "10.00",
    }


def test_a_declared_total_for_a_type_the_till_never_took_is_not_reported(h: Harness) -> None:
    """The corollary: the summary reports the money the API can SEE. A payment
    type nobody paid with does not appear because a closing cashier typed a
    number against it."""
    assert (
        h.put(
            h.path(CLOSE_REGISTER),
            json.dumps({"payments": [{"payment_type_id": c.SEED_PAYMENT_TYPE_INTERNAL_ID, "total": "40.00"}]}),
        ).status
        == 200
    )
    summary = h.get(h.path(SUMMARY)).json()["data"]
    assert c.SEED_PAYMENT_TYPE_INTERNAL_ID not in {row["payment_type_id"] for row in summary["payments"]}


def test_a_type_the_close_did_not_declare_is_still_reported(h: Harness) -> None:
    """A closure's totals are the money the register TOOK, so a payment type
    the close did not declare still appears.

    The scenario's closed sale was paid by card -- 2 x 12.50 of trail mix plus
    24.90 of socks, at the catalogue's own prices -- and the close below
    declares nothing at all, so the card row is the sale's payment and nothing
    else.
    """
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    summary = h.get(h.path(SUMMARY)).json()["data"]
    totals = {row["payment_type_id"]: row for row in summary["payments"]}
    assert totals[c.SEED_PAYMENT_TYPE_CARD_ID] == {
        "payment_type_id": c.SEED_PAYMENT_TYPE_CARD_ID,
        "payment_type_name": "Credit Card",
        "total": "49.90",
    }
    assert totals[c.SEED_PAYMENT_TYPE_CASH_ID]["total"] == "10.00"


def test_a_total_naming_an_unknown_payment_type_is_refused(h: Harness) -> None:
    answered = h.put(h.path(CLOSE_REGISTER), json.dumps({"payments": [{"payment_type_id": "nope", "total": "1.00"}]}))
    assert answered.status == 422
    assert answered.json()["unit_error"]["field"] == "payments[0].payment_type_id"


def test_closing_needs_both_of_its_documented_scopes(h: Harness) -> None:
    """``CloseRegister``'s description names a pair:
    ``🔒 Requires: register:close payment_types:read scopes``."""
    one_scope = h.restricted_token("register:close")
    answered = h.put(h.path(CLOSE_REGISTER), "{}", headers=one_scope)
    assert answered.status == 403
    assert answered.json()["unit_error"]["missing"] == ["payment_types:read"]


# -- the closure and its summary ---------------------------------------------


def test_the_summary_is_a_404_before_the_first_close(h: Harness) -> None:
    """JUDGMENT: every member of the documented example names a closure, and a
    body full of nulls would be a worse answer than a refusal."""
    assert h.get(h.path(SUMMARY)).status == 404


def test_the_summary_answers_the_documented_members(h: Harness) -> None:
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    summary = h.get(h.path(SUMMARY)).json()["data"]
    assert set(summary) == {
        "payments",
        "register_closure_id",
        "register_closure_sequence_number",
        "register_open_time",
    }
    assert summary["register_closure_sequence_number"] == 1
    assert summary["register_open_time"] == "2026-09-01T08:00:00Z"


def test_the_sequence_number_counts_closures_for_that_register(h: Harness) -> None:
    """JUDGMENT: the documented example prints ``5`` and nothing says what it
    counts; per-register is the reading that makes the number useful."""
    for _ in range(3):
        assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
        assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/open"), "{}").status == 200
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert h.get(h.path(SUMMARY)).json()["data"]["register_closure_sequence_number"] == 4


def test_a_closure_is_stored_as_its_own_entity(h: Harness) -> None:
    """There is no REST resource for one anywhere in the 135 documented paths,
    so the closure exists only because this unit synthesises it -- and that
    insert is what the journal turns into the webhook."""
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    rows = h.unit.context.store.collection(COL.register_closures).all()
    assert len(rows) == 1
    closure = RegisterClosureEntity.from_entity(rows[0])
    assert closure.register_id == c.SEED_REGISTER_MAIN_ID
    assert closure.outlet_id == c.SEED_OUTLET_MAIN_ID


# -- one closure's money is not the next one's -------------------------------


def _cash_sale(h: Harness, amount: float) -> str:
    """A closed cash sale of ``amount`` on the main register, and its id."""
    answered = h.post(
        h.path("/sales"),
        json.dumps(
            {
                "state": "closed",
                "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
                "line_items": [
                    {
                        "product": {"id": c.SEED_PRODUCT_TRAIL_MIX_ID},
                        "quantity": 1,
                        "pricing": {"price": amount},
                        "tax": {"id": c.SEED_TAX_ID, "amount": 0},
                    }
                ],
                "payments": [{"amount": amount, "type": {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID}}],
            }
        ),
    )
    assert answered.status == 200, answered.text
    return str(answered.json()["data"]["id"])


def _parked_cash_sale(amount: float) -> str:
    """A parked sale carrying one cash payment and NO payment id, which is the
    body the schema documents: ``SalePayment.id`` is optional and a caller
    resending the payment has no reason to carry it."""
    return json.dumps(
        {
            "state": "parked",
            "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
            "line_items": [
                {
                    "product": {"id": c.SEED_PRODUCT_TRAIL_MIX_ID},
                    "quantity": 1,
                    "pricing": {"price": amount},
                    "tax": {"id": c.SEED_TAX_ID, "amount": 0},
                }
            ],
            "payments": [{"amount": amount, "type": {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID}}],
        }
    )


_PARKED_CASH_SALE = _parked_cash_sale(12.50)


def _cash_total(h: Harness) -> str:
    totals = {row["payment_type_id"]: row for row in h.get(h.path(SUMMARY)).json()["data"]["payments"]}
    return str(totals.get(c.SEED_PAYMENT_TYPE_CASH_ID, {}).get("total", "0.00"))


def test_a_second_closure_in_the_same_second_reports_only_its_own_session(h: Harness) -> None:
    """THE REGRESSION: ``wire_time`` spells instants to the SECOND, so a
    close, a reopen and a second close driven in a few milliseconds all carry
    one instant. Windowing on those alone gave the second closure ``[T, T]``
    and re-admitted the first session's money, which is the ordinary case for
    a consumer's end-of-day test rather than a race.

    The scenario's layby already put $10.00 of cash through this register, so
    the first session is 10.00 + 12.50 and the second is 3.25 and nothing
    else.
    """
    _cash_sale(h, 12.50)
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert _cash_total(h) == "22.50"

    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/open"), "{}").status == 200
    _cash_sale(h, 3.25)
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    summary = h.get(h.path(SUMMARY)).json()["data"]
    assert summary["register_closure_sequence_number"] == 2
    assert _cash_total(h) == "3.25"


def test_editing_a_sale_between_two_closures_does_not_count_its_money_twice(h: Harness) -> None:
    """THE REGRESSION beside the window one: a PUT REPLACES, and
    ``SalePayment.id`` is optional, so the ordinary POS day -- park a sale with
    a cash part-payment, close the till, reopen, correct the sale with a body
    that resends the payment as the schema documents it (amount and type, no
    id), close again -- used to re-mint the payment's id. The first closure's
    ``counted_payment_ids`` addressed the old id, so the same 12.50 walked into
    the second closure and an end-of-day reconciliation summing the two
    sessions was over by the full amount of every edited sale.

    The seeded layby puts 10.00 of cash through this register, so the first
    session is 10.00 + 12.50 and the second is nothing at all.
    """
    parked = h.post(h.path("/sales"), _PARKED_CASH_SALE)
    assert parked.status == 200, parked.text
    sale = parked.json()["data"]
    payment_id = sale["payments"][0]["id"]

    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert _cash_total(h) == "22.50"

    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/open"), "{}").status == 200
    edited = h.put(h.path(f"/sales/{sale['id']}"), _PARKED_CASH_SALE)
    assert edited.status == 200, edited.text
    # The same money, so the same payment: the id survives the replacement.
    assert edited.json()["data"]["payments"][0]["id"] == payment_id

    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert h.get(h.path(SUMMARY)).json()["data"]["register_closure_sequence_number"] == 2
    assert _cash_total(h) == "0.00"


def test_a_payment_whose_amount_changed_is_a_new_payment(h: Harness) -> None:
    """The other half of the matching rule, and JUDGMENT: an id-less payment
    inherits a stored id only when type and amount both agree. Money that
    actually moved is a different payment, mints a fresh id, and is counted
    afresh -- which is what a till reconciliation wants."""
    parked = h.post(h.path("/sales"), _PARKED_CASH_SALE)
    assert parked.status == 200, parked.text
    sale = parked.json()["data"]

    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert _cash_total(h) == "22.50"

    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_MAIN_ID}/actions/open"), "{}").status == 200
    edited = h.put(h.path(f"/sales/{sale['id']}"), _parked_cash_sale(20.00))
    assert edited.status == 200, edited.text
    assert edited.json()["data"]["payments"][0]["id"] != sale["payments"][0]["id"]

    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    assert _cash_total(h) == "20.00"


def test_a_voided_sales_payments_are_not_money_the_till_took(h: Harness) -> None:
    """JUDGMENT beside the window: an update rebuilds the whole sale document
    including ``payments``, so a cancelled sale keeps its payment rows.
    Counting them would tell a consumer's cancel-a-sale test that the drawer
    holds cash for a sale that never completed."""
    parked = h.post(
        h.path("/sales"),
        json.dumps(
            {
                "state": "parked",
                "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
                "line_items": [
                    {
                        "product": {"id": c.SEED_PRODUCT_TRAIL_MIX_ID},
                        "quantity": 1,
                        "pricing": {"price": 12.50},
                        "tax": {"id": c.SEED_TAX_ID, "amount": 0},
                    }
                ],
                "payments": [{"amount": 12.50, "type": {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID}}],
            }
        ),
    )
    assert parked.status == 200, parked.text
    sale = parked.json()["data"]
    voided = h.put(
        h.path(f"/sales/{sale['id']}"),
        json.dumps(
            {
                "state": "voided",
                "source": {"author_id": c.SEED_USER_ID, "register_id": c.SEED_REGISTER_MAIN_ID},
                "line_items": [
                    {
                        "product": {"id": c.SEED_PRODUCT_TRAIL_MIX_ID},
                        "quantity": 1,
                        "pricing": {"price": 12.50},
                        "tax": {"id": c.SEED_TAX_ID, "amount": 0},
                    }
                ],
                "payments": [{"amount": 12.50, "type": {"config_id": c.SEED_PAYMENT_TYPE_CASH_ID}}],
            }
        ),
    )
    assert voided.status == 200, voided.text
    assert voided.json()["data"]["state"] == "voided"
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    # The seeded layby's 10.00 and nothing from the cancelled sale.
    assert _cash_total(h) == "10.00"


# -- the webhook -------------------------------------------------------------


def test_closing_delivers_register_closure_create(h: Harness) -> None:
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    deliveries = h.deliveries()
    assert len(deliveries) == 1
    assert deliveries[0].url == c.SEED_WEBHOOK_URL


def test_the_delivery_is_form_encoded_with_the_documented_fields(h: Harness) -> None:
    """DOCUMENTED: POST, ``application/x-www-form-urlencoded``, UTF-8, with
    ``payload`` required and ``domain_prefix``/``environment`` optional. This
    unit sends all three."""
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    delivered = h.deliveries()[0]
    assert delivered.headers["content-type"] == "application/x-www-form-urlencoded"
    fields = dict(parse_qsl(delivered.body.decode("utf-8")))
    assert set(fields) == {PAYLOAD_FIELD, DOMAIN_PREFIX_FIELD, ENVIRONMENT_FIELD}
    assert fields[DOMAIN_PREFIX_FIELD] == c.SEED_DOMAIN_PREFIX
    assert fields[ENVIRONMENT_FIELD] == "production"


def test_the_payload_is_the_closure_as_json(h: Harness) -> None:
    assert (
        h.put(
            h.path(CLOSE_REGISTER),
            json.dumps({"payments": [{"payment_type_id": c.SEED_PAYMENT_TYPE_CASH_ID, "total": "12.50"}]}),
        ).status
        == 200
    )
    fields = dict(parse_qsl(h.deliveries()[0].body.decode("utf-8")))
    payload = json.loads(fields[PAYLOAD_FIELD])
    assert payload["register_id"] == c.SEED_REGISTER_MAIN_ID
    assert payload["register_closure_sequence_number"] == 1
    # The 10.00 of cash the scenario's layby put through this register while
    # it was open. The 12.50 this close DECLARES is the same money counted
    # again, so it is validated and not added.
    totals = {row["payment_type_id"]: row for row in payload["payments"]}
    assert totals[c.SEED_PAYMENT_TYPE_CASH_ID] == {
        "payment_type_id": c.SEED_PAYMENT_TYPE_CASH_ID,
        "payment_type_name": "Cash",
        "total": "10.00",
    }
    assert isinstance(payload["version"], int)


def test_the_delivered_closure_does_not_carry_the_internal_bookkeeping(h: Harness) -> None:
    """``counted_payment_ids`` is how one closure stops the next counting its
    money twice; it is not a member of anything the vendor prints, so it must
    not reach a subscriber."""
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    fields = dict(parse_qsl(h.deliveries()[0].body.decode("utf-8")))
    assert "counted_payment_ids" not in json.loads(fields[PAYLOAD_FIELD])


def test_the_delivery_carries_a_verifiable_signature(h: Harness) -> None:
    """DOCUMENTED format:
    ``X-Signature: signature=<hex>,algorithm=HMAC-SHA256``. The secret is the
    application's ``client_secret``: ``WebhookRequest`` carries none of its
    own."""
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    delivered = h.deliveries()[0]
    header = delivered.headers[SIGNATURE_HEADER]
    assert header.startswith("signature=") and header.endswith(",algorithm=HMAC-SHA256")
    assert verify_lightspeed_signature(c.SEED_CLIENT_SECRET, delivered.body, header)


def test_a_wrong_secret_does_not_verify(h: Harness) -> None:
    assert h.put(h.path(CLOSE_REGISTER), "{}").status == 200
    delivered = h.deliveries()[0]
    assert not verify_lightspeed_signature("wrong", delivered.body, delivered.headers[SIGNATURE_HEADER])


def test_a_refused_close_delivers_nothing(h: Harness) -> None:
    """The journal is the event source, so a mutation that did not commit
    cannot produce an event."""
    assert h.put(h.path(f"/registers/{c.SEED_REGISTER_SECOND_ID}/actions/close"), "{}").status == 409
    assert h.deliveries() == []
