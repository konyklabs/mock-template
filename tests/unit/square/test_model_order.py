"""The order projection: absent keys, and money arithmetic that is not Python's.

The rounding tests are the point of this file. `Math.round` and Python's
`round` disagree on every halfway case, and money in minor units multiplied by
a string quantity is exactly where a Python port diverges from the oracle
silently -- the response is still a 200 and still well-formed, it is just one
cent wrong.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vendorfake.core.util.json import dump_json
from vendorfake.square.entities import Money, OrderEntity, OrderLineItem, Tender
from vendorfake.square.model.order import (
    MoneyWire,
    line_item_total,
    order_total,
    project_order,
    project_order_entry,
    tendered_total,
)


def line(quantity: str, amount: int = 550, **kwargs: object) -> OrderLineItem:
    return OrderLineItem(uid="u1", quantity=quantity, base_price_money=Money(amount, "USD"), **kwargs)  # type: ignore[arg-type]


def order(**kwargs: object) -> OrderEntity:
    base = {"id": "CAIS1", "location_id": "L1", "merchant_id": "M1", "currency": "USD"}
    return OrderEntity(**{**base, **kwargs})  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Money.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "quantity", "expected", "python_round_would_give"),
    [
        # Math.round(2.5) === 3; round(2.5) == 2. Banker's rounding is the
        # divergence, and it bites on the commonest fractional quantity there is.
        (1, "2.5", 3, 2),
        (1, "3.5", 4, 4),
        (5, "0.5", 3, 2),
        (5, "1.5", 8, 8),
        # Math.round(-0.5) is -0, i.e. 0. floor(-0.5 + 0.5) is 0 too.
        (-1, "0.5", 0, 0),
        (-5, "0.5", -2, -2),
        (-1, "2.5", -2, -2),
        (550, "2", 1100, 1100),
        (550, "1.5", 825, 825),
    ],
)
def test_line_totals_round_the_way_javascript_does(
    amount: int, quantity: str, expected: int, python_round_would_give: int
) -> None:
    assert line_item_total(line(quantity, amount)) == expected
    # Named so a reader can see which cases the two disagree on rather than
    # taking the module docstring's word for it.
    assert round(amount * float(quantity)) == python_round_would_give


@pytest.mark.parametrize(
    ("quantity", "expected"),
    [
        ("2", 1100),
        # Number.parseFloat consumes the longest numeric prefix. Python's
        # float() raises on both of these, which would turn a documented 200
        # into a 500 on a string field a consumer may legitimately fill with junk.
        ("2 pieces", 1100),
        ("", 0),
        ("pieces", 0),
        ("Infinity", 0),
        ("0", 0),
    ],
)
def test_a_non_numeric_quantity_is_a_zero_total_not_a_crash(quantity: str, expected: int) -> None:
    assert line_item_total(line(quantity)) == expected


def test_order_and_tendered_totals() -> None:
    subject = order(
        line_items=(line("2"), line("1")),
        tenders=(
            Tender(
                id="t1",
                location_id="L1",
                transaction_id="x",
                created_at="2026-01-01T00:00:00.000Z",
                amount_money=Money(1000, "USD"),
                payment_id="p1",
            ),
        ),
    )
    assert order_total(subject) == 1650
    assert tendered_total(subject) == 1000
    assert project_order(subject)["net_amount_due_money"] == {"amount": 650, "currency": "USD"}


def test_over_tendering_leaves_nothing_due_rather_than_owing() -> None:
    subject = order(
        line_items=(line("1"),),
        tenders=(
            Tender(
                id="t1",
                location_id="L1",
                transaction_id="x",
                created_at="2026-01-01T00:00:00.000Z",
                amount_money=Money(10_000, "USD"),
                payment_id="p1",
            ),
        ),
    )
    assert project_order(subject)["net_amount_due_money"] == {"amount": 0, "currency": "USD"}


def test_a_fractional_money_amount_is_refused_at_the_wire_model() -> None:
    """Minor units are whole numbers. Strict mode is what stops 2.5 becoming 2
    somewhere between the projection and the response body."""
    with pytest.raises(ValidationError):
        MoneyWire(amount=2.5, currency="USD")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Absence.
# ---------------------------------------------------------------------------


def test_an_order_with_nothing_optional_set_emits_no_null_keys() -> None:
    projected = project_order(order(created_at="2026-01-01T00:00:00.000Z", updated_at="2026-01-01T00:00:00.000Z"))
    for absent in (
        "reference_id",
        "customer_id",
        "ticket_name",
        "source",
        "line_items",
        "metadata",
        "tenders",
        "closed_at",
    ):
        assert absent not in projected, absent
    assert b"null" not in dump_json(projected)


def test_the_documented_key_set_of_a_bare_order() -> None:
    projected = project_order(order(created_at="2026-01-01T00:00:00.000Z", updated_at="2026-01-01T00:00:00.000Z"))
    assert list(projected) == [
        "id",
        "location_id",
        "created_at",
        "updated_at",
        "state",
        "version",
        "total_money",
        "total_tax_money",
        "total_discount_money",
        "total_tip_money",
        "total_service_charge_money",
        "net_amounts",
        "net_amount_due_money",
    ]


def test_net_amounts_equals_total_money_because_the_slice_models_no_deductions() -> None:
    projected = project_order(order(line_items=(line("2"),)))
    assert projected["total_money"] == {"amount": 1100, "currency": "USD"}
    assert projected["net_amounts"]["total_money"] == projected["total_money"]
    for zero in ("tax_money", "discount_money", "tip_money", "service_charge_money"):
        assert projected["net_amounts"][zero] == {"amount": 0, "currency": "USD"}


def test_a_line_item_omits_its_own_absent_optionals() -> None:
    projected = project_order(order(line_items=(line("2"),)))
    item = projected["line_items"][0]
    for absent in ("catalog_object_id", "variation_name", "name", "note"):
        assert absent not in item
    assert list(item)[:3] == ["uid", "quantity", "base_price_money"]
    assert item["total_money"] == {"amount": 1100, "currency": "USD"}


def test_a_line_item_keeps_squares_field_order_when_everything_is_set() -> None:
    projected = project_order(
        order(line_items=(line("2", name="Coffee", note="hot", catalog_object_id="V1", variation_name="Large"),))
    )
    assert list(projected["line_items"][0]) == [
        "uid",
        "catalog_object_id",
        "variation_name",
        "name",
        "quantity",
        "note",
        "base_price_money",
        "variation_total_price_money",
        "gross_sales_money",
        "total_tax_money",
        "total_discount_money",
        "total_money",
        "total_service_charge_money",
    ]


def test_source_is_a_nested_object_built_from_a_stored_name() -> None:
    projected = project_order(order(source_name="Kiosk"))
    assert projected["source"] == {"name": "Kiosk"}


def test_a_closed_order_carries_the_terminal_timestamp() -> None:
    projected = project_order(order(state="COMPLETED", closed_at="2026-01-01T00:10:00.000Z"))
    assert projected["closed_at"] == "2026-01-01T00:10:00.000Z"
    assert projected["state"] == "COMPLETED"


def test_the_order_entry_projection() -> None:
    assert project_order_entry(order(version=3)) == {"order_id": "CAIS1", "version": 3, "location_id": "L1"}
