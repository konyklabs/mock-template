"""What a restaurant-ordering integration does against Square, run against the fake.

Every request below is the request your service would make against Square's
sandbox -- same paths, same bodies, same headers -- and every assertion is on
what Square documents. Nothing here knows it is talking to a fake except the
fixtures and the two ``square.`` control-plane calls that arm a fault.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import httpx

from vendorfake.square.signer import verify_square_signature
from vendorfake.testing import StartedUnit, WebhookReceiver

# ---------------------------------------------------------------------------
# 1. OAuth exchange -> create order -> pay -> read the state back.
# ---------------------------------------------------------------------------


def test_oauth_exchange_then_an_order_is_created_paid_and_read_back(square: StartedUnit) -> None:
    seed = square.seed
    client = square.client

    # The merchant clicks "Allow": the authorize redirect carries the code.
    authorize = client.get(
        "/oauth2/authorize",
        params={
            "client_id": seed.application_id,
            "scope": "MERCHANT_PROFILE_READ ORDERS_READ ORDERS_WRITE PAYMENTS_WRITE ITEMS_READ",
            "state": "csrf-token",
        },
    )
    assert authorize.status_code == 302
    code = parse_qs(urlsplit(authorize.headers["location"]).query)["code"][0]

    # Your callback exchanges it for a token.
    token = client.post(
        "/oauth2/token",
        json={
            "client_id": seed.application_id,
            "client_secret": seed.application_secret,
            "grant_type": "authorization_code",
            "code": code,
        },
    )
    assert token.status_code == 200, token.text
    granted = token.json()
    assert granted["merchant_id"] == seed.merchant_id
    auth = {"Authorization": f"Bearer {granted['access_token']}"}

    # The order your service creates when a ticket is sent to the POS.
    created = client.post(
        "/v2/orders",
        headers=auth,
        json={
            "idempotency_key": "ticket-1042",
            "order": {
                "location_id": seed.location_id,
                "line_items": [{"catalog_object_id": seed.tea_mug_variation_id, "quantity": "2"}],
            },
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()["order"]
    assert order["state"] == "OPEN"
    assert order["total_money"] == {"amount": 300, "currency": "USD"}

    # Paid externally (cash at the counter): CreatePayment with the order id.
    paid = client.post(
        "/v2/payments",
        headers=auth,
        json={
            "idempotency_key": "ticket-1042-pay",
            "source_id": "EXTERNAL",
            "amount_money": {"amount": 300, "currency": "USD"},
            "external_details": {"type": "OTHER", "source": "Counter"},
            "order_id": order["id"],
        },
    )
    assert paid.status_code == 200, paid.text
    payment = paid.json()["payment"]
    assert payment["status"] == "COMPLETED"

    # A fresh read sees the consequence: the order is COMPLETED and tendered.
    fetched = client.get(f"/v2/orders/{order['id']}", headers=auth).json()["order"]
    assert fetched["state"] == "COMPLETED"
    assert fetched["version"] == order["version"] + 1
    assert fetched["tenders"][0]["payment_id"] == payment["id"]
    assert fetched["net_amount_due_money"]["amount"] == 0


# ---------------------------------------------------------------------------
# 2. The catalog your menu sync reads.
# ---------------------------------------------------------------------------


def test_the_catalog_lists_items_with_their_variations(square: StartedUnit) -> None:
    listed = square.client.get("/v2/catalog/list", params={"types": "ITEM"}, headers=square.seed.auth)
    assert listed.status_code == 200
    items = {row["id"]: row for row in listed.json()["objects"]}
    tea = items[square.seed.tea_item_id]
    assert tea["type"] == "ITEM"
    assert tea["item_data"]["name"] == "Tea"
    variation_ids = [v["id"] for v in tea["item_data"]["variations"]]
    assert square.seed.tea_mug_variation_id in variation_ids


# ---------------------------------------------------------------------------
# 3. A webhook lands on your receiver, and its signature verifies.
# ---------------------------------------------------------------------------


def test_an_order_created_webhook_is_delivered_and_verifies(square: StartedUnit, receiver: WebhookReceiver) -> None:
    client = square.client
    auth = square.seed.auth

    # Register the subscription the way you register with Square; the
    # signature key comes back in the response.
    registered = client.post(
        "/v2/webhooks/subscriptions",
        headers=auth,
        json={
            "idempotency_key": "sub-1",
            "subscription": {
                "name": "orders",
                "event_types": ["order.created", "order.updated"],
                "notification_url": receiver.url,
            },
        },
    )
    assert registered.status_code == 200, registered.text
    signature_key = registered.json()["subscription"]["signature_key"]

    created = client.post(
        "/v2/orders",
        headers=auth,
        json={
            "idempotency_key": "ticket-2001",
            "order": {
                "location_id": square.seed.location_id,
                "line_items": [{"catalog_object_id": square.seed.tea_mug_variation_id, "quantity": "1"}],
            },
        },
    )
    assert created.status_code == 200
    square.drain()  # wait for delivery, retries included

    (delivery,) = receiver.received
    # The check your handler performs, with the helper vendorfake exports --
    # the same algorithm Square's SDK helper implements.
    assert verify_square_signature(
        signature_key, receiver.url, delivery.body, delivery.header("x-square-hmacsha256-signature") or ""
    )
    event = json.loads(delivery.body)
    assert event["type"] == "order.created"
    assert event["merchant_id"] == square.seed.merchant_id
    assert event["data"]["object"]["order_created"]["order_id"] == created.json()["order"]["id"]


# ---------------------------------------------------------------------------
# 4. Chaos: a rate limit your retry loop must survive, and a 401 that must
#    NOT deactivate the connection.
# ---------------------------------------------------------------------------


def test_a_rate_limited_create_is_retried_and_the_idempotency_key_holds(square: StartedUnit) -> None:
    square.add_chaos_rule(
        {
            "id": "flaky-create",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": "POST /v2/orders"},
            "when": {"nth": [1, 2]},
            "params": {"retry_after_seconds": 0},
        }
    )
    body = {
        "idempotency_key": "ticket-3003",
        "order": {
            "location_id": square.seed.location_id,
            "line_items": [{"catalog_object_id": square.seed.tea_mug_variation_id, "quantity": "1"}],
        },
    }
    statuses = []
    response: httpx.Response | None = None
    for _attempt in range(5):  # your client's retry loop
        response = square.client.post("/v2/orders", headers=square.seed.auth, json=body)
        statuses.append(response.status_code)
        if response.status_code != 429:
            break
        assert response.headers["retry-after"] == "0"
        assert response.json()["errors"][0]["code"] == "RATE_LIMITED"
    assert statuses == [429, 429, 200]
    assert response is not None
    order_id = response.json()["order"]["id"]

    # Replaying the same key returns the same order, not a second one.
    again = square.client.post("/v2/orders", headers=square.seed.auth, json=body)
    assert again.json()["order"]["id"] == order_id


def test_a_transient_401_is_followed_by_a_200_so_do_not_deactivate_on_the_first(square: StartedUnit) -> None:
    square.add_chaos_rule(
        {
            "id": "expire-once",
            "scope": "request",
            "fault": "token_expiry",
            "match": {"route": "GET /v2/locations"},
            "when": {"nth": [1]},
        }
    )
    first = square.client.get("/v2/locations", headers=square.seed.auth)
    assert first.status_code == 401
    assert first.json()["errors"][0]["code"] == "ACCESS_TOKEN_EXPIRED"
    second = square.client.get("/v2/locations", headers=square.seed.auth)
    assert second.status_code == 200
