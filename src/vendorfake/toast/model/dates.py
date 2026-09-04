"""Instants on Toast's two wires, and the business date. Converts between
epoch milliseconds, the core clock's unit, and the spellings below.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiUnderstandingGuidsEntityIdentifiersAndMultilocationIds_V2.html):
REST dates are ``2025-01-15T14:30:00.000+0000``, webhook timestamps are
``2024-03-28T15:11:01.050Z``, both UTC to the millisecond, and
``businessDate`` is an integer ``yyyyMMdd``. Query dates (``GET
/ordersBulk``, toast-orders-api.yaml) are ISO-8601 with an offset;
:func:`parse_rest_date` also accepts the colon-separated form and ``Z``.

JUDGMENT: Toast documents ``closeoutHour`` and ``businessDate`` but not how
one derives from the other; this project's reading is that an instant
belongs to the local calendar day it falls on after subtracting
``closeoutHour`` hours in the restaurant's ``timeZone``.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = ["business_date", "parse_business_date", "parse_rest_date", "rest_date", "webhook_date"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

_REST_DATE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})T(?P<time>\d{2}:\d{2}:\d{2})(?:\.(?P<ms>\d{1,6}))?(?P<zone>Z|[+-]\d{2}:?\d{2})$"
)
_BUSINESS_DATE = re.compile(r"^\d{8}$")


def rest_date(epoch_ms: float) -> str:
    """``2025-01-15T14:30:00.000+0000`` -- the REST spelling, always UTC."""
    moment = _EPOCH + timedelta(milliseconds=math.floor(epoch_ms))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}+0000"


def webhook_date(epoch_ms: float) -> str:
    """``2024-03-28T15:11:01.050Z`` -- the webhook spelling."""
    moment = _EPOCH + timedelta(milliseconds=math.floor(epoch_ms))
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def parse_rest_date(text: str, *, field: str) -> int:
    """A documented date spelling as epoch milliseconds, or a 400 naming ``field``."""
    matched = _REST_DATE.match(text.strip())
    if matched is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} must be an ISO-8601 instant with milliseconds and an offset, e.g. 2025-01-15T14:30:00.000+0000.",
            field=field,
            info={"supplied": text},
        )
    zone = matched.group("zone")
    zone = "+00:00" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
    millis = (matched.group("ms") or "0").ljust(3, "0")[:3]
    try:
        moment = datetime.fromisoformat(f"{matched.group('date')}T{matched.group('time')}.{millis}000{zone}")
    except ValueError:
        # The regex checks shape only; an out-of-range date/time/offset lands here.
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} is not a real instant: the date, time or offset is out of range.",
            field=field,
            info={"supplied": text},
        ) from None
    return math.floor((moment - _EPOCH).total_seconds() * 1000)


def parse_business_date(text: str, *, field: str) -> int:
    """``yyyyMMdd`` as an integer, validated as a real calendar date."""
    stripped = text.strip()
    if _BUSINESS_DATE.match(stripped):
        try:
            datetime.strptime(stripped, "%Y%m%d")
            return int(stripped)
        except ValueError:
            pass
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be a business date in yyyyMMdd form, e.g. 20250115.",
        field=field,
        info={"supplied": text},
    )


def business_date(epoch_ms: float, *, time_zone: str, closeout_hour: int, field: str | None = None) -> int:
    """The restaurant's business date for an instant (JUDGMENT; see module
    docstring). An unknown zone falls back to UTC rather than raising. A
    calendar underflow raises the documented 400 naming ``field`` when the
    instant was caller input, or propagates when it was not
    (konyklabs/roadmap#41).
    """
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    try:
        moment = (_EPOCH + timedelta(milliseconds=math.floor(epoch_ms))).astimezone(zone)
        shifted = moment - timedelta(hours=closeout_hour)
    except (OverflowError, ValueError, OSError):
        if field is None:
            raise
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} is too far in the past or future for a business date to exist for it.",
            field=field,
        ) from None
    return shifted.year * 10000 + shifted.month * 100 + shifted.day
