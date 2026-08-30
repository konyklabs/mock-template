"""The documented delivery headers, read from the envelope; the one fake-only retry header."""

from __future__ import annotations

from vendorfake.core.kernel.types import PreparedEvent
from vendorfake.core.webhooks.models import DeliveryMetadata, DeliveryOutcome
from vendorfake.toast.delivery_headers import ToastDeliveryHeaders

ENVELOPE = {
    "timestamp": "2024-03-28T15:11:01.050Z",
    "eventCategory": "stock",
    "eventType": "low_quantity",
    "guid": "e1",
    "details": {"itemGuid": "i1", "restaurantGuid": "r1"},
}


def meta(
    body: object, *, attempt: int = 1, reason: DeliveryOutcome | None = None, event_type: str = "low_quantity"
) -> DeliveryMetadata:
    return DeliveryMetadata(
        event=PreparedEvent(
            type=event_type, event_id="e1", entity_id="i1", created_at="2024-03-28T15:11:01.050Z", body=body
        ),
        subscription_id="sub_1",
        notification_url="https://example.test/hooks",
        attempt=attempt,
        retry_number=attempt - 1,
        retry_reason=reason,
        initial_delivery_at="2024-03-28T15:11:01.050Z",
    )


def test_the_documented_headers_come_from_the_envelope_on_a_first_send() -> None:
    headers = dict(ToastDeliveryHeaders().headers(meta(ENVELOPE)))
    assert headers == {
        "content-type": "application/json",
        "Toast-Attempt-Number": "1",
        "Toast-Event-Type": "low_quantity",
        "Toast-Event-Category": "stock",
        "Toast-Restaurant-External-ID": "r1",
    }


def test_a_retry_bumps_the_attempt_number_and_adds_only_the_fake_reason_header() -> None:
    first = dict(ToastDeliveryHeaders().headers(meta(ENVELOPE)))
    retried = dict(ToastDeliveryHeaders().headers(meta(ENVELOPE, attempt=2, reason=DeliveryOutcome.HTTP_ERROR)))
    assert retried["Toast-Attempt-Number"] == "2"
    assert set(retried) - set(first) == {"x-vendorfake-retry-reason"}
    assert retried["x-vendorfake-retry-reason"] == "http_error"
    assert not any(name.startswith("x-unit-") for name in retried)


def test_a_non_envelope_body_falls_back_to_the_event_type_and_omits_the_restaurant_header() -> None:
    headers = dict(ToastDeliveryHeaders().headers(meta({"probe": "one"}, event_type="conformance.probe")))
    assert headers["Toast-Event-Type"] == "conformance.probe"
    assert headers["Toast-Event-Category"] == "conformance.probe"
    assert "Toast-Restaurant-External-ID" not in headers
