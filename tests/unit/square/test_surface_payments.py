"""The Payments surface: external payments against orders, and how the order
moves with them.

https://developer.squareup.com/reference/square/payments-api/create-payment
https://developer.squareup.com/reference/square/objects/Payment
https://developer.squareup.com/docs/orders-api/pay-for-orders
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.webhooks.sink import MemorySink
from vendorfake.square.seed.constants import (
    SEED_COMPLETED_ORDER_ID,
    SEED_KIOSK_LOCATION_ID,
    SEED_LOCATION_ID,
    SEED_MERCHANT_ID,
    SEED_OPEN_ORDER_ID,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from build_harness("orders-only")


def create_order(h: Harness, amount: int = 500, key: str = "pay-order", **order: Any) -> dict[str, Any]:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": key,
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": amount}}],
                **order,
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return dict(response.json()["order"])


def payment_body(amount: int, key: str = "pay-1", **fields: Any) -> dict[str, Any]:
    return {
        "idempotency_key": key,
        "source_id": "EXTERNAL",
        "amount_money": {"amount": amount},
        "external_details": {"type": "OTHER", "source": "Food Delivery Service"},
        **fields,
    }


def pay(h: Harness, amount: int, key: str = "pay-1", **fields: Any) -> Any:
    return h.api.post("/v2/payments", payment_body(amount, key, **fields), headers=h.auth)


def retrieve_order(h: Harness, order_id: str) -> dict[str, Any]:
    return dict(h.api.get(f"/v2/orders/{order_id}", headers=h.auth).json()["order"])


def journal_seq(h: Harness) -> int:
    return int(h.api.get("/__unit/journal").json()["seq"])


# ---------------------------------------------------------------------------
# CreatePayment
# ---------------------------------------------------------------------------


def test_an_autocompleted_payment_is_completed_and_shaped_as_documented(h: Harness) -> None:
    """`autocomplete` defaults to true: "this payment will be completed when
    possible." The shape is the Payment object's, restricted to what an
    external payment carries."""
    response = pay(h, 500, reference_id="ticket-9", note="cash at counter")
    assert response.status == 200, response.text
    payment = response.json()["payment"]
    assert list(payment) == [
        "id",
        "created_at",
        "updated_at",
        "amount_money",
        "total_money",
        "approved_money",
        "status",
        "source_type",
        "location_id",
        "reference_id",
        "note",
        "external_details",
        "receipt_number",
        "receipt_url",
        "application_details",
        "version_token",
    ]
    assert len(payment["id"]) == 29
    assert payment["status"] == "COMPLETED"
    assert payment["source_type"] == "EXTERNAL"
    assert (
        payment["amount_money"]
        == payment["total_money"]
        == payment["approved_money"]
        == {"amount": 500, "currency": "USD"}
    )
    # No order and no location named: the merchant's main location.
    assert payment["location_id"] == SEED_LOCATION_ID
    assert payment["external_details"] == {"type": "OTHER", "source": "Food Delivery Service"}
    assert payment["receipt_number"] == payment["id"][:4]
    assert payment["receipt_url"] == f"https://squareup.com/receipt/preview/{payment['id']}"
    assert payment["application_details"] == {
        "square_product": "ECOMMERCE_API",
        "application_id": "sandbox-sq0idb-unit-square-application",
    }
    assert len(payment["version_token"]) == 43
    assert "order_id" not in payment


def test_a_payment_against_an_order_tenders_and_completes_it(h: Harness) -> None:
    """The consumer's close: CreatePayment with `order_id` for the amount due
    moves the order to COMPLETED with a tender naming the payment."""
    order = create_order(h, 500)
    seq = journal_seq(h)
    response = pay(h, 500, order_id=order["id"])
    assert response.status == 200, response.text
    payment = response.json()["payment"]
    assert payment["order_id"] == order["id"]
    assert payment["location_id"] == SEED_LOCATION_ID

    after = retrieve_order(h, order["id"])
    assert after["state"] == "COMPLETED"
    assert after["version"] == order["version"] + 1
    assert after["closed_at"]
    (tender,) = after["tenders"]
    assert tender["payment_id"] == payment["id"]
    assert tender["amount_money"] == {"amount": 500, "currency": "USD"}
    assert tender["type"] == "OTHER"
    assert after["net_amount_due_money"] == {"amount": 0, "currency": "USD"}
    # payment.created, payment.updated (capture), order.updated -- in that order.
    entries = h.api.get("/__unit/journal", query={"since": str(seq)}).json()["entries"]
    assert [(e["collection"], e["op"]) for e in entries] == [
        ("payments", "insert"),
        ("payments", "update"),
        ("orders", "update"),
    ]
    assert all(e["meta"]["operation_id"] == "CreatePayment" for e in entries)


def test_a_partial_payment_leaves_the_order_open_with_the_remainder_due(h: Harness) -> None:
    """Split tender is real: two payments of 300 and 200 against a 500 order."""
    order = create_order(h, 500)
    first = pay(h, 300, key="pay-a", order_id=order["id"])
    assert first.status == 200, first.text
    mid = retrieve_order(h, order["id"])
    assert mid["state"] == "OPEN"
    assert mid["net_amount_due_money"] == {"amount": 200, "currency": "USD"}
    assert len(mid["tenders"]) == 1

    second = pay(h, 200, key="pay-b", order_id=order["id"])
    assert second.status == 200, second.text
    done = retrieve_order(h, order["id"])
    assert done["state"] == "COMPLETED"
    assert len(done["tenders"]) == 2


def test_a_payment_exceeding_what_is_due_is_refused(h: Harness) -> None:
    """JUDGMENT stated on the surface: `net_amount_due_money` never goes
    negative through this route."""
    order = create_order(h, 500)
    response = pay(h, 501, order_id=order["id"])
    assert response.status == 400
    assert first_error(response)["field"] == "amount_money.amount"
    assert response.json()["unit_error"]["due"] == 500
    assert retrieve_order(h, order["id"])["state"] == "OPEN"


def test_a_tip_is_added_to_the_total_and_the_tender(h: Harness) -> None:
    order = create_order(h, 500)
    response = pay(h, 500, order_id=order["id"], tip_money={"amount": 100})
    assert response.status == 200, response.text
    payment = response.json()["payment"]
    assert payment["tip_money"] == {"amount": 100, "currency": "USD"}
    assert payment["total_money"] == {"amount": 600, "currency": "USD"}
    after = retrieve_order(h, order["id"])
    assert after["tenders"][0]["amount_money"] == {"amount": 600, "currency": "USD"}
    assert after["state"] == "COMPLETED"


def test_a_draft_or_finished_order_cannot_be_paid(h: Harness) -> None:
    draft = create_order(h, 500, key="draft", state="DRAFT")
    response = pay(h, 500, key="pay-draft", order_id=draft["id"])
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_transition"

    done = pay(h, 1, key="pay-done", order_id=SEED_COMPLETED_ORDER_ID)
    assert done.status == 400
    assert done.json()["unit_error"]["kind"] == "invalid_transition"


def test_a_rejected_payment_leaves_no_payment_and_no_journal_entry(h: Harness) -> None:
    """The invariant across the surface: nothing is written before every
    check has passed."""
    seq = journal_seq(h)
    assert pay(h, 500, order_id="CAISnope").status == 400
    assert journal_seq(h) == seq
    assert h.api.get("/__unit/state").json()["entities"].get("payments", 0) == 0


def test_an_order_and_a_different_location_do_not_mix(h: Harness) -> None:
    order = create_order(h, 500)
    response = pay(h, 500, order_id=order["id"], location_id=SEED_KIOSK_LOCATION_ID)
    assert response.status == 400
    assert first_error(response)["field"] == "location_id"


def test_only_the_external_source_is_taken(h: Harness) -> None:
    """The SHRINK, refused loudly rather than half-modelled."""
    nonce = h.api.post("/v2/payments", {**payment_body(500), "source_id": "cnon:card-nonce-ok"}, headers=h.auth)
    assert nonce.status == 400
    assert first_error(nonce)["field"] == "source_id"

    body = payment_body(500)
    del body["external_details"]
    missing = h.api.post("/v2/payments", body, headers=h.auth)
    assert missing.status == 400
    assert first_error(missing)["field"] == "external_details"

    wrong_type = pay(h, 500, external_details={"type": "PIGEON", "source": "x"})
    assert wrong_type.status == 400
    assert first_error(wrong_type)["field"] == "external_details.type"


def test_a_zero_or_negative_amount_is_refused(h: Harness) -> None:
    assert first_error(pay(h, 0))["field"] == "amount_money.amount"
    assert first_error(pay(h, -5))["field"] == "amount_money.amount"


def test_create_requires_an_idempotency_key_and_replays_under_it(h: Harness) -> None:
    """ "idempotency_key ... Min Length 1 Max Length 45" -- required, and the
    replay returns the same payment rather than taking the money twice."""
    body = payment_body(500)
    del body["idempotency_key"]
    assert first_error(h.api.post("/v2/payments", body, headers=h.auth))["field"] == "idempotency_key"

    order = create_order(h, 500)
    first = pay(h, 500, order_id=order["id"])
    again = pay(h, 500, order_id=order["id"])
    assert first.json() == again.json()
    assert len(retrieve_order(h, order["id"])["tenders"]) == 1


# ---------------------------------------------------------------------------
# Hold, then capture or void
# ---------------------------------------------------------------------------


def test_autocomplete_false_holds_the_payment_approved(h: Harness) -> None:
    """ "held in an approved state until either explicitly completed
    (captured) or canceled (voided)." No receipt yet, no tender yet."""
    order = create_order(h, 500)
    response = pay(h, 500, order_id=order["id"], autocomplete=False)
    assert response.status == 200, response.text
    payment = response.json()["payment"]
    assert payment["status"] == "APPROVED"
    assert payment["approved_money"] == {"amount": 500, "currency": "USD"}
    assert "receipt_number" not in payment
    assert "receipt_url" not in payment
    held = retrieve_order(h, order["id"])
    assert held["state"] == "OPEN"
    assert "tenders" not in held


