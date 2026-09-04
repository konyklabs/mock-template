"""The delivery retry ladder, the timeout, and the outcomes that are retried.

Three of the four constraints here are the vendor's own numbers -- 20 attempts,
48 hours, a 5-second timeout -- and the intervals between them are this
project's, because "exponential" plus those three is all the docs give. These
tests are what keeps the ladder inside the one documented bound it has to
respect.
"""

from __future__ import annotations

from tests.unit.lightspeed.test_signer import _event
from vendorfake.core.config.models import RetryPolicy
from vendorfake.core.webhooks.models import DeliveryOutcome
from vendorfake.core.webhooks.retry import retry_delay_ms, schedule_exhausted
from vendorfake.lightspeed.delivery_headers import LightspeedDeliveryHeaders
from vendorfake.lightspeed.retry import (
    ATTEMPT_NUMBER_HEADER,
    CONTENT_TYPE,
    DOCUMENTED_ATTEMPTS,
    DOCUMENTED_WINDOW_MS,
    LIGHTSPEED_RETRY_SCHEDULE_MS,
    LIGHTSPEED_TIMEOUT_MS,
    RETRY_REASON_HEADER,
    RETRY_REASONS,
    TOTAL_LADDER_MS,
    lightspeed_retry_defaults,
)


def test_the_documented_numbers_are_what_the_docs_say() -> None:
    assert DOCUMENTED_ATTEMPTS == 20
    assert DOCUMENTED_WINDOW_MS == 48 * 60 * 60 * 1000
    assert LIGHTSPEED_TIMEOUT_MS == 5_000


def test_twenty_attempts_means_nineteen_intervals() -> None:
    assert len(LIGHTSPEED_RETRY_SCHEDULE_MS) == DOCUMENTED_ATTEMPTS - 1


def test_the_ladder_is_exponential_from_thirty_seconds() -> None:
    assert LIGHTSPEED_RETRY_SCHEDULE_MS[0] == 30_000
    assert LIGHTSPEED_RETRY_SCHEDULE_MS[1] == 60_000
    assert LIGHTSPEED_RETRY_SCHEDULE_MS[2] == 120_000


def test_the_ladder_never_goes_backwards_and_caps() -> None:
    assert list(LIGHTSPEED_RETRY_SCHEDULE_MS) == sorted(LIGHTSPEED_RETRY_SCHEDULE_MS)
    assert max(LIGHTSPEED_RETRY_SCHEDULE_MS) == 4 * 60 * 60 * 1000


def test_the_twentieth_attempt_lands_inside_the_documented_forty_eight_hours() -> None:
    """The one bound the ladder has to respect, asserted here as well as at
    import so an edit that lengthened an interval is a red test with a reason
    rather than a raise with a traceback."""
    assert TOTAL_LADDER_MS <= DOCUMENTED_WINDOW_MS
    assert sum(LIGHTSPEED_RETRY_SCHEDULE_MS) == TOTAL_LADDER_MS


def test_the_policy_is_exhausted_only_after_the_twentieth_attempt() -> None:
    policy = RetryPolicy(schedule_ms=LIGHTSPEED_RETRY_SCHEDULE_MS, time_scale=1.0, timeout_ms=LIGHTSPEED_TIMEOUT_MS)
    assert not schedule_exhausted(policy, 0)
    assert not schedule_exhausted(policy, DOCUMENTED_ATTEMPTS - 2)
    assert schedule_exhausted(policy, DOCUMENTED_ATTEMPTS - 1)


def test_each_retry_waits_the_ladder_interval() -> None:
    policy = RetryPolicy(schedule_ms=LIGHTSPEED_RETRY_SCHEDULE_MS, time_scale=1.0, timeout_ms=LIGHTSPEED_TIMEOUT_MS)
    assert retry_delay_ms(policy, 0) == LIGHTSPEED_RETRY_SCHEDULE_MS[0]
    assert retry_delay_ms(policy, 5) == LIGHTSPEED_RETRY_SCHEDULE_MS[5]


def test_the_vendor_defaults_carry_the_ladder_the_timeout_and_the_scale() -> None:
    """A profile that sets no ``webhooks.retry`` of its own inherits all three;
    the core refuses to start a vendor that declares ``webhooks`` with an empty
    schedule."""
    document = lightspeed_retry_defaults()
    assert document.webhooks.retry.schedule_ms == LIGHTSPEED_RETRY_SCHEDULE_MS
    assert document.webhooks.retry.timeout_ms == LIGHTSPEED_TIMEOUT_MS
    assert document.webhooks.retry.time_scale < 1


