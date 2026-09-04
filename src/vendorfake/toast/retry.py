"""Toast's webhook retry schedule and delivery header vocabulary, wired into
unit construction through ``VendorDefinition.retry_defaults``.

DOCUMENTED, and NOT reproducible here: Toast resends only on a timeout, 404,
429 or 5xx (apiRetrySupport.html); the core's dispatcher retries every non-2xx
instead (konyklabs/roadmap#40). Delivery is at-least-once with no ordering
guarantee (https://doc.toasttab.com/doc/devguide/apiEndpointRequirements.html).
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.config.models import ProfileDocument, RetryPolicy, WebhooksSection
from vendorfake.core.webhooks.models import DeliveryOutcome

__all__ = [
    "ATTEMPT_NUMBER_HEADER",
    "CONTENT_TYPE",
    "RETRY_REASONS",
    "RETRY_REASON_HEADER",
    "TOAST_RETRY_SCHEDULE_MS",
    "TOAST_TIMEOUT_MS",
    "TOAST_TIME_SCALE",
    "toast_retry_defaults",
]

TOAST_RETRY_SCHEDULE_MS: tuple[int, ...] = (300_000, 600_000)
"""DOCUMENTED: five minutes, then ten; three attempts in all (https://doc.toasttab.com/doc/devguide/apiRetrySupport.html)."""

TOAST_TIMEOUT_MS = 2_000
"""DOCUMENTED: 2-second connect/socket timeout (https://doc.toasttab.com/doc/devguide/apiTimeouts.html)."""

TOAST_TIME_SCALE = 1 / 6000
"""Compresses the schedule for tests: 5 min -> 50 ms, 10 min -> 100 ms."""

CONTENT_TYPE = "application/json"
"""DOCUMENTED on every delivery (apiMessageDataSchema.html)."""

ATTEMPT_NUMBER_HEADER = "Toast-Attempt-Number"
"""DOCUMENTED, starts at 1 (apiHttpHeaders.html)."""

RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""JUDGMENT -- this fake's own header, see ``delivery_headers.py``."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> wire string, identity map: Toast publishes no vocabulary."""


def toast_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged under a profile. Fresh per call."""
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=TOAST_RETRY_SCHEDULE_MS,
                time_scale=TOAST_TIME_SCALE,
                timeout_ms=TOAST_TIMEOUT_MS,
            )
        )
    )
