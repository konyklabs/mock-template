"""What every Square surface is handed, and the two readers they share.

A surface reads its configuration and id stream through the vendor, live, so
each is re-resolved after every ``POST /__unit/state/reset``."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from vendorfake.core.time.clock import Clock
from vendorfake.square.config import SquareConfig
from vendorfake.square.ids import SquareIds

__all__ = ["SquareDeps", "instant_ms", "is_expired"]

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@runtime_checkable
class SquareDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> SquareConfig:
        """The resolved configuration, re-read on every access."""
        ...

    @property
    def ids(self) -> SquareIds:
        """This unit's one id stream."""
        ...


def is_expired(at: str, clock: Clock) -> bool:
    """True when the RFC 3339 instant ``at`` is at or before the unit clock;
    an unparseable value reads as not-yet-expired."""
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH).total_seconds() * 1000.0 <= clock.now()


def instant_ms(value: str | None) -> float | None:
    """An RFC 3339 timestamp as epoch milliseconds, or ``None`` when absent or
    unparseable, which callers treat as "no opinion"."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH).total_seconds() * 1000.0
