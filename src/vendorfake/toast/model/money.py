"""Money on Toast's wire: decimal dollars as JSON numbers, converted from the
integer cents every vendor here stores internally.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiOrderPrices.html): amounts are ``number``/``double`` dollars;
one guide example (https://doc.toasttab.com/doc/devguide/apiCreatingAnOrderWithPaymentInformation.html) shows the
same fields as strings, so input accepts both and output is always a number.

INVARIANT: ``to_cents(to_dollars(c)) == c`` for every integer ``c``.

JUDGMENT: an input finer than a cent rounds half-up rather than being refused.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["opt_cents", "to_cents", "to_dollars"]

_CENT = Decimal("0.01")


def to_dollars(cents: int) -> float:
    """``899`` -> ``8.99``; ``900`` -> ``9.0``; ``0`` -> ``0.0``."""
    return float(Decimal(cents).scaleb(-2))


def to_cents(value: object, *, field: str, allow_negative: bool = False) -> int:
    """A wire amount -- number or string -- as integer cents, or a 400 naming
    ``field``. A boolean is refused despite being an ``int`` in Python; a
    negative amount is refused unless allowed (documented 400: "a negatively
    priced item")."""
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
        # Outside the parse guard until konyklabs/roadmap#41: an extreme
        # finite value (e.g. "1e999") can overflow here, not above.
        cents = int(amount.quantize(_CENT, rounding=ROUND_HALF_UP).scaleb(2))
    except InvalidOperation:
        raise _refuse(field, value) from None
    if cents < 0 and not allow_negative:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must not be negative.",
            field=field,
            info={"supplied": value},
        )
    return cents


def opt_cents(value: object, *, field: str, allow_negative: bool = False) -> int | None:
    """:func:`to_cents`, with ``None`` passing through as absent."""
    if value is None:
        return None
    return to_cents(value, field=field, allow_negative=allow_negative)


def _refuse(field: str, value: object) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be a decimal amount in dollars (a number, or a numeric string).",
        field=field,
        info={"supplied": value if isinstance(value, str | int | float) else str(value)},
    )
