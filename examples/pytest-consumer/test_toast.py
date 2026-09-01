"""What a restaurant-ordering integration does against Toast, run against the fake.

Toast asks three things of a client that Square and Clover do not. The
credentials are a machine-client login rather than an OAuth redirect. The
restaurant is named by the ``Toast-Restaurant-External-ID`` **header** rather
than a path segment, so a bearer on its own is a 400 -- the token is fine, the
request just never named a restaurant. And money is **decimal dollars** on the
wire, not integer cents: a client that assumed cents everywhere is wrong by a
factor of a hundred on precisely this vendor.

``seed.auth`` carries the bearer and the restaurant header together, which is
what every restaurant-scoped call sends.
"""

from __future__ import annotations

import json
from typing import Any

from vendorfake.testing import StartedUnit, ToastSeed, WebhookReceiver
from vendorfake.toast.signer import verify_toast_signature

LOGIN_PATH = "/authentication/v1/authentication/login"


def one_soup(seed: ToastSeed) -> dict[str, Any]:
    """The ticket both ``/prices`` and ``/orders`` take: one bowl, dine in."""
    return {
        "entityType": "Order",
        "diningOption": {"guid": seed.dining_option_dine_in_guid, "entityType": "DiningOption"},
        "checks": [
            {
                "entityType": "Check",
                "selections": [{"item": {"guid": seed.item_soup_guid, "entityType": "MenuItem"}, "quantity": 1}],
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Machine-client login -> quote -> create order -> pay -> read the state back.
# ---------------------------------------------------------------------------


def test_machine_client_login_then_an_order_is_quoted_created_and_paid(toast: StartedUnit) -> None:
    seed = toast.seed
    client = toast.client

    # No redirect and no user: the partner's machine client posts its
    # credentials and gets a JWT back.
    login = client.post(
        LOGIN_PATH,
        json={
            "clientId": seed.client_id,
            "clientSecret": seed.client_secret,
            "userAccessType": "TOAST_MACHINE_CLIENT",
        },
    )
    assert login.status_code == 200, login.text
    granted = login.json()
    assert granted["status"] == "SUCCESS"
    assert granted["token"]["tokenType"] == "Bearer"
    assert granted["token"]["accessToken"].count(".") == 2  # a JWT, not an opaque string
    auth = {"Authorization": f"Bearer {granted['token']['accessToken']}", **seed.restaurant_header}

    # What the ticket costs, before anything is written: the documented
    # example, 8.99 at the restaurant's 6.25%.
    quoted = client.post("/orders/v2/prices", headers=auth, json=one_soup(seed))
    assert quoted.status_code == 200, quoted.text
    assert quoted.json()["guid"] is None  # a quote persists nothing, so nothing has a guid
    quote = quoted.json()["checks"][0]
    assert (quote["amount"], quote["taxAmount"], quote["totalAmount"]) == (8.99, 0.56, 9.55)
    # Dollars on the wire. 955 is what a client that assumed cents would have
    # put in front of the guest.
    assert json.dumps(quote["totalAmount"]) == "9.55"

    created = client.post("/orders/v2/orders", headers=auth, json={**one_soup(seed), "externalId": "ticket-1042"})
    assert created.status_code == 200, created.text
    order = created.json()
    check = order["checks"][0]
    assert check["totalAmount"] == quote["totalAmount"]  # the quote is what you are charged
    assert check["paymentStatus"] == "OPEN"
    assert check["payments"] == []

    # Paid at the counter: an OTHER payment naming the configured alternate
    # payment type. The body is a list even for one payment (see below).
    paid = client.post(
        f"/orders/v2/orders/{order['guid']}/checks/{check['guid']}/payments",
        headers=auth,
        json=[
            {
                "type": "OTHER",
                "amount": check["totalAmount"],
                "tipAmount": 0,
                "otherPayment": {"guid": seed.alt_payment_external_guid},
            }
        ],
    )
    assert paid.status_code == 200, paid.text
    (payment,) = paid.json()["checks"][0]["payments"]
    assert payment["amount"] == 9.55
    assert payment["paymentStatus"] == "CAPTURED"

    # A fresh read sees the consequence: the check is settled.
    fetched = client.get(f"/orders/v2/orders/{order['guid']}", headers=auth).json()
    assert fetched["checks"][0]["paymentStatus"] == "PAID"
    assert fetched["checks"][0]["payments"][0]["guid"] == payment["guid"]


# ---------------------------------------------------------------------------
# 2. The menu your sync reads -- priced in dollars.
# ---------------------------------------------------------------------------


def test_the_published_menu_prices_items_in_dollars(toast: StartedUnit) -> None:
    seed = toast.seed
    published = toast.client.get("/menus/v3/menus", headers=seed.auth)
    assert published.status_code == 200, published.text
    document = published.json()
    assert document["restaurantGuid"] == seed.restaurant_guid
    items = {
        item["guid"]: item for menu in document["menus"] for group in menu["menuGroups"] for item in group["menuItems"]
    }
    soup = items[seed.item_soup_guid]
    assert soup["name"] == "Tomato Soup"
    # 899 cents in the scenario, 8.99 on the wire: the conversion your client
    # must NOT do a second time.
    assert soup["price"] == seed.item_soup_price_cents / 100
    assert soup["taxInfo"] == [seed.tax_rate_guid]


# ---------------------------------------------------------------------------
# 3. The restaurant is a header: the two refusals, told apart.
# ---------------------------------------------------------------------------


def test_a_bearer_without_the_restaurant_header_is_a_400_not_a_401(toast: StartedUnit) -> None:
    seed = toast.seed
    # A good token, no restaurant named. A client that reads this as an
    # expired token logs in again for ever and never sends the header it is
    # actually missing.
    unscoped = toast.client.get("/menus/v3/menus", headers=seed.bearer_only)
    assert unscoped.status_code == 400, unscoped.text
    refusal = unscoped.json()
    assert refusal["message"] == "The Toast-Restaurant-External-ID header is required on this endpoint."
    assert refusal["status"] == 400  # Toast repeats the status inside the body

    # The other way round is the 401 you expect.
    nameless = toast.client.get("/menus/v3/menus", headers=seed.restaurant_header)
    assert nameless.status_code == 401, nameless.text
    assert nameless.json()["status"] == 401

    assert toast.client.get("/menus/v3/menus", headers=seed.auth).status_code == 200


# ---------------------------------------------------------------------------
# 4. One payment is still a list.
# ---------------------------------------------------------------------------


def test_a_single_payment_is_still_sent_as_an_array(toast: StartedUnit) -> None:
    seed = toast.seed
    path = f"/orders/v2/orders/{seed.open_order_guid}/checks/{seed.open_order_check_guid}/payments"
    check = toast.client.get(f"/orders/v2/orders/{seed.open_order_guid}", headers=seed.auth).json()["checks"][0]
    assert check["totalAmount"] == 3.45  # the seeded lemonade: 3.25 and 0.20 of tax
    payment = {
        "type": "OTHER",
        "amount": check["totalAmount"],
        "tipAmount": 0,
        "otherPayment": {"guid": seed.alt_payment_external_guid},
    }

    # The shape mistake a client makes first: one payment, sent as an object.
    refused = toast.client.post(path, headers=seed.auth, json=payment)
    assert refused.status_code == 400, refused.text
    assert refused.json()["message"] == "The request body must be a non-empty array of payments."

    accepted = toast.client.post(path, headers=seed.auth, json=[payment])
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["checks"][0]["paymentStatus"] == "PAID"


# ---------------------------------------------------------------------------
# 5. An order_updated webhook lands on your receiver, and its HMAC verifies.
# ---------------------------------------------------------------------------


def test_an_order_updated_webhook_is_delivered_and_verifies(toast: StartedUnit, receiver: WebhookReceiver) -> None:
    seed = toast.seed
    secret = "secret-from-the-partner-portal"

    # Toast partners register a callback in the partner portal, and the fake's
    # stand-in for it demands HTTPS the way the portal does -- so a loopback
    # receiver cannot be registered through it, and the control plane stands in
    # instead, as it does for Clover.
    portal = toast.client.post(
        "/__toast/webhooks/subscriptions", json={"url": receiver.url, "eventCategories": ["order_updated"]}
    )
    assert portal.status_code == 400, portal.text
    toast.subscribe(receiver.url, ["order_updated"], signature_key=secret)

    created = toast.client.post(
        "/orders/v2/orders", headers=seed.auth, json={**one_soup(seed), "externalId": "ticket-2001"}
    )
    assert created.status_code == 200, created.text
    toast.drain()  # wait for delivery, retries included

    (delivery,) = receiver.received
    # The check your handler performs, with the helper vendorfake exports:
    # HMAC-SHA256 over the body and the envelope's own timestamp, base64.
    assert verify_toast_signature(secret, delivery.body, delivery.header("toast-signature") or "")
    assert delivery.header("toast-event-type") == "order_updated"
    assert delivery.header("toast-restaurant-external-id") == seed.restaurant_guid
    assert delivery.header("toast-attempt-number") == "1"

    envelope = json.loads(delivery.body)
    assert envelope["eventCategory"] == "order_updated"
    assert envelope["details"]["restaurantGuid"] == seed.restaurant_guid
    # "A new order is also considered an update", and the details carry the
    # order exactly as GET answers it -- dollars included.
    assert envelope["details"]["order"]["guid"] == created.json()["guid"]
    assert envelope["details"]["order"]["checks"][0]["totalAmount"] == 9.55


# ---------------------------------------------------------------------------
# 6. Chaos: the 429 your retry loop must survive, and a 401 that is transient.
# ---------------------------------------------------------------------------


def test_a_rate_limited_read_carries_retry_after_and_the_retry_succeeds(toast: StartedUnit) -> None:
    toast.add_chaos_rule(
        {
            "id": "limit-once",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": "GET /menus/v3/metadata"},
            "when": {"nth": [1]},
            "params": {"retry_after_seconds": 1},
        }
    )
    limited = toast.client.get("/menus/v3/metadata", headers=toast.seed.auth)
    assert limited.status_code == 429
    assert limited.headers["retry-after"] == "1"
    assert toast.client.get("/menus/v3/metadata", headers=toast.seed.auth).status_code == 200


def test_a_transient_401_is_followed_by_a_200(toast: StartedUnit) -> None:
    seed = toast.seed
    toast.add_chaos_rule(
        {
            "id": "expire-once",
            "scope": "request",
            "fault": "token_expiry",
            "match": {"route": "GET /orders/v2/orders/{guid}"},
            "when": {"nth": [1]},
        }
    )
    path = f"/orders/v2/orders/{seed.open_order_guid}"
    first = toast.client.get(path, headers=seed.auth)
    assert first.status_code == 401
    assert first.json()["code"] == 10008  # the documented "token invalid/expired"
    assert toast.client.get(path, headers=seed.auth).status_code == 200
