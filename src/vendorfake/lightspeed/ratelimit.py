"""The documented fixed-window rate limiter, and the headers it stamps.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/rate_limiting): quota is
``300 x <registers> + 50`` per app per retailer over a 5-minute window; the 429
body is ``{"error": "Too Many Requests", "message": "Rate limiting enforced"}``
with ``Retry-After`` as an RFC 1123 date. NOT MODELLED: the page's separate
per-retailer POS-staff counter (see ``LIGHTSPEED_NOT_MODELED``).

Counts every authenticated request before token checking, matching the real
limiter's request-based count.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import UnitContext, UnitError, UnitErrorKind
from vendorfake.lightspeed.errors import RATE_LIMITED_MESSAGE

__all__ = ["LightspeedRateLimiter", "RateLimitSnapshot"]


class RateLimitSnapshot:
    """What the two headers say right now."""

    __slots__ = ("limit", "remaining")

    def __init__(self, limit: int, remaining: int) -> None:
        self.limit = limit
        self.remaining = remaining


class LightspeedRateLimiter:
    """One fixed window over the unit's clock, for one retailer. The window
    boundary is ``floor(now / window)``, so a virtual clock jump opens the
    next window with no timer.
    """

    __slots__ = ("_count", "_limit", "_window_index", "_window_ms")

    def __init__(self, *, limit: int, window_ms: int) -> None:
        self._limit = limit
        self._window_ms = window_ms
        self._window_index: int | None = None
        self._count = 0

    def reset(self, *, limit: int) -> None:
        """Start again with a recomputed quota, once the seed's registers are loaded."""
        self._limit = limit
        self._window_index = None
        self._count = 0

    @property
    def limit(self) -> int:
        return self._limit

    def _index(self, ctx: UnitContext) -> int:
        return int(ctx.clock.now() // self._window_ms)

    def _roll(self, ctx: UnitContext) -> None:
        index = self._index(ctx)
        if self._window_index != index:
            self._window_index = index
            self._count = 0

    def snapshot(self, ctx: UnitContext) -> RateLimitSnapshot:
        """The headers' current values, without spending anything."""
        self._roll(ctx)
        return RateLimitSnapshot(limit=self._limit, remaining=max(self._limit - self._count, 0))

    def consume(self, ctx: UnitContext) -> None:
        """Count one request, or refuse with the documented 429; the count moves even on a refusal."""
        self._roll(ctx)
        self._count += 1
        if self._count > self._limit:
            window_start = self._index(ctx) * self._window_ms
            remaining_ms = int(window_start + self._window_ms - ctx.clock.now())
            raise UnitError(
                UnitErrorKind.RATE_LIMITED,
                detail=RATE_LIMITED_MESSAGE,
                info={
                    "limit": self._limit,
                    "window_ms": self._window_ms,
                    "retry_after_ms": max(remaining_ms, 0),
                },
            )
