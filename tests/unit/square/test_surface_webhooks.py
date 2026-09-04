"""The Webhook Subscriptions surface, and what a subscriber actually receives.

Three rules this file follows:

* **the signature is verified against an independent implementation.**
  :func:`independent_signature` writes the documented algorithm out from the
  standard library rather than calling this unit's signer, so a green run says
  the delivery matches Square's published scheme and not that the signer agrees
  with itself.
* **headers are asserted as a whole set.** The claim being tested is that the
  core contributes *nothing* to a delivery, so an extra header is as much a
  failure as a missing one and a key-by-key assertion could not see it.
* **the retry cascade runs on the virtual clock at Square's real schedule.**
  Compressing the schedule proves the shape; running it uncompressed proves the
  numbers, and the virtual clock makes twenty-four hours cost microseconds.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from collections.abc import Iterator
from typing import Any

import pytest

from tests.unit.square.harness import Harness, first_error
from tests.unit.square.harness import harness as build_harness
from vendorfake.core.webhooks.sink import MemorySink, SinkRequest
from vendorfake.square.delivery_headers import ENVIRONMENT_HEADER
from vendorfake.square.events import ORDER_CREATED, ORDER_UPDATED, SQUARE_EVENT_TYPES
from vendorfake.square.retry import (
    INITIAL_DELIVERY_HEADER,
    RETRY_NUMBER_HEADER,
    RETRY_REASON_HEADER,
    SQUARE_RETRY_SCHEDULE_MS,
)
from vendorfake.square.seed.constants import SEED_LOCATION_ID, SEED_OPEN_ORDER_ID
from vendorfake.square.signer import SIGNATURE_HEADER

SUBSCRIPTION_ID = re.compile(r"^wbhk_[0-9a-f]{32}$")
"""``wbhk_`` plus 32 lowercase hex characters, matching Square's own examples."""

HOOKS = "https://api-created.test/hooks"
OTHER_HOOKS = "https://api-other.test/hooks"


def independent_signature(signature_key: str, notification_url: str, raw_body: bytes) -> str:
    """base64(HMAC-SHA256(key, notification_url + raw_body)), from stdlib.

    https://developer.squareup.com/docs/webhooks/step3validate
    https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py
    """
    payload = notification_url.encode("utf-8") + raw_body
    return base64.b64encode(hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()).decode()


@pytest.fixture
def sink() -> MemorySink:
    return MemorySink()


@pytest.fixture
def h(sink: MemorySink) -> Iterator[Harness]:
    """The full profile: every capability, a real clock, the schedule scaled so
    a retry is ten milliseconds rather than a minute."""
    yield from build_harness("full", sink=sink)


@pytest.fixture
def virtual(sink: MemorySink) -> Iterator[Harness]:
    """The same unit on a virtual clock, so a twenty-four-hour cascade is a call."""
    yield from build_harness("full", sink=sink, env={"VENDORFAKE_CLOCK": "virtual"})


def create_subscription(h: Harness, **spec: Any) -> dict[str, Any]:
    body = {
        "idempotency_key": spec.pop("idempotency_key", "sub-1"),
        "subscription": {"event_types": [ORDER_CREATED], "notification_url": HOOKS, **spec},
    }
    response = h.api.post("/v2/webhooks/subscriptions", body, headers=h.auth)
    assert response.status == 200, response.text
    return dict(response.json()["subscription"])


def create_order(h: Harness, key: str = "wh-create") -> str:
    response = h.api.post(
        "/v2/orders",
        {
            "idempotency_key": key,
            "order": {
                "location_id": SEED_LOCATION_ID,
                "line_items": [{"quantity": "1", "base_price_money": {"amount": 250}}],
            },
        },
        headers=h.auth,
    )
    assert response.status == 200, response.text
    return str(response.json()["order"]["id"])


def deliveries(h: Harness) -> list[dict[str, Any]]:
    h.api.post("/__unit/webhooks/drain", {})
    return list(h.api.get("/__unit/webhooks/deliveries").json()["deliveries"])


