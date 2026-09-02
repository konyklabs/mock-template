"""Orders v2: the prices contract, create, read, list, selections, void, discounts."""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from vendorfake.toast.seed import constants as c
from vendorfake.toast.surface.orders import GUID_MALFORMED, ONLY_OTHER_VOIDABLE, ORDER_NOT_FOUND, VOIDED_IMMUTABLE

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
REST_DATE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}\+0000")

SOUP = {"item": {"guid": c.ITEM_SOUP_GUID, "entityType": "MenuItem"}, "quantity": 1}
DINE_IN = {"guid": c.DINING_OPTION_DINE_IN_GUID, "entityType": "DiningOption"}


def order_body(*selections: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "entityType": "Order",
        "diningOption": DINE_IN,
        "checks": [{"entityType": "Check", "selections": list(selections or (SOUP,))}],
        **extra,
    }


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def drawn(h: Harness) -> int:
    return h.unit.context.vendor.ids.draw_count  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# /prices and /orders: the documented example, and the contract between them.
# ---------------------------------------------------------------------------


def test_prices_reproduces_the_documented_example_with_null_guids_and_persists_nothing(h: Harness) -> None:
    """8.99 at 0.0625: tax 0.56, total 9.55; guid null on order, check and
    selection; no journal entry, no id drawn (apiOrderPrices.html)."""
    before, ids = h.journal_len(), drawn(h)
    response = h.post("/orders/v2/prices", order_body())
    assert response.status == 200, response.text
    body = response.json()
    assert body["guid"] is None and body["entityType"] == "Order"
    (check,) = body["checks"]
    assert check["guid"] is None
    assert (check["amount"], check["taxAmount"], check["totalAmount"]) == (8.99, 0.56, 9.55)
    (selection,) = check["selections"]
    assert selection["guid"] is None
    assert (selection["preDiscountPrice"], selection["price"], selection["tax"]) == (8.99, 8.99, 0.56)
    assert selection["appliedTaxes"][0]["rate"] == 0.0625 and selection["appliedTaxes"][0]["taxAmount"] == 0.56
    assert selection["appliedTaxes"][0]["taxRate"] == {"guid": c.TAX_RATE_DEFAULT_GUID, "entityType": "TaxRate"}
    assert h.journal_len() == before and drawn(h) == ids


def test_create_assigns_guids_computes_the_same_amounts_and_journals_once(h: Harness) -> None:
    before = h.journal_len()
    priced = h.post("/orders/v2/prices", order_body()).json()
    response = h.post("/orders/v2/orders", order_body(externalId="ext-1"))
    assert response.status == 200, response.text
    order = response.json()
    assert UUID.fullmatch(order["guid"])
    check = order["checks"][0]
    assert UUID.fullmatch(check["guid"]) and UUID.fullmatch(check["selections"][0]["guid"])
    assert order["checks"][0]["totalAmount"] == priced["checks"][0]["totalAmount"] == 9.55
    assert order["externalId"] == "ext-1"
    assert order["source"] == "API" and order["approvalStatus"] == "APPROVED"
    assert order["guestOrderStatus"] == "RECEIVED" and order["voided"] is False
    assert check["paymentStatus"] == "OPEN" and check["payments"] == []
    assert REST_DATE.fullmatch(order["openedDate"]) and REST_DATE.fullmatch(order["modifiedDate"])
    assert isinstance(order["businessDate"], int) and 20260000 < order["businessDate"] < 21000000
    assert order["displayNumber"] == "2"  # the seeded order is 1
    assert order["diningOption"] == {**DINE_IN, "externalId": None}
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [(e["collection"], e["op"], e["meta"]["operation_id"]) for e in entries] == [
        ("orders", "insert", "OrderCreate")
    ]
    fetched = h.get(f"/orders/v2/orders/{order['guid']}")
    assert fetched.status == 200 and fetched.json() == order


def test_two_units_create_the_same_guids_for_the_same_traffic() -> None:
    minted = []
    for _ in range(2):
        for h in harness():
            order = h.post("/orders/v2/orders", order_body()).json()
            minted.append((order["guid"], order["checks"][0]["guid"], order["checks"][0]["selections"][0]["guid"]))
    assert minted[0] == minted[1]


