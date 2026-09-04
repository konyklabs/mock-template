"""Numeric coercions vendor wire behaviour depends on: half-away-from-zero rounding, an integral float
rendered without its fractional part, and a quantity string that must answer 200, not 500, on junk.
JUDGMENT: :func:`as_int`, :func:`as_float` and :func:`as_str` default a bad fault parameter rather than degrade it silently.
"""

from __future__ import annotations

import math
import re

__all__ = ["as_float", "as_int", "as_str", "js_number", "js_parse_float", "js_round"]

#: The longest leading numeric run accepted, anchored at the start.
_NUMERIC_PREFIX = re.compile(
    r"\A[\s\u00a0\ufeff]*([+-]?(?:Infinity|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?))",
)


def js_round(value: float) -> int:
    """Half away from zero upwards (``floor(x + 0.5)``); a non-finite input raises rather than reach the wire."""
    return math.floor(value + 0.5)


def js_number(value: float) -> int | float:
    """Render an integral float without its fractional part: ``100.0`` becomes ``100`` in a response body."""
    if math.isfinite(value) and float(value).is_integer():
        return int(value)
    return value


def js_parse_float(text: str) -> float | None:
    """The longest numeric prefix of ``text``, or ``None`` (not a poisoning NaN) if it has none."""
    matched = _NUMERIC_PREFIX.match(text)
    if matched is None:
        return None
    return float(matched.group(1))


def as_float(value: object, default: float) -> float:
    """Coerce a fault parameter to a finite float, or ``default`` (``True`` is ``1.0``; blank is ``0.0``)."""
    if value is None:
        return default
    # bool before int: bool is an int subclass, and True must coerce to 1.0.
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, int):
        return float(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else default
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return 0.0
        try:
            parsed = float(stripped)
        except ValueError:
            return default
        return parsed if math.isfinite(parsed) else default
    return default


def as_int(value: object, default: int) -> int:
    """:func:`as_float`, truncated toward zero, not rounded, so a fraction never invents an extra second."""
    coerced = as_float(value, float(default))
    if not math.isfinite(coerced):
        return default
    return int(coerced)


def as_str(value: object, default: str) -> str:
    """Coerce to a string, integral floats included: ``3.0`` becomes ``"3"``, not ``"3.0"``."""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(js_number(value))
    return default
