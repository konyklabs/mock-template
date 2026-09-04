"""Lightspeed's webhook retry schedule, and the delivery header vocabulary.

FOR: holding, in one place, every value that governs how this unit retries a
webhook delivery, wired into unit construction through
``VendorDefinition.retry_defaults`` (the core refuses to start a vendor that
declares ``webhooks`` with an empty schedule).

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/webhooks):

* the subscriber has "5 seconds to respond with a 2xx response" --
  :data:`LIGHTSPEED_TIMEOUT_MS`;
* delivery is attempted "up to 20 times over 48h";
* the backoff is "exponential", with no formula and no interval numbers;
* "3xx and 4xx will not trigger retries".

JUDGMENT -- **the intervals are this project's, and the ladder is written down
once, here.** "Exponential" plus "20 attempts" plus "48 hours" is three
constraints and no numbers. The ladder below doubles from 30 seconds and caps
each interval at 4 hours, which is what makes twenty attempts fit:

    30s, 1m, 2m, 4m, 8m, 16m, 32m, 1h04, 2h08, 4h, 4h, 4h, ... 4h

Nineteen intervals separate twenty attempts. Their sum is
:data:`TOTAL_LADDER_MS`, checked at import to be inside 48 hours -- an
assertion rather than a comment, because a later edit that lengthened one
interval would otherwise silently break the one documented bound this ladder
has to respect.

DOCUMENTED, and NOT reproducible here (known limitation, the same one Toast
records): the core's dispatcher retries every non-2xx outcome and offers a
vendor no hook to say otherwise
(``core/webhooks/dispatcher.py::_run_attempt`` decides with
``200 <= status < 300``), so a subscriber answering 400 or 404 is retried here
where Lightspeed would stop. The seam it needs is a core change, tracked as
konyklabs/roadmap#40.

THE DELIVERY HEADERS. Lightspeed documents exactly one -- ``X-Signature`` --
plus the content type. Everything else on a delivery here is this fake's, is
prefixed ``x-vendorfake-`` so a consumer cannot mistake it for the vendor's,
and is described in :mod:`vendorfake.lightspeed.delivery_headers`.
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
"""Compresses the schedule so a test can watch the whole cascade without
waiting two days: the 30-second first retry becomes 2 milliseconds and the
four-hour tail 960, keeping every ratio.

THE FLOOR IS TWO MILLISECONDS, and it is why this is 1/15000 rather than
something rounder. Conformance C21 walks the schedule on a virtual clock by
advancing to one millisecond *short* of each interval, asserting nothing moved,
then advancing the last millisecond -- so an interval that scales below 2ms
makes "one millisecond before it was due" inexpressible and the contract skips
rather than fails. The shortest interval here is 30 seconds, so the scale must
keep 30000 x scale >= 2.

The single source: the shipped profiles set no ``webhooks.retry`` of their own,
so every one inherits this and the 5-second timeout through
``retry_defaults``; a consumer profile overrides it there."""

CONTENT_TYPE = "application/x-www-form-urlencoded"
"""DOCUMENTED: delivery is "POST, application/x-www-form-urlencoded, UTF-8".
Note this is the OUTBOUND shape only -- registering a webhook is an ordinary
JSON API call."""

ATTEMPT_NUMBER_HEADER = "x-vendorfake-attempt-number"
"""This fake's, not Lightspeed's: the vendor documents no attempt header."""

RETRY_REASON_HEADER = "x-vendorfake-retry-reason"
"""Retry-only, and this fake's own -- see ``delivery_headers.py``."""

RETRY_REASONS: Mapping[DeliveryOutcome, str] = {
    DeliveryOutcome.TIMEOUT: DeliveryOutcome.TIMEOUT.value,
    DeliveryOutcome.TRANSPORT_ERROR: DeliveryOutcome.TRANSPORT_ERROR.value,
    DeliveryOutcome.HTTP_ERROR: DeliveryOutcome.HTTP_ERROR.value,
}
"""Core outcome -> the string on the wire: the identity map, because Lightspeed
publishes no reason vocabulary."""


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
