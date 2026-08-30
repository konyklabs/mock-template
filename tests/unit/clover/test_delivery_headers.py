"""Clover's delivery headers -- which are this fake's, and say so.

Two things under test: that the retry-only rule the core's contract needs is
implemented (number and reason on a retry, never on a first send), and that
none of it is spelled as if Clover documented it. Clover documents one
webhook header, ``X-Clover-Auth``, and it lives in the signer.
"""

from __future__ import annotations

from vendorfake.clover.delivery_headers import CloverDeliveryHeaders
from vendorfake.clover.retry import (
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
    RETRY_REASONS,
)
from vendorfake.core.kernel.types import PreparedEvent
from vendorfake.core.webhooks.models import DeliveryMetadata, DeliveryOutcome

FIRST_SENT_AT = "2026-08-30T12:00:00.000Z"

EVENT = PreparedEvent(
    type="O:CREATE",
    event_id="7c9a1f00-0000-0000-0000-000000000000",
    entity_id="GHIVJT2ABCRSC",
    created_at=FIRST_SENT_AT,
    body={},
)


def meta(*, retry_number: int = 0, reason: DeliveryOutcome | None = None) -> DeliveryMetadata:
    return DeliveryMetadata(
        event=EVENT,
        subscription_id="wbhk_0123456789ab",
        notification_url="https://example.test/hooks",
        attempt=retry_number + 1,
        retry_number=retry_number,
        retry_reason=reason,
        initial_delivery_at=FIRST_SENT_AT,
    )


def test_a_first_delivery_carries_exactly_the_content_type_and_the_initial_timestamp() -> None:
    """Asserted as a whole set: a third header on a first send would be either
    the core contributing something or a retry header leaking forward."""
    assert CloverDeliveryHeaders().headers(meta()) == {
        "content-type": "application/json",
        INITIAL_DELIVERY_HEADER: FIRST_SENT_AT,
    }


def test_a_retry_adds_its_number_and_reason_and_keeps_the_initial_timestamp() -> None:
    assert CloverDeliveryHeaders().headers(meta(retry_number=2, reason=DeliveryOutcome.HTTP_ERROR)) == {
        "content-type": "application/json",
        INITIAL_DELIVERY_HEADER: FIRST_SENT_AT,
        RETRY_NUMBER_HEADER: "2",
        RETRY_REASON_HEADER: "http_error",
    }


def test_a_retry_with_no_known_reason_omits_the_reason_rather_than_inventing_one() -> None:
    headers = CloverDeliveryHeaders().headers(meta(retry_number=1))
    assert headers[RETRY_NUMBER_HEADER] == "1"
    assert RETRY_REASON_HEADER not in headers


def test_every_core_outcome_has_a_wire_string_and_it_is_the_neutral_name() -> None:
    """Clover publishes no retry-reason vocabulary, so the core's own words go
    on the wire rather than an invented Clover-flavoured one."""
    assert set(RETRY_REASONS) == set(DeliveryOutcome)
    for outcome, wire in RETRY_REASONS.items():
        assert wire == outcome.value


def test_the_invented_headers_are_namespaced_as_the_fakes_own_not_as_clovers() -> None:
    """The one place a consumer could be misled about Clover's wire format is
    a header that *looks* documented. `x-clover-` would; `x-unit-` is the
    core's response namespace and the conformance suite refuses it on a
    delivery; so the product's own name is the prefix."""
    for name in (INITIAL_DELIVERY_HEADER, RETRY_NUMBER_HEADER, RETRY_REASON_HEADER):
        assert name.startswith("x-vendorfake-"), name
        assert not name.startswith("x-clover-"), name
        assert not name.startswith("x-unit-"), name
