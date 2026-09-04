"""DOCUMENTED -- Square's webhook retry schedule and delivery header vocabulary
(https://developer.squareup.com/docs/webhooks/overview).
:data:`SQUARE_RETRY_SCHEDULE_MS` is the *time since last attempt* column,
verbatim: 1, 2, 4, 8, 16, 32, 60 minutes, then 2, 4, 8, 8 hours."""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.config.models import ProfileDocument, RetryPolicy, WebhooksSection
from vendorfake.core.webhooks.models import DeliveryOutcome

__all__ = [
    "CONTENT_TYPE",
    "INITIAL_DELIVERY_HEADER",
    "RETRY_NUMBER_HEADER",
    "RETRY_REASONS",
    "RETRY_REASON_HEADER",
    "SQUARE_RETRY_SCHEDULE_MS",
    "SQUARE_TIMEOUT_MS",
    "SQUARE_TIME_SCALE",
    "square_retry_defaults",
]

SQUARE_RETRY_SCHEDULE_MS: tuple[int, ...] = (
    60_000,
    120_000,
    240_000,
    480_000,
    960_000,
    1_920_000,
    3_600_000,
    7_200_000,
    14_400_000,
    28_800_000,
    28_800_000,
)
"""Eleven retries over twenty-four hours. See the module docstring."""

SQUARE_TIMEOUT_MS = 10_000
""""your application has 10 seconds to respond"."""

SQUARE_TIME_SCALE = 1 / 6000
"""JUDGMENT -- compresses the schedule so a test can watch the whole cascade,
keeping every interval's ratio."""

CONTENT_TYPE = "application/json"
"""The content type every delivery carries."""

RETRY_NUMBER_HEADER = "square-retry-number"
RETRY_REASON_HEADER = "square-retry-reason"
INITIAL_DELIVERY_HEADER = "square-initial-delivery-timestamp"
"""Sent on every attempt, so a consumer can measure total latency."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: "http_timeout",
    DeliveryOutcome.TRANSPORT_ERROR: "other_error",
    DeliveryOutcome.HTTP_ERROR: "http_error",
}
"""DOCUMENTED -- core's neutral outcome -> Square's ``square-retry-reason``
string. ``ssl_error`` is unreachable here since nothing terminates TLS."""


def square_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged under whatever a profile says. Fresh each
    call, since a shared mutable default would couple two units."""
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=SQUARE_RETRY_SCHEDULE_MS,
                time_scale=SQUARE_TIME_SCALE,
                timeout_ms=SQUARE_TIMEOUT_MS,
            )
        )
    )
