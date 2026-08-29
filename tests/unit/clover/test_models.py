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


def test_money_is_integer_cents_and_floats_are_refused() -> None:
    """'$20.99 is represented as an amount value of 2099' -- and strict mode
    means 20.99 is a type error here, never a silent truncation."""
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
    assert item.printed is False
    assert item.exchanged is False
    assert item.refunded is False
    assert item.unitQty is None
    # 1.5 units is 1500: "unit quantity multiplied by 1000".
    assert LineItemWire(id="L", price=750, unitQty=1500).unitQty == 1500


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
    wire = order.wire()
    assert wire["state"] == "open"
    assert wire["orderType"] == {"id": "KFRPRVCZ73JHM"}
    assert wire["lineItems"][0]["item"] == {"id": "NEWITEM123ABC"}
    assert wire["lineItems"][0]["price"] == 750


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
