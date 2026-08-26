"""JavaScript's numeric behaviour, written down instead of assumed.

FOR: the handful of places this port must reproduce a *JavaScript* number
rather than a Python one, so that money, retry delays and fault parameters
come out the same on both sides of the oracle.

INVARIANT: **no arithmetic that crosses the wire is left to language
defaults.** Four divergences are closed here, each of which is silent in the
direction that matters -- a wrong number rather than an exception:

``Math.round`` is not ``round``
    ``round(2.5) == 2`` in Python (banker's rounding) and
    ``Math.round(2.5) === 3``. The reference rounds a line total as
    ``Math.round(amount * quantity)``, so a ``quantity`` of ``"0.5"`` at an odd
    price is a one-cent difference that no type checker can see.
    :func:`js_round` is ``floor(x + 0.5)``, which agrees on negative halves too
    (``Math.round(-2.5) === -2`` and ``floor(-2.0) == -2``).

``Number.parseFloat`` does not raise
    ``Number.parseFloat("2 pieces") === 2`` and ``Number.parseFloat("") is
    NaN``; Python's ``float()`` raises on both. ``quantity`` is a *string*
    field in the vendor documentation this project imitates, so junk in it is
    expected traffic: the reference answers 200 with a line total of 0 where an
    unguarded ``float()`` would answer 500. :func:`js_parse_float` scans the
    longest numeric prefix and reports ``None`` rather than raising.

``Number(x)`` yields ``NaN``, and NaN is worse than a default
    Fault parameters arrive as strings on the in-band path
    (``chaos:timeout:delay_ms=250`` is split textually) and as arbitrary JSON
    on the rule path. The reference wraps every read in ``Number(...)`` or
    ``String(...)``, so a misspelled parameter becomes ``NaN`` and then a
    zero-length sleep or the literal header value ``NaN``. :func:`as_int`,
    :func:`as_float` and :func:`as_str` take an explicit default and return it
    instead, which is a JUDGMENT deviation: it turns a silently degraded fault
    into the fault the catalogue documents.

``str(3.0)`` is ``"3.0"``; ``String(3)`` is ``"3"``
    JavaScript has one number type and prints an integral value without a
    fractional part. A ``retry-after`` header built from a float parameter is
    asserted as ``"3"`` by the reference's own test. :func:`js_number` is the
    one place that trimming happens, and :func:`as_str` goes through it.
"""

from __future__ import annotations

import math
import re

__all__ = ["as_float", "as_int", "as_str", "js_number", "js_parse_float", "js_round"]

#: The longest leading run ``Number.parseFloat`` would consume: optional
#: JavaScript whitespace, an optional sign, then either ``Infinity`` or a
#: decimal with an optional exponent. Anchored at the start only -- that is the
#: whole point of a *prefix* scan.
_NUMERIC_PREFIX = re.compile(
    r"\A[\s\u00a0\ufeff]*([+-]?(?:Infinity|(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?))",
)


def js_round(value: float) -> int:
    """``Math.round``: half away from zero upwards, i.e. ``floor(x + 0.5)``.

    Returns an ``int`` because every call site in this project rounds to a
    whole number of minor currency units or a whole number of milliseconds.

    A non-finite input raises (``math.floor`` does), deliberately: an infinite
    or undefined money amount must not quietly become a number on the wire.
    Callers that may receive junk coerce through :func:`as_float` first, which
    is where the default lives.
    """
    return math.floor(value + 0.5)


def js_number(value: float) -> int | float:
    """Render a float the way JavaScript would hold it: ``100.0`` becomes ``100``.

    Not cosmetic. The value goes into ``UnitError.info`` and from there into a
    response body, so ``{"delay_ms": 100.0}`` versus ``{"delay_ms": 100}`` is a
    byte difference in a document a consumer may be diffing against the
    oracle's.
    """
    if math.isfinite(value) and float(value).is_integer():
        return int(value)
    return value


def js_parse_float(text: str) -> float | None:
    """``Number.parseFloat``: the longest numeric prefix, or ``None``.

    ``"2 pieces"`` is ``2.0``, ``"1e"`` is ``1.0`` (the exponent is incomplete,
    so the scan stops before it), ``".5"`` is ``0.5``, ``"Infinity"`` is
    ``inf``, and ``""`` -- like ``"pieces"`` -- is ``None`` where JavaScript
    says ``NaN``. ``None`` rather than ``nan`` because Python has no NaN-poison
    discipline: ``nan`` propagates through arithmetic silently, where ``None``
    forces the caller to say what absence means.
    """
    matched = _NUMERIC_PREFIX.match(text)
    if matched is None:
        return None
    return float(matched.group(1))


def as_float(value: object, default: float) -> float:
    """Coerce a fault parameter to a finite float, or fall back to ``default``.

    Follows ``Number(x)`` where it is unambiguous -- ``True`` is ``1.0``, an
    empty or whitespace-only string is ``0.0`` -- and departs from it where
    ``Number`` produces ``NaN`` or an infinity, both of which are values no
    delay, count or interval can use.
    """
    if value is None:
        return default
    # bool before int: bool is an int subclass and `Number(true) === 1`.
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
    """:func:`as_float`, truncated toward zero -- ``Math.trunc(Number(x))``.

    Truncation, not rounding: the reference reaches ``Number()`` and then uses
    the value as a count or a seconds interval, and rounding ``0.9`` up to a
    whole second of ``retry-after`` would invent a wait the rule never asked
    for.
    """
    coerced = as_float(value, float(default))
    if not math.isfinite(coerced):
        return default
    return int(coerced)


def as_str(value: object, default: str) -> str:
    """Coerce to a string the way ``String(x)`` would, integral floats included.

    ``3.0`` becomes ``"3"``, not ``"3.0"``. That single character is the
    difference between the reference's asserted ``retry-after: 3`` and a header
    a consumer's own client library would parse as invalid.
    """
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