def test_complete_captures_and_tenders_the_order(h: Harness) -> None:
    order = create_order(h, 500)
    payment = pay(h, 500, order_id=order["id"], autocomplete=False).json()["payment"]
    response = h.api.post(f"/v2/payments/{payment['id']}/complete", {}, headers=h.auth)
    assert response.status == 200, response.text
    completed = response.json()["payment"]
    assert completed["status"] == "COMPLETED"
    assert completed["receipt_number"] == payment["id"][:4]
    assert completed["version_token"] != payment["version_token"]
    after = retrieve_order(h, order["id"])
    assert after["state"] == "COMPLETED"
    assert after["tenders"][0]["payment_id"] == payment["id"]


def test_complete_honours_the_version_token(h: Harness) -> None:
    """ "If the server has a different version of the Payment, the update
    fails and a response with a VERSION_MISMATCH error is returned." """
    payment = pay(h, 500, autocomplete=False).json()["payment"]
    stale = h.api.post(f"/v2/payments/{payment['id']}/complete", {"version_token": "nope"}, headers=h.auth)
    assert stale.status == 400
    assert first_error(stale)["code"] == "VERSION_MISMATCH"
    assert h.api.get(f"/v2/payments/{payment['id']}", headers=h.auth).json()["payment"]["status"] == "APPROVED"

    fresh = h.api.post(
        f"/v2/payments/{payment['id']}/complete", {"version_token": payment["version_token"]}, headers=h.auth
    )
    assert fresh.status == 200, fresh.text