def test_modifiers_and_pre_modifiers_are_priced_as_selections_of_their_own(h: Harness) -> None:
    """Burger 12.50 with a Side Salad (1.50) EXTRA (x2 = 3.00): the modifier
    carries its own price and tax; the check sums both (JUDGMENT rules)."""
    burger = {
        "item": {"guid": c.ITEM_BURGER_GUID},
        "quantity": 2,
        "modifiers": [
            {
                "item": {"guid": c.MODIFIER_OPTION_SALAD_GUID},
                "quantity": 1,
                "preModifier": {"guid": c.PRE_MODIFIER_EXTRA_GUID},
            },
            {
                "item": {"guid": c.MODIFIER_OPTION_FRIES_GUID},
                "quantity": 1,
                "preModifier": {"guid": c.PRE_MODIFIER_NO_GUID},
            },
        ],
    }
    check = h.post("/orders/v2/prices", order_body(burger)).json()["checks"][0]
    (selection,) = check["selections"]
    assert selection["price"] == 25.0 and selection["quantity"] == 2
    salad, fries = selection["modifiers"]
    assert salad["price"] == 6.0  # 1.50 x EXTRA(2) x parent quantity 2
    assert salad["preModifier"] == {"guid": c.PRE_MODIFIER_EXTRA_GUID, "entityType": "PreModifier"}
    assert fries["price"] == 0.0
    assert salad["tax"] == 0.38  # 600 x 0.0625 = 37.5 -> 38
    assert check["amount"] == 31.0
    assert check["taxAmount"] == round(1.56 + 0.38, 2)  # 2500 x .0625 = 156.25 -> 156
    assert check["totalAmount"] == round(31.0 + 1.94, 2)


def test_an_option_the_item_does_not_offer_is_refused(h: Harness) -> None:
    soup_with_salad = {**SOUP, "modifiers": [{"item": {"guid": c.MODIFIER_OPTION_SALAD_GUID}, "quantity": 1}]}
    response = h.post("/orders/v2/prices", order_body(soup_with_salad))
    assert response.status == 400
    assert response.json()["unit_error"]["field"] == "checks[0].selections[0].modifiers[0].item.guid"


@pytest.mark.parametrize(
    ("body", "status", "field"),
    [
        (
            order_body({"item": {"guid": "3c9a1f00-0000-4000-8000-00000000c2ff"}, "quantity": 1}),
            404,
            "checks[0].selections[0].item.guid",
        ),
        (order_body(diningOption={"guid": "5d0e2b11-0000-4000-8000-00000000d0ff"}), 404, "diningOption.guid"),
        (order_body(table={"guid": "5d0e2b11-0000-4000-8000-00000000d5ff"}), 404, "table.guid"),
        ({**order_body(), "checks": []}, 400, "checks"),
        ({"checks": [{"selections": [SOUP]}]}, 400, "diningOption"),
        (order_body({"item": {"guid": c.ITEM_SOUP_GUID}, "quantity": 0}), 400, "checks[0].selections[0].quantity"),
        (order_body({"item": {"guid": c.ITEM_SOUP_GUID}}), 400, "checks[0].selections[0].quantity"),
        (order_body(SOUP, {**SOUP, "externalId": "dup"}, externalId="dup"), 400, "externalId"),
        (order_body(externalId="seed-open-order"), 400, "externalId"),
    ],
)
def test_a_refused_create_journals_nothing_and_draws_no_id(
    h: Harness, body: dict[str, Any], status: int, field: str
) -> None:
    before, ids = h.journal_len(), drawn(h)
    response = h.post("/orders/v2/orders", body)
    assert response.status == status, response.text
    assert response.json()["unit_error"]["field"] == field
    assert h.journal_len() == before and drawn(h) == ids


def test_the_documented_get_refusals(h: Harness) -> None:
    malformed = h.get("/orders/v2/orders/not-a-guid")
    assert malformed.status == 400 and malformed.json()["message"] == GUID_MALFORMED
    unknown = h.get("/orders/v2/orders/9a7b6c5d-0000-4000-8000-0000000000ff")
    assert unknown.status == 404 and unknown.json()["message"] == ORDER_NOT_FOUND


def test_customer_and_delivery_info_need_their_documented_scopes(h: Harness) -> None:
    body = order_body(
        deliveryInfo={"address1": "1 Main St", "city": "Springfield", "notes": "ring twice"},
    )
    body["checks"][0]["customer"] = {"firstName": "Ada", "lastName": "Lovelace", "phone": "2175550199"}
    body["diningOption"] = {"guid": c.DINING_OPTION_TAKE_OUT_GUID}
    order = h.post("/orders/v2/orders", body).json()
    assert order["checks"][0]["customer"]["firstName"] == "Ada"
    assert order["deliveryInfo"]["notes"] == "ring twice"
    narrow = h.api.get(f"/orders/v2/orders/{order['guid']}", headers=h.read_auth).json()
    assert "customer" not in narrow["checks"][0]
    assert "deliveryInfo" not in narrow


