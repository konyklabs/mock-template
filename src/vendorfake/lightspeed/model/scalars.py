"""The three scalar spellings the products/inventory/customers wire uses.

FOR: one place that knows how this vendor writes a decimal number, a quantity
and an instant, so a projection never has to think about float repr, exponent
notation or a locale.

DOCUMENTED, and the reason this module exists at all: **money is a JSON
*number* on this half of the surface and a JSON *string* on the other.** The
register payments summary prints ``"total": "255.00"`` and
``RegisterClosePaymentType.total`` is typed ``string`` (see ``model/money.py``);
``Product.price_excluding_tax`` is typed ``number`` and the specification's own
example prints ``110``, ``126.5`` and ``2.63158`` -- an integer, one decimal
place and five, in the same document. ``Inventory.current_inventory_level`` and
its siblings are ``format: double``, likewise numbers. A consumer that assumes
one spelling across the API is wrong here, which is exactly the kind of thing a
fake exists to show them.

HOW A NUMBER SURVIVES A ROUND TRIP. The store holds the decimal TEXT and
:func:`wire_number` turns it into the JSON number at the edge. Holding a float
instead would make ``12.50`` come back as ``12.500000000000002`` on some path
eventually, and would put a platform-dependent value into the state digest that
two units are required to agree on. Text in, text stored, number out.

FIVE DECIMAL PLACES, and why that number. ``2.63158`` is the vendor's own
rendering of a tax-exclusive price, so five is the precision the documented
examples actually carry. :func:`decimal_text` quantizes to five and then
normalises, so ``12.50`` stores as ``"12.5"`` and prints as ``12.5``, and
``110.00`` stores as ``"110"`` and prints as ``110`` -- both of which are what
the examples show. Rounding is half-up, matching ``model/money.py``.

INSTANTS. The wire spelling is the one ``surface/common.wire_time`` picks --
RFC 3339 seconds with a ``Z``. The core store stamps its own ``created_at`` and
``updated_at`` in milliseconds (``2026-09-04T12:00:00.000Z``), and those two
fields are the ones ``Product`` and ``Customer`` publish, so
:func:`wire_instant` re-spells the store's stamp rather than the package
keeping a second copy of every timestamp.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["DECIMAL_PLACES", "decimal_text", "wire_instant", "wire_number"]

DECIMAL_PLACES = 5
"""How many decimal places a stored value keeps. See the module docstring."""

_UNIT = Decimal(1).scaleb(-DECIMAL_PLACES)


def decimal_text(value: object, *, field: str, allow_negative: bool = False) -> str:
    """A caller's number -- ``int``, ``float``, ``Decimal`` or numeric string --
    as the canonical decimal text this package stores, or a 422 naming ``field``.

    ``Decimal`` is accepted so a value this package computed (a derived price,
    an adjusted stock level) goes back to text through the same function a
    caller's value does, and cannot pick up a second rounding rule on the way.

    ``True`` is refused although it is an ``int`` in Python: a boolean is not a
    price and not a quantity.
    """
    if isinstance(value, bool) or value is None:
        raise _refuse(field, value)
    try:
        if isinstance(value, Decimal):
            amount = value
        elif isinstance(value, int):
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
    if amount < 0 and not allow_negative:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must not be negative.",
            field=field,
            info={"supplied": value},
        )
    return _canonical(amount, field=field, supplied=value)


def _canonical(amount: Decimal, *, field: str, supplied: object) -> str:
    try:
        quantized = amount.quantize(_UNIT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # A FINITE value needing more than the decimal context's 28 significant
        # digits ("1e999") raises here rather than at Decimal().
        raise _refuse(field, supplied) from None
    # `normalize` drops trailing zeros but can produce exponent notation
    # (Decimal("110.00000") -> Decimal("1.1E+2")); format(..., "f") never does.
    return format(quantized.normalize(), "f")


def wire_number(text: str | None) -> int | float | None:
    """Stored decimal text as the JSON number the wire carries.

    An integral value comes back as an ``int`` -- ``"110"`` -> ``110``, not
    ``110.0`` -- because the vendor's own examples print
    ``"price_excluding_tax": 110`` and ``"supply_price": 0``. ``None`` passes
    through, so a nullable member stays null.
    """
    if text is None:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:  # pragma: no cover - stored text is always ours
        return None
    if amount == amount.to_integral_value():
        return int(amount)
    return float(amount)


def wire_instant(stored: str | None) -> str | None:
    """The store's millisecond stamp as this vendor's seconds spelling.

    ``"2026-09-04T12:00:00.000Z"`` -> ``"2026-09-04T12:00:00Z"``. A value that
    already carries no fraction is returned unchanged, so a seed that states an
    instant in the vendor's own spelling passes through untouched.
    """
    if stored is None:
        return None
    head, _, tail = stored.partition(".")
    if not tail:
        return stored
    suffix = "Z" if tail.endswith("Z") else ""
    return f"{head}{suffix}"


def _refuse(field: str, value: object) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be a number (Lightspeed sends prices and quantities as JSON numbers here).",
        field=field,
        info={"supplied": value if isinstance(value, str | int | float) else str(value)},
    )
