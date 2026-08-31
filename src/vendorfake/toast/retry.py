"""Toast's published webhook retry schedule, and the delivery header vocabulary.

FOR: holding, in one place, every value that governs how this unit retries a
webhook delivery, wired into unit construction through
``VendorDefinition.retry_defaults`` (the core refuses to start a vendor that
declares ``webhooks`` with an empty schedule).

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiRetrySupport.html): "wait
five minutes and then resend ... wait 10 minutes and resend a second time. If
the second resend attempt fails, the Toast platform does not send the update
again." Three attempts in all, and :data:`TOAST_RETRY_SCHEDULE_MS` is the two
intervals between them.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiTimeouts.html): connect
timeout 2 s, socket timeout 2 s -- "return a 2xx response within the 2-second
window". :data:`TOAST_TIMEOUT_MS` is that window.

DOCUMENTED, and NOT reproducible here (known limitation): the same retry page
says Toast resends on a timeout, a 404, a 429 or a 5xx and *not* on any other
4xx or on a 3xx. The core's dispatcher retries every non-2xx outcome and offers
a vendor no hook to say otherwise (``core/webhooks/dispatcher.py::_run_attempt``
decides with ``200 <= status < 300``), so a subscriber answering 400 or 401 is
retried here where Toast would stop. Recorded rather than papered over: the
seam it needs is a core change, tracked as konyklabs/roadmap#40; until it
lands this fake retries on any non-2xx.

DOCUMENTED (https://doc.toasttab.com/doc/devguide/apiEndpointRequirements.html):
"updates to be sent to your endpoint more than once" -- at-least-once with no
ordering guarantee, which is what the core's dispatcher provides.

THE DELIVERY HEADERS. ``Toast-Attempt-Number`` is documented and appears on
every attempt, starting at 1 (apiHttpHeaders.html). The one retry-only header
below is this fake's, not Toast's: see :mod:`vendorfake.toast.delivery_headers`.
The retry reasons are the core's neutral outcome names verbatim, because Toast
publishes no vocabulary to translate them into.
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
"""Five minutes, then ten. Documented; three attempts in all."""

TOAST_TIMEOUT_MS = 2_000
"""The documented 2-second window."""

TOAST_TIME_SCALE = 1 / 6000
"""Compresses the schedule so a test can watch the whole cascade: the
five-minute first retry becomes 50 milliseconds and the ten-minute second one
100, keeping their ratio. The single source: the shipped profiles set no
``webhooks.retry`` of their own, so every one inherits this and the 2-second
timeout through ``retry_defaults``; a consumer profile overrides it there."""

CONTENT_TYPE = "application/json"
""""Content-Type: application/json" on every delivery (apiMessageDataSchema.html)."""

ATTEMPT_NUMBER_HEADER = "Toast-Attempt-Number"
"""Documented: "starts at 1" (apiHttpHeaders.html). On every attempt."""

RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""Retry-only, and this fake's own -- JUDGMENT, see ``delivery_headers.py``."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> the string on the wire: the identity map, because Toast
publishes no reason vocabulary."""


def toast_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged **under** whatever a profile says. A fresh
    document per call, so two units in one process share nothing mutable."""
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=TOAST_RETRY_SCHEDULE_MS,
                time_scale=TOAST_TIME_SCALE,
                timeout_ms=TOAST_TIMEOUT_MS,
            )
        )
    )
