"""The Clover delivery retry policy -- which Clover does not publish.

FOR: holding, in one place, every value that governs how this unit retries a
webhook delivery, wired into unit construction through
``VendorDefinition.retry_defaults`` (the core refuses to start a vendor that
declares ``webhooks`` with an empty schedule).

JUDGMENT -- **all of it.** Clover documents NO retry policy, no delivery
deadline, no ordering guarantee and no dedup semantics for webhooks. The
webhooks page (https://docs.clover.com/dev/docs/webhooks, fetched 2026-08-29)
documents only the consumer's side of the contract: "the response ... needs to
be a 200 OK code". Every number below is therefore this project's invention,
chosen so that retry behaviour is *testable* rather than accurate:

* schedule ``30s, 2m, 10m, 30m, 2h`` -- five attempts, roughly geometric, a
  plausible at-least-once policy and nothing more;
* timeout 10 seconds -- a common industry acknowledgement window (it happens
  to match Square's documented one, which is a coincidence of convention, not
  a Clover fact);
* at-least-once with no ordering guarantee is what the core's dispatcher
  provides and what this schedule implies.

A consumer must not carry any of these numbers to the real Clover platform.
The Square package's retry module is the shape template; the difference is
that every value there is verbatim from Square's docs and every value here is
labelled invention.

WHAT COUNTS AS DELIVERED. The one sentence Clover publishes on the consumer's
side is "the response ... needs to be a 200 OK code". The core's dispatcher
accepts any 2xx (``200 <= status < 300``), so a callback answering 204 is
delivered here and -- reading the documentation literally -- might not be
against Clover. JUDGMENT, recorded rather than resolved: narrowing it would be
a core change for a reading the page does not clearly support. Anything else
-- a 4xx, a 5xx, a timeout, a refused connection -- is retried on the
schedule above until it succeeds or the schedule is exhausted.

THE DELIVERY HEADERS named below are this fake's, not Clover's; the reasoning
is in :mod:`vendorfake.clover.delivery_headers`. The retry reasons are the
core's neutral outcome names verbatim, because Clover publishes no vocabulary
to translate them into.
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
"""Five retries over ~2.7 hours. JUDGMENT -- see the module docstring."""

CLOVER_TIMEOUT_MS = 10_000
"""Acknowledgement window per attempt. JUDGMENT -- Clover documents none."""

CLOVER_TIME_SCALE = 1 / 6000
"""Compresses the schedule so a test can watch the whole cascade: the 30-second
first retry becomes 5 milliseconds, and every interval keeps its ratio to every
other. A mock affordance, kept separate from the schedule itself so that
neither can be mistaken for the other."""

CONTENT_TYPE = "application/json"
"""The content type every delivery carries. Documented by example: the
payload on the webhooks page is a JSON document."""

RETRY_NUMBER_HEADER = "x-vendorfake-retry-number"
RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""Retry-only, and this fake's own -- JUDGMENT, see ``delivery_headers.py``."""

INITIAL_DELIVERY_HEADER = "x-vendorfake-initial-delivery"
"""On every attempt with the same value, so a consumer can measure total
latency across a cascade. Same provenance as the two above."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> the string on the wire. The identity map, spelled out,
because the hook exists for a vendor with a documented vocabulary and this
one has none: the mapping is where Clover's words would go if it had any."""


def clover_retry_defaults() -> ProfileDocument:
    """The vendor defaults, merged **under** whatever a profile says.

    Returned as a fresh document rather than shared, because
    ``VendorDefinition.retry_defaults`` is read at unit construction and a
    shared mutable default is the kind of thing that couples two units in one
    process.
    """
    return ProfileDocument(
        webhooks=WebhooksSection(
            retry=RetryPolicy(
                schedule_ms=CLOVER_RETRY_SCHEDULE_MS,
                time_scale=CLOVER_TIME_SCALE,
                timeout_ms=CLOVER_TIMEOUT_MS,
            )
        )
    )
