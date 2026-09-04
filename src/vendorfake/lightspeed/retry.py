"""Lightspeed's webhook retry schedule, and the delivery header vocabulary.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks): 5 seconds
to answer with 2xx, "up to 20 times over 48h" with an unspecified exponential
backoff, 3xx/4xx should not retry -- NOT reproducible here, since the core
dispatcher retries every non-2xx outcome (konyklabs/roadmap#40). JUDGMENT:
the ladder below doubles from 30 seconds and caps at 4 hours, fitting twenty
attempts inside 48 hours (checked at import via :data:`TOTAL_LADDER_MS`).
Lightspeed documents one delivery header, ``X-Signature``, plus content
type; the rest here is this fake's own ``x-vendorfake-`` prefix.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.config.models import ProfileDocument, RetryPolicy, WebhooksSection
from vendorfake.core.webhooks.models import DeliveryOutcome

__all__ = [
    "ATTEMPT_NUMBER_HEADER",
    "CONTENT_TYPE",
    "DOCUMENTED_ATTEMPTS",
    "DOCUMENTED_WINDOW_MS",
    "LIGHTSPEED_RETRY_SCHEDULE_MS",
    "LIGHTSPEED_TIMEOUT_MS",
    "LIGHTSPEED_TIME_SCALE",
    "RETRY_REASONS",
    "RETRY_REASON_HEADER",
    "TOTAL_LADDER_MS",
    "lightspeed_retry_defaults",
]

DOCUMENTED_ATTEMPTS = 20
"""'up to 20 times' -- attempts in all, so nineteen intervals."""

DOCUMENTED_WINDOW_MS = 48 * 60 * 60 * 1000
"""'over 48h' -- the bound every interval below has to fit inside."""

LIGHTSPEED_TIMEOUT_MS = 5_000
"""'5 seconds to respond with a 2xx response'."""

_FIRST_INTERVAL_MS = 30_000
_CAP_MS = 4 * 60 * 60 * 1000


def _ladder() -> tuple[int, ...]:
    """Doubling from 30 seconds, each interval capped at four hours."""
    intervals: list[int] = []
    delay = _FIRST_INTERVAL_MS
    for _ in range(DOCUMENTED_ATTEMPTS - 1):
        intervals.append(delay)
        delay = min(delay * 2, _CAP_MS)
    return tuple(intervals)


LIGHTSPEED_RETRY_SCHEDULE_MS: tuple[int, ...] = _ladder()
"""Nineteen intervals between twenty attempts. JUDGMENT; see the module docstring."""

TOTAL_LADDER_MS = sum(LIGHTSPEED_RETRY_SCHEDULE_MS)
"""How long the whole cascade takes. Must be inside the documented 48 hours."""

LIGHTSPEED_TIME_SCALE = 1 / 15_000
"""Compresses the schedule for tests, keeping every ratio; the 2ms floor
(30000 x scale) is what conformance C21 needs to stay short of an interval."""

CONTENT_TYPE = "application/x-www-form-urlencoded"
"""DOCUMENTED outbound shape only -- registering a webhook is ordinary JSON."""

ATTEMPT_NUMBER_HEADER = "x-vendorfake-attempt-number"
"""This fake's, not Lightspeed's: the vendor documents no attempt header."""

RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""Retry-only, and this fake's own -- see ``delivery_headers.py``."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> wire string, an identity map since Lightspeed publishes no
reason vocabulary."""


def lightspeed_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged **under** whatever a profile says. A fresh
    document per call, so two units in one process share nothing mutable."""
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=LIGHTSPEED_RETRY_SCHEDULE_MS,
                time_scale=LIGHTSPEED_TIME_SCALE,
                timeout_ms=LIGHTSPEED_TIMEOUT_MS,
            )
        )
    )


if len(LIGHTSPEED_RETRY_SCHEDULE_MS) != DOCUMENTED_ATTEMPTS - 1:
    raise RuntimeError(
        f"the ladder must hold {DOCUMENTED_ATTEMPTS - 1} intervals for the documented "
        f"{DOCUMENTED_ATTEMPTS} attempts, not {len(LIGHTSPEED_RETRY_SCHEDULE_MS)}"
    )
if TOTAL_LADDER_MS > DOCUMENTED_WINDOW_MS:
    raise RuntimeError(
        f"the twentieth attempt lands {TOTAL_LADDER_MS} ms after the first, outside the documented "
        f"{DOCUMENTED_WINDOW_MS} ms window"
    )
