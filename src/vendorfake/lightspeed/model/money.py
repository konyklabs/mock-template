"""Money on Lightspeed's wire: decimal amounts as JSON strings, converted to
and from the store's integer minor units.
DOCUMENTED: amounts here are strings, not numbers
(``RegisterClosePaymentType.total`` is ``"type": "string"``, e.g. ``"255.00"``).
Invariant: ``to_minor(to_amount(c)) == c``, two decimal places always.
JUDGMENT: a sub-minor-unit input rounds half-up, and every currency's
minor-unit count is assumed two (no exponent field to read one from)."""

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
    """A wire amount (string or number) as integer minor units, or a 422
    naming ``field``. A boolean is refused despite being an ``int`` in Python."""
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
        # A finite value needing more than 28 significant digits ("1e999")
        # raises here rather than at Decimal().
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


def to_number(minor: int) -> float:
    """``25500`` -> ``255.0``; ``0`` -> ``0.0``. DOCUMENTED: sale money fields
    (``SaleTotals.price``, ``SalePayment.amount``, etc.) are JSON numbers with
    ``format: double``, unlike the string-typed totals :func:`to_amount`
    serves. JUDGMENT: always emits a float, even for whole amounts, so a
    consumer's deserialiser never sees two Python types for one field."""
    return float(Decimal(minor).scaleb(-MINOR_UNITS))