# ---------------------------------------------------------------------------
# Registering a subscriber through Square's own API.
# ---------------------------------------------------------------------------


def test_create_returns_the_documented_subscription_with_its_signature_key(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/webhook-subscriptions-api/create-webhook-subscription

    The signature key is returned because a consumer has nowhere else to learn
    it -- this is a fake, and verifying what it sends is the point.
    """
    subscription = create_subscription(h, name="Example Webhook Subscription")
    assert SUBSCRIPTION_ID.match(subscription["id"])
    assert subscription["name"] == "Example Webhook Subscription"
    assert subscription["enabled"] is True
    assert subscription["event_types"] == [ORDER_CREATED]
    assert subscription["notification_url"] == HOOKS
    assert subscription["signature_key"]
    assert subscription["api_version"] == "2026-08-19"
    assert subscription["created_at"]


def test_an_unnamed_subscriber_gets_a_placeholder_rather_than_a_missing_key(h: Harness) -> None:
    """`name` is optional on Square's object and is for a human reading a list;
    omitting the key entirely would make the list column empty."""
    assert create_subscription(h)["name"] == "Subscription"


def test_notification_url_is_required(h: Harness) -> None:
    response = h.api.post(
        "/v2/webhooks/subscriptions",
        {"subscription": {"event_types": [ORDER_CREATED]}},
        headers=h.auth,
    )
    assert response.status == 400
    assert response.headers["x-unit-error"] == "missing_field"
    assert first_error(response)["field"] == "subscription.notification_url"


def test_an_empty_event_types_is_the_same_failure_as_an_absent_one(h: Harness) -> None:
    """ "You did not tell me what to send you" is one mistake with two
    spellings. Pydantic reports them as two different error types, which would
    surface as `missing_field` for one and `invalid_value` for the other."""
    for event_types in ([], None):
        payload: dict[str, Any] = {"notification_url": HOOKS}
        if event_types is not None:
            payload["event_types"] = event_types
        response = h.api.post("/v2/webhooks/subscriptions", {"subscription": payload}, headers=h.auth)
        assert response.status == 400
        assert response.headers["x-unit-error"] == "missing_field"
        assert first_error(response)["field"] == "subscription.event_types"


def test_a_non_boolean_enabled_is_refused_rather_than_read_as_false(h: Harness) -> None:
    """The reference tests `spec.enabled === true`, so `{"enabled": "yes"}` is
    silently false and a consumer who meant to register a live subscriber gets
    a dead one with a 200. Strict validation names the field instead."""
    response = h.api.post(
        "/v2/webhooks/subscriptions",
        {"subscription": {"notification_url": HOOKS, "event_types": [ORDER_CREATED], "enabled": "yes"}},
        headers=h.auth,
    )
    assert response.status == 400
    assert response.headers["x-unit-error"] == "invalid_value"
    assert first_error(response)["field"] == "subscription.enabled"


def test_create_is_idempotent_on_its_key(h: Harness) -> None:
    """Two sends of one request return one subscriber, not two -- which is what
    a retrying HTTP client produces and what the idempotency spec is for."""
    first = create_subscription(h, idempotency_key="sub-once")
    second = create_subscription(h, idempotency_key="sub-once")
    assert first == second
    listed = h.api.get("/v2/webhooks/subscriptions", headers=h.auth).json()["subscriptions"]
    assert [row["id"] for row in listed] == [first["id"]]


def test_list_retrieve_and_delete_agree_about_one_subscriber(h: Harness) -> None:
    subscription = create_subscription(h)
    listed = h.api.get("/v2/webhooks/subscriptions", headers=h.auth).json()["subscriptions"]
    assert [row["id"] for row in listed] == [subscription["id"]]

    retrieved = h.api.get(f"/v2/webhooks/subscriptions/{subscription['id']}", headers=h.auth)
    assert retrieved.status == 200
    assert retrieved.json()["subscription"] == subscription

    deleted = h.api.delete(f"/v2/webhooks/subscriptions/{subscription['id']}", headers=h.auth)
    assert deleted.status == 200
    assert deleted.json() == {}

    gone = h.api.get(f"/v2/webhooks/subscriptions/{subscription['id']}", headers=h.auth)
    assert gone.status == 404
    assert gone.headers["x-unit-error"] == "not_found"


def test_deleting_twice_is_a_404_and_not_a_200(h: Harness) -> None:
    """The membership check runs before the delete, so a repeated DELETE says
    what happened rather than reporting success for a row that is not there."""
    subscription = create_subscription(h)
    assert h.api.delete(f"/v2/webhooks/subscriptions/{subscription['id']}", headers=h.auth).status == 200
    assert h.api.delete(f"/v2/webhooks/subscriptions/{subscription['id']}", headers=h.auth).status == 404


def test_a_subscriber_registered_through_either_door_reaches_one_list(h: Harness) -> None:
    """One subscription collection, owned by the core. A vendor keeping its own
    list would give a consumer two ways to register and one that receives
    nothing."""
    through_the_api = create_subscription(h)["id"]
    control = h.api.post(
        "/__unit/webhooks/subscriptions",
        {"notification_url": "https://control.test/hooks", "event_types": ["*"], "signature_key": "k"},
    )
    assert control.status == 201
    through_control = control.json()["subscription"]["id"]

    vendor_view = [row["id"] for row in h.api.get("/v2/webhooks/subscriptions", headers=h.auth).json()["subscriptions"]]
    control_view = [row["id"] for row in h.api.get("/__unit/webhooks/subscriptions").json()["subscriptions"]]
    assert vendor_view == control_view == [through_the_api, through_control]


def test_the_event_types_endpoint_advertises_what_the_mapper_can_send(h: Harness) -> None:
    """https://developer.squareup.com/reference/square/webhook-subscriptions-api/list-webhook-event-types"""
    body = h.api.get("/v2/webhooks/event-types", headers=h.auth).json()
    assert body["event_types"] == list(SQUARE_EVENT_TYPES)
    assert body["metadata"] == [
        {"event_type": event_type, "api_version_introduced": "2026-08-19", "release_status": "PUBLIC"}
        for event_type in SQUARE_EVENT_TYPES
    ]


# ---------------------------------------------------------------------------
# What lands on the wire.
# ---------------------------------------------------------------------------


def test_the_delivery_verifies_against_an_independent_implementation(h: Harness, sink: MemorySink) -> None:
    """And does not verify under a different key, a different URL or a
    different body -- three separate negatives, because a signature that
    ignored one of its inputs would still pass the positive."""
    subscription = create_subscription(h)
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})

    (request,) = sink.received
    assert isinstance(request, SinkRequest)
    assert request.url == HOOKS
    signature = request.headers[SIGNATURE_HEADER]
    body = bytes(request.body)
    key = subscription["signature_key"]

    assert independent_signature(key, HOOKS, body) == signature
    assert independent_signature("wrong-key", HOOKS, body) != signature
    assert independent_signature(key, "https://elsewhere.test/hooks", body) != signature
    assert independent_signature(key, HOOKS, body + b" ") != signature


