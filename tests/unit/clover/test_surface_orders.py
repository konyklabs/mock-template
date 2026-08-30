"""The orders surface: client-owned totals, the machine, soft delete, lists,
expansions, line items, the atomic calculators, and the two invariants every
write route must keep (no journal entry on a 4xx; the merchant scope)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.clover.harness import (
    ITEM_BEER,
    ITEM_ESPRESSO,
    MERCHANT_ID,
    Harness,
    harness,
)
from vendorfake.clover.entities import COL, OrderEntity


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


# ---------------------------------------------------------------------------
# POST /orders -- create, with everything client-owned
# ---------------------------------------------------------------------------


def test_the_documented_create_body_round_trips_verbatim(h: Harness) -> None:
    """{"orderType":{"id":...},"currency":"USD","total":1500,"state":"Open"}
    is the documented example; every field comes back as sent, the state's
    casing included (stored verbatim, compared case-insensitively)."""
    response = h.post(
        "/orders", {"orderType": {"id": "KFRPRVCZ73JHM"}, "currency": "USD", "total": 1500, "state": "Open"}
    )
    assert response.status == 200
    order = response.json()
    assert order["orderType"] == {"id": "KFRPRVCZ73JHM"}
    assert order["currency"] == "USD"
    assert order["total"] == 1500
    assert order["state"] == "Open"
    assert order["paymentState"] == "OPEN"
    assert len(order["id"]) == 13
    assert order["createdTime"] == order["modifiedTime"] == order["clientCreatedTime"]
    assert order["createdTime"] > 10**12  # milliseconds, not seconds


def test_create_defaults_are_the_labelled_judgments(h: Harness) -> None:
    """Missing currency -> the merchant's; missing total -> 0; no state ->
    absent (null, 'hidden'); an invalid state -> 400."""
    response = h.post("/orders", {})
    assert response.status == 200
    order = response.json()
    assert order["currency"] == "USD"
    assert order["total"] == 0
    assert "state" not in order
    bad = h.post("/orders", {"state": "shipped"})
    assert bad.status == 400
    assert bad.json()["unit_error"]["field"] == "state"


def test_documented_but_unmodelled_fields_are_tolerated_on_create(h: Harness) -> None:
    response = h.post("/orders", {"total": 1, "isVat": False, "employee": {"id": "E"}, "unpaidBalance": 1})
    assert response.status == 200
    assert "isVat" not in response.json()


# ---------------------------------------------------------------------------
# GET / POST / DELETE /orders/{orderId}
# ---------------------------------------------------------------------------


def test_get_returns_the_order_and_404s_an_unknown_one(h: Harness) -> None:
    order = h.create_order(title="Table 4")
    assert h.get(f"/orders/{order['id']}").json()["title"] == "Table 4"
    missing = h.get("/orders/NOSUCHORDER01")
    assert missing.status == 404
    assert missing.json()["unit_error"]["kind"] == "not_found"


def test_update_is_post_and_sparse(h: Harness) -> None:
    """Only the fields sent change; a null clears; total is client-owned and
    changes only when the client says so."""
    order = h.create_order(title="Table 4", note="rush")
    updated = h.post(f"/orders/{order['id']}", {"title": "Table 5", "note": None})
    assert updated.status == 200
    body = updated.json()
    assert body["title"] == "Table 5"
    assert "note" not in body
    assert body["total"] == 1500  # untouched
    assert body["modifiedTime"] >= body["createdTime"]
    assert h.post(f"/orders/{order['id']}", {"total": 2099}).json()["total"] == 2099


def test_update_moves_state_through_the_machine_case_insensitively(h: Harness) -> None:
    """open -> locked is the one transition; the value is stored as sent
    ("Locked"), and a locked order refuses every further write with a 400."""
    order = h.create_order(state="open")
    same = h.post(f"/orders/{order['id']}", {"state": "OPEN"})  # self-transition on open: legal
    assert same.status == 200
    assert same.json()["state"] == "OPEN"
    locked = h.post(f"/orders/{order['id']}", {"state": "Locked"})
    assert locked.status == 200
    assert locked.json()["state"] == "Locked"
    reopened = h.post(f"/orders/{order['id']}", {"state": "open"})
    assert reopened.status == 400
    assert reopened.json()["unit_error"]["kind"] == "invalid_transition"
    retitled = h.post(f"/orders/{order['id']}", {"title": "x"})
    assert retitled.status == 400  # terminal: no write of any kind
    assert h.post(f"/orders/{order['id']}/line_items", {"price": 100}).status == 400


def test_a_hidden_order_with_no_state_can_be_locked(h: Harness) -> None:
    """JUDGMENT: an absent state (null, 'hidden') reads as open for the
    machine, so it can be locked and cannot be moved anywhere else."""
    hidden = h.post("/orders", {"total": 1}).json()
    assert "state" not in hidden
    assert h.post(f"/orders/{hidden['id']}", {"state": "locked"}).status == 200
    assert h.post(f"/orders/{hidden['id']}", {"state": "open"}).status == 400


def test_delete_is_soft(h: Harness) -> None:
    """deletedTime is set (the documented filter field is the hook); the order
    then 404s and leaves the list -- but still exists in the store."""
    order = h.create_order()
    response = h.delete(f"/orders/{order['id']}")
    assert response.status == 200
    assert response.json()["id"] == order["id"]
    assert response.json()["deletedTime"] > 10**12
    assert h.get(f"/orders/{order['id']}").status == 404
    ids = [element["id"] for element in h.get("/orders").json()["elements"]]
    assert order["id"] not in ids
    stored = OrderEntity.from_entity(h.unit.context.store.collection(COL.orders).require(order["id"]))
    assert stored.is_deleted
    assert h.delete(f"/orders/{order['id']}").status == 404  # already gone


# ---------------------------------------------------------------------------
# GET /orders -- envelope, filter, pagination, expand
# ---------------------------------------------------------------------------


def test_the_list_is_the_elements_envelope_with_self_hrefs(h: Harness) -> None:
    order = h.create_order()
    body = h.get("/orders").json()
    assert set(body) == {"elements"}
    element = body["elements"][0]
    assert element["href"] == f"https://apisandbox.dev.clover.com/v3/merchants/{MERCHANT_ID}/orders/{order['id']}"
    assert element["id"] == order["id"]
    assert element["total"] == 1500


def test_an_empty_list_is_still_an_envelope(h: Harness) -> None:
    assert h.get("/orders").json() == {"elements": []}


def test_pagination_is_overlap_free_and_complete(h: Harness) -> None:
    """The N-3e mutant class: walk more rows than one page holds and check
    the pages are disjoint and their union is the whole list."""
    created = [h.create_order(title=f"t{i}")["id"] for i in range(7)]
    seen: list[str] = []
    offset = 0
    while True:
        page = h.get("/orders", query={"limit": "3", "offset": str(offset)}).json()["elements"]
        if not page:
            break
        seen.extend(element["id"] for element in page)
        offset += 3
    assert len(seen) == len(set(seen)) == 7
    assert seen == created  # insertion order, stable


def test_limit_and_offset_are_validated_and_the_limit_is_clamped(h: Harness) -> None:
    for i in range(3):
        h.create_order(title=f"t{i}")
    assert h.get("/orders", query={"limit": "abc"}).status == 400
    assert h.get("/orders", query={"limit": "0"}).status == 400
    assert h.get("/orders", query={"offset": "-1"}).status == 400
    assert len(h.get("/orders", query={"limit": "5000"}).json()["elements"]) == 3  # clamped, not refused
    assert len(h.get("/orders", query={"offset": "2"}).json()["elements"]) == 1


def test_filters_on_the_documented_fields(h: Harness) -> None:
    a = h.create_order(state="open", total=100, externalReferenceId="POS-1")
    b = h.create_order(state="Locked", total=300, externalReferenceId="POS-2")

    def ids(**query: str) -> list[str]:
        return [e["id"] for e in h.get("/orders", query=query).json()["elements"]]

    assert ids(filter="state=LOCKED") == [b["id"]]  # case-insensitive
    assert ids(filter="total>=300") == [b["id"]]
    assert ids(filter="total<=100") == [a["id"]]
    assert ids(filter="total=300") == [b["id"]]
    assert ids(filter="externalReferenceId=POS-1") == [a["id"]]
    assert ids(filter=f"id={a['id']}") == [a["id"]]
    assert ids(filter=f"createdTime>={a['createdTime']}") == [a["id"], b["id"]]
    assert ids(filter="modifiedTime<=1") == []


def test_unknown_or_malformed_filters_are_400(h: Harness) -> None:
    for bad in ("employee=E", "total>=abc", "state>=open", "nonsense"):
        response = h.get("/orders", query={"filter": bad})
        assert response.status == 400, bad
        assert response.json()["unit_error"]["field"] == "filter"


def test_expansions_govern_the_nested_collections(h: Harness) -> None:
    """Unexpanded, the nested collections are absent; expanded, present;
    dotted one level for line-item discounts; more than three -> 400."""
    order = h.post(
        "/atomic_order/orders",
        {
            "orderCart": {
                "lineItems": [{"price": 500, "discounts": [{"name": "line", "amount": -50}]}],
                "discounts": [{"name": "order", "amount": -100}],
                "serviceCharge": {"name": "svc", "percentageDecimal": 100000},
            }
        },
    ).json()
    bare = h.get(f"/orders/{order['id']}").json()
    assert "lineItems" not in bare and "discounts" not in bare and "serviceCharge" not in bare
    lines_only = h.get(f"/orders/{order['id']}", query={"expand": "lineItems"}).json()
    assert len(lines_only["lineItems"]) == 1
    assert "discounts" not in lines_only["lineItems"][0]
    dotted = h.get(f"/orders/{order['id']}", query={"expand": "lineItems,lineItems.discounts,serviceCharge"}).json()
    assert dotted["lineItems"][0]["discounts"] == [{"name": "line", "amount": -50}]
    assert dotted["serviceCharge"]["percentageDecimal"] == 100000
    assert "discounts" not in dotted  # not asked for
    too_many = h.get(f"/orders/{order['id']}", query={"expand": "lineItems,discounts,serviceCharge,orderType"})
    assert too_many.status == 400
    unknown = h.get(f"/orders/{order['id']}", query={"expand": "employee"})
    assert unknown.status == 400
    listed = h.get("/orders", query={"expand": "discounts"}).json()["elements"][0]
    assert listed["discounts"] == [{"name": "order", "amount": -100}]


# ---------------------------------------------------------------------------
# Line items
# ---------------------------------------------------------------------------


def test_adding_a_line_item_never_changes_the_order_total(h: Harness) -> None:
    """DOCUMENTED, the headline fidelity point: 'If your app modifies an
    order, it must update the total as well.' A Square-habituated consumer
    expects the fake to recompute; Clover does not."""
    order = h.create_order(total=1500)
    line = h.post(f"/orders/{order['id']}/line_items", {"price": 750, "name": "Craft Beer"})
    assert line.status == 200
    assert line.json()["price"] == 750
    assert len(line.json()["id"]) == 13
    after = h.get(f"/orders/{order['id']}", query={"expand": "lineItems"}).json()
    assert after["total"] == 1500
    assert len(after["lineItems"]) == 1


def test_a_line_item_needs_a_price_or_a_resolving_item_reference(h: Harness) -> None:
    order = h.create_order()
    neither = h.post(f"/orders/{order['id']}/line_items", {"name": "Mystery"})
    assert neither.status == 400
    assert neither.json()["unit_error"]["field"] == "price"
    unknown = h.post(f"/orders/{order['id']}/line_items", {"item": {"id": "NOSUCHITEM001"}})
    assert unknown.status == 400
    assert unknown.json()["unit_error"]["field"] == "item.id"
    resolved = h.post(f"/orders/{order['id']}/line_items", {"item": {"id": ITEM_BEER}, "unitQty": 1500}).json()
    assert resolved["price"] == 750  # copied from inventory
    assert resolved["name"] == "Craft Beer"
    assert resolved["unitQty"] == 1500
    assert resolved["item"] == {"id": ITEM_BEER}
    assert resolved["exchanged"] is False and resolved["refunded"] is False
    overridden = h.post(f"/orders/{order['id']}/line_items", {"item": {"id": ITEM_BEER}, "price": 700}).json()
    assert overridden["price"] == 700  # an explicit price wins


def test_the_three_thousand_line_item_cap(h: Harness) -> None:
    order = h.create_order()
    orders = h.unit.context.store.collection(COL.orders)

    def fill(draft: dict[str, Any]) -> None:
        draft["lineItems"] = [{"id": f"L{i:012d}", "price": 1} for i in range(3000)]

    orders.update(order["id"], fill, silent=True)
    response = h.post(f"/orders/{order['id']}/line_items", {"price": 1})
    assert response.status == 400
    assert response.json()["unit_error"]["max"] == 3000


def test_bulk_line_items_take_at_most_100_each_with_a_price(h: Harness) -> None:
    order = h.create_order()
    ok = h.post(f"/orders/{order['id']}/bulk_line_items", {"items": [{"price": 100}, {"price": 200, "name": "b"}]})
    assert ok.status == 200
    assert [line["price"] for line in ok.json()["items"]] == [100, 200]
    no_price = h.post(f"/orders/{order['id']}/bulk_line_items", {"items": [{"price": 1}, {"item": {"id": ITEM_BEER}}]})
    assert no_price.status == 400
    assert no_price.json()["unit_error"]["field"] == "items[1].price"
    too_many = h.post(f"/orders/{order['id']}/bulk_line_items", {"items": [{"price": 1}] * 101})
    assert too_many.status == 400
    assert too_many.json()["unit_error"]["field"] == "items"
    after = h.get(f"/orders/{order['id']}", query={"expand": "lineItems"}).json()
    assert len(after["lineItems"]) == 2  # only the accepted request landed
    assert after["total"] == 1500


# ---------------------------------------------------------------------------
# Atomic orders and checkouts
# ---------------------------------------------------------------------------

CART = {
    "orderCart": {
        "orderType": {"id": "KFRPRVCZ73JHM"},
        "lineItems": [
            {"item": {"id": ITEM_BEER}, "unitQty": 2000},
            {"item": {"id": ITEM_ESPRESSO}},
            {"price": 1000, "name": "Special", "discounts": [{"name": "half", "percentage": 50}]},
        ],
        "discounts": [{"name": "loyalty", "amount": -200}],
        "serviceCharge": {"name": "service", "percentageDecimal": 180000},
        "title": "Party of 4",
    }
}
EXPECTED_TOTAL = 2419
"""2x750 + 300 + (1000 - 500) = 2300; -200 = 2100; +18% (378) = 2478? No:
half-up on cents of 2100 x 0.18 = 378.0 -> 2478. See the assertion, which
computes it from the documented units rather than trusting this constant."""


def _expected(cart: dict[str, Any]) -> int:
    lines = 750 * 2 + 300 + (1000 - 500)
    discounted = lines - 200
    return discounted + round(discounted * 180000 / 10000 / 100)


def test_an_atomic_order_creates_and_totals(h: Harness) -> None:
    """DOCUMENTED: /atomic_order/orders 'calculate[s] the order totals' --
    the one create path that does. Units: price x unitQty/1000, negative
    amounts and percentages, percentageDecimal = percent x 10000."""
    response = h.post("/atomic_order/orders", CART)
    assert response.status == 200
    order = response.json()
    assert order["total"] == _expected(CART) == 2478
    assert order["state"] == "open"
    assert order["title"] == "Party of 4"
    assert order["orderType"] == {"id": "KFRPRVCZ73JHM"}
    assert [line["price"] for line in order["lineItems"]] == [750, 300, 1000]
    assert order["lineItems"][0]["name"] == "Craft Beer"
    assert order["lineItems"][2]["discounts"] == [{"name": "half", "percentage": 50}]
    assert order["discounts"] == [{"name": "loyalty", "amount": -200}]
    assert order["serviceCharge"]["percentageDecimal"] == 180000
    fetched = h.get(f"/orders/{order['id']}").json()
    assert fetched["total"] == 2478  # persisted
    assert h.get(f"/orders/{order['id']}", query={"expand": "lineItems"}).json()["lineItems"][1]["price"] == 300


def test_an_atomic_checkout_totals_and_creates_nothing(h: Harness) -> None:
    before = h.journal_len()
    count = len(h.get("/orders").json()["elements"])
    response = h.post("/atomic_order/checkouts", CART)
    assert response.status == 200
    body = response.json()
    assert body["total"] == 2478
    assert "id" not in body  # nothing exists to have an id
    assert [line["price"] for line in body["lineItems"]] == [750, 300, 1000]
    assert h.journal_len() == before
    assert len(h.get("/orders").json()["elements"]) == count


def test_atomic_carts_are_validated_before_anything_is_written(h: Harness) -> None:
    before = h.journal_len()
    bad_item = h.post("/atomic_order/orders", {"orderCart": {"lineItems": [{"item": {"id": "NOSUCHITEM001"}}]}})
    assert bad_item.status == 400
    assert bad_item.json()["unit_error"]["field"] == "orderCart.lineItems[0].item.id"
    empty_discount = h.post("/atomic_order/orders", {"orderCart": {"lineItems": [{"price": 1}], "discounts": [{}]}})
    assert empty_discount.status == 400
    assert empty_discount.json()["unit_error"]["field"] == "orderCart.discounts[0].amount"
    assert h.post("/atomic_order/orders", {}).status == 400  # no orderCart
    assert h.journal_len() == before


# ---------------------------------------------------------------------------
# The invariants every write route keeps
# ---------------------------------------------------------------------------


def test_no_4xx_on_a_write_route_leaves_a_journal_entry(h: Harness) -> None:
    order = h.create_order()
    orders = h.unit.context.store.collection(COL.orders)
    before = h.journal_len()
    assert h.post("/orders", {"state": "shipped"}).status == 400
    assert h.post(f"/orders/{order['id']}", {"state": "shipped"}).status == 400
    assert h.post(f"/orders/{order['id']}/line_items", {"name": "no price"}).status == 400
    assert h.post(f"/orders/{order['id']}/bulk_line_items", {"items": [{"price": 1}] * 101}).status == 400
    assert h.post("/items", {"name": "no price"}).status == 400
    assert h.delete("/orders/NOSUCHORDER01").status == 404
    assert h.journal_len() == before
    assert OrderEntity.from_entity(orders.require(order["id"])).lineItems == ()


def test_another_merchants_path_is_401_for_this_token(h: Harness) -> None:
    """JUDGMENT: the mismatch answers the documented conflated 401 -- the
    same bytes as a bad token -- and the sidecar says why."""
    order = h.create_order()
    for method, path, body in (
        ("GET", "/v3/merchants/OTHERMERCH001/orders", None),
        ("POST", "/v3/merchants/OTHERMERCH001/orders", {"total": 1}),
        ("GET", f"/v3/merchants/OTHERMERCH001/orders/{order['id']}", None),
        ("GET", "/v3/merchants/OTHERMERCH001", None),
        ("GET", "/v3/merchants/OTHERMERCH001/items", None),
    ):
        response = h.api.call(method=method, path=path, body=body, headers=h.auth)
        assert response.status == 401, (method, path)
        assert response.json()["message"] == "401 Unauthorized"
        assert response.json()["unit_error"]["reason"] == "merchant_mismatch"


def test_a_magic_value_in_a_documented_order_field_reaches_the_chaos_engine(h: Harness) -> None:
    """note, title and externalReferenceId are the declared magic paths --
    fields a real Clover client can set -- so a consumer drives a fault
    through their own SDK."""
    before = h.journal_len()
    for field in ("note", "title", "externalReferenceId"):
        response = h.post("/orders", {"total": 1, field: "chaos:rate_limit"})
        assert response.status == 429, field
        assert response.headers["x-ratelimit-tokenlimit"] == "16"
    assert h.journal_len() == before
    plain = h.post("/orders", {"total": 1, "note": "not magic"})
    assert plain.status == 200
