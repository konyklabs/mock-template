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
"""

from __future__ import annotations

from vendorfake.core.config.models import ProfileDocument, RetryPolicy, WebhooksSection

__all__ = [
    "CLOVER_RETRY_SCHEDULE_MS",
    "CLOVER_TIMEOUT_MS",
    "CLOVER_TIME_SCALE",
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