def test_completing_twice_is_refused_and_tenders_once(h: Harness) -> None:
    order = create_order(h, 500)
    payment = pay(h, 500, order_id=order["id"], autocomplete=False).json()["payment"]
    assert h.api.post(f"/v2/payments/{payment['id']}/complete", {}, headers=h.auth).status == 200
    again = h.api.post(f"/v2/payments/{payment['id']}/complete", {}, headers=h.auth)
    assert again.status == 400
    assert again.json()["unit_error"]["kind"] == "invalid_transition"
    assert len(retrieve_order(h, order["id"])["tenders"]) == 1


def test_cancel_voids_an_approved_payment_and_touches_no_order(h: Harness) -> None:
    """ "You can use this endpoint to cancel a payment with the APPROVED
    status." The order keeps its state and gains no tender."""
    order = create_order(h, 500)
    payment = pay(h, 500, order_id=order["id"], autocomplete=False).json()["payment"]
    seq = journal_seq(h)
    response = h.api.post(f"/v2/payments/{payment['id']}/cancel", {}, headers=h.auth)
    assert response.status == 200, response.text
    canceled = response.json()["payment"]
    assert canceled["status"] == "CANCELED"
    assert canceled["approved_money"] == {"amount": 0, "currency": "USD"}
    assert journal_seq(h) == seq + 1
    after = retrieve_order(h, order["id"])
    assert after["state"] == "OPEN"
    assert "tenders" not in after


