"""What every Clover surface is handed, and the time helpers they share.

FOR: naming the dependency a surface has on its vendor -- the resolved
configuration and the unit's id stream -- as a protocol, so a surface module
never imports :mod:`vendorfake.clover.vendor` and the vendor is free to import
the surfaces.

INVARIANT: **a surface reads its configuration through the vendor, live.**
Both members of :class:`CloverDeps` are properties on the vendor object, not
values copied at route construction: a profile's ``vendor`` block resolves in
``hydrate``, which runs *after* the routes are built and again on every
``POST /__unit/state/reset``. A surface that captured ``config.client_id``
when its routes were made would authenticate against the default secret
forever. The same holds harder for ``ids``: there is exactly one id stream per
unit, re-seeded at hydrate.

The two time helpers are the only place this package converts between its
units. Entities store epoch **milliseconds** (the core clock's unit, and
Clover's own for ``createdTime``/``modifiedTime``); the documented OAuth wire
carries Unix **seconds** (``"access_token_expiration": 1677875430``,
https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token).
:func:`wire_seconds` is that conversion, spelled once, floor division so a
value never rounds up into a second that has not arrived.

:func:`is_past_ms` answers "has this stored instant passed?" for both the auth
adapter and the OAuth surface, with the same boundary the Square package
settled on: **at or before** is expired, so a consumer that advances a virtual
clock by exactly the TTL sees the token gone rather than landing on the one
millisecond where it still works.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vendorfake.clover.config import CloverConfig
from vendorfake.clover.ids import CloverIds
from vendorfake.core.time.clock import Clock

__all__ = ["CloverDeps", "is_past_ms", "wire_seconds"]


@runtime_checkable
class CloverDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> CloverConfig:
        """The resolved configuration, re-read on every access."""
        ...

    @property
    def ids(self) -> CloverIds:
        """This unit's one id stream."""
        ...


def is_past_ms(instant_ms: int, clock: Clock) -> bool:
    """Whether the epoch-ms ``instant_ms`` is at or before the unit clock."""
    return instant_ms <= clock.now()


def wire_seconds(instant_ms: int) -> int:
    """An epoch-ms instant as the Unix-seconds integer the OAuth wire carries.

    Floor division, deliberately: truncating matches the Square package's
    iso-seconds convention (never round up into a second that has not
    happened) and keeps ``wire_seconds(is_past_ms boundary)`` on the safe
    side.
    """
    return instant_ms // 1000
