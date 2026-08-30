"""The wire vocabulary: units, enums, defaults, and absence-is-absence."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from vendorfake.clover.model.inventory import ItemWire, PriceType
from vendorfake.clover.model.merchant import AddressWire, MerchantWire, OwnerWire
from vendorfake.clover.model.oauth import TokenResponse
from vendorfake.clover.model.order import (
    ItemRefWire,
    LineItemWire,
    OrderTypeRefWire,
    OrderWire,
    PaymentState,
    PayType,
)

# ---------------------------------------------------------------------------
# Orders
# ---------------------------------------------------------------------------


def test_the_documented_payment_state_values_and_the_open_default() -> None:
    assert {s.value for s in PaymentState} == {
        "OPEN",
        "PAID",
        "REFUNDED",
        "CREDITED",
        "PARTIALLY_PAID",
        "PARTIALLY_REFUNDED",
    }
    order = OrderWire(id="ABCDEFGHJKMN1", currency="USD", total=1500)
    assert order.paymentState is PaymentState.OPEN  # JUDGMENT default


def test_the_documented_pay_type_values() -> None:
    assert {s.value for s in PayType} == {"SPLIT_GUEST", "SPLIT_ITEM", "SPLIT_CUSTOM", "FULL"}


def test_money_is_integer_cents_and_fractional_amounts_are_refused() -> None:
    """'$20.99 is represented as an amount value of 2099' -- and even on the
    lax parse path a fractional 20.99 is a validation error, never a silent
    truncation: that is the one coercion that would corrupt an amount."""
    assert OrderWire(id="X", currency="USD", total=2099).total == 2099
    with pytest.raises(ValidationError):
        OrderWire(id="X", currency="USD", total=20.99)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        LineItemWire(id="L", price=7.5)  # type: ignore[arg-type]


def test_entity_timestamps_are_unix_milliseconds_and_oauth_expirations_seconds() -> None:
    """The two units coexist on Clover's API: modifiedTime 1755786102000 (ms,
    inventory example) vs access_token_expiration 1677875430 (s, oauth
    example). Both documented; a fake that mixed them up would teach a
    consumer to misparse real timestamps by a factor of 1000."""
    order = OrderWire(id="X", currency="USD", total=0, createdTime=1755786102000)
    assert order.createdTime == 1755786102000
    token = TokenResponse(
        access_token="a" * 8,
        access_token_expiration=1677875430,
        refresh_token="r" * 8,
        refresh_token_expiration=1709497830,
    )
    assert token.access_token_expiration == 1677875430
    assert token.refresh_token_expiration - token.access_token_expiration == 31_622_400  # ~366 days, in seconds


def test_the_token_response_wire_is_exactly_the_documented_four_fields() -> None:
    wire = TokenResponse(
        access_token="at",
        access_token_expiration=1,
        refresh_token="rt",
        refresh_token_expiration=2,
    ).wire()
    assert wire == {
        "access_token": "at",
        "access_token_expiration": 1,
        "refresh_token": "rt",
        "refresh_token_expiration": 2,
    }


def test_line_item_defaults_and_the_unit_qty_fixed_point() -> None:
    item = LineItemWire(id="L", price=750)
    assert item.printed is None  # documented field, undocumented default: omitted
    assert item.exchanged is False
    assert item.refunded is False
    assert item.unitQty is None
    assert "printed" not in item.wire()
    # 1.5 units is 1500: "unit quantity multiplied by 1000".
    assert LineItemWire(id="L", price=750, unitQty=1500).unitQty == 1500


def test_a_documented_order_body_parses_in_python_mode() -> None:
    """The parse path must accept what Clover's own examples send: enum values
    as strings, arrays as lists, and documented fields this build does not
    model (isVat, unpaidBalance, employee, customers, discounts,
    serviceCharge) tolerated rather than 400ed."""
    body = {
        "id": "ABCDEFGHJKMN1",
        "orderType": {"id": "KFRPRVCZ73JHM"},
        "currency": "USD",
        "total": 1500,
        "state": "Open",
        "paymentState": "PAID",
        "lineItems": [{"id": "L1", "price": 750, "item": {"id": "NEWITEM123ABC"}}],
        "isVat": False,
        "unpaidBalance": 0,
        "employee": {"id": "EMPLOYEE12345"},
        "customers": [],
        "discounts": [],
        "serviceCharge": {"name": "svc"},
    }
    order = OrderWire.model_validate(body)
    assert order.paymentState is PaymentState.PAID
    assert order.state == "Open"  # stored verbatim; casing is the machine's problem
    assert order.lineItems[0].price == 750
    assert order.lineItems[0].item is not None
    assert order.lineItems[0].item.id == "NEWITEM123ABC"
    assert order.orderType is not None
    assert order.orderType.id == "KFRPRVCZ73JHM"
    assert not hasattr(order, "isVat")  # tolerated on parse, not silently modelled


def test_a_documented_item_body_parses_in_python_mode() -> None:
    body = {
        "name": "Craft Beer",
        "price": 750,
        "id": "NEWITEM123ABC",
        "priceType": "VARIABLE",
        "isAgeRestricted": True,
    }
    item = ItemWire.model_validate(body)
    assert item.priceType is PriceType.VARIABLE
    assert not hasattr(item, "isAgeRestricted")


def test_absent_optionals_emit_no_key() -> None:
    wire = OrderWire(id="X", currency="USD", total=1500).wire()
    assert wire == {"id": "X", "currency": "USD", "total": 1500, "paymentState": "OPEN"}
    assert "note" not in wire
    assert "state" not in wire  # null-for-hidden: an absent state emits nothing
    assert "lineItems" not in wire  # empty array omitted, per the package rule


def test_a_full_order_round_trips_its_documented_fields() -> None:
    order = OrderWire(
        id="ABCDEFGHJKMN1",
        currency="USD",
        total=1500,
        state="open",
        title="Table 4",
        note="rush",
        externalReferenceId="POS-991",
        orderType=OrderTypeRefWire(id="KFRPRVCZ73JHM"),
        lineItems=(LineItemWire(id="L1", price=750, item=ItemRefWire(id="NEWITEM123ABC")),),
    )
    wire = order.wire(expand={"lineItems"})
    assert wire["state"] == "open"
    assert wire["orderType"] == {"id": "KFRPRVCZ73JHM"}
    assert wire["lineItems"][0]["item"] == {"id": "NEWITEM123ABC"}
    assert wire["lineItems"][0]["price"] == 750
    assert "lineItems" not in order.wire()  # nested collections need an expansion


def test_the_atomic_total_arithmetic_from_the_documented_units() -> None:
    """price x unitQty/1000 (absent = one unit), negative amounts, whole
    percentages of the undiscounted base, percentageDecimal = percent x 10000
    on the discounted subtotal, half-up on cents (JUDGMENT)."""
    from vendorfake.clover.model.order import atomic_total, line_total

    assert line_total({"price": 750}) == 750
    assert line_total({"price": 750, "unitQty": 2000}) == 1500
    assert line_total({"price": 333, "unitQty": 1500}) == 500  # 499.5 -> half-up
    assert line_total({"price": 1000, "discounts": [{"amount": -200}]}) == 800
    assert line_total({"price": 1000, "discounts": [{"percentage": 15}]}) == 850
    assert atomic_total([{"price": 1000}], [{"amount": -200}]) == 800
    assert atomic_total([{"price": 1000}], [{"percentage": 10}]) == 900
    assert atomic_total([{"price": 1000}], [], {"percentageDecimal": 180000}) == 1180
    assert atomic_total([{"price": 1000}], [{"amount": -200}], {"percentageDecimal": 180000}) == 944
    assert atomic_total([]) == 0
    # A disabled service charge charges nothing; modifications scale with unitQty.
    assert atomic_total([{"price": 1000}], [], {"percentageDecimal": 180000, "enabled": False}) == 1000
    assert line_total({"price": 300, "unitQty": 2000, "modifications": [{"amount": 50}]}) == 700
    # A line's own discounts floor at zero (JUDGMENT): exactly zeroed, and over-discounted.
    assert line_total({"price": 450, "discounts": [{"amount": -450}]}) == 0
    assert line_total({"price": 450, "discounts": [{"amount": -1000}]}) == 0
    assert line_total({"price": 450, "discounts": [{"percentage": 100}]}) == 0


def test_the_totals_block_never_goes_negative() -> None:
    """The #25 gate repro: a -1000 line discount on a 450 line taxed at
    7.25% produced subtotal -550, tax -40 and a persisted total of -40. Every
    figure floors at zero, and the block refuses to exist otherwise."""
    from vendorfake.clover.model.order import AtomicTotals, atomic_totals

    sales = {"id": "T1", "name": "Sales", "rate": 725000}
    over = atomic_totals([{"price": 450, "discounts": [{"amount": -1000}]}], [[sales]])
    assert (over.subtotal, over.totalTaxAmount, over.total) == (0, 0, 0)
    assert over.taxSummaries[0]["amount"] == 0
    zeroed = atomic_totals([{"price": 450, "discounts": [{"amount": -450}]}], [[sales]])
    assert (zeroed.subtotal, zeroed.totalTaxAmount, zeroed.total) == (0, 0, 0)
    with pytest.raises(ValueError, match="atomic total is negative: -1"):
        AtomicTotals(subtotal=0, total=-1, totalTaxAmount=0, taxSummaries=())


def test_the_totals_block_taxes_each_line_at_its_own_rates() -> None:
    """subtotal, totalTaxAmount, taxSummaries and a receipt total; tax is
    per line on the line's discounted total at rate / TAX_RATE_SCALE percent
    (JUDGMENT scale, one constant)."""
    from vendorfake.clover.model.order import TAX_RATE_SCALE, atomic_totals

    assert TAX_RATE_SCALE == 100000
    sales = {"id": "T1", "name": "Sales", "rate": 725000}
    beverage = {"id": "T2", "name": "Beverage", "rate": 1000000}
    totals = atomic_totals(
        [{"price": 1500}, {"price": 300}, {"price": 1000, "discounts": [{"percentage": 50}]}],
        [[beverage], [sales], [sales]],
        [{"amount": -200}],
        {"percentageDecimal": 180000},
    )
    assert totals.subtotal == 2300
    # tax: 150 + 22 (21.75) + 36 (36.25) = 208
    assert totals.totalTaxAmount == 208
    assert {s["id"]: s["amount"] for s in totals.taxSummaries} == {"T2": 150, "T1": 58}
    assert totals.total == (2300 - 200) + round(2100 * 0.18) + 208
    assert set(totals.wire()) == {"subtotal", "total", "totalTaxAmount", "taxSummaries"}


def test_projection_omits_unexpanded_nested_collections_and_caps_at_100() -> None:
    from vendorfake.clover.model.order import NESTED_CAP, project_order

    entity = {
        "id": "X",
        "merchant_id": "M",
        "currency": "USD",
        "total": 1,
        "lineItems": [{"id": f"L{i}", "price": 1, "discounts": [{"amount": -1}]} for i in range(150)],
        "discounts": [{"amount": -5}],
        "serviceCharge": {"percentageDecimal": 1},
        "version": 3,
        "created_at": "x",
    }
    bare = project_order(entity)
    assert set(bare) == {"id", "currency", "total", "paymentState"}
    expanded = project_order(entity, {"lineItems", "lineItems.discounts", "serviceCharge"})
    assert len(expanded["lineItems"]) == NESTED_CAP == 100
    assert expanded["lineItems"][0]["discounts"] == [{"amount": -1}]
    assert expanded["serviceCharge"] == {"percentageDecimal": 1}
    assert "discounts" not in expanded
    assert "discounts" not in project_order(entity, {"lineItems"})["lineItems"][0]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------


def test_item_defaults_match_the_documented_create_example() -> None:
    """hidden/available/priceType/defaultTaxRates read off the
    inventorycreateitem response; isRevenue=False is the labelled JUDGMENT."""
    item = ItemWire(id="NEWITEM123ABC", name="Craft Beer", price=750)
    assert item.hidden is False
    assert item.available is True
    assert item.priceType is PriceType.FIXED
    assert item.defaultTaxRates is True
    assert item.isRevenue is False


def test_item_requires_name_and_price() -> None:
    with pytest.raises(ValidationError):
        ItemWire(id="X", name="No price")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        ItemWire(id="X", price=100)  # type: ignore[call-arg]


def test_the_documented_price_type_values() -> None:
    assert {s.value for s in PriceType} == {"FIXED", "VARIABLE", "PER_UNIT"}


# ---------------------------------------------------------------------------
# Merchant
# ---------------------------------------------------------------------------


def test_merchant_wire_carries_owner_and_address_and_omits_absences() -> None:
    bare = MerchantWire(id="MXHW24RNRHW16", name="Harvest & Rye").wire()
    assert bare == {"id": "MXHW24RNRHW16", "name": "Harvest & Rye"}
    full = MerchantWire(
        id="MXHW24RNRHW16",
        name="Harvest & Rye",
        owner=OwnerWire(id="OWNER12345678", name="A. Owner"),
        address=AddressWire(address1="1 Main St", city="Springfield", state="IL", zip="62701", country="US"),
    ).wire()
    assert full["owner"] == {"id": "OWNER12345678", "name": "A. Owner"}
    assert full["address"]["city"] == "Springfield"