def test_a_completed_payment_cannot_be_canceled(h: Harness) -> None:
    payment = pay(h, 500).json()["payment"]
    response = h.api.post(f"/v2/payments/{payment['id']}/cancel", {}, headers=h.auth)
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_transition"


def test_get_payment_reads_it_back_and_404s_an_unknown_one(h: Harness) -> None:
    payment = pay(h, 500).json()["payment"]
    assert h.api.get(f"/v2/payments/{payment['id']}", headers=h.auth).json()["payment"] == payment
    missing = h.api.get("/v2/payments/nope", headers=h.auth)
    assert missing.status == 404
    assert first_error(missing)["field"] == "payment_id"


# ---------------------------------------------------------------------------
# PayOrder over stored payments -- the documented two-step flow
# ---------------------------------------------------------------------------


def test_pay_order_captures_the_approved_payments_it_names(h: Harness) -> None:
    """CreatePayment with `autocomplete: false`, then PayOrder with the ids:
    "The total of the `payment_ids` listed in the request must be equal to
    the order total." Both payments are captured and the order completes."""
    order = create_order(h, 500)
    a = pay(h, 300, key="hold-a", order_id=order["id"], autocomplete=False).json()["payment"]
    b = pay(h, 200, key="hold-b", autocomplete=False).json()["payment"]
    response = h.api.post(
        f"/v2/orders/{order['id']}/pay",
        {"idempotency_key": "pay-order-1", "order_version": order["version"], "payment_ids": [a["id"], b["id"]]},
        headers=h.auth,
    )
    assert response.status == 200, response.text
    paid = response.json()["order"]
    assert paid["state"] == "COMPLETED"
    assert [(t["payment_id"], t["amount_money"]["amount"]) for t in paid["tenders"]] == [(a["id"], 300), (b["id"], 200)]
    for payment_id in (a["id"], b["id"]):
        stored = h.api.get(f"/v2/payments/{payment_id}", headers=h.auth).json()["payment"]
        assert stored["status"] == "COMPLETED"
        assert stored["order_id"] == order["id"]


def test_pay_order_refuses_payments_that_do_not_sum_to_the_total(h: Harness) -> None:
    order = create_order(h, 500)
    a = pay(h, 300, key="hold-a", autocomplete=False).json()["payment"]
    response = h.api.post(
        f"/v2/orders/{order['id']}/pay",
        {"idempotency_key": "pay-order-2", "payment_ids": [a["id"]]},
        headers=h.auth,
    )
    assert response.status == 400
    assert first_error(response)["field"] == "payment_ids"
    assert retrieve_order(h, order["id"])["state"] == "OPEN"
    assert h.api.get(f"/v2/payments/{a['id']}", headers=h.auth).json()["payment"]["status"] == "APPROVED"


def test_pay_order_refuses_a_mix_of_stored_and_unknown_ids(h: Harness) -> None:
    order = create_order(h, 500)
    a = pay(h, 500, key="hold-a", autocomplete=False).json()["payment"]
    response = h.api.post(
        f"/v2/orders/{order['id']}/pay",
        {"idempotency_key": "pay-order-3", "payment_ids": [a["id"], "not-a-payment"]},
        headers=h.auth,
    )
    assert response.status == 400
    assert response.json()["unit_error"]["payment_id"] == "not-a-payment"


