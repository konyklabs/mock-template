"""Payments: OTHER and pre-authorised CREDIT, the 10025 code, tips, the reads."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import order_body
from vendorfake.toast.seed import constants as c
from vendorfake.toast.surface.payments import CREDIT_NOT_AUTHORIZED, PAYMENT_AMOUNT_EMPTY, UNSUPPORTED_PAYMENT_TYPE

OTHER = {"type": "OTHER", "amount": 9.55, "tipAmount": 0, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def created(h: Harness) -> tuple[str, str]:
    order = h.post("/orders/v2/orders", order_body()).json()
    return order["guid"], order["checks"][0]["guid"]


def pay(h: Harness, guid: str, check: str, *payments: dict[str, Any]) -> Any:
    return h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", list(payments))


def test_an_other_payment_pays_the_check_and_journals_under_one_operation(h: Harness) -> None:
    guid, check_guid = created(h)
    before = h.journal_len()
    response = pay(h, guid, check_guid, {**OTHER, "amountTendered": 10.0})
    assert response.status == 200, response.text
    order = response.json()
    check = order["checks"][0]
    # CLOSED, not PAID: an OTHER payment leaves no tip to adjust (documented; konyklabs/roadmap#56).
    assert check["paymentStatus"] == "CLOSED"
    assert check["paidDate"] and order["paidDate"]
    (payment,) = check["payments"]
    assert payment["type"] == "OTHER" and payment["amount"] == 9.55 and payment["tipAmount"] == 0.0
    assert payment["amountTendered"] == 10.0
    assert payment["otherPayment"] == {"guid": c.ALT_PAYMENT_EXTERNAL_GUID, "entityType": "AlternatePaymentType"}
    assert payment["paymentStatus"] == "CAPTURED" and payment["refundStatus"] == "NONE"
    assert (
        payment["entityType"] == "OrderPayment" and payment["checkGuid"] == check_guid and payment["orderGuid"] == guid
    )
    assert isinstance(payment["paidBusinessDate"], int)
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [(e["collection"], e["op"], e["meta"]["operation_id"]) for e in entries] == [
        ("payments", "insert", "CheckPaymentsPost"),
        ("orders", "update", "CheckPaymentsPost"),
    ]
    assert h.get(f"/orders/v2/payments/{payment['guid']}").json() == payment
    assert h.get("/orders/v2/payments", query={"paidBusinessDate": str(payment["paidBusinessDate"])}).json() == [
        payment["guid"]
    ]


def test_two_partial_payments_cover_the_check_and_a_third_is_refused(h: Harness) -> None:
    """The documented add-payments example carries two payments on one check."""
    guid, check_guid = created(h)
    first = pay(h, guid, check_guid, {**OTHER, "amount": 5.0, "tipAmount": 1.0}).json()["checks"][0]
    assert first["paymentStatus"] == "OPEN" and len(first["payments"]) == 1
    second = pay(h, guid, check_guid, {**OTHER, "amount": 4.55}).json()["checks"][0]
    assert second["paymentStatus"] == "CLOSED" and len(second["payments"]) == 2
    third = pay(h, guid, check_guid, OTHER)
    assert third.status == 400 and "CLOSED" in third.json()["message"]


def test_an_empty_amount_is_the_one_documented_code(h: Harness) -> None:
    guid, check_guid = created(h)
    before = h.journal_len()
    for body in (
        {"type": "OTHER", "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}},
        {"type": "OTHER", "amount": ""},
    ):
        response = pay(h, guid, check_guid, body)
        assert response.status == 400, response.text
        assert response.json()["code"] == 10025
        assert response.json()["message"] == PAYMENT_AMOUNT_EMPTY
    assert h.journal_len() == before


@pytest.mark.parametrize(
    ("payment", "status", "message"),
    [
        ({"type": "CASH", "amount": 9.55}, 400, UNSUPPORTED_PAYMENT_TYPE),
        ({"type": "GIFTCARD", "amount": 9.55}, 400, UNSUPPORTED_PAYMENT_TYPE),
        ({"type": "OTHER", "amount": 9.55}, 400, "requires otherPayment.guid"),
        ({"type": "OTHER", "amount": 9.55, "otherPayment": {"guid": "nope"}}, 404, "was not found"),
        ({"type": "CREDIT", "amount": 9.55}, 400, CREDIT_NOT_AUTHORIZED),
        (
            {"type": "CREDIT", "amount": 9.55, "guid": "7c65cc16-0000-4000-8000-0000000000ff"},
            400,
            CREDIT_NOT_AUTHORIZED,
        ),
        ({"type": "CREDIT", "amount": 60.0, "guid": c.CREDIT_AUTHORIZATION_GUID}, 400, "exceeds the authorized amount"),
        ({"type": "OTHER", "amount": -1, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}, 400, "negative"),
    ],
)
def test_refused_payments_name_the_documented_reason_and_write_nothing(
    h: Harness, payment: dict[str, Any], status: int, message: str
) -> None:
    guid, check_guid = created(h)
    before = h.journal_len()
    ids = h.unit.context.vendor.ids.draw_count  # type: ignore[attr-defined]
    response = pay(h, guid, check_guid, payment)
    assert response.status == status, response.text
    assert message in response.json()["message"]
    assert h.journal_len() == before
    assert h.unit.context.vendor.ids.draw_count == ids  # type: ignore[attr-defined]


def test_a_credit_payment_takes_the_seeded_authorisation_and_cannot_be_captured_twice(h: Harness) -> None:
    guid, check_guid = created(h)
    response = pay(
        h,
        guid,
        check_guid,
        {"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": "9.55", "tipAmount": "2.00"},
    )
    assert response.status == 200, response.text
    (payment,) = response.json()["checks"][0]["payments"]
    assert payment["guid"] == c.CREDIT_AUTHORIZATION_GUID
    assert (
        payment["cardType"] == "VISA" and payment["last4Digits"] == "4242" and payment["cardEntryMode"] == "PRE_AUTHED"
    )
    assert payment["tipAmount"] == 2.0
    other_guid, other_check = created(h)
    again = pay(h, other_guid, other_check, {"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 1.0})
    assert again.status == 400 and "already captured" in again.json()["message"]


def test_payments_can_ride_along_on_create(h: Harness) -> None:
    body = order_body()
    body["checks"][0]["payments"] = [OTHER]
    before = h.journal_len()
    response = h.post("/orders/v2/orders", body)
    assert response.status == 200, response.text
    assert response.json()["checks"][0]["paymentStatus"] == "CLOSED"
    assert response.json()["paidDate"]
    ops = [
        (e["collection"], e["op"], e["meta"]["operation_id"])
        for e in h.api.get("/__unit/journal").json()["entries"][before:]
    ]
    assert ops == [
        ("orders", "insert", "OrderCreate"),
        ("payments", "insert", "OrderCreate"),
        ("orders", "update", "OrderCreate"),
    ]
    bad = order_body()
    bad["checks"][0]["payments"] = [{"type": "CASH", "amount": 9.55}]
    refused = h.post("/orders/v2/orders", bad)
    assert refused.status == 400 and refused.json()["unit_error"]["field"] == "checks[0].payments[0].type"
    assert h.journal_len() == before + 3


def test_the_tip_patch_takes_tip_amount_only_and_answers_the_payment(h: Harness) -> None:
    guid, check_guid = created(h)
    payment = pay(h, guid, check_guid, OTHER).json()["checks"][0]["payments"][0]
    response = h.patch(f"/orders/v2/orders/{guid}/checks/{check_guid}/payments/{payment['guid']}", {"tipAmount": 15})
    assert response.status == 200, response.text
    assert response.json()["guid"] == payment["guid"] and response.json()["tipAmount"] == 15.0
    assert h.get(f"/orders/v2/orders/{guid}").json()["checks"][0]["payments"][0]["tipAmount"] == 15.0
    missing = h.patch(f"/orders/v2/orders/{guid}/checks/{check_guid}/payments/{payment['guid']}", {})
    assert missing.status == 400 and missing.json()["unit_error"]["field"] == "tipAmount"
    unknown = h.patch(f"/orders/v2/orders/{guid}/checks/{check_guid}/payments/{guid}", {"tipAmount": 1})
    assert unknown.status == 404


def test_the_payments_list_needs_exactly_one_business_date_parameter(h: Harness) -> None:
    none = h.get("/orders/v2/payments")
    both = h.get("/orders/v2/payments", query={"paidBusinessDate": "20250821", "voidBusinessDate": "20250821"})
    assert none.status == 400 and both.status == 400
    assert h.get("/orders/v2/payments", query={"refundBusinessDate": "20250821"}).json() == []
    assert h.get("/orders/v2/payments", query={"paidBusinessDate": "2025-08-21"}).status == 400
    assert h.get("/orders/v2/payments/not-a-guid").status == 400
    assert h.get("/orders/v2/payments/7c65cc16-0000-4000-8000-0000000000ff").status == 404


def test_payment_routes_need_the_payments_scope_and_the_restaurant_header(h: Harness) -> None:
    guid, check_guid = created(h)
    path = f"/orders/v2/orders/{guid}/checks/{check_guid}/payments"
    assert h.api.post(path, [OTHER], headers=h.read_auth).status == 403
    assert h.api.post(path, [OTHER], headers=h.restricted_token("orders:write", "orders:read")).status == 403
    assert h.api.post(path, [OTHER], headers=h.bearer_only).status == 400


def test_a_credit_payment_is_paid_until_its_tip_is_adjusted_then_closed(h: Harness) -> None:
    """The Check schema's own value descriptions: PAID is "a credit card
    payment was applied, but the tip has not been adjusted"; CLOSED is "there
    is no remaining amount due". An OTHER payment closes the check outright
    (the payment walkthrough's example); a CREDIT one waits for its tip.
    Found by the fidelity corpus, konyklabs/roadmap#56."""
    guid, check_guid = created(h)
    paid = pay(h, guid, check_guid, {"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 9.55})
    assert paid.status == 200
    assert paid.json()["checks"][0]["paymentStatus"] == "PAID"
    tipped = h.patch(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/payments/{c.CREDIT_AUTHORIZATION_GUID}", {"tipAmount": 2.0}
    )
    assert tipped.status == 200 and tipped.json()["tipAmount"] == 2.0
    after = h.get(f"/orders/v2/orders/{guid}").json()
    assert after["checks"][0]["paymentStatus"] == "CLOSED"
