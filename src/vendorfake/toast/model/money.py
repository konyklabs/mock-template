"""Money on Toast's wire: decimal dollars as JSON numbers.

FOR: the one conversion between what the store holds -- integer cents, like
every other vendor in this distribution -- and what Toast puts on the wire.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiOrderPrices.html and the
orders specification): every amount is ``number``/``double`` in *dollars* --
a check ``amount`` of ``8.99``, ``taxAmount`` ``0.56``, ``totalAmount``
``9.55``; a selection's ``preDiscountPrice`` ``8.99``; a tax rate of
``0.0625``. One guide example
(https://doc.toasttab.com/doc/devguide/apiCreatingAnOrderWithPaymentInformation.html)
shows the same fields as strings (``"amount": "9.55"``), so input accepts both
spellings (audit gap 8) and output is always a number.

INVARIANT: **cents in, cents out.** ``to_cents(to_dollars(c)) == c`` for every
integer ``c``, which is what keeps a stored amount stable across a round trip
through the wire and is pinned by a test over a range of values. The float
emitted for ``899`` is ``8.99`` exactly as :func:`json.dumps` prints it, because
it is built from the decimal ``8.99`` and not from ``899 / 100``.

JUDGMENT: an input finer than a cent is rounded half-up to the cent rather
than refused. The specification types money as a double and says nothing about
precision; a consumer summing ``8.99 + 0.56`` in binary floating point sends
``9.549999999999999`` and means ``9.55``.
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
    """A wire amount -- number or string -- as integer cents, or a 400 naming ``field``.

    A boolean is refused although it is an ``int`` in Python; ``true`` is not
    an amount. A negative amount is refused unless the caller says otherwise
    (a documented 400 on ``POST /orders`` is "a negatively priced item").
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
        # Outside the parse guard above until konyklabs/roadmap#41: a FINITE
        # value needing more than the context's 28 significant digits (1e308,
        # "1e999") raises InvalidOperation here, not at Decimal().
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