def test_the_core_contributes_no_headers_of_its_own(h: Harness, sink: MemorySink) -> None:
    """The whole set, not a subset. Every header on a first delivery comes from
    this vendor -- three from its header provider and one from its signer -- so
    a fifth key would be the core putting something on the wire."""
    create_subscription(h)
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})

    headers = dict(sink.received[0].headers)
    assert set(headers) == {
        "content-type",
        ENVIRONMENT_HEADER,
        INITIAL_DELIVERY_HEADER,
        SIGNATURE_HEADER,
    }
    assert headers["content-type"] == "application/json"
    assert headers[ENVIRONMENT_HEADER] == "Sandbox"


def test_the_signed_bytes_are_the_bytes_that_were_sent(h: Harness, sink: MemorySink) -> None:
    """Not a re-serialisation of the event object. Re-encoding between signing
    and sending is the classic way a scheme becomes unverifiable over key order
    or whitespace, and it would be invisible to a test that re-serialised on
    its own side too."""
    subscription = create_subscription(h)
    order_id = create_order(h)
    h.api.post("/__unit/webhooks/drain", {})

    request = sink.received[0]
    parsed = json.loads(bytes(request.body).decode("utf-8"))
    assert parsed["data"]["id"] == order_id
    assert (
        independent_signature(subscription["signature_key"], HOOKS, bytes(request.body))
        == request.headers[SIGNATURE_HEADER]
    )