def test_orders_are_visible_only_to_the_client_that_submitted_them(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    other = h.unit.context.store.collection("tokens").get("b1a2c3d4-0000-4000-8000-0000000000f1")
    assert other is not None
    h.unit.context.store.collection("tokens").insert(
        {**other, "id": "other-client", "access_token": "other-client-token", "client_id": "someone-else"},
        {"seed": True},
    )
    headers = {**h.auth, "authorization": "Bearer other-client-token"}
    assert h.api.get(f"/orders/v2/orders/{order['guid']}", headers=headers).status == 404
    assert (
        h.api.get("/orders/v2/orders", query={"businessDate": str(order["businessDate"])}, headers=headers).json() == []
    )


# ---------------------------------------------------------------------------
# Lists.
# ---------------------------------------------------------------------------


def test_orders_bulk_filters_by_business_date_or_modified_range_and_pages_from_zero(h: Harness) -> None:
    created = [h.post("/orders/v2/orders", order_body()).json() for _ in range(3)]
    business = str(created[0]["businessDate"])
    listed = h.get("/orders/v2/ordersBulk", query={"businessDate": business}).json()
    assert [o["guid"] for o in listed] == [o["guid"] for o in created]
    assert listed[0]["checks"][0]["totalAmount"] == 9.55
    first = h.get("/orders/v2/ordersBulk", query={"businessDate": business, "page": "0", "pageSize": "2"}).json()
    second = h.get("/orders/v2/ordersBulk", query={"businessDate": business, "page": "1", "pageSize": "2"}).json()
    assert [o["guid"] for o in first] == [created[0]["guid"], created[1]["guid"]]
    assert [o["guid"] for o in second] == [created[2]["guid"]]
    seeded = h.get("/orders/v2/ordersBulk", query={"businessDate": str(c.SEED_ORDER_BUSINESS_DATE)}).json()
    assert [o["guid"] for o in seeded] == [c.SEED_ORDER_GUID]
    ranged = h.get(
        "/orders/v2/ordersBulk",
        query={"startDate": "2025-08-21T14:21:42.000+0000", "endDate": "2025-08-21T14:21:42.001+0000"},
    ).json()
    assert [o["guid"] for o in ranged] == [c.SEED_ORDER_GUID]
    exclusive_end = h.get(
        "/orders/v2/ordersBulk",
        query={"startDate": "2025-08-21T14:00:00.000+0000", "endDate": "2025-08-21T14:21:42.000+0000"},
    ).json()
    assert exclusive_end == []
    deprecated = h.get("/orders/v2/orders", query={"businessDate": business}).json()
    assert deprecated == [o["guid"] for o in created]


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ({}, "startDate"),
        ({"startDate": "2025-08-21T14:00:00.000+0000"}, "startDate"),
        ({"businessDate": "20250821", "startDate": "2025-08-21T14:00:00.000+0000"}, "businessDate"),
        ({"businessDate": "2025-08-21"}, "businessDate"),
        ({"startDate": "2015-01-01T00:00:00.000+0000", "endDate": "2016-01-01T00:00:00.000+0000"}, "startDate"),
        ({"businessDate": "20250821", "pageSize": "101"}, "pageSize"),
        ({"businessDate": "20250821", "page": "-1"}, "page"),
    ],
)
def test_orders_bulk_refuses_bad_parameters_naming_them(h: Harness, query: dict[str, str], field: str) -> None:
    response = h.get("/orders/v2/ordersBulk", query=query)
    assert response.status == 400, response.text
    assert response.json()["unit_error"]["field"] == field


# ---------------------------------------------------------------------------
# Selections, void, discounts, delivery info.
# ---------------------------------------------------------------------------


