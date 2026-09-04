"""The three scalar spellings the products/inventory/customers wire uses.
DOCUMENTED: money here is a JSON number, unlike the string-typed
register-close totals (``model/money.py``); vendor examples print varying
precision (``110``, ``126.5``, ``2.63158``). Values are stored as decimal
TEXT, becoming a number only at :func:`wire_number`, quantized to
:data:`DECIMAL_PLACES` (5) with half-up rounding."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["DECIMAL_PLACES", "decimal_text", "wire_instant", "wire_number"]

DECIMAL_PLACES = 5
"""How many decimal places a stored value keeps. See the module docstring."""

_UNIT = Decimal(1).scaleb(-DECIMAL_PLACES)


def decimal_text(value: object, *, field: str, allow_negative: bool = False) -> str:
    """A caller's number (``int``, ``float``, ``Decimal`` or numeric string) as
    the canonical decimal text this package stores, or a 422 naming ``field``.
    ``Decimal`` is accepted so a computed value shares the caller's rounding;
    ``bool`` is refused although it is an ``int`` in Python."""
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
            # Coerced like `_refuse`: `info` is serialised with `json.dumps`,
            # which has no Decimal encoder, so an uncoerced Decimal here would
            # turn a shaped 422 into an unhandled TypeError.
            info={"supplied": value if isinstance(value, str | int | float) else str(value)},
        )
    return _canonical(amount, field=field, supplied=value)


def _canonical(amount: Decimal, *, field: str, supplied: object) -> str:
    try:
        quantized = amount.quantize(_UNIT, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # A finite value needing more than 28 significant digits ("1e999")
        # raises here rather than at Decimal().
        raise _refuse(field, supplied) from None
    # `normalize` can produce exponent notation; format(..., "f") never does.
    return format(quantized.normalize(), "f")


def wire_number(text: str | None) -> int | float | None:
    """Stored decimal text as the JSON number the wire carries. An integral
    value comes back as ``int`` (``"110"`` -> ``110``, not ``110.0``),
    matching the vendor's own examples; ``None`` passes through."""
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
    """The store's millisecond stamp as this vendor's seconds spelling:
    ``"...12:00:00.000Z"`` -> ``"...12:00:00Z"``. No fraction: unchanged."""
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