def test_only_subscribers_that_asked_for_the_type_receive_it(h: Harness, sink: MemorySink) -> None:
    create_subscription(
        h, idempotency_key="s1", event_types=["payment.created"], notification_url="https://payments.test/hooks"
    )
    create_subscription(
        h, idempotency_key="s2", event_types=[ORDER_CREATED], notification_url="https://orders.test/hooks"
    )
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})
    assert [request.url for request in sink.received] == ["https://orders.test/hooks"]


def test_a_glob_matches_both_order_events(h: Harness, sink: MemorySink) -> None:
    create_subscription(h, event_types=["order.*"])
    create_order(h)
    h.api.put(
        f"/v2/orders/{SEED_OPEN_ORDER_ID}",
        {"idempotency_key": "wh-upd", "order": {"version": 1, "ticket_name": "Window"}},
        headers=h.auth,
    )
    h.api.post("/__unit/webhooks/drain", {})
    types = [json.loads(bytes(request.body).decode("utf-8"))["type"] for request in sink.received]
    assert sorted(types) == [ORDER_CREATED, ORDER_UPDATED]


def test_a_disabled_subscriber_receives_nothing(h: Harness, sink: MemorySink) -> None:
    """Skipped at fan-out rather than at send time, so a disabled subscriber
    produces no delivery record at all -- not a record explaining that it was
    disabled."""
    create_subscription(h, enabled=False)
    create_order(h)
    assert deliveries(h) == []
    assert sink.received == []


# ---------------------------------------------------------------------------
# Retries.
# ---------------------------------------------------------------------------


def test_a_failing_subscriber_is_retried_on_the_documented_backoff_shape(h: Harness, sink: MemorySink) -> None:
    """Square's first two intervals are 1 minute and 2 minutes; the `full`
    profile scales by 0.000167, so a test observes 10 ms and 20 ms and the
    *ratio* between them is the documented one."""
    create_subscription(h)
    sink.respond_with = lambda _req, index: 500 if index < 2 else 200
    create_order(h)

    log = deliveries(h)
    assert [record["status"] for record in log] == ["failed", "failed", "delivered"]
    assert [record["retry_number"] for record in log] == [0, 1, 2]
    assert log[0]["next_attempt_in_ms"] == 10
    assert log[1]["next_attempt_in_ms"] == 20
    # One event id across every attempt: that is the consumer's dedup handle.
    assert len({record["event_id"] for record in log}) == 1
    assert log[1]["headers"][RETRY_NUMBER_HEADER] == "1"
    assert log[1]["headers"][RETRY_REASON_HEADER] == "http_error"


def test_a_timed_out_subscriber_is_reported_as_http_timeout(h: Harness, sink: MemorySink) -> None:
    """ "If your application fails to acknowledge the notification in a timely
    manner, a duplicate event is sent and your application has 10 seconds to
    respond." https://developer.squareup.com/docs/webhooks/overview

    The memory sink reports index 0 as status 0, which is a timeout rather than
    a status that came back.
    """
    create_subscription(h)
    sink.respond_with = lambda _req, index: 0 if index == 0 else 200
    create_order(h)

    log = deliveries(h)
    assert log[0]["status"] == "failed"
    assert log[1]["headers"][RETRY_REASON_HEADER] == "http_timeout"
    assert log[1]["status"] == "delivered"


