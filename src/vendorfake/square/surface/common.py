"""What every Square surface is handed, and the two readers they share.

FOR: naming the dependency a surface has on its vendor -- the resolved
configuration and the unit's id stream -- as a protocol, so a surface module
never imports :mod:`vendorfake.square.vendor` and the vendor is free to import
the surfaces.

INVARIANT: **a surface reads its configuration through the vendor, live.** Both
members below are properties on the vendor object, not values copied at route
construction: a profile's ``vendor`` block resolves in ``hydrate``, which runs
*after* the routes are built and again on every ``POST /__unit/state/reset``.
A surface that captured ``config.application_id`` when its routes were made
would authenticate against the default secret forever, and the symptom would be
an OAuth flow that fails with credentials the operator can see in the profile.

The same reasoning applies with more force to ``ids``: there is exactly one id
stream per unit, and it is re-seeded at hydrate. A surface that built its own
:class:`~vendorfake.square.ids.SquareIds` would draw the same ids as the
vendor's, so two collections would mint colliding identifiers from step one.

The reference's ``readBody`` is deliberately absent. It moved into the core as
``HandlerArgs.body()``, which is content-type general, so every vendor inherits
the guarantee rather than rediscovering it -- and so no transport adapter is
ever asked to decide what a body is.

:func:`is_expired` is here rather than in either caller because both the auth
adapter and the OAuth surface answer "has this timestamp passed?", and Square's
two answers -- an expired access token and an expired authorization code --
must agree about what the boundary instant means.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from vendorfake.core.time.clock import Clock
from vendorfake.square.config import SquareConfig
from vendorfake.square.ids import SquareIds

__all__ = ["SquareDeps", "is_expired"]

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
    """Whether the RFC 3339 instant ``at`` is at or before the unit clock.

    At **or** before: the reference writes ``Date.parse(expiresAt) <=
    clock.now()``, so the expiry instant itself is already too late, and a
    consumer that advances a virtual clock by exactly the TTL must see the
    token gone rather than land on the one millisecond where it still works.

    An unparseable value reads as not-yet-expired. That is the direction
    ``Date.parse`` takes too -- ``NaN <= now`` is ``false`` -- and it is the
    safer of the two: a hand-edited seed with a malformed timestamp produces a
    token that keeps working, not one that has already expired everywhere.
    """
    try:
        parsed = datetime.fromisoformat(at)
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return (parsed - _EPOCH).total_seconds() * 1000.0 <= clock.now()
