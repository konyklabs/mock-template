"""What a retail integration does against Lightspeed X-Series, run against the fake.

Lightspeed asks four things of a client that Square, Clover and Toast do not.

The **token endpoint is form-encoded and lives under a different version
segment** from everything else: ``POST /api/1.0/token`` while the resource API
is ``/api/2026-07/...``.

**Refreshing revokes the access token that came with the consumed refresh
token.** Not just the refresh token -- the bearer too, immediately. A client
that keeps using the pre-refresh bearer works against a naive fake and fails
in production, so that is the first thing rehearsed below.

**Pagination is a version cursor, not a page token.** Every entity carries a
``version``, one monotonically increasing integer per retailer across all
resource types; a list answers ``{"data": [...], "version": {"max", "min"}}``
and the next page is ``after=<the previous max>``. The walk ends on an empty
``data``, at which point both members of ``version`` are ``null``.

**A sale carries its payments inline.** There is no payment operation anywhere
in this API version, and a payment at a register that is not open is refused
in ``PaymentErrorResponse`` -- ``{"error": {"code": <int>, "message": <str>}}``
-- which is the only error schema the specification names.

Every assertion below is on something Lightspeed publishes. Where this unit had
to choose -- the status a refusal gets, the integer inside a payment error, what
exactly the webhook signature covers -- the assertion is weakened to the part a
consumer could rely on against the real API, and the comment says why. Do not
strengthen one of those back: ``src/vendorfake/lightspeed/`` labels each of them
JUDGMENT at its source, and this file is copied.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

from vendorfake.lightspeed.signer import verify_lightspeed_signature
from vendorfake.testing import LightspeedSeed, StartedUnit, WebhookReceiver

TOKEN_PATH = "/api/1.0/token"
API = "/api/2026-07"
FORM = {"content-type": "application/x-www-form-urlencoded"}
REDIRECT_URI = "https://consumer.example/callback"


def a_sale(seed: LightspeedSeed, register_id: str, *, quantity: int = 1) -> dict[str, Any]:
    """One trail mix rung up at ``register_id``, paid in cash.

    ``source.author_id`` is required and there is no Users surface in this
    scoped build, so the scenario points it at the retailer -- which is also
    what ``StockAdjustment.user_id`` does.
    """
    price, tax = 10.87, 1.63
    return {
        "state": "closed",
        "source": {"author_id": seed.cashier_user_id, "register_id": register_id},
        "customer_id": seed.customer_ada_id,
        "line_items": [
            {
                "product": {"id": seed.product_trail_mix_id},
                "quantity": quantity,
                "pricing": {"price": price},
                "tax": {"id": seed.tax_id, "amount": tax},
            }
        ],
        "payments": [
            {
                "type": {"config_id": seed.payment_type_cash_id},
                "amount": round((price + tax) * quantity, 2),
            }
        ],
    }


# ---------------------------------------------------------------------------
# 1. Authorize -> exchange -> refresh, and the bearer that dies with it.
# ---------------------------------------------------------------------------


def test_the_refresh_revokes_the_access_token_it_was_issued_with(lightspeed: StartedUnit) -> None:
    seed = lightspeed.seed
    client = lightspeed.client

    # The real authorize page is on a fixed host and is an interactive consent
    # screen; this unit serves a stand-in at the documented path that approves
    # and redirects. The parameters are the documented ones.
    redirected = client.get(
        "/connect",
        params={
            "response_type": "code",
            "client_id": seed.credentials.app_id,
            "redirect_uri": REDIRECT_URI,
            "state": "opaque-consumer-state",
            "scope": "retailer:read products:read sales:write registers:read register:close webhooks",
        },
        follow_redirects=False,
    )
    assert redirected.status_code == 302, redirected.text
    location = urlsplit(redirected.headers["location"])
    handed_back = parse_qs(location.query)
    # The state comes back untouched -- that is what it is for.
    assert handed_back["state"] == ["opaque-consumer-state"]
    code = handed_back["code"][0]

    # The exchange: form-encoded, five documented parameters, under /api/1.0.
    granted = client.post(
        TOKEN_PATH,
        headers=FORM,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": seed.credentials.app_id,
            "client_secret": seed.credentials.app_secret,
            "redirect_uri": REDIRECT_URI,
        },
    )
    assert granted.status_code == 200, granted.text
    token = granted.json()
    # The seven documented members, and no others.
    assert set(token) == {
        "access_token",
        "token_type",
        "expires",
        "expires_in",
        "refresh_token",
        "domain_prefix",
        "scope",
    }
    assert token["token_type"] == "Bearer"
    assert token["domain_prefix"] == seed.domain_prefix
    assert "products:read" in token["scope"].split()
    # `expires` is a Unix timestamp and `expires_in` its remaining seconds.
    # The VALUE of `expires_in` is not asserted: 86400 is what the docs page's
    # own example shows, not a lifetime the vendor promises (`config.py`).
    assert isinstance(token["expires"], int) and isinstance(token["expires_in"], int)

    first = {"Authorization": f"Bearer {token['access_token']}"}
    assert client.get(f"{API}/products", headers=first).status_code == 200

    # DOCUMENTED, both halves: "Using a refresh token will revoke the access
    # token that was returned with it" and "You must save this new refresh
    # token and use it the next time."
    rotated = client.post(
        TOKEN_PATH,
        headers=FORM,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": seed.credentials.app_id,
            "client_secret": seed.credentials.app_secret,
        },
    )
    assert rotated.status_code == 200, rotated.text
    fresh = rotated.json()
    assert fresh["access_token"] != token["access_token"]
    assert fresh["refresh_token"] != token["refresh_token"]

    # The bearer you were using a moment ago is dead. This is the line worth
    # copying into your own suite. The STATUS is this unit's choice -- no page
    # says what a revoked token gets -- so what a consumer can rely on is that
    # it is refused, and that re-authenticating is what fixes it.
    stale = client.get(f"{API}/products", headers=first)
    assert stale.status_code == 401, stale.text

    assert (
        client.get(f"{API}/products", headers={"Authorization": f"Bearer {fresh['access_token']}"}).status_code == 200
    )

    # And the consumed refresh token is retired: the new one is the only one
    # that works from here.
    reused = client.post(
        TOKEN_PATH,
        headers=FORM,
        data={
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
            "client_id": seed.credentials.app_id,
            "client_secret": seed.credentials.app_secret,
        },
    )
    assert 400 <= reused.status_code < 500, reused.text


# ---------------------------------------------------------------------------
# 2. The version cursor: a full forward sync, in the shape the docs describe.
# ---------------------------------------------------------------------------


def test_a_forward_sync_walks_the_version_cursor_and_stops_on_an_empty_page(lightspeed: StartedUnit) -> None:
    seed = lightspeed.seed
    client = lightspeed.client

    # This is the loop, and it is the whole of pagination on this API: no
    # `after` on the first request ("the value of the after parameter will be
    # assumed as equal 0"), then `after=<the previous response's version.max>`.
    seen: list[dict[str, Any]] = []
    versions: list[int] = []
    after: int | None = None
    pages = 0
    while pages < 10:
        params = {"page_size": 2, **({"after": after} if after is not None else {})}
        page = client.get(f"{API}/products", headers=seed.auth, params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        if not body["data"]:
            # The documented terminator, and the documented null pair with it.
            assert body["version"] == {"max": None, "min": None}
            break
        seen.extend(body["data"])
        versions.extend(row["version"] for row in body["data"])
        # version.max is the last row's version, which is what makes it a cursor.
        assert body["version"]["max"] == body["data"][-1]["version"]
        assert body["version"]["min"] == body["data"][0]["version"]
        after = body["version"]["max"]
        pages += 1

    ids = [row["id"] for row in seen]
    # A version walk repeats no row and loses none, and rows arrive ascending.
    assert len(set(ids)) == len(ids), ids
    assert versions == sorted(versions), versions
    assert seen[0]["id"] == seed.product_trail_mix_id

    # Money on the catalogue is a JSON NUMBER. On the register surface the same
    # API sends decimal strings ("255.00"); a client with one money parser
    # meets both.
    trail_mix = seen[0]
    assert isinstance(trail_mix["price_including_tax"], (int, float))
    assert trail_mix["price_including_tax"] == 12.5

    # Every response carries the documented quota headers, not only a 429:
    # 300 x registers + 50, and the scenario has two registers.
    fresh = client.get(f"{API}/products", headers=seed.auth)
    assert fresh.headers["x-ratelimit-limit"] == "650"
    assert int(fresh.headers["x-ratelimit-remaining"]) < 650


# ---------------------------------------------------------------------------
# 3. A sale, with its payments inline -- and the refusal when the till is shut.
# ---------------------------------------------------------------------------


def test_a_sale_carries_its_payments_and_a_closed_register_refuses_one(lightspeed: StartedUnit) -> None:
    seed = lightspeed.seed
    client = lightspeed.client

    rung_up = client.post(f"{API}/sales", headers=seed.auth, json=a_sale(seed, seed.register_main_id, quantity=2))
    assert rung_up.status_code == 200, rung_up.text
    sale = rung_up.json()["data"]
    assert sale["state"] == "closed"
    assert sale["source"]["register_id"] == seed.register_main_id
    # The outlet is derived from the register: a sale names the till, not the shop.
    assert sale["source"]["outlet_id"] == seed.outlet_main_id

    # Totals are computed from the line items and cannot be declared -- there is
    # no `totals` member on the request schema at all.
    assert sale["totals"]["price"] == 21.74
    assert sale["totals"]["tax"] == 3.26
    assert sale["totals"]["price_incl_tax"] == 25.0
    (payment,) = sale["payments"]
    assert payment["amount"] == 25.0
    # The payment type's display name is filled in from the retailer's own
    # payment types; you send only the config_id.
    assert payment["type"]["config_id"] == seed.payment_type_cash_id

    # A read-back is the same sale, payment and all.
    fetched = client.get(f"{API}/sales/{sale['id']}", headers=seed.auth)
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data"]["payments"][0]["id"] == payment["id"]

    # The second register is seeded closed. "Open a register to create sales and
    # payments" is the closest the vendor comes to stating the rule, and the
    # refusal comes back in the one error schema the specification names.
    refused = client.post(f"{API}/sales", headers=seed.auth, json=a_sale(seed, seed.register_second_id))
    assert 400 <= refused.status_code < 500, refused.text
    body = refused.json()
    # The SHAPE is documented (`PaymentErrorResponse`: error.code integer,
    # error.message string). The code's VALUE is not -- `code` is declared
    # "type: integer" with no enum, no example and no range, and Lightspeed
    # publishes no error-codes page at all -- so nothing here branches on it
    # (`lightspeed/model/error.py`).
    assert isinstance(body["error"], dict)
    assert isinstance(body["error"]["code"], int)
    assert isinstance(body["error"]["message"], str)
    # The nested shape, not the generalised {"error", "message"} one.
    assert "message" not in body, body


# ---------------------------------------------------------------------------
# 4. A sale.update webhook lands on your receiver, and its HMAC verifies.
# ---------------------------------------------------------------------------


def test_a_sale_update_webhook_is_delivered_form_encoded_and_verifies(
    lightspeed: StartedUnit, receiver: WebhookReceiver
) -> None:
    seed = lightspeed.seed
    client = lightspeed.client

    # Registration is an ordinary JSON call on the vendor's own surface -- the
    # form encoding below applies only to the OUTBOUND delivery.
    registered = client.post(
        f"{API}/webhooks",
        headers=seed.auth,
        json={"active": True, "type": "sale.update", "url": receiver.url},
    )
    assert registered.status_code == 201, registered.text
    assert registered.json()["data"]["type"] == "sale.update"

    # The same type and url again is the documented 409, and its body has
    # exactly one member -- the shape the Webhooks tag's own schema declares,
    # unlike every other refusal on this API.
    duplicate = client.post(
        f"{API}/webhooks",
        headers=seed.auth,
        json={"active": True, "type": "sale.update", "url": receiver.url},
    )
    assert duplicate.status_code == 409, duplicate.text
    assert duplicate.json() == {"error": "A webhook with this type and URL already exists."}

    created = client.post(f"{API}/sales", headers=seed.auth, json=a_sale(seed, seed.register_main_id))
    assert created.status_code == 200, created.text
    lightspeed.drain()  # wait for delivery, retries included

    (delivery,) = receiver.received
    # DOCUMENTED: the delivery is application/x-www-form-urlencoded and its
    # required field is `payload`, "a JSON-encoded object with entity details".
    assert delivery.header("content-type") == "application/x-www-form-urlencoded"
    fields = parse_qs(delivery.body.decode())
    assert set(fields) >= {"payload"}
    entity = json.loads(fields["payload"][0])
    assert entity["id"] == created.json()["data"]["id"]
    assert entity["state"] == "closed"

    # DOCUMENTED: "X-Signature: signature=<value>,algorithm=HMAC-SHA256".
    header = delivery.header("x-signature") or ""
    assert header.endswith(",algorithm=HMAC-SHA256")
    # JUDGMENT, and it is worth knowing before you write your own verifier:
    # the docs say only "hashing the webhook request body", and the body is
    # form-encoded with JSON inside a field, so "the body" has two readings.
    # This unit signs the RAW FORM BYTES and encodes hex; the other reading is
    # `lightspeed_signature_over_payload`, and both are published at
    # GET /__unit/info under `signer`. The secret is the application's
    # client_secret -- WebhookRequest carries no per-hook secret.
    assert verify_lightspeed_signature(seed.credentials.app_secret, delivery.body, header)


# ---------------------------------------------------------------------------
# 5. Chaos: the 429 your retry loop must survive, with a date, not a number.
# ---------------------------------------------------------------------------


def test_a_rate_limited_read_carries_an_http_date_retry_after(lightspeed: StartedUnit) -> None:
    seed = lightspeed.seed
    lightspeed.add_chaos_rule(
        {
            "id": "limit-once",
            "scope": "request",
            "fault": "rate_limit",
            "match": {"route": f"GET {API}/products"},
            "when": {"nth": [1]},
        }
    )
    limited = lightspeed.client.get(f"{API}/products", headers=seed.auth)
    assert limited.status_code == 429, limited.text
    # DOCUMENTED, and the trap: Retry-After here is an RFC 1123 HTTP-DATE
    # ("Wed, 15 Jul 2020 15:04:05 GMT"), not delta-seconds. A retry loop that
    # calls int() on it raises. Parse it as a date, or fall back to a fixed
    # backoff -- the rate-limiting page's own example is a date.
    retry_after = limited.headers["retry-after"]
    assert not retry_after.isdigit(), retry_after
    assert retry_after.endswith(" GMT"), retry_after
    # The documented title. The sentence beside it is the injected fault's here;
    # the real limiter sends "Rate limiting enforced".
    assert limited.json()["error"] == "Too Many Requests"
    # The quota headers ride the refusal too.
    assert limited.headers["x-ratelimit-limit"] == "650"

    assert lightspeed.client.get(f"{API}/products", headers=seed.auth).status_code == 200
