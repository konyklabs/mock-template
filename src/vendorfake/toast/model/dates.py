"""Instants on Toast's two wires, and the business date.

FOR: the conversions between what the store holds -- epoch milliseconds, the
core clock's unit -- and the three spellings Toast documents.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiUnderstandingGuidsEntityIdentifiersAndMultilocationIds_V2.html):
REST dates are ``2025-01-15T14:30:00.000+0000`` and webhook timestamps are
``2024-03-28T15:11:01.050Z`` -- both UTC to the millisecond, one with a
four-digit numeric offset and one with ``Z``. ``businessDate`` is an integer
``yyyyMMdd``.

DOCUMENTED (``GET /ordersBulk``, toast-orders-api.yaml): query dates arrive as
ISO-8601 with milliseconds and an offset, e.g. ``2016-01-01T14:13:12.000+0400``.
:func:`parse_rest_date` accepts that, the colon-separated offset form, and
``Z``; anything else is a 400 naming the field.

JUDGMENT -- the business date. Toast documents ``closeoutHour`` (0-12) on the
restaurant and ``businessDate`` on orders, and nothing about how one derives
from the other. This project's reading: an instant belongs to the local
calendar day it falls on in the restaurant's ``timeZone`` *after* subtracting
``closeoutHour`` hours -- so with closeout at 4, an order at 02:00 local on the
16th has business date 20250115. Held in one function so a consumer who learns
the real rule changes one line.
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
    moment = datetime.fromisoformat(f"{matched.group('date')}T{matched.group('time')}.{millis}000{zone}")
    return math.floor((moment - _EPOCH).total_seconds() * 1000)


def parse_business_date(text: str, *, field: str) -> int:
    """``yyyyMMdd`` as an integer, validated as a real calendar date."""
    stripped = text.strip()
    if _BUSINESS_DATE.match(stripped):
        try:
            datetime.strptime(stripped, "%Y%m%d")  # a calendar date, not an instant
            return int(stripped)
        except ValueError:
            pass
    raise UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} must be a business date in yyyyMMdd form, e.g. 20250115.",
        field=field,
        info={"supplied": text},
    )


def business_date(epoch_ms: float, *, time_zone: str, closeout_hour: int) -> int:
    """The restaurant's business date for an instant. JUDGMENT; module docstring.

    An unknown zone falls back to UTC rather than raising: a scenario author
    who misspelt a zone gets a business date off by hours, which a test sees,
    where a 500 on every order create would hide the order.
    """
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo("UTC")
    moment = (_EPOCH + timedelta(milliseconds=math.floor(epoch_ms))).astimezone(zone)
    shifted = moment - timedelta(hours=closeout_hour)
    return shifted.year * 10000 + shifted.month * 100 + shifted.day
