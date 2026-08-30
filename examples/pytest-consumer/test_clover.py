"""What a restaurant-ordering integration does against Clover, run against the fake.

Clover clients usually configure two hosts -- the OAuth host and the ``/v3``
API host. The fake serves both on one origin, so both settings point at the
same base URL. Every ``/v3`` path is scoped to the merchant; ``seed.path()``
fills that in.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

from vendorfake.clover.signer import verify_clover_auth
from vendorfake.testing import StartedUnit, WebhookReceiver

# ---------------------------------------------------------------------------
# 1. Token exchange -> atomic order -> payment -> locked / PAID.
# ---------------------------------------------------------------------------


def test_token_exchange_then_an_atomic_order_is_paid_and_locked(clover: StartedUnit) -> None:
    seed = clover.seed
    client = clover.client

    authorize = client.get("/oauth/v2/authorize", params={"client_id": seed.client_id})
    assert authorize.status_code == 302
    redirect = parse_qs(urlsplit(authorize.headers["location"]).query)
    assert redirect["merchant_id"] == [seed.merchant_id]
    code = redirect["code"][0]

    token = client.post(
        "/oauth/v2/token",
        json={"client_id": seed.client_id, "client_secret": seed.client_secret, "code": code},
    )
    assert token.status_code == 200, token.text
    granted = token.json()
    assert set(granted) == {"access_token", "access_token_expiration", "refresh_token", "refresh_token_expiration"}
    auth = {"Authorization": f"Bearer {granted['access_token']}"}

    # The atomic endpoint totals the cart: taxes and the default service charge.
    created = client.post(
        seed.path("/atomic_order/orders"),
        headers=auth,
        json={
            "orderCart": {
                "orderType": {"id": seed.order_type_dine_in_id},
                "lineItems": [
                    {
                        "item": {"id": seed.item_espresso_id},
                        "modifications": [{"modifier": {"id": seed.modifier_oat_id}}],
                    },
                    {"item": {"id": seed.item_croissant_id}},
                ],
                "serviceCharge": {"id": seed.service_charge_id},
            }
        },
    )
    assert created.status_code == 200, created.text
    order = created.json()
    assert order["state"] == "open"
    assert order["paymentState"] == "OPEN"
    assert order["total"] == 1002  # 300 + 50 (oat) + 450, 18% service, 7.25% tax

    paid = client.post(
        seed.path(f"/orders/{order['id']}/payments"),
        headers=auth,
        json={
            "tender": {"id": seed.tender_external_id},
            "employee": {"id": seed.employee_barista_id},
            "amount": order["total"],
            "offline": False,
        },
    )
    assert paid.status_code == 200, paid.text
    assert paid.json()["result"] == "SUCCESS"

    fetched = client.get(seed.path(f"/orders/{order['id']}"), headers=auth).json()
    assert fetched["state"] == "locked"
    assert fetched["paymentState"] == "PAID"


# ---------------------------------------------------------------------------
# 2. The inventory your menu sync reads, modifier groups expanded.
# ---------------------------------------------------------------------------


def test_items_expand_modifier_groups(clover: StartedUnit) -> None:
    seed = clover.seed
    listed = clover.client.get(seed.path("/items"), params={"expand": "modifierGroups"}, headers=seed.auth)
    assert listed.status_code == 200
    items = {row["id"]: row for row in listed.json()["elements"]}
    espresso = items[seed.item_espresso_id]
    assert espresso["price"] == 300
    (milk,) = espresso["modifierGroups"]["elements"]
    assert milk["id"] == seed.modifier_group_milk_id
    assert seed.modifier_oat_id in milk["modifierIds"].split(",")
    assert items[seed.item_croissant_id]["modifierGroups"]["elements"] == []


# ---------------------------------------------------------------------------
# 3. A webhook lands on your receiver with X-Clover-Auth, and it verifies.
# ---------------------------------------------------------------------------


def test_an_order_webhook_arrives_with_the_auth_code_you_configured(
    clover: StartedUnit, receiver: WebhookReceiver
) -> None:
    seed = clover.seed
    # Clover has no subscriptions API -- callbacks are configured in the
    # developer dashboard -- so the fake's control plane stands in for it.
    clover.subscribe(receiver.url, ["O:*"], signature_key="auth-code-from-the-dashboard")

    created = clover.client.post(
        seed.path("/orders"),
        headers=seed.auth,
        json={"currency": "USD", "total": 1500, "state": "open", "title": "Table 4"},
    )
    assert created.status_code == 200, created.text
    clover.drain()

    (delivery,) = receiver.received
    assert verify_clover_auth(delivery.headers, "auth-code-from-the-dashboard")
    payload = json.loads(delivery.body)
    assert payload["appId"] == seed.client_id
    (event,) = payload["merchants"][seed.merchant_id]
    assert event == {"objectId": f"O:{created.json()['id']}", "type": "CREATE", "ts": event["ts"]}


# ---------------------------------------------------------------------------
# 4. Chaos: the documented 429 with its X-RateLimit headers, and the
#    undifferentiated 401 that is transient here.
# ---------------------------------------------------------------------------


def test_a_rate_limited_create_carries_the_documented_headers(clover: StartedUnit) -> None:
    seed = clover.seed
    clover.add_chaos_rule(
        {
            "id": "limit-once",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": "POST /v3/merchants/{mId}/orders"},
            "when": {"nth": [1]},
            "params": {"retry_after_seconds": 1},
        }
    )
    body = {"currency": "USD", "total": 700, "state": "open"}
    limited = clover.client.post(seed.path("/orders"), headers=seed.auth, json=body)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "1"
    assert limited.headers["x-ratelimit-tokenlimit"]
    assert clover.client.post(seed.path("/orders"), headers=seed.auth, json=body).status_code == 200


def test_a_transient_401_is_followed_by_a_200(clover: StartedUnit) -> None:
    seed = clover.seed
    clover.add_chaos_rule(
        {
            "id": "expire-once",
            "scope": "request",
            "fault": "token_expiry",
            "match": {"route": "GET /v3/merchants/{mId}/orders/{orderId}"},
            "when": {"nth": [1]},
        }
    )
    path = seed.path(f"/orders/{seed.open_order_id}")
    first = clover.client.get(path, headers=seed.auth)
    assert first.status_code == 401
    assert first.json()["message"] == "401 Unauthorized"  # Clover's one auth error, as documented
    assert clover.client.get(path, headers=seed.auth).status_code == 200
