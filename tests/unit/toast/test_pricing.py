"""The arithmetic, pure: the documented example first."""

from __future__ import annotations

import uuid
from decimal import Decimal

from vendorfake.toast.model.pricing import (
    APPLIED_TAX_NAMESPACE,
    TaxRate,
    discount_amount,
    quantity_price,
    tax_on,
    taxes_on,
)

SALES_TAX = TaxRate(guid="t1", name="Sales Tax", rate=Decimal("0.0625"))


def test_the_documented_example_8_99_at_0_0625_is_0_56() -> None:
    """899 x 0.0625 = 56.1875 -> 56 half-up; 899 + 56 = 955 (apiOrderPrices.html)."""
    assert tax_on(899, SALES_TAX) == 56
    assert 899 + tax_on(899, SALES_TAX) == 955
    (applied,) = taxes_on(899, [SALES_TAX], owner="sel-1")
    assert applied.pop("guid") == str(uuid.uuid5(APPLIED_TAX_NAMESPACE, "sel-1:t1"))
    assert applied == {
        "entityType": "AppliedTaxRate",
        "taxRate": {"guid": "t1", "entityType": "TaxRate"},
        "name": "Sales Tax",
        "rate": 0.0625,
        "taxAmount": 56,
        "type": "PERCENT",
    }


def test_each_documented_rounding_type_rounds_its_own_way() -> None:
    """325 x 0.0625 = 20.3125; 1000 x 0.0625 = 62.5 -- the half case."""
    for rounding, half in (("HALF_UP", 63), ("HALF_EVEN", 62), ("ALWAYS_UP", 63), ("ALWAYS_DOWN", 62)):
        rate = TaxRate(guid="t", name="t", rate=Decimal("0.0625"), rounding=rounding)
        assert tax_on(1000, rate) == half, rounding
        assert tax_on(325, rate) == (21 if rounding == "ALWAYS_UP" else 20), rounding


def test_non_percent_rates_levy_nothing_here() -> None:
    assert tax_on(899, TaxRate(guid="t", name="t", rate=Decimal("0.0625"), type="FIXED")) == 0
    assert taxes_on(899, []) == []


def test_quantity_and_pre_modifier_factor_scale_the_unit_price_half_up() -> None:
    assert quantity_price(899, 1) == 899
    assert quantity_price(899, 2) == 1798
    assert quantity_price(899, 0.5) == 450  # 449.5 -> 450
    assert quantity_price(150, 1, factor=2) == 300
    assert quantity_price(150, 1, factor=0) == 0
    assert quantity_price(333, 1.5) == 500  # 499.5 -> 500


def test_discounts_take_a_percentage_or_a_fixed_amount_and_never_exceed_the_target() -> None:
    assert discount_amount(899, {"type": "PERCENT", "percentage": 100}) == 899
    assert discount_amount(899, {"type": "PERCENT", "percentage": 10}) == 90  # 89.9 -> 90
    assert discount_amount(899, {"type": "FIXED", "amount": 100}) == 100
    assert discount_amount(50, {"type": "FIXED", "amount": 100}) == 50
    assert discount_amount(899, {"type": "BOGO"}) == 0
    assert discount_amount(899, {"type": "PERCENT"}) == 0


def test_tax_rate_reads_a_config_row() -> None:
    rate = TaxRate.from_entity(
        {"id": "t1", "name": "Sales Tax", "rate": 0.0625, "roundingType": "HALF_UP", "type": "PERCENT"}
    )
    assert rate == SALES_TAX
    assert TaxRate.from_entity({"id": "t2", "name": "None", "rate": None, "type": "NONE"}).rate == 0