def test_pay_order_refuses_a_payment_that_is_not_approved_or_belongs_elsewhere(h: Harness) -> None:
    order = create_order(h, 500)
    other = create_order(h, 500, key="other")
    completed = pay(h, 500, key="done", autocomplete=True).json()["payment"]
    elsewhere = pay(h, 500, key="else", order_id=other["id"], autocomplete=False).json()["payment"]
    for payment_id in (completed["id"], elsewhere["id"]):
        response = h.api.post(
            f"/v2/orders/{order['id']}/pay",
            {"idempotency_key": f"pay-order-{payment_id}", "payment_ids": [payment_id]},
            headers=h.auth,
        )
        assert response.status == 400
        assert first_error(response)["field"] == "payment_ids"


def test_pay_order_still_takes_opaque_ids_when_none_is_stored(h: Harness) -> None:
    """The form this unit accepted before it had a Payments surface."""
    order = create_order(h, 500)
    response = h.api.post(
        f"/v2/orders/{order['id']}/pay",
        {"idempotency_key": "pay-order-4", "payment_ids": ["ext-1", "ext-2"]},
        headers=h.auth,
    )
    assert response.status == 200, response.text
    assert [t["amount_money"]["amount"] for t in response.json()["order"]["tenders"]] == [500, 0]


# ---------------------------------------------------------------------------
# Scopes, capability, events
# ---------------------------------------------------------------------------


def test_scopes_are_the_documented_pair(h: Harness) -> None:
    """PAYMENTS_WRITE to take, complete or cancel; PAYMENTS_READ to read.
    https://developer.squareup.com/docs/oauth-api/square-permissions"""
    payment = pay(h, 500).json()["payment"]
    assert h.api.get(f"/v2/payments/{payment['id']}", headers=h.read_auth).status == 200
    refused = h.api.post("/v2/payments", payment_body(500, key="ro"), headers=h.read_auth)
    assert refused.status == 403
    assert first_error(refused)["code"] == "INSUFFICIENT_SCOPES"
    assert h.api.post(f"/v2/payments/{payment['id']}/cancel", {}, headers=h.read_auth).status == 403


def test_the_surface_is_its_own_capability() -> None:
    for scoped in build_harness("oauth-only"):
        response = scoped.api.post("/v2/payments", payment_body(500), headers=scoped.auth)
        assert response.status == 501
        assert response.headers["x-unit-capability"] == "payments"


def test_payment_events_carry_the_whole_payment() -> None:
    """https://developer.squareup.com/reference/square/webhooks/payment.created
    -- `data.type` is `payment` and `data.object.payment` is the Payment, the
    opposite of the order events' five-scalar summary."""
    sink = MemorySink()
    for scoped in build_harness("full", sink=sink):
        scoped.api.post(
            "/__unit/webhooks/subscriptions",
            {"notification_url": "https://example.test/hooks", "event_types": ["payment.*"], "signature_key": "k"},
        )
        payment = scoped.api.post("/v2/payments", payment_body(500), headers=scoped.auth).json()["payment"]
        scoped.api.post("/__unit/webhooks/drain", {})
        bodies = [json.loads(bytes(r.body).decode("utf-8")) for r in sink.received]
        assert [b["type"] for b in bodies] == ["payment.created", "payment.updated"]
        for body in bodies:
            assert body["merchant_id"] == SEED_MERCHANT_ID
            assert body["data"]["type"] == "payment"
            assert body["data"]["id"] == payment["id"]
            assert list(body["data"]["object"]) == ["payment"]
        assert bodies[0]["data"]["object"]["payment"]["status"] == "APPROVED"
        assert bodies[1]["data"]["object"]["payment"] == payment


def test_a_seeded_completed_order_is_not_disturbed_by_the_payments_surface(h: Harness) -> None:
    """The seed's tender names an opaque payment id; nothing here resolves it."""
    order = retrieve_order(h, SEED_COMPLETED_ORDER_ID)
    assert h.api.get(f"/v2/payments/{order['tenders'][0]['payment_id']}", headers=h.auth).status == 404
    assert retrieve_order(h, SEED_OPEN_ORDER_ID)["state"] == "OPEN"
