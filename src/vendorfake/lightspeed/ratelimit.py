"""The documented fixed-window rate limiter, and the headers it stamps.

DOCUMENTED, all of it, on https://x-series-api.lightspeedhq.com/docs/rate_limiting:

* the quota is "300 x <number of registers> + 50", per retailer per
  application -- so a one-register store gets 350 requests per window;
* "The rate limiter is currently based on a 5 minute (300 seconds) window" --
  a FIXED window, not a leaky bucket. X-Series does not share the R-Series
  product line's mechanism, and a build that carried that assumption over
  would be wrong;
* ``X-RateLimit-Limit`` and ``X-RateLimit-Remaining`` are present on EVERY
  response (the page's own example values, 100/99, do not match the formula
  and are illustrative only -- the formula is the fact);
* the 429 body is ``{"error": "Too Many Requests", "message": "Rate limiting
  enforced"}`` with ``Retry-After`` as an RFC 1123 date.

NOT MODELLED, and recorded: the page describes TWO independent counters --
"per retailer per application" for integrated traffic and "per retailer for
all users" for in-store staff -- so that application requests cannot starve
POS transactions. This unit has no POS UI and therefore models the
application counter only. See ``LIGHTSPEED_NOT_MODELED``.

WHY THIS IS IN THE VENDOR PACKAGE. The core has no rate-limit seam. It has a
``rate_limit`` *chaos fault*, which is a different thing: that one is armed by
a rule and fires on a schedule a test chose, where this counts real requests
and refuses when a documented quota is spent. Nothing in ``core/`` was
extended for this.

**THE LIMITER IS NOT CHAOS AND THE ``no-chaos`` PROFILE DOES NOT SWITCH IT
OFF.** It is vendor behaviour, so it runs on every profile. The quota is a
knob on :class:`~vendorfake.lightspeed.config.LightspeedConfig` instead, and
the shipped seed's two registers put it at 650 per five minutes -- far above
anything a conformance run or a consumer's test spends.

WHERE IT IS CALLED. Every authenticated route goes through
:meth:`~vendorfake.lightspeed.auth.LightspeedAuth.resolve`, which consumes one
unit of quota before it looks at the token: the real limiter counts requests,
not successful ones. The two unauthenticated routes -- the ``/connect``
stand-in and the token endpoint -- consume explicitly in their handlers. The
control plane does not: ``/__unit/*`` is this project's own side channel and
not part of any vendor's documented quota.
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
    """One fixed window over the unit's clock, for one retailer.

    Held on the vendor rather than in the store for the same reason the version
    counter is: it is a counter, not an entity, and a row in the store would be
    a row every state digest had to know to ignore. :meth:`reset` runs at
    hydrate, so ``POST /__unit/state/reset`` starts a fresh window.

    The window boundary is computed from the clock, not from a stored start
    instant, so a virtual clock advanced past the boundary opens the next
    window with no timer and no background work: ``floor(now / window)`` is the
    window's index, and a change of index is a new window.
    """

    __slots__ = ("_count", "_limit", "_window_index", "_window_ms")

    def __init__(self, *, limit: int, window_ms: int) -> None:
        self._limit = limit
        self._window_ms = window_ms
        self._window_index: int | None = None
        self._count = 0

    def reset(self, *, limit: int) -> None:
        """Start again with a recomputed quota. Called at hydrate, once the
        seed's registers are loaded and ``300 x registers + 50`` is known."""
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
        """The headers' current values, without spending anything.

        Read by ``vendor.decorate`` on the way out, so the two headers ride
        every answer -- the success, the shaped refusal, and the 429 itself.
        """
        self._roll(ctx)
        return RateLimitSnapshot(limit=self._limit, remaining=max(self._limit - self._count, 0))

    def consume(self, ctx: UnitContext) -> None:
        """Count one request, or refuse it with the documented 429.

        The refusal carries ``retry_after_ms``: how long is left of this
        window, which is what the ``Retry-After`` date is computed from in
        ``errors.py``. The count still moves on a refused request, because the
        real limiter counts requests rather than answers -- a caller who keeps
        hammering a spent window does not get their quota back by being
        refused.
        """
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