def test_appending_selections_recomputes_the_check_and_journals_one_update(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    check_guid = order["checks"][0]["guid"]
    before = h.journal_len()
    response = h.post(
        f"/orders/v2/orders/{order['guid']}/checks/{check_guid}/selections",
        [{"item": {"guid": c.ITEM_LEMONADE_GUID}, "quantity": 1}],
    )
    assert response.status == 200, response.text
    check = response.json()["checks"][0]
    assert [s["displayName"] for s in check["selections"]] == ["Tomato Soup", "Lemonade"]
    assert check["amount"] == 12.24 and check["taxAmount"] == 0.76 and check["totalAmount"] == 13.0
    entries = h.api.get("/__unit/journal").json()["entries"][before:]
    assert [(e["op"], e["meta"]["operation_id"]) for e in entries] == [("update", "CheckSelectionsPost")]
    unknown = h.post(
        f"/orders/v2/orders/{order['guid']}/checks/{check_guid}/selections", [{"item": {"guid": "nope"}, "quantity": 1}]
    )
    assert unknown.status == 404
    wrong_check = h.post(f"/orders/v2/orders/{order['guid']}/checks/{order['guid']}/selections", [SOUP])
    assert wrong_check.status == 404 and wrong_check.json()["unit_error"]["field"] == "checkGuid"


def test_void_follows_the_documented_walkthrough(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid = order["guid"]
    response = h.post(
        f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}}
    )
    assert response.status == 200, response.text
    voided = response.json()
    assert voided["voided"] is True and REST_DATE.fullmatch(voided["voidDate"])
    assert isinstance(voided["voidBusinessDate"], int)
    assert voided["guestOrderStatus"] == "VOIDED"
    assert voided["checks"][0]["paymentStatus"] == "VOIDED"
    assert voided["checks"][0]["selections"][0]["voided"] is True
    assert REST_DATE.fullmatch(voided["checks"][0]["selections"][0]["voidDate"])
    again = h.post(f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}})
    assert again.status == 400 and again.json()["message"] == VOIDED_IMMUTABLE
    assert again.headers["x-unit-error"] == "invalid_transition"
    appended = h.post(f"/orders/v2/orders/{guid}/checks/{order['checks'][0]['guid']}/selections", [SOUP])
    assert appended.status == 400 and appended.json()["message"] == VOIDED_IMMUTABLE


def test_void_needs_both_void_all_flags_and_only_other_payments(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check_guid = order["guid"], order["checks"][0]["guid"]
    half = h.post(f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": False}})
    assert half.status == 400 and half.json()["message"] == "Each voidAll value must be set to true."
    assert half.json()["unit_error"]["field"] == "payments.voidAll"
    paid = h.post(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/payments",
        [{"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 9.55, "tipAmount": 0}],
    )
    assert paid.status == 200, paid.text
    refused = h.post(f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}})
    assert refused.status == 400 and refused.json()["message"] == ONLY_OTHER_VOIDABLE
    assert refused.json()["status"] == 400


def test_voiding_an_other_paid_order_voids_its_payments_too(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check_guid = order["guid"], order["checks"][0]["guid"]
    paid = h.post(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/payments",
        [
            {
                "type": "OTHER",
                "amount": "9.55",
                "tipAmount": "1.00",
                "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID},
            }
        ],
    ).json()
    assert paid["checks"][0]["paymentStatus"] == "CLOSED"
    voided = h.post(
        f"/orders/v2/orders/{guid}/void", {"selections": {"voidAll": True}, "payments": {"voidAll": True}}
    ).json()
    (payment,) = voided["checks"][0]["payments"]
    assert payment["paymentStatus"] == "VOIDED" and isinstance(payment["voidInfo"]["voidBusinessDate"], int)
    listed = h.get(
        "/orders/v2/payments", query={"voidBusinessDate": str(payment["voidInfo"]["voidBusinessDate"])}
    ).json()
    assert listed == [payment["guid"]]


def test_item_discount_reproduces_the_documented_applied_discount(h: Harness) -> None:
    """'Enjoy more soup.' at 100% on the 8.99 soup: discountAmount 8.99,
    price 0, tax 0 (apiDiscountingOrders.html)."""
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]
    selection_guid = check["selections"][0]["guid"]
    response = h.post(
        f"/orders/v2/orders/{guid}/checks/{check['guid']}/selections/{selection_guid}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}, "appliedPromoCode": "SOUP"}],
    )
    assert response.status == 200, response.text
    selection = response.json()["checks"][0]["selections"][0]
    (applied,) = selection["appliedDiscounts"]
    assert list(applied) == [
        "guid",
        "entityType",
        "externalId",
        "approver",
        "processingState",
        "loyaltyDetails",
        "name",
        "comboItems",
        "discountAmount",
        "discount",
        "triggers",
        "appliedPromoCode",
    ]
    assert applied["entityType"] == "AppliedCustomDiscount" and applied["name"] == "Enjoy more soup."
    assert applied["discountAmount"] == 8.99 and UUID.fullmatch(applied["guid"])
    assert applied["discount"] == {"guid": c.DISCOUNT_SOUP_GUID, "entityType": "Discount"}
    assert applied["triggers"] == [
        {"selection": {"guid": selection_guid, "entityType": "MenuItemSelection"}, "quantity": 1}
    ]
    assert applied["appliedPromoCode"] == "SOUP"
    assert (selection["preDiscountPrice"], selection["price"], selection["tax"]) == (8.99, 0.0, 0.0)
    assert response.json()["checks"][0]["totalAmount"] == 0.0
    missing_code = h.post(
        f"/orders/v2/orders/{guid}/checks/{check['guid']}/selections/{selection_guid}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}}],
    )
    assert missing_code.status == 400 and missing_code.json()["unit_error"]["field"] == "[0].appliedPromoCode"


def test_check_discount_takes_check_type_discounts_only_and_reprices_the_check(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check_guid = order["guid"], order["checks"][0]["guid"]
    wrong_level = h.post(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/appliedDiscounts", [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}}]
    )
    assert wrong_level.status == 400
    response = h.post(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_REGULARS_GUID}}],
    )
    assert response.status == 200, response.text
    check = response.json()["checks"][0]
    assert check["appliedDiscounts"][0]["discountAmount"] == 0.9  # 10% of 8.99 -> 0.899 -> 0.90
    assert check["amount"] == 8.09 and check["taxAmount"] == 0.56 and check["totalAmount"] == 8.65
    unknown = h.post(f"/orders/v2/orders/{guid}/checks/{check_guid}/appliedDiscounts", [{"discount": {"guid": "nope"}}])
    assert unknown.status == 404