def test_the_defaults_are_a_fresh_document_each_call() -> None:
    """Two units in one process share nothing mutable."""
    assert lightspeed_retry_defaults() is not lightspeed_retry_defaults()


def test_every_scaled_interval_is_at_least_two_milliseconds() -> None:
    """Conformance C21 walks the schedule by advancing to one millisecond short
    of each interval and asserting nothing moved, so an interval that scales
    below 2ms makes the contract unaskable and it skips instead of running.
    The shortest interval is the one that decides the scale."""
    scale = lightspeed_retry_defaults().webhooks.retry.time_scale
    scaled = [round(interval * scale) for interval in LIGHTSPEED_RETRY_SCHEDULE_MS]
    assert min(scaled) >= 2, scaled


def test_the_scaled_cascade_is_virtual_time_not_wall_time() -> None:
    """Two days of declared delay compresses to about eleven seconds of
    scenario time -- which a virtual clock crosses instantly, so the whole
    twenty-attempt cascade costs a test nothing but the advances."""
    scale = lightspeed_retry_defaults().webhooks.retry.time_scale
    assert TOTAL_LADDER_MS * scale < 15_000


# -- the outcomes that are and are not retried -------------------------------


def test_the_three_failure_outcomes_are_classified_as_the_core_names_them() -> None:
    """Exactly three: nothing came back in time, the transport failed before a
    status existed, or a status came back that was not a success. The vendor
    maps these onto its own strings, and Lightspeed publishes none -- so
    ``RETRY_REASONS`` is the identity map."""
    assert DeliveryOutcome.of(500, timed_out=False) is DeliveryOutcome.HTTP_ERROR
    assert DeliveryOutcome.of(0, timed_out=True) is DeliveryOutcome.TIMEOUT
    assert set(RETRY_REASONS) == set(DeliveryOutcome)
    assert all(RETRY_REASONS[outcome] == outcome.value for outcome in DeliveryOutcome)


def test_a_4xx_is_retried_here_where_lightspeed_would_stop() -> None:
    """DOCUMENTED, and NOT reproducible: "3xx and 4xx will not trigger
    retries". The core dispatcher retries every non-2xx and offers no vendor
    hook; the seam is konyklabs/roadmap#40. This test pins the KNOWN
    LIMITATION so it cannot be forgotten, and fails the day the seam lands."""
    from vendorfake.lightspeed.capabilities import LIGHTSPEED_NOT_MODELED

    assert DeliveryOutcome.of(404, timed_out=False) is DeliveryOutcome.HTTP_ERROR
    assert "retry-only-on-some-outcomes" in LIGHTSPEED_NOT_MODELED


# -- the delivery headers ----------------------------------------------------


def _meta(attempt: int, retry_reason: DeliveryOutcome | None = None):  # type: ignore[no-untyped-def]
    from vendorfake.core.webhooks.models import DeliveryMetadata

    return DeliveryMetadata(
        event=_event({"payload": {}}),
        subscription_id="sub_1",
        notification_url="https://consumer.example/h",
        attempt=attempt,
        retry_number=attempt - 1,
        retry_reason=retry_reason,
        initial_delivery_at="2026-09-04T12:00:00.000Z",
    )


def test_the_content_type_is_the_documented_form_encoding() -> None:
    headers = LightspeedDeliveryHeaders().headers(_meta(1))
    assert headers["content-type"] == CONTENT_TYPE == "application/x-www-form-urlencoded"


def test_the_attempt_number_starts_at_one_and_is_this_fakes_own() -> None:
    """Lightspeed documents no attempt header at all, so the name carries this
    project's prefix and a handler must not depend on it."""
    assert LightspeedDeliveryHeaders().headers(_meta(1))[ATTEMPT_NUMBER_HEADER] == "1"
    assert ATTEMPT_NUMBER_HEADER.startswith("x-vendorfake-")


def test_a_retry_carries_a_reason_and_a_first_send_does_not() -> None:
    """C16 asks that a retry be distinguishable from a first send by a header
    the vendor names; the attempt number cannot be it, because it is on both."""
    assert RETRY_REASON_HEADER not in LightspeedDeliveryHeaders().headers(_meta(1))
    retried = LightspeedDeliveryHeaders().headers(_meta(2, DeliveryOutcome.TIMEOUT))
    assert retried[RETRY_REASON_HEADER] == RETRY_REASONS[DeliveryOutcome.TIMEOUT] == "timeout"


def test_no_event_type_header_is_invented() -> None:
    """It travels in the form body's ``payload`` where the vendor puts it;
    a header would tempt a consumer to route on something Lightspeed never
    sends."""
    headers = LightspeedDeliveryHeaders().headers(_meta(1))
    assert not any("event" in name.lower() for name in headers)
