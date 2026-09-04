"""What every Lightspeed surface is handed, and the helpers they share.

FOR: naming the dependency a surface has on its vendor -- the resolved
configuration, the two id streams, the version counter and the rate limiter --
as a protocol, so a surface module never imports
:mod:`vendorfake.lightspeed.vendor` and the vendor is free to import the
surfaces.

INVARIANT: **a surface reads its configuration through the vendor, live.**
Every member of :class:`LightspeedDeps` is a property on the vendor object, not
a value copied at route construction: a profile's ``vendor`` block resolves in
``hydrate``, which runs after the routes are built and again on every
``POST /__unit/state/reset``.

THE ONE AUTH MODE. The whole specification uses a single flat ``bearerAuth``
HTTP-bearer scheme, applied globally at the document root -- there is no second
scheme and no per-operation override anywhere in the 201 operations. So there
is one :data:`BEARER_AUTH` mode, and tenancy is the unit's (one retailer, its
``domain_prefix``) rather than a header or a path segment the way Toast's and
Clover's are.

TIME ON THIS VENDOR'S WIRE is RFC 3339 with a ``Z``: the specification's own
examples are ``"2017-05-05T05:58:53+00:00"`` for a register open time and
``'2019-08-15T23:05:28Z'`` for a retailer's ``created_at`` -- the same instant
in two spellings, in one document. :func:`wire_time` picks the ``Z`` form, so
this package spells it one way; the choice is JUDGMENT and is recorded once
here rather than at each call site.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vendorfake.core.kernel.types import UnitContext, UnitError, UnitErrorKind
from vendorfake.core.time.clock import Clock
from vendorfake.lightspeed.config import LightspeedConfig
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION
from vendorfake.lightspeed.ids import LightspeedCredentialIds, LightspeedIds
from vendorfake.lightspeed.paths import _API_PREFIX, _TOKEN_PREFIX
from vendorfake.lightspeed.ratelimit import LightspeedRateLimiter
from vendorfake.lightspeed.versioning import LightspeedVersions

__all__ = [
    "API_PREFIX",
    "BEARER_AUTH",
    "TOKEN_PREFIX",
    "LightspeedDeps",
    "is_past_ms",
    "require_retailer",
    "stamp_version",
    "wire_time",
]

BEARER_AUTH = "bearer"
"""The one ``Route.auth`` mode: ``Authorization: Bearer <access_token>``."""

API_PREFIX = _API_PREFIX
TOKEN_PREFIX = _TOKEN_PREFIX
"""Re-exported from ``paths.py``, which keeps them private so the drift test's
scan of that module's ``UPPER_SNAKE`` constants sees only route paths."""


@runtime_checkable
class LightspeedDeps(Protocol):
    """What a surface needs from the vendor that owns it."""

    @property
    def config(self) -> LightspeedConfig: ...

    @property
    def ids(self) -> LightspeedIds: ...

    @property
    def credential_ids(self) -> LightspeedCredentialIds: ...

    @property
    def versions(self) -> LightspeedVersions: ...

    @property
    def limiter(self) -> LightspeedRateLimiter: ...


def is_past_ms(instant_ms: int | None, clock: Clock) -> bool:
    """Whether the epoch-ms ``instant_ms`` is at or before the unit clock.

    ``None`` is "never expires", which is what a personal token is and what a
    refresh token is here: the authorization page states no lifetime for
    either, and inventing one would teach a consumer a rule the vendor has not
    published.
    """
    return instant_ms is not None and instant_ms <= clock.now()


def wire_time(clock: Clock, offset_ms: float = 0.0) -> str:
    """The unit's clock as this package spells an instant. See the module docstring."""
    return clock.iso_seconds(offset_ms)


def stamp_version(entity: dict[str, object], deps: LightspeedDeps) -> None:
    """Draw the next retailer-global version and stamp it on ``entity``.

    Every mutation of any resource draws one -- that is the documented meaning
    of the counter -- so this is called from inside the store mutator, where a
    write that is about to be refused has not reached it yet.
    """
    entity[OBJECT_VERSION] = deps.versions.bump()


def require_retailer(ctx: UnitContext) -> dict[str, object]:
    """The one retailer this unit serves.

    A unit with no retailer in its store cannot answer anything: every route in
    this surface is scoped to one. That is a scenario defect rather than a bad
    request, so it is the vendor's 500 and not a plausible 4xx.
    """
    rows = ctx.store.collection(COL.retailer).all()
    if not rows:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=(
                "This unit's scenario loaded no retailer. Every Lightspeed route is scoped to one; the seed "
                "document's `retailer` block is required."
            ),
        )
    return dict(rows[0])