def test_the_retry_signature_is_the_first_attempts_signature(h: Harness, sink: MemorySink) -> None:
    """A consumer that verified the first copy and deduplicated on `event_id`
    must be able to verify the redelivery. The signature covers the URL, the
    secret and the body -- and not the attempt."""
    subscription = create_subscription(h)
    sink.respond_with = lambda _req, index: 500 if index == 0 else 200
    create_order(h)
    h.api.post("/__unit/webhooks/drain", {})

    first, second = sink.received[0], sink.received[1]
    assert bytes(first.body) == bytes(second.body)
    assert first.headers[SIGNATURE_HEADER] == second.headers[SIGNATURE_HEADER]
    assert (
        independent_signature(subscription["signature_key"], HOOKS, bytes(second.body))
        == second.headers[SIGNATURE_HEADER]
    )


def test_the_whole_documented_schedule_runs_on_the_virtual_clock(virtual: Harness, sink: MemorySink) -> None:
    """Eleven retries after the initial send, at Square's published intervals,
    over twenty-four hours and three minutes of unit time.

    Run **uncompressed** -- `time_scale` is put back to 1.0 -- so the assertion
    is about the documented numbers and not about the profile's test scaling.
    That is only affordable on a virtual clock, where advancing eight hours is
    a loop iteration.

    https://developer.squareup.com/docs/webhooks/overview
    """
    create_subscription(virtual)
    sink.respond_with = 500
    patched = virtual.api.post(
        "/__unit/webhooks/retry-policy",
        {"schedule_ms": list(SQUARE_RETRY_SCHEDULE_MS), "time_scale": 1.0},
    )
    assert patched.status == 200

    started_at = virtual.unit.context.clock.now()
    create_order(virtual)
    log = deliveries(virtual)

    # 1 initial attempt + 11 documented retries.
    assert len(log) == 12
    assert [record["status"] for record in log] == ["failed"] * 11 + ["exhausted"]
    assert [record["retry_number"] for record in log] == list(range(12))
    # The delay announced before each retry IS the published table, row for row.
    assert [record["next_attempt_in_ms"] for record in log[:-1]] == list(SQUARE_RETRY_SCHEDULE_MS)
    assert "next_attempt_in_ms" not in log[-1]
    # "Square resends the event notification for up to 24 hours after the
    # originating event": the per-attempt column sums to 24h03m, which is
    # Square's own rounding of its cumulative column and not a discrepancy.
    assert virtual.unit.context.clock.now() - started_at == sum(SQUARE_RETRY_SCHEDULE_MS)
    assert len(sink.received) == 12


def test_every_retry_carries_its_number_and_reason_and_one_initial_timestamp(
    virtual: Harness, sink: MemorySink
) -> None:
    """The header rules across a whole cascade, which a two-attempt test cannot
    see: the first send carries no retry headers, retries 1..11 carry their own
    number, and all twelve carry the same initial-delivery timestamp."""
    create_subscription(virtual)
    sink.respond_with = 500
    virtual.api.post(
        "/__unit/webhooks/retry-policy",
        {"schedule_ms": list(SQUARE_RETRY_SCHEDULE_MS), "time_scale": 1.0},
    )
    create_order(virtual)
    log = deliveries(virtual)

    assert RETRY_NUMBER_HEADER not in log[0]["headers"]
    assert RETRY_REASON_HEADER not in log[0]["headers"]
    assert [record["headers"][RETRY_NUMBER_HEADER] for record in log[1:]] == [str(n) for n in range(1, 12)]
    assert {record["headers"][RETRY_REASON_HEADER] for record in log[1:]} == {"http_error"}
    assert len({record["headers"][INITIAL_DELIVERY_HEADER] for record in log}) == 1


# ---------------------------------------------------------------------------
# TestWebhookSubscription.
# ---------------------------------------------------------------------------


def test_the_test_route_sends_a_signed_event_and_reports_the_status_code(h: Harness, sink: MemorySink) -> None:
    """https://developer.squareup.com/reference/square/webhook-subscriptions-api/test-webhook-subscription

    The real delivery path, so the subscriber sees a genuinely signed request;
    only the payload is synthetic, and it says so.
    """
    subscription = create_subscription(h)
    response = h.api.post(
        f"/v2/webhooks/subscriptions/{subscription['id']}/test",
        {"event_type": ORDER_CREATED},
        headers=h.auth,
    )
    assert response.status == 200
    result = response.json()["subscription_test_result"]
    assert result["id"] == "evt_test_1"
    assert result["status_code"] == 200
    assert result["created_at"] and result["updated_at"]

    (request,) = sink.received
    body = json.loads(bytes(request.body).decode("utf-8"))
    assert body["data"] == {"type": "test", "id": subscription["id"], "object": {"test": True}}
    assert (
        independent_signature(subscription["signature_key"], HOOKS, bytes(request.body))
        == request.headers[SIGNATURE_HEADER]
    )


