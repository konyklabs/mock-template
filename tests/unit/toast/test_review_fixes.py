"""The konyklabs/roadmap#39 review findings, pinned so none can come back.

B1: a payments array is validated as a whole against an accumulating batch,
so a refusal anywhere -- above all two CREDIT captures of one authorisation
in one array -- leaves the journal, the store and the id streams untouched
and never reaches the store-level collision the review demonstrated.
B2: a PAID check refuses discounts for the reason the selection guard states.
F4: a payment whose earlier array-mates already cover the check is refused.
F5: a taxExempt check's selections agree with it: tax 0, no appliedTaxes.
F6: a seeded selection's dangling preModifier is refused at parse, by path.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.toast.harness import Harness, harness
from tests.unit.toast.test_surface_orders import SOUP, drawn, order_body
from vendorfake.toast.seed import constants as c
from vendorfake.toast.seed.document import parse_seed_document

CREDIT = {"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 9.55}
#: Two of these do NOT cover the 9.55 check, so the double-capture guard --
#: not the coverage guard -- is what a pair of them exercises.
PARTIAL_CREDIT = {"type": "CREDIT", "guid": c.CREDIT_AUTHORIZATION_GUID, "amount": 4.0}
OTHER = {"type": "OTHER", "amount": 9.55, "otherPayment": {"guid": c.ALT_PAYMENT_EXTERNAL_GUID}}


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


def _sizes(h: Harness) -> tuple[int, int, int]:
    store = h.unit.context.store
    return store.collection("orders").size, store.collection("payments").size, h.journal_len()


def test_b1_two_credit_captures_of_one_authorisation_in_one_create_write_nothing(h: Harness) -> None:
    """The review's exact repro: an order whose check carries the same
    authorisation twice. Before the batch, the dry run passed both, the
    second insert collided (409 naming the internal collection), the order
    and one payment persisted, the journal moved by two and the authorisation
    was burned. Now: one 400, nothing written, and the authorisation still
    captures for a legitimate order afterwards."""
    body = order_body()
    body["checks"][0]["payments"] = [dict(PARTIAL_CREDIT), dict(PARTIAL_CREDIT)]
    before, ids = _sizes(h), drawn(h)
    refused = h.post("/orders/v2/orders", body)
    assert refused.status == 400, refused.text
    assert "already captured" in refused.json()["message"]
    assert refused.json()["unit_error"]["field"] == "checks[0].payments[1].guid"
    assert _sizes(h) == before and drawn(h) == ids
    # The authorisation was not burned: a clean capture still works.
    clean = order_body()
    clean["checks"][0]["payments"] = [dict(CREDIT)]
    accepted = h.post("/orders/v2/orders", clean)
    assert accepted.status == 200, accepted.text
    assert accepted.json()["checks"][0]["payments"][0]["guid"] == c.CREDIT_AUTHORIZATION_GUID


def test_b1_the_same_double_capture_through_the_payments_route_writes_nothing(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]["guid"]
    before, ids = _sizes(h), drawn(h)
    refused = h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", [dict(PARTIAL_CREDIT), dict(PARTIAL_CREDIT)])
    assert refused.status == 400 and "already captured" in refused.json()["message"]
    assert refused.json()["unit_error"]["field"] == "[1].guid"
    assert _sizes(h) == before and drawn(h) == ids


def test_f4_a_payment_already_covered_by_its_array_mates_is_refused_whole(h: Harness) -> None:
    """Two payments each covering the 9.55 check must not both land (19.10 on
    a 9.55 check); the documented two-PARTIAL-payments example stays legal."""
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]["guid"]
    before, ids = _sizes(h), drawn(h)
    refused = h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", [dict(OTHER), dict(OTHER)])
    assert refused.status == 400, refused.text
    assert "already covered by the preceding payments" in refused.json()["message"]
    assert refused.json()["unit_error"]["field"] == "[1].amount"
    assert _sizes(h) == before and drawn(h) == ids
    partials = [{**OTHER, "amount": 5.0}, {**OTHER, "amount": 4.55}]
    accepted = h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", partials)
    assert accepted.status == 200, accepted.text
    assert accepted.json()["checks"][0]["paymentStatus"] == "PAID"


def test_f4_the_same_rule_holds_on_create(h: Harness) -> None:
    body = order_body()
    body["checks"][0]["payments"] = [dict(OTHER), dict(OTHER)]
    before, ids = _sizes(h), drawn(h)
    refused = h.post("/orders/v2/orders", body)
    assert refused.status == 400 and "already covered" in refused.json()["message"]
    assert _sizes(h) == before and drawn(h) == ids


def test_b2_a_paid_check_refuses_discounts_at_both_levels(h: Harness) -> None:
    """A discount on a paid check would drop totalAmount below what was paid
    (8.99/9.55 paid 9.55 -> 8.65 with payments [9.55]); refused like a
    selection on a paid check, and the amounts never move."""
    order = h.post("/orders/v2/orders", order_body()).json()
    guid, check = order["guid"], order["checks"][0]["guid"]
    selection = order["checks"][0]["selections"][0]["guid"]
    assert h.post(f"/orders/v2/orders/{guid}/checks/{check}/payments", [dict(OTHER)]).status == 200
    before = h.journal_len()
    check_level = h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/appliedDiscounts", [{"discount": {"guid": c.DISCOUNT_REGULARS_GUID}}]
    )
    assert check_level.status == 400, check_level.text
    assert "PAID" in check_level.json()["message"] and "discount" in check_level.json()["message"]
    selection_level = h.post(
        f"/orders/v2/orders/{guid}/checks/{check}/selections/{selection}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}, "appliedPromoCode": "SOUP"}],
    )
    assert selection_level.status == 400
    assert h.journal_len() == before
    after = h.get(f"/orders/v2/orders/{guid}").json()["checks"][0]
    assert after["totalAmount"] == 9.55 and after["appliedDiscounts"] == []
    assert sum(p["amount"] for p in after["payments"]) == after["totalAmount"]


def test_f5_a_tax_exempt_check_agrees_with_its_selections(h: Harness) -> None:
    body = order_body()
    body["checks"][0]["taxExempt"] = True
    for path in ("/orders/v2/prices", "/orders/v2/orders"):
        check = h.post(path, body if path.endswith("prices") else copy.deepcopy(body)).json()["checks"][0]
        assert check["taxExempt"] is True
        assert (check["amount"], check["taxAmount"], check["totalAmount"]) == (8.99, 0.0, 8.99)
        (selection,) = check["selections"]
        assert selection["tax"] == 0.0 and selection["appliedTaxes"] == []


def test_f5_the_exemption_survives_a_selection_append_and_a_discount(h: Harness) -> None:
    body = order_body()
    body["checks"][0]["taxExempt"] = True
    order = h.post("/orders/v2/orders", body).json()
    guid, check_guid = order["guid"], order["checks"][0]["guid"]
    appended = h.post(f"/orders/v2/orders/{guid}/checks/{check_guid}/selections", [dict(SOUP)]).json()["checks"][0]
    assert appended["taxAmount"] == 0.0 and all(s["tax"] == 0.0 for s in appended["selections"])
    selection = appended["selections"][0]["guid"]
    discounted = h.post(
        f"/orders/v2/orders/{guid}/checks/{check_guid}/selections/{selection}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}, "appliedPromoCode": "SOUP"}],
    ).json()["checks"][0]
    assert discounted["taxAmount"] == 0.0
    assert all(s["tax"] == 0.0 and s["appliedTaxes"] == [] for s in discounted["selections"])


def test_f7_a_service_charge_reference_resolves_like_every_other_and_free_json_is_refused(h: Harness) -> None:
    """The one place a client could inject free JSON into a projected document
    (finding 7): a dangling serviceCharge.guid now 404s like every other
    reference, an unknown key is a 400 naming it, and what is stored is the
    controlled shape -- config vocabulary plus the caller's chargeAmount."""
    body = order_body()
    body["checks"][0]["appliedServiceCharges"] = [{"serviceCharge": {"guid": "5d0e2b11-0000-4000-8000-0000000000ff"}}]
    before = h.journal_len()
    dangling = h.post("/orders/v2/orders", body)
    assert dangling.status == 404, dangling.text
    assert dangling.json()["unit_error"]["field"] == "checks[0].appliedServiceCharges[0].serviceCharge.guid"
    body["checks"][0]["appliedServiceCharges"] = [
        {"serviceCharge": {"guid": c.SERVICE_CHARGE_GRATUITY_GUID}, "junk": "passthrough"}
    ]
    injected = h.post("/orders/v2/orders", body)
    assert injected.status == 400, injected.text
    assert "junk" in injected.json()["message"]
    assert h.journal_len() == before
    body["checks"][0]["appliedServiceCharges"] = [
        {"serviceCharge": {"guid": c.SERVICE_CHARGE_GRATUITY_GUID}, "chargeAmount": 1.72}
    ]
    accepted = h.post("/orders/v2/orders", body)
    assert accepted.status == 200, accepted.text
    check = accepted.json()["checks"][0]
    (charge,) = check["appliedServiceCharges"]
    assert charge == {
        "entityType": "AppliedServiceCharge",
        "serviceCharge": {"guid": c.SERVICE_CHARGE_GRATUITY_GUID, "entityType": "ServiceCharge"},
        "chargeAmount": 1.72,
        "chargeType": "PERCENT",
        "name": "Gratuity",
        "gratuity": True,
        "taxable": False,
    }
    assert "junk" not in charge
    # Declared: never computed into the amounts.
    assert (check["amount"], check["totalAmount"]) == (8.99, 9.55)


