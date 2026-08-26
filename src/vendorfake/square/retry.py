"""Square's published webhook retry schedule, and the delivery header vocabulary.

FOR: holding, in one place, every value that is a documented property of
*Square's* webhook system rather than of webhook delivery in general -- the
eleven retry intervals, the ten-second acknowledgement window, the three
``square-*`` header names and the strings the ``square-retry-reason`` header
takes.

INVARIANT: **none of this appears anywhere else.** The reference put the
schedule, the timeout and all three header names in vendor-neutral core, where
the vendor-slug rule could not see them: ``http_timeout`` and ``other_error``
contain no brand name, so a check that greps for slugs would pass a build that
still shipped one vendor's retry vocabulary to every other vendor. Core now
computes a neutral :class:`~vendorfake.core.webhooks.models.DeliveryOutcome`
and this module is the only thing that knows what Square calls it.

The schedule is verbatim from the table on
https://developer.squareup.com/docs/webhooks/overview, whose columns are
"Retry attempt | Time since last attempt | Time since event":

    1  1 minute     2  2 minutes    3  4 minutes    4  8 minutes
    5  16 minutes   6  32 minutes   7  60 minutes   8  2 hours
    9  4 hours     10  8 hours     11  8 hours

:data:`SQUARE_RETRY_SCHEDULE_MS` follows the *time since last attempt* column,
row for row. Square's own cumulative column reads "2 hours" for row 7 where
63 + 60 minutes is 2h03m; that is Square's rounding of its own table and is not
a discrepancy to be "corrected" here.

Prose from the same page, all verbatim: "Square resends the event notification
for up to 24 hours after the originating event, using exponential backoff";
"Your application must respond with a 2xx status code as soon as possible to
acknowledge that the notification was received"; "If your application fails to
acknowledge the notification in a timely manner, a duplicate event is sent and
your application has 10 seconds to respond"; "Retried notifications include the
square-retry-number and square-retry-reason headers."
"""

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
"""Compresses the documented schedule so a test can watch the whole cascade.

Derived from the schedule rather than chosen: it turns the documented
one-minute first retry into ten milliseconds, which keeps the *shape* of the
backoff -- every interval keeps its ratio to every other -- while making
exhaustion observable in a test. A mock affordance, deliberately kept separate
from the schedule itself so that neither can be mistaken for the other.
"""

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
"""Core's neutral outcome -> the string Square puts in ``square-retry-reason``.

Square documents four values -- ``http_timeout``, ``http_error``,
``ssl_error``, ``other_error``. This fake produces three of them: nothing here
terminates TLS, so ``ssl_error`` is unreachable and is recorded as such rather
than mapped onto an outcome that does not mean it.
"""


def square_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged **under** whatever a profile says.

    Returned as a fresh document rather than shared, because
    ``VendorDefinition.retry_defaults`` is read at unit construction and a
    shared mutable default is the kind of thing that couples two units in one
    process.
    """
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=SQUARE_RETRY_SCHEDULE_MS,
                time_scale=SQUARE_TIME_SCALE,
                timeout_ms=SQUARE_TIMEOUT_MS,
            )
        )
    )
