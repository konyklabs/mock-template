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


def test_closing_records_the_declared_totals_as_wire_strings(h: Harness) -> None:
    """DOCUMENTED: ``RegisterClosePaymentType.total`` is typed ``string``, and
    every example prints two decimal places.

    The declared total is SUMMED WITH the payments the register actually took
    (see the module docstring): the scenario's layby put $10.00 of cash through
    this register, so a declared $255 is reported as $265.00.
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
        "total": "265.00",
    }


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
    # 12.50 declared by this close, plus the 10.00 of cash the scenario's layby
    # put through this register while it was open.
    assert payload["payments"][0] == {
        "payment_type_id": c.SEED_PAYMENT_TYPE_CASH_ID,
        "payment_type_name": "Cash",
        "total": "22.50",
    }
    assert isinstance(payload["version"], int)


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
