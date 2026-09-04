"""What every Lightspeed surface is handed, and the helpers they share.
:class:`LightspeedDeps` is a protocol (config, id streams, version counter, rate limiter)
so a surface never imports :mod:`vendorfake.lightspeed.vendor`; every member reads live
off the vendor object, since ``hydrate`` re-resolves it on every ``POST /__unit/state/reset``.
DOCUMENTED: one flat ``bearerAuth`` scheme for the whole specification, hence one
:data:`BEARER_AUTH` mode; tenancy is the unit's single retailer, not a header or path
segment. JUDGMENT: the spec spells an instant both with an offset and with ``Z``;
:func:`wire_time` always picks ``Z``.
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
"""Re-exported from ``paths.py`` (kept private there so the drift test's scan sees only route paths)."""


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
    """Whether epoch-ms ``instant_ms`` is at or before the unit clock; ``None`` means "never expires"."""
    return instant_ms is not None and instant_ms <= clock.now()


def wire_time(clock: Clock, offset_ms: float = 0.0) -> str:
    """The unit's clock as this package spells an instant. See the module docstring."""
    return clock.iso_seconds(offset_ms)


def stamp_version(entity: dict[str, object], deps: LightspeedDeps) -> None:
    """Draw the next retailer-global version and stamp it on ``entity``; call from inside the store mutator."""
    entity[OBJECT_VERSION] = deps.versions.bump()


def require_retailer(ctx: UnitContext) -> dict[str, object]:
    """The one retailer this unit serves; a missing retailer is a scenario defect (500), not a 4xx."""
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
