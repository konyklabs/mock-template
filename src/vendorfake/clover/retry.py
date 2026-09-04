"""The Clover delivery retry policy -- which Clover does not publish.

JUDGMENT -- all of it. Clover documents only the consumer's side ("the
response ... needs to be a 200 OK code",
https://docs.clover.com/dev/docs/webhooks); no retry/deadline/dedup
semantics. The dispatcher accepts any 2xx as delivered.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.config.models import ProfileDocument, RetryPolicy, WebhooksSection
from vendorfake.core.webhooks.models import DeliveryOutcome

__all__ = [
    "CLOVER_RETRY_SCHEDULE_MS",
    "CLOVER_TIMEOUT_MS",
    "CLOVER_TIME_SCALE",
    "CONTENT_TYPE",
    "INITIAL_DELIVERY_HEADER",
    "RETRY_NUMBER_HEADER",
    "RETRY_REASONS",
    "RETRY_REASON_HEADER",
    "clover_retry_defaults",
]

CLOVER_RETRY_SCHEDULE_MS: tuple[int, ...] = (
    30_000,
    120_000,
    600_000,
    1_800_000,
    7_200_000,
)
"""Five retries over ~2.7 hours. JUDGMENT."""

CLOVER_TIMEOUT_MS = 10_000
"""Acknowledgement window per attempt. JUDGMENT."""

CLOVER_TIME_SCALE = 1 / 6000
"""Compresses the schedule for tests, keeping each interval's ratio."""

CONTENT_TYPE = "application/json"
"""The content type every delivery carries, documented by example."""

RETRY_NUMBER_HEADER = "x-vendorfake-retry-number"
RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""This fake's own headers -- JUDGMENT."""

INITIAL_DELIVERY_HEADER = "x-vendorfake-initial-delivery"
"""Same value on every attempt, for measuring total cascade latency."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> wire string; Clover publishes no vocabulary of its own."""


def clover_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged **under** whatever a profile says."""
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=CLOVER_RETRY_SCHEDULE_MS,
                time_scale=CLOVER_TIME_SCALE,
                timeout_ms=CLOVER_TIMEOUT_MS,
            )
        )
    )
