"""Payment records on an order, and what paying does to the order."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import (
    EMPLOYEE_BARISTA,
    MERCHANT_ID,
    TENDER_CASH,
    TENDER_EXTERNAL,
    Harness,
    harness,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def test_a_payment_record_has_the_documented_shape_and_locks_the_order(h: Harness) -> None:
    """The get-all-payments guide's verbatim element shape; 'locked is
    automatically set by Clover' when a payment is taken."""
    order = h.create_order(total=1500)
    before = h.journal_len()
    response = h.post(
        f"/orders/{order['id']}/payments",
        {"tender": {"id": TENDER_EXTERNAL}, "employee": {"id": EMPLOYEE_BARISTA}, "offline": False, "amount": 1500},
    )
    assert response.status == 200
    payment = response.json()
    assert len(payment["id"]) == 13
    assert payment["order"] == {"id": order["id"]}
    assert payment["tender"] == {
        "href": f"https://apisandbox.dev.clover.com/v3/merchants/{MERCHANT_ID}/tenders/{TENDER_EXTERNAL}",
        "id": TENDER_EXTERNAL,
    }
    assert payment["amount"] == 1500
    assert payment["employee"] == {"id": EMPLOYEE_BARISTA}
    assert payment["offline"] is False
    assert payment["result"] == "SUCCESS"
    assert payment["cashbackAmount"] == 0
    assert payment["createdTime"] == payment["clientCreatedTime"] == payment["modifiedTime"] > 10**12
    after = h.get(f"/orders/{order['id']}", query={"expand": "payments"}).json()
    assert after["state"] == "locked"
    assert after["paymentState"] == "PAID"
    assert after["payments"] == [{"id": payment["id"]}]
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert {e["meta"]["operation_id"] for e in entries} == {"CreatePayment"}
    assert [(e["collection"], e["op"]) for e in entries] == [("payments", "insert"), ("orders", "update")]


def test_a_partial_payment_is_partially_paid_and_a_second_completes_it(h: Harness) -> None:
    """JUDGMENT: PAID when the payments cover the total, PARTIALLY_PAID
    otherwise; the second payment lands on an already-locked order."""
    order = h.create_order(total=1000)
    first = h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 400})
    assert first.status == 200
    mid = h.get(f"/orders/{order['id']}").json()
    assert mid["paymentState"] == "PARTIALLY_PAID"
    assert mid["state"] == "locked"
    second = h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 600})
    assert second.status == 200
    assert h.get(f"/orders/{order['id']}").json()["paymentState"] == "PAID"


def test_payment_refusals_precede_every_write(h: Harness) -> None:
    order = h.create_order(total=1000)
    before = h.journal_len()
    bad_tender = h.post(f"/orders/{order['id']}/payments", {"tender": {"id": "NOSUCHTENDER1"}, "amount": 1})
    assert bad_tender.status == 400
    assert bad_tender.json()["unit_error"]["field"] == "tender.id"
    zero = h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 0})
    assert zero.status == 400
    assert "positive amount" in zero.json()["message"]
    bad_employee = h.post(
        f"/orders/{order['id']}/payments",
        {"tender": {"id": TENDER_CASH}, "amount": 1, "employee": {"id": "NOSUCHEMPL001"}},
    )
    assert bad_employee.status == 400
    missing_tender = h.post(f"/orders/{order['id']}/payments", {"amount": 1})
    assert missing_tender.status == 400
    assert h.post("/orders/NOSUCHORDER01/payments", {"tender": {"id": TENDER_CASH}, "amount": 1}).status == 404
    assert h.journal_len() == before
    assert h.get(f"/orders/{order['id']}").json()["state"] == "open"


def test_paying_needs_payments_w(h: Harness) -> None:
    order = h.create_order()
    writer = h.restricted_token("ORDERS_W")
    denied = h.api.post(
        h.path(f"/orders/{order['id']}/payments"), {"tender": {"id": TENDER_CASH}, "amount": 1}, headers=writer
    )
    assert denied.status == 401
    assert denied.json()["message"] == "401 Unauthorized"