def test_the_test_route_defaults_to_a_type_the_subscriber_asked_for(h: Harness, sink: MemorySink) -> None:
    """Sending `order.created` to a subscriber that asked only for
    `order.updated` would be filtered out and reported as `status_code: 0`,
    which reads as "your endpoint is down"."""
    subscription = create_subscription(h, event_types=[ORDER_UPDATED])
    response = h.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=h.auth)
    assert response.json()["subscription_test_result"]["status_code"] == 200
    assert json.loads(bytes(sink.received[0].body).decode("utf-8"))["type"] == ORDER_UPDATED


def test_the_test_route_reaches_only_the_subscriber_under_test(h: Harness, sink: MemorySink) -> None:
    """Square's TestWebhookSubscription targets one subscription.

    A broadcast would send every other enabled subscriber whose patterns cover
    the type a synthetic event it never asked for -- signed with its own key,
    so indistinguishable from a genuine one.
    """
    other = create_subscription(h, idempotency_key="sub-other", notification_url=OTHER_HOOKS)
    under_test = create_subscription(h, idempotency_key="sub-under-test")

    response = h.api.post(f"/v2/webhooks/subscriptions/{under_test['id']}/test", {}, headers=h.auth)

    assert response.status == 200
    (request,) = sink.received
    assert request.url == HOOKS
    assert OTHER_HOOKS not in [r.url for r in sink.received]
    assert other["id"] != under_test["id"]


def test_the_test_route_reports_the_status_code_of_the_subscriber_under_test(h: Harness, sink: MemorySink) -> None:
    """The reported `status_code` describes the targeted endpoint, not whichever
    record the single delivery worker happened to write first.

    With a broadcast, `event_id` is shared across the fan-out, so the lookup
    returns the first subscriber inserted -- and a wrong non-zero code is a
    failure shaped like success: the caller cannot tell it was told about
    somebody else.
    """
    sink.respond_with = lambda request, _index: 500 if request.url == HOOKS else 200
    # Inserted first, so its record would be written first under a broadcast.
    create_subscription(h, idempotency_key="sub-a")
    healthy = create_subscription(h, idempotency_key="sub-b", notification_url=OTHER_HOOKS)

    response = h.api.post(f"/v2/webhooks/subscriptions/{healthy['id']}/test", {}, headers=h.auth)

    result = response.json()["subscription_test_result"]
    assert result["status_code"] == 200, "reported the other subscriber's failure"
    (request,) = sink.received
    assert request.url == OTHER_HOOKS


def test_the_test_route_resolves_a_glob_to_a_real_event_type(h: Harness, sink: MemorySink) -> None:
    """`event_types` holds patterns. Returning the first one verbatim puts
    `"type": "*"` on the wire, and a consumer dispatching on `body["type"]`
    falls through to its unknown-event branch on the very request meant to
    prove its wiring works."""
    subscription = create_subscription(h, event_types=["*"])
    response = h.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=h.auth)
    assert response.json()["subscription_test_result"]["status_code"] == 200
    delivered = json.loads(bytes(sink.received[0].body).decode("utf-8"))["type"]
    assert delivered in SQUARE_EVENT_TYPES, f"delivered a pattern, not a type: {delivered!r}"


def test_the_test_route_prefers_a_literal_type_the_subscriber_named(h: Harness, sink: MemorySink) -> None:
    """A glob alongside a literal must not displace the literal."""
    subscription = create_subscription(h, event_types=[ORDER_UPDATED, "order.*"])
    h.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=h.auth)
    assert json.loads(bytes(sink.received[0].body).decode("utf-8"))["type"] == ORDER_UPDATED


