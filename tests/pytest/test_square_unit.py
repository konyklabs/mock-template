"""Consumer-side integration test (Python / pytest).

The Python half of the DX story: a team whose services are Python gets the same
unit, the same profiles and the same control plane, with no TypeScript in
sight. Assertions mirror tests/vitest/square-unit.test.ts so a behaviour change
fails on both sides.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid
from urllib.parse import parse_qs, urlparse

from conftest import APPLICATION_ID, APPLICATION_SECRET, SEED_LOCATION, SEEDED_TOKEN, TEA_MUG
from launch import UnitHandle, call
from subscriber import Subscriber


def square_signature(signature_key: str, notification_url: str, raw_body: bytes) -> str:
    """base64(HMAC-SHA256(signature_key, notification_url + raw_body)).

    https://developer.squareup.com/docs/webhooks/step3validate
    """
    payload = notification_url.encode("utf-8") + raw_body
    digest = hmac.new(signature_key.encode("utf-8"), payload, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


def test_health_and_self_description(unit: UnitHandle) -> None:
    status, _, body = call(unit.base_url, "GET", "/__unit/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["vendor"] == "square"

    status, _, info = call(unit.base_url, "GET", "/__unit/info")
    assert status == 200
    assert info["vendor"]["apiVersion"] == "2026-08-19"
    assert [c["name"] for c in info["capabilities"]] == [
        "oauth",
        "order-lifecycle",
        "merchant-directory",
        "webhooks",
        "webhooks.chaos",
    ]


def test_oauth_flow_then_order_lifecycle(unit: UnitHandle) -> None:
    scope = "ORDERS_READ ORDERS_WRITE PAYMENTS_WRITE MERCHANT_PROFILE_READ ITEMS_READ"
    status, headers, _ = call(
        unit.base_url,
        "GET",
        f"/oauth2/authorize?client_id={APPLICATION_ID}&scope={scope.replace(' ', '+')}"
        "&state=pytest&redirect_uri=https%3A%2F%2Fexample.test%2Foauth%2Fcallback",
    )
    assert status == 302
    query = parse_qs(urlparse(headers["location"]).query)
    assert query["response_type"] == ["code"]
    assert query["state"] == ["pytest"]
    code = query["code"][0]
    assert code.startswith("sq0cgb-")

    status, _, token = call(
        unit.base_url,
        "POST",
        "/oauth2/token",
        {
            "client_id": APPLICATION_ID,
            "client_secret": APPLICATION_SECRET,
            "grant_type": "authorization_code",
            "code": code,
        },
    )
    assert status == 200
    assert token["token_type"] == "bearer"
    assert token["merchant_id"] == "MLQW2MYBY81PZ"
    bearer = {"authorization": f"Bearer {token['access_token']}"}

    status, _, created = call(
        unit.base_url,
        "POST",
        "/v2/orders",
        {
            "idempotency_key": f"pytest-{uuid.uuid4()}",
            "order": {
                "location_id": SEED_LOCATION,
                "line_items": [{"catalog_object_id": TEA_MUG, "quantity": "2"}],
            },
        },
        bearer,
    )
    assert status == 200
    order_id = created["order"]["id"]
    assert created["order"]["state"] == "OPEN"
    assert created["order"]["version"] == 1
    assert created["order"]["total_money"] == {"amount": 300, "currency": "USD"}

    status, _, paid = call(
        unit.base_url,
        "POST",
        f"/v2/orders/{order_id}/pay",
        {"idempotency_key": f"pytest-pay-{uuid.uuid4()}", "order_version": 1, "payment_ids": ["PAY_PYTEST"]},
        bearer,
    )
    assert status == 200
    assert paid["order"]["state"] == "COMPLETED"

    # The mutation outlived the request that made it.
    status, _, fetched = call(unit.base_url, "GET", f"/v2/orders/{order_id}", headers=bearer)
    assert status == 200
    assert fetched["order"]["state"] == "COMPLETED"
    assert fetched["order"]["version"] == 2


def test_optimistic_concurrency_rejects_a_stale_version(unit: UnitHandle, auth: dict[str, str]) -> None:
    status, _, created = call(
        unit.base_url,
        "POST",
        "/v2/orders",
        {
            "idempotency_key": f"pytest-occ-{uuid.uuid4()}",
            "order": {"location_id": SEED_LOCATION, "line_items": [{"catalog_object_id": TEA_MUG, "quantity": "1"}]},
        },
        auth,
    )
    order_id = created["order"]["id"]

    status, _, _ = call(
        unit.base_url,
        "PUT",
        f"/v2/orders/{order_id}",
        {"idempotency_key": f"pytest-u1-{uuid.uuid4()}", "order": {"version": 1, "ticket_name": "python"}},
        auth,
    )
    assert status == 200

    status, _, error = call(
        unit.base_url,
        "PUT",
        f"/v2/orders/{order_id}",
        {"idempotency_key": f"pytest-u2-{uuid.uuid4()}", "order": {"version": 1, "ticket_name": "stale"}},
        auth,
    )
    assert status == 400
    assert error["errors"][0]["code"] == "VERSION_MISMATCH"
    assert error["errors"][0]["category"] == "INVALID_REQUEST_ERROR"


def test_webhook_is_signed_and_retried(unit: UnitHandle, subscriber: Subscriber, auth: dict[str, str]) -> None:
    notification_url = unit.host_url(subscriber.port)
    signature_key = "pytest-signature-key"

    status, _, _ = call(
        unit.base_url,
        "POST",
        "/__unit/webhooks/subscriptions",
        {
            "id": "wbhk_pytest",
            "notificationUrl": notification_url,
            "eventTypes": ["order.created"],
            "signatureKey": signature_key,
        },
    )
    assert status == 201

    subscriber.received.clear()
    # Reject the first delivery so the retry crosses the network for real.
    subscriber.respond_with = lambda index: 500 if index == 0 else 200

    call(
        unit.base_url,
        "POST",
        "/v2/orders",
        {
            "idempotency_key": f"pytest-webhook-{uuid.uuid4()}",
            "order": {"location_id": SEED_LOCATION, "line_items": [{"catalog_object_id": TEA_MUG, "quantity": "1"}]},
        },
        auth,
    )
    call(unit.base_url, "POST", "/__unit/webhooks/drain", {})

    assert len(subscriber.received) == 2
    first, retry = subscriber.received

    for delivery in (first, retry):
        expected = square_signature(signature_key, notification_url, delivery.raw_body)
        assert delivery.headers["x-square-hmacsha256-signature"] == expected
        assert delivery.headers["square-environment"] == "Sandbox"

    event = json.loads(first.raw_body)
    assert event["type"] == "order.created"
    assert event["data"]["type"] == "order_created"
    assert event["data"]["object"]["order_created"]["state"] == "OPEN"

    # At-least-once: the same event_id arrives twice, so the consumer dedupes.
    assert json.loads(retry.raw_body)["event_id"] == event["event_id"]
    assert retry.headers["square-retry-number"] == "1"
    assert retry.headers["square-retry-reason"] == "http_error"

    subscriber.respond_with = 200
    call(unit.base_url, "DELETE", "/__unit/webhooks/subscriptions/wbhk_pytest")


def test_disabled_capability_and_deterministic_rate_limit(unit: UnitHandle, auth: dict[str, str]) -> None:
    call(unit.base_url, "POST", "/__unit/capabilities", {"disable": ["merchant-directory"]})
    status, headers, body = call(unit.base_url, "GET", "/v2/locations", headers=auth)
    assert status == 501
    assert headers["x-unit-error"] == "capability_disabled"
    assert body["errors"][0]["code"] == "NOT_IMPLEMENTED"
    assert body["unit_error"]["capability"] == "merchant-directory"
    call(unit.base_url, "POST", "/__unit/capabilities", {"enable": ["merchant-directory"]})

    call(
        unit.base_url,
        "POST",
        "/__unit/chaos/rules",
        {
            "id": "pytest-429",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": "GET /v2/locations"},
            "when": {"nth": [2]},
        },
    )
    statuses = [call(unit.base_url, "GET", "/v2/locations", headers=auth)[0] for _ in range(3)]
    assert statuses == [200, 429, 200]
    call(unit.base_url, "POST", "/__unit/chaos/reset", {})