def test_f8_receipt_line_price_follows_an_item_discount(h: Harness) -> None:
    order = h.post("/orders/v2/orders", order_body()).json()
    selection = order["checks"][0]["selections"][0]
    assert selection["receiptLinePrice"] == selection["price"] == 8.99
    discounted = h.post(
        f"/orders/v2/orders/{order['guid']}/checks/{order['checks'][0]['guid']}/selections/{selection['guid']}/appliedDiscounts",
        [{"discount": {"guid": c.DISCOUNT_SOUP_GUID}, "appliedPromoCode": "SOUP"}],
    ).json()["checks"][0]["selections"][0]
    assert discounted["price"] == 0.0
    assert discounted["receiptLinePrice"] == 0.0  # was 8.99 before finding 8


def test_f6_a_dangling_seeded_pre_modifier_is_refused_at_parse_by_path() -> None:
    document: dict[str, Any] = dict(json.loads(c.DEFAULT_SEED_PATH.read_text(encoding="utf-8")))
    broken = copy.deepcopy(document)
    broken["orders"][0]["checks"][0]["selections"][0]["preModifier"] = "3c9a1f00-0000-4000-8000-0000000000ff"
    with pytest.raises(Exception) as caught:
        parse_seed_document(broken)
    info = getattr(caught.value, "info", None)
    assert info is not None and info["path"] == "orders[0].checks[0].selections[0].preModifier"
    nested = copy.deepcopy(document)
    nested["orders"][0]["checks"][0]["selections"][0]["modifiers"] = [
        {
            "guid": "9a7b6c5d-0000-4000-8000-00000000f301",
            "item": "3c9a1f00-0000-4000-8000-00000000c401",
            "preModifier": "nope",
        }
    ]
    with pytest.raises(Exception) as caught:
        parse_seed_document(nested)
    info = getattr(caught.value, "info", None)
    assert info is not None and info["path"] == "orders[0].checks[0].selections[0].modifiers[0].preModifier"