def test_the_test_route_reports_the_first_attempt_not_the_eventual_outcome(h: Harness, sink: MemorySink) -> None:
    """`status_code` is the subscriber's own answer to the *first* attempt.

    Here the subscriber refuses once and then accepts, so the delivery
    ultimately succeeds and the reported code is still 503 -- which is the
    right answer to "what did my endpoint say", and is why the route reads the
    first matching delivery record rather than the last.
    """
    subscription = create_subscription(h)
    sink.respond_with = lambda _req, index: 503 if index == 0 else 200
    response = h.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=h.auth)
    assert response.json()["subscription_test_result"]["status_code"] == 503
    assert [record["status"] for record in deliveries(h)] == ["failed", "delivered"]


def test_the_test_route_reports_a_subscriber_that_never_answers(virtual: Harness, sink: MemorySink) -> None:
    """The route reports the first refusal and returns without moving the
    virtual clock: one record, then the cascade once the clock is drained."""
    subscription = create_subscription(virtual)
    sink.respond_with = 503
    response = virtual.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=virtual.auth)
    assert response.json()["subscription_test_result"]["status_code"] == 503
    before_drain = virtual.api.get("/__unit/webhooks/deliveries").json()["deliveries"]
    assert [record["status"] for record in before_drain] == ["failed"]
    log = deliveries(virtual)
    assert len(log) == 12
    assert log[-1]["status"] == "exhausted"


def test_the_test_route_answers_at_once_for_a_disabled_subscriber(h: Harness, sink: MemorySink) -> None:
    """Nothing is queued for a disabled subscriber, so the route reports 0 without
    holding the request lock for the delivery timeout."""
    subscription = create_subscription(h, enabled=False)
    started = time.monotonic()
    response = h.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=h.auth)
    assert time.monotonic() - started < 1.0
    assert response.json()["subscription_test_result"]["status_code"] == 0
    assert sink.received == []


def test_the_test_route_does_not_wait_out_a_virtual_clock_delay(virtual: Harness, sink: MemorySink) -> None:
    """A webhook.delay rule puts the first attempt on the virtual clock; the route
    reports 0 at once rather than holding the unit until the wall-clock timeout,
    and the attempt lands when the clock is drained."""
    subscription = create_subscription(virtual)
    virtual.api.post(
        "/__unit/chaos/rules",
        {"id": "slow", "scope": "webhook", "fault": "webhook.delay", "params": {"delay_ms": 60_000}},
    )
    started = time.monotonic()
    response = virtual.api.post(f"/v2/webhooks/subscriptions/{subscription['id']}/test", {}, headers=virtual.auth)
    assert time.monotonic() - started < 1.0
    assert response.json()["subscription_test_result"]["status_code"] == 0
    assert [record["status"] for record in deliveries(virtual)] == ["delivered"]


def test_the_test_route_404s_for_an_unknown_subscriber(h: Harness) -> None:
    response = h.api.post("/v2/webhooks/subscriptions/wbhk_nope/test", {}, headers=h.auth)
    assert response.status == 404
    assert first_error(response)["field"] == "subscription_id"


def test_every_vendor_route_runs_under_the_request_lock() -> None:
    """The request log's journal window is read under the pipeline lock and
    assumes every vendor route holds it."""
    for h in build_harness("full"):
        routes = h.api.get("/__unit/routes").json()["routes"]
        vendor_unserialized = [
            f"{route['method']} {route['path']}"
            for route in routes
            if not route["serialized"] and not route["internal"]
        ]
        assert vendor_unserialized == []


# ---------------------------------------------------------------------------
# Capability and auth gating, which this surface inherits rather than implements.
# ---------------------------------------------------------------------------


def test_the_surface_is_gone_when_the_capability_is_off_and_says_so() -> None:
    """Not a 404: a consumer must be able to tell "this unit does not do
    webhooks" from "you typed the path wrong"."""
    for h in build_harness("orders-only"):
        response = h.api.get("/v2/webhooks/subscriptions", headers=h.auth)
        assert response.status == 501
        assert response.headers["x-unit-error"] == "capability_disabled"
        assert "webhooks" in response.text


