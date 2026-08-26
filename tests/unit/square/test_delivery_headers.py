"""Square's delivery headers, and the de-vendoring they complete.

Two things are under test here and they are not the same thing:

* that this vendor spells Square's four documented delivery headers correctly,
  including the rule that two of them appear only on a retry;
* that **the core contributes none of them**. The reference wrote the content
  type and all three ``square-*`` names into vendor-neutral core, and computed
  the retry reason there as one of three literal strings that carry no brand
  name. The second half is the one a slug-scanning checker cannot see, so it is
  asserted here instead.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import PreparedEvent
from vendorfake.core.webhooks.models import DeliveryMetadata, DeliveryOutcome
from vendorfake.square.delivery_headers import ENVIRONMENT_HEADER, SquareDeliveryHeaders
from vendorfake.square.retry import (
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
)
from vendorfake.square.vendor import create_square_vendor

FIRST_SENT_AT = "2026-06-01T12:00:00.000Z"

EVENT = PreparedEvent(
    type="order.created",
    event_id="7c9a1f00-0000-0000-0000-000000000000",
    entity_id="CAISENgvlJ6jLWAzERDzjyHVybY",
    created_at=FIRST_SENT_AT,
    body={},
)


def meta(*, retry_number: int = 0, reason: DeliveryOutcome | None = None) -> DeliveryMetadata:
    return DeliveryMetadata(
        event=EVENT,
        subscription_id="wbhk_0123456789abcdef0123456789abcdef",
        notification_url="https://example.test/hooks",
        attempt=retry_number + 1,
        retry_number=retry_number,
        retry_reason=reason,
        initial_delivery_at=FIRST_SENT_AT,
    )


def provider(environment: str = "Sandbox") -> SquareDeliveryHeaders:
    vendor = create_square_vendor(vendor_config={"environment": environment})
    return SquareDeliveryHeaders(vendor)


def test_a_first_delivery_carries_exactly_three_headers() -> None:
    """Exactly three, asserted as a whole set rather than key by key: a fourth
    header appearing here is as much a wire change as a missing one."""
    assert provider().headers(meta()) == {
        "content-type": "application/json",
        ENVIRONMENT_HEADER: "Sandbox",
        INITIAL_DELIVERY_HEADER: FIRST_SENT_AT,
    }


def test_the_retry_headers_are_absent_on_a_first_delivery() -> None:
    """ "Retried notifications include the square-retry-number and
    square-retry-reason headers."
    https://developer.squareup.com/docs/webhooks/overview

    A consumer distinguishes a redelivery by the header's *absence*, so sending
    `square-retry-number: 0` on the first attempt would be wrong in a way that
    looks right.
    """
    headers = provider().headers(meta())
    assert RETRY_NUMBER_HEADER not in headers
    assert RETRY_REASON_HEADER not in headers


def test_a_retry_carries_its_number_and_its_reason() -> None:
    headers = provider().headers(meta(retry_number=1, reason=DeliveryOutcome.HTTP_ERROR))
    assert headers[RETRY_NUMBER_HEADER] == "1"
    assert headers[RETRY_REASON_HEADER] == "http_error"


def test_every_neutral_outcome_reaches_the_wire_as_squares_word_for_it() -> None:
    """The core computes a neutral outcome; this is where it becomes Square's
    vocabulary. A missing row would ship a retry with no reason header at all."""
    observed = {
        outcome: provider().headers(meta(retry_number=3, reason=outcome))[RETRY_REASON_HEADER]
        for outcome in DeliveryOutcome
    }
    assert observed == {
        DeliveryOutcome.TIMEOUT: "http_timeout",
        DeliveryOutcome.TRANSPORT_ERROR: "other_error",
        DeliveryOutcome.HTTP_ERROR: "http_error",
    }


def test_a_retry_with_no_known_reason_sends_no_reason_header() -> None:
    """Rather than a plausible default. The core leaves the reason `None` only
    when it genuinely does not know, and answering `other_error` there would
    tell a consumer something untrue about their own endpoint."""
    headers = provider().headers(meta(retry_number=2, reason=None))
    assert headers[RETRY_NUMBER_HEADER] == "2"
    assert RETRY_REASON_HEADER not in headers


def test_the_initial_delivery_timestamp_is_the_same_on_every_attempt() -> None:
    """It is what lets a consumer measure total latency across a cascade, so it
    must not drift to the current attempt's time."""
    first = provider().headers(meta())[INITIAL_DELIVERY_HEADER]
    eleventh = provider().headers(meta(retry_number=11, reason=DeliveryOutcome.TIMEOUT))[INITIAL_DELIVERY_HEADER]
    assert first == eleventh == FIRST_SENT_AT


def test_the_environment_header_follows_the_resolved_config() -> None:
    """https://developer.squareup.com/docs/webhooks/build-with-webhooks

    Read live, so a profile configuring Production says Production. The config
    model refuses the lower-case spelling outright, which is what stops
    `environment=production` quietly meaning Sandbox.
    """
    assert provider("Production").headers(meta())[ENVIRONMENT_HEADER] == "Production"
