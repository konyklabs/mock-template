"""Decimal dollars on the wire, integer cents in the store: the round trip."""

from __future__ import annotations

import json

import pytest

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.toast.model.money import opt_cents, to_cents, to_dollars


def test_the_documented_example_amounts_print_as_the_page_shows_them() -> None:
    """8.99, 0.56, 9.55 (apiOrderPrices.html) -- as JSON numbers, not strings."""
    assert json.dumps([to_dollars(899), to_dollars(56), to_dollars(955)]) == "[8.99, 0.56, 9.55]"
    assert to_dollars(0) == 0.0
    assert to_dollars(900) == 9.0
    assert isinstance(to_dollars(1), float)


@pytest.mark.parametrize("cents", [*range(0, 2000), 99999, 123456789, 10**12])
def test_every_amount_survives_the_round_trip(cents: int) -> None:
    assert to_cents(to_dollars(cents), field="amount") == cents


def test_strings_are_accepted_on_input_as_one_guide_example_shows() -> None:
    """`"amount": "9.55"` (apiCreatingAnOrderWithPaymentInformation.html)."""
    assert to_cents("9.55", field="amount") == 955
    assert to_cents(" 0.00 ", field="tipAmount") == 0
    assert to_cents("15", field="tipAmount") == 1500


def test_numbers_of_both_kinds_are_accepted() -> None:
    assert to_cents(15, field="tipAmount") == 1500
    assert to_cents(35.21, field="amount") == 3521
    assert to_cents(8.99 + 0.56, field="amount") == 955  # 9.549999999999999 in binary floating point


def test_finer_than_a_cent_rounds_half_up() -> None:
    """JUDGMENT, labelled: the spec types money as a double and says nothing."""
    assert to_cents(0.565, field="x") == 57
    assert to_cents("0.564", field="x") == 56
    assert to_cents("0.005", field="x") == 1


@pytest.mark.parametrize("junk", [None, True, False, "", "abc", "1,5", "nan", "inf", [], {}])
def test_junk_is_refused_naming_the_field(junk: object) -> None:
    with pytest.raises(UnitError) as caught:
        to_cents(junk, field="checks[0].payments[0].amount")
    assert caught.value.kind is UnitErrorKind.INVALID_VALUE
    assert caught.value.field == "checks[0].payments[0].amount"


def test_negative_amounts_are_refused_unless_allowed() -> None:
    with pytest.raises(UnitError) as caught:
        to_cents(-1, field="amount")
    assert "negative" in str(caught.value)
    assert to_cents(-1.25, field="discountAmount", allow_negative=True) == -125


def test_opt_cents_passes_absent_through() -> None:
    assert opt_cents(None, field="tipAmount") is None
    assert opt_cents("1.00", field="tipAmount") == 100