def test_the_surface_needs_a_token(h: Harness) -> None:
    response = h.api.get("/v2/webhooks/subscriptions")
    assert response.status == 401
    assert response.headers["x-unit-error"] == "unauthorized"


def test_a_magic_value_in_the_subscription_name_drives_a_fault(h: Harness) -> None:
    """`subscription.name` is one of this vendor's declared magic body paths.

    Prior art is Square's own sandbox, which uses magic values in ordinary
    request fields rather than a control channel, so a consumer's real client
    library can drive a fault:
    https://developer.squareup.com/docs/devtools/sandbox/testing
    """
    response = h.api.post(
        "/v2/webhooks/subscriptions",
        {"subscription": {"notification_url": HOOKS, "event_types": [ORDER_CREATED], "name": "chaos:rate_limit"}},
        headers=h.auth,
    )
    assert response.status == 429
    assert response.headers["x-unit-error"] == "rate_limited"


def test_a_read_only_token_is_refused_by_every_webhook_route(h: Harness) -> None:
    """The property `WEBHOOK_SUBSCRIPTIONS_SCOPE` exists to provide, exercised.

    Review caught this: `SEED_SCOPES`'s docstring and the fix commit both
    described "a read-only token cannot register a subscriber or read a signing
    key" as testable against the seeded fixtures, and nothing tested it. The
    standing invariant in ``tests/unit/test_route_scopes.py`` asserts only that
    each route *declares* a non-empty ``scopes`` tuple; it never calls a route,
    so it cannot see whether the kernel actually refuses an under-scoped caller.

    The gap that leaves is exact: a typo in the scope name, or dropping it from
    one ``Route(...)`` while adding that path to ``SCOPELESS_BY_DESIGN`` for an
    unrelated reason, would let the read-only token reach these routes again --
    the precise vulnerability the scope was added to close -- and CI would stay
    green. ``test_the_surface_needs_a_token`` covers only the no-token 401, not
    the wrong-scope 403 that is the whole point.

    So this drives all six routes with the read-only bearer token and requires
    ``forbidden_scope`` on each, which is a different answer from
    ``unauthorized`` and must not be confusable with it.
    """
    created = h.api.call(
        method="POST",
        path="/v2/webhooks/subscriptions",
        headers=h.auth,
        body={"subscription": {"notification_url": "https://scoped.test/hooks", "event_types": ["order.created"]}},
    )
    assert created.status == 200, created.text
    subscription_id = created.json()["subscription"]["id"]

    probes = [
        ("GET", "/v2/webhooks/event-types", None),
        ("GET", "/v2/webhooks/subscriptions", None),
        ("POST", "/v2/webhooks/subscriptions", {"subscription": {"notification_url": "https://x.test/h"}}),
        ("GET", f"/v2/webhooks/subscriptions/{subscription_id}", None),
        ("POST", f"/v2/webhooks/subscriptions/{subscription_id}/test", {}),
        ("DELETE", f"/v2/webhooks/subscriptions/{subscription_id}", None),
    ]

    refused: list[str] = []
    for method, path, body in probes:
        response = h.api.call(method=method, path=path, headers=h.read_auth, body=body)
        refused.append(f"{method} {path} -> {response.status}:{response.headers.get('x-unit-error')}")
        assert response.status == 403, f"{method} {path} was not refused: {response.text}"
        assert response.headers.get("x-unit-error") == "forbidden_scope", (
            f"{method} {path} answered {response.headers.get('x-unit-error')}; a token that authenticated but "
            "lacks the scope must be distinguishable from one that did not authenticate"
        )

    assert len(refused) == 6, refused
    # And the signing key never reaches an under-scoped caller, which is the
    # consequence that made this a security finding rather than a nit.
    listing = h.api.call(method="GET", path="/v2/webhooks/subscriptions", headers=h.read_auth)
    assert "signature_key" not in listing.text