def test_applicable_discounts_answers_the_documented_shape(h: Harness) -> None:
    response = h.post("/orders/v2/applicableDiscounts", order_body({**SOUP, "externalId": "s1"}))
    assert response.status == 200, response.text
    by_guid = {row["discount"]["guid"]: row for row in response.json()}
    assert set(by_guid) == {c.DISCOUNT_SOUP_GUID, c.DISCOUNT_REGULARS_GUID}
    soup = by_guid[c.DISCOUNT_SOUP_GUID]
    assert soup["discount"] == {"guid": c.DISCOUNT_SOUP_GUID, "entityType": "Discount"}
    assert soup["applicableChecks"] == []
    assert soup["applicableSelections"] == [{"guid": None, "entityType": "SELECTION", "externalId": "s1"}]
    regulars = by_guid[c.DISCOUNT_REGULARS_GUID]
    assert regulars["applicableSelections"] == [] and len(regulars["applicableChecks"]) == 1


def test_delivery_info_patch_merges_and_journals(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body(deliveryInfo={"address1": "1 Main St"})).json()
    response = h.patch(
        f"/orders/v2/orders/{order['guid']}/deliveryInfo", {"notes": "leave at door", "deliveryState": "IN_PROGRESS"}
    )
    assert response.status == 200, response.text
    assert response.json()["deliveryInfo"] == {
        "address1": "1 Main St",
        "notes": "leave at door",
        "deliveryState": "IN_PROGRESS",
    }


def test_the_seeded_order_was_priced_by_the_same_builder(h: Harness) -> None:
    order = h.get(f"/orders/v2/orders/{c.SEED_ORDER_GUID}").json()
    assert order["businessDate"] == c.SEED_ORDER_BUSINESS_DATE
    assert order["openedDate"] == "2025-08-21T14:21:42.000+0000"
    check = order["checks"][0]
    assert check["guid"] == c.SEED_ORDER_CHECK_GUID and check["selections"][0]["guid"] == c.SEED_ORDER_SELECTION_GUID
    assert (check["amount"], check["taxAmount"], check["totalAmount"]) == (3.25, 0.2, 3.45)
    assert order["table"] == {"guid": c.TABLE_1_GUID, "entityType": "Table"}
    assert order["serviceArea"] == {"guid": c.SERVICE_AREA_GUID, "entityType": "ServiceArea"}
    assert order["displayNumber"] == "1"


def test_the_restaurant_header_and_scopes_are_required_on_every_order_route(h: Harness) -> None:
    assert h.api.post("/orders/v2/orders", order_body(), headers=h.bearer_only).status == 400
    assert h.api.post("/orders/v2/orders", order_body(), headers=h.read_auth).status == 403
    assert h.api.post("/orders/v2/prices", order_body(), headers=h.read_auth).status == 403  # documented on /prices
    assert h.api.get(f"/orders/v2/orders/{c.SEED_ORDER_GUID}", headers=h.read_auth).status == 200
