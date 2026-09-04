"""Money on Lightspeed's wire: decimal amounts as JSON **strings**.

FOR: the one conversion between what the store holds -- integer minor units,
like every other vendor in this distribution -- and what Lightspeed puts on
the wire.

DOCUMENTED, and unusual enough to be worth stating plainly: the amounts on the
routes this slice serves are **strings**, not numbers. The register payments
summary's own example prints ``"total": "0.00"``, ``"total": "255.00"`` and
``"total": "1038.77"``; ``RegisterClosePaymentType.total`` is typed
``"type": "string"`` in the specification. A consumer that parses these as JSON
numbers fails here, which is the point.

INVARIANT: **minor units in, minor units out.** ``to_minor(to_amount(c)) == c``
for every integer ``c``, which is what keeps a stored amount stable across a
round trip through the wire, and is pinned by a test over a range of values.
Two decimal places always, so ``900`` is ``"9.00"`` and never ``"9"`` -- every
documented example shows two.

JUDGMENT: an input finer than a minor unit is rounded half-up rather than
refused; and a currency's minor-unit count is assumed to be two, because every
currency in the documented examples (NZD, AUD, GBP) has two and the
specification carries no exponent field to read a different one from.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["MINOR_UNITS", "to_amount", "to_minor", "to_number"]

MINOR_UNITS = 2
"""Decimal places on the wire. JUDGMENT -- see the module docstring."""

_UNIT = Decimal(1).scaleb(-MINOR_UNITS)


def to_amount(minor: int) -> str:
    """``25500`` -> ``"255.00"``; ``900`` -> ``"9.00"``; ``0`` -> ``"0.00"``."""
    return f"{Decimal(minor).scaleb(-MINOR_UNITS):.{MINOR_UNITS}f}"


def to_minor(value: object, *, field: str, allow_negative: bool = False) -> int:
    """A wire amount -- string or number -- as integer minor units, or a 422
    naming ``field``.

    A number is accepted although the vendor documents a string: a consumer
    sending ``255.0`` means ``"255.00"``, and refusing it would fail on the
    thing that is not under test. A boolean is refused although it is an
    ``int`` in Python; ``true`` is not an amount.
    """
    if isinstance(value, bool) or value is None:
        raise _refuse(field, value)
    try:
        if isinstance(value, int):
            amount = Decimal(value)
        elif isinstance(value, float):
            amount = Decimal(repr(value))
        elif isinstance(value, str):
            amount = Decimal(value.strip())
        else:
            raise _refuse(field, value)
    except InvalidOperation:
        raise _refuse(field, value) from None
    if not amount.is_finite():
        raise _refuse(field, value)
    try:
        minor = int(amount.quantize(_UNIT, rounding=ROUND_HALF_UP).scaleb(MINOR_UNITS))
    except InvalidOperation:
        # A FINITE value needing more than the decimal context's 28 significant
        # digits ("1e999") raises here rather than at Decimal().
        raise _refuse(field, value) from None
    if minor < 0 and not allow_negative:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must not be negative.",
            field=field,
            info={"supplied": value},
        )
    return minor


def _refuse(field: str, value: object) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f'{field} must be a decimal amount (Lightspeed sends these as strings, e.g. "255.00").',
        field=field,
        info={"supplied": value if isinstance(value, str | int | float) else str(value)},
    )


# ---------------------------------------------------------------------------
# SALES MONEY (slice L2b of konyklabs/roadmap#94).
# ---------------------------------------------------------------------------


def to_number(minor: int) -> float:
    """``25500`` -> ``255.0``; ``103877`` -> ``1038.77``; ``0`` -> ``0.0``.

    THE SECOND WIRE SHAPE, and it is the vendor's, not an inconsistency of this
    package's. The register close totals are decimal **strings**
    (``RegisterClosePaymentType.total`` is ``"type": "string"``, and its
    examples print ``"255.00"``); every money member of a *sale* is a JSON
    **number** with ``format: double`` -- ``SaleTotals.price``,
    ``SaleTotals.price_incl_tax``, ``SaleTotals.tax``, ``LineItemPricing.price``,
    ``LineItemPricing.cost``, ``LineItemPricing.discount``,
    ``LineItemTax.amount`` and ``SalePayment.amount`` are all typed that way. A
    consumer that assumes one shape across the whole API fails on the other,
    which is exactly what a fake is for.

    JUDGMENT: a **float** is emitted even where the value is whole, so
    ``255.00`` reaches the wire as ``255.0`` rather than as ``255``. Both are
    valid JSON for ``type: number`` and the vendor's own examples print whole
    amounts as integers (``"price": 200``); emitting one Python type for every
    amount is the choice that keeps a consumer's deserialiser from seeing an
    ``int`` on one line item and a ``float`` on the next.

    :func:`to_minor` is the inverse for both shapes and accepts a number, so
    ``to_minor(to_number(c), field=...) == c`` for every integer ``c`` a
    two-place decimal can hold -- pinned by a test.
    """
    return float(Decimal(minor).scaleb(-MINOR_UNITS))
