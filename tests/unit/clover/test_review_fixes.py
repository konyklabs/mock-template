"""The PR C review findings, each pinned: a required field cannot be cleared
(and one bad request never bricks a list), references resolve, integers are
parsed strictly, the atomic path honours the line cap, discount sign rules,
dotted expansions imply their parent, a locked order cannot be deleted,
negative money is refused, a PAID order takes no more payment, paymentState
is not client-set, and a refused request never advances the id stream."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.unit.clover.harness import (
    CUSTOMER_ADA,
    EMPLOYEE_BARISTA,
    ITEM_BEER,
    ITEM_ESPRESSO,
    MOD_OAT,
    ORDER_TYPE_DINE_IN,
    TENDER_CASH,
    Harness,
    harness,
)


@pytest.fixture
def h() -> Iterator[Harness]:
    yield from harness()


# -- 1. null clearing --------------------------------------------------------


@pytest.mark.parametrize("field", ["total", "currency"])
def test_a_required_order_field_cannot_be_cleared(h: Harness, field: str) -> None:
    order = h.create_order()
    before = h.journal_len()
    response = h.post(f"/orders/{order['id']}", {field: None})
    assert response.status == 400
    assert response.json()["unit_error"]["field"] == field
    assert h.journal_len() == before
    fetched = h.get(f"/orders/{order['id']}").json()
    assert fetched["total"] == 1500 and fetched["currency"] == "USD"


@pytest.mark.parametrize("field", ["name", "price"])
def test_a_required_item_field_cannot_be_cleared_and_the_list_survives(h: Harness, field: str) -> None:
    before = h.journal_len()
    response = h.post(f"/items/{ITEM_BEER}", {field: None})
    assert response.status == 400
    assert response.json()["unit_error"]["field"] == field
    assert h.journal_len() == before
    # One bad request never bricks the list or the item.
    assert h.get("/items").status == 200
    item = h.get(f"/items/{ITEM_BEER}").json()
    assert item["name"] == "Craft Beer" and item["price"] == 750
    # And a line built from the item still carries the real price and name.
    order = h.create_order()
    line = h.post(f"/orders/{order['id']}/line_items", {"item": {"id": ITEM_BEER}}).json()
    assert line["price"] == 750 and line["name"] == "Craft Beer"


def test_a_malformed_stored_item_cannot_500_the_list(h: Harness) -> None:
    """Defence in depth behind the clearing refusal: the projection round-trips
    through the tolerant entity reader."""
    from vendorfake.clover.entities import COL

    items = h.unit.context.store.collection(COL.items)

    def corrupt(draft: dict) -> None:  # type: ignore[type-arg]
        draft.pop("name", None)

    items.update(ITEM_BEER, corrupt, silent=True)
    assert h.get("/items").status == 200
    assert h.get(f"/items/{ITEM_BEER}").status == 200


# -- 3. references ------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "field"),
    [
        ({"orderType": {"id": "NOSUCHTYPE001"}}, "orderType.id"),
        ({"employee": {"id": "NOSUCHEMPL001"}}, "employee.id"),
        ({"customers": [{"id": CUSTOMER_ADA}, {"id": "NOSUCHCUST001"}]}, "customers[1].id"),
        ({"orderType": {"id": ""}}, "orderType.id"),
    ],
)
def test_order_level_references_must_resolve(h: Harness, body: dict, field: str) -> None:  # type: ignore[type-arg]
    before = h.journal_len()
    created = h.post("/orders", {"total": 1, **body})
    assert created.status == 400
    assert created.json()["unit_error"]["field"] == field
    order = h.create_order()
    updated = h.post(f"/orders/{order['id']}", body)
    assert updated.status == 400
    assert updated.json()["unit_error"]["field"] == field
    atomic = h.post("/atomic_order/checkouts", {"orderCart": {"lineItems": [{"price": 1}], **body}})
    assert atomic.status == 400
    assert atomic.json()["unit_error"]["field"] == f"orderCart.{field}"
    assert h.journal_len() == before + 1  # only the one good create
    good = h.post(
        "/orders",
        {
            "total": 1,
            "orderType": {"id": ORDER_TYPE_DINE_IN},
            "employee": {"id": EMPLOYEE_BARISTA},
            "customers": [{"id": CUSTOMER_ADA}],
        },
    )
    assert good.status == 200


# -- Opus 3. strict integers ---------------------------------------------------


@pytest.mark.parametrize("bad", ["--5", "+5", "5x", "abc", "1e3", " "])
def test_integer_query_parameters_are_parsed_strictly(h: Harness, bad: str) -> None:
    for name in ("limit", "offset"):
        response = h.get("/orders", query={name: bad})
        assert response.status == 400, (name, bad)
        assert response.json()["unit_error"]["field"] == name
    response = h.get("/orders", query={"filter": f"total={bad}"})
    assert response.status == 400, bad
    assert response.json()["unit_error"]["field"] == "filter"


def test_negative_limit_and_offset_are_400_and_negative_filter_values_parse(h: Harness) -> None:
    assert h.get("/orders", query={"limit": "-5"}).status == 400
    assert h.get("/orders", query={"offset": "-5"}).status == 400
    assert h.get("/orders", query={"filter": "total>=-5"}).status == 200


def test_an_unsupported_filter_operator_is_named(h: Harness) -> None:
    response = h.get("/orders", query={"filter": "total>500"})
    assert response.status == 400
    assert "unsupported operator" in response.json()["message"]
    assert "no operator" in h.get("/orders", query={"filter": "total"}).json()["message"]


# -- Opus 4. atomic cap ---------------------------------------------------------


def test_the_atomic_path_honours_the_line_item_cap_before_any_write(h: Harness) -> None:
    before = h.journal_len()
    draws = h.unit.context.vendor.ids.draw_count  # type: ignore[attr-defined]
    cart = {"orderCart": {"lineItems": [{"price": 1}] * 3001}}
    assert h.post("/atomic_order/orders", cart).status == 400
    assert h.post("/atomic_order/checkouts", cart).status == 400
    assert h.journal_len() == before
    assert h.unit.context.vendor.ids.draw_count == draws  # type: ignore[attr-defined]


# -- Opus 5. discount rules ----------------------------------------------------


def test_discount_sign_rules(h: Harness) -> None:
    def total(cart: dict) -> int:  # type: ignore[type-arg]
        response = h.post("/atomic_order/checkouts", {"orderCart": cart})
        assert response.status == 200, response.text
        return int(response.json()["total"])

    assert total({"lineItems": [{"price": 1000}], "discounts": [{"amount": -200}]}) == 800
    # A positive amount is accepted as sent (signed integer, JUDGMENT).
    assert total({"lineItems": [{"price": 1000}], "discounts": [{"amount": 200}]}) == 1200
    # Over-discounting floors at zero rather than owing the customer money.
    assert total({"lineItems": [{"price": 1000}], "discounts": [{"amount": -5000}]}) == 0
    for bad, field in (
        ({"lineItems": [{"price": 1000}], "discounts": [{"percentage": -10}]}, "orderCart.discounts[0].percentage"),
        ({"lineItems": [{"price": 1000}], "discounts": [{"percentage": 150}]}, "orderCart.discounts[0].percentage"),
        ({"lineItems": [{"price": 1000, "unitQty": -1000}]}, "orderCart.lineItems[0].unitQty"),
    ):
        response = h.post("/atomic_order/checkouts", {"orderCart": bad})
        assert response.status == 400, bad
        assert response.json()["unit_error"]["field"] == field


# -- Opus 6. dotted expansion implies its parent ---------------------------------


def test_a_dotted_expansion_alone_implies_its_parent(h: Harness) -> None:
    order = h.post(
        "/atomic_order/orders",
        {"orderCart": {"lineItems": [{"price": 500, "discounts": [{"amount": -50}]}]}},
    ).json()
    alone = h.get(f"/orders/{order['id']}", query={"expand": "lineItems.discounts"}).json()
    assert alone["lineItems"][0]["discounts"] == [{"amount": -50}]
    # The implied parent does not count against the three-expansion cap.
    four = h.get(f"/orders/{order['id']}", query={"expand": "lineItems.discounts,discounts,serviceCharge"})
    assert four.status == 200


# -- Opus 7. delete on a locked order ------------------------------------------


def test_a_locked_order_cannot_be_deleted(h: Harness) -> None:
    order = h.create_order(total=100)
    assert h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 100}).status == 200
    before = h.journal_len()
    response = h.delete(f"/orders/{order['id']}")
    assert response.status == 400
    assert response.json()["unit_error"]["kind"] == "invalid_transition"
    assert h.journal_len() == before
    assert h.get(f"/orders/{order['id']}").status == 200


# -- minors --------------------------------------------------------------------


def test_negative_money_is_refused_everywhere(h: Harness) -> None:
    order = h.create_order(total=1000)
    cases = [
        ("POST", "/items", {"name": "x", "price": -100}, "price"),
        ("POST", f"/items/{ITEM_BEER}", {"price": -1}, "price"),
        ("POST", "/orders", {"total": -1}, "total"),
        ("POST", f"/orders/{order['id']}", {"total": -1}, "total"),
        ("POST", f"/orders/{order['id']}/line_items", {"price": -1}, "price"),
        (
            "POST",
            f"/orders/{order['id']}/payments",
            {"tender": {"id": TENDER_CASH}, "amount": 1, "tipAmount": -1},
            "tipAmount",
        ),
        ("POST", f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": -1}, "amount"),
        (
            "POST",
            "/atomic_order/checkouts",
            {
                "orderCart": {
                    "lineItems": [
                        {"item": {"id": ITEM_ESPRESSO}, "modifications": [{"modifier": {"id": MOD_OAT}, "amount": -5}]}
                    ]
                }
            },
            "orderCart.lineItems[0].modifications[0].amount",
        ),
        (
            "POST",
            "/atomic_order/checkouts",
            {"orderCart": {"lineItems": [], "serviceCharge": {"percentageDecimal": -1}}},
            "orderCart.serviceCharge.percentageDecimal",
        ),
    ]
    before = h.journal_len()
    for method, suffix, body, field in cases:
        response = h.api.call(method=method, path=h.path(suffix), body=body, headers=h.auth)
        assert response.status == 400, (suffix, body)
        assert response.json()["unit_error"]["field"] == field, (suffix, response.json())
    assert h.journal_len() == before


def test_a_paid_order_takes_no_further_payment(h: Harness) -> None:
    order = h.create_order(total=500)
    assert h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 700}).status == 200
    assert h.get(f"/orders/{order['id']}").json()["paymentState"] == "PAID"  # over-tendered once: allowed
    before = h.journal_len()
    again = h.post(f"/orders/{order['id']}/payments", {"tender": {"id": TENDER_CASH}, "amount": 1})
    assert again.status == 400
    assert "already PAID" in again.json()["message"]
    assert h.journal_len() == before


def test_payment_state_is_not_client_set(h: Harness) -> None:
    assert h.post("/orders", {"total": 1, "paymentState": "PAID"}).status == 400
    assert h.post("/orders", {"total": 1, "paymentState": "OPEN"}).status == 200
    order = h.create_order()
    response = h.post(f"/orders/{order['id']}", {"paymentState": "PAID"})
    assert response.status == 400
    assert response.json()["unit_error"]["field"] == "paymentState"


def test_a_refused_line_item_request_never_advances_the_id_stream(h: Harness) -> None:
    from vendorfake.clover.entities import COL

    order = h.create_order()
    orders = h.unit.context.store.collection(COL.orders)

    def fill(draft: dict) -> None:  # type: ignore[type-arg]
        draft["lineItems"] = [{"id": f"L{i:012d}", "price": 1} for i in range(3000)]

    orders.update(order["id"], fill, silent=True)
    draws = h.unit.context.vendor.ids.draw_count  # type: ignore[attr-defined]
    assert h.post(f"/orders/{order['id']}/line_items", {"price": 1}).status == 400
    assert h.post(f"/orders/{order['id']}/bulk_line_items", {"items": [{"price": 1}]}).status == 400
    assert h.unit.context.vendor.ids.draw_count == draws  # type: ignore[attr-defined]


# -- final round: implied parents are bound by EXPANDABLE ------------------------


def test_an_implied_parent_must_itself_be_expandable() -> None:
    from vendorfake.clover.surface.common import expansions
    from vendorfake.core.kernel.types import UnitError

    class _Args:
        def __init__(self, expand: str) -> None:
            self._expand = expand

        def query(self, name: str) -> str | None:
            return self._expand if name == "expand" else None

    # A scratch set in which the dotted child is allowed but its parent is
    # not: the parent used to be implied without a check.
    allowed = frozenset({"lineItems", "lineItems.discounts", "payments.tender"})
    assert expansions(_Args("lineItems.discounts"), allowed) == frozenset({"lineItems", "lineItems.discounts"})  # type: ignore[arg-type]
    with pytest.raises(UnitError) as refused:
        expansions(_Args("payments.tender"), allowed)  # type: ignore[arg-type]
    assert refused.value.field == "expand"
    assert "payments.tender" in (refused.value.detail or "")


# -- final round: references resolve against the path merchant only -------------


def test_another_merchants_employee_is_not_a_reference(h: Harness) -> None:
    from vendorfake.clover.entities import COL
    from vendorfake.clover.seed.hydrate import SEED_META

    other = "OTHERMERCHNT1"
    h.unit.context.store.collection(COL.employees).insert(
        {"id": "OTHEREMPLOY01", "name": "Not ours", "merchant_id": other}, SEED_META
    )
    h.unit.context.store.collection(COL.customers).insert({"id": "OTHERCUSTOM01", "merchant_id": other}, SEED_META)
    before = h.journal_len()
    created = h.post("/orders", {"total": 1, "employee": {"id": "OTHEREMPLOY01"}})
    assert created.status == 400
    assert created.json()["unit_error"]["field"] == "employee.id"
    with_customer = h.post("/orders", {"total": 1, "customers": [{"id": "OTHERCUSTOM01"}]})
    assert with_customer.status == 400
    assert with_customer.json()["unit_error"]["field"] == "customers[0].id"
    order = h.create_order(total=100)
    paid = h.post(
        f"/orders/{order['id']}/payments",
        {"tender": {"id": TENDER_CASH}, "employee": {"id": "OTHEREMPLOY01"}, "amount": 100},
    )
    assert paid.status == 400
    assert paid.json()["unit_error"]["field"] == "employee.id"
    assert h.journal_len() == before + 1  # only the good create
    # Neither row is listed for this merchant, and the internal stamp never leaks.
    employees = h.get("/employees").json()["elements"]
    assert EMPLOYEE_BARISTA in [e["id"] for e in employees]
    assert "OTHEREMPLOY01" not in [e["id"] for e in employees]
    assert all("merchant_id" not in e for e in employees)
    customers = h.get("/customers").json()["elements"]
    assert [c["id"] for c in customers] == [CUSTOMER_ADA]
    assert all("merchant_id" not in c for c in customers)
