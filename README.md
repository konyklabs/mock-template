# vendorfake

High-fidelity **fakes** of third-party vendor APIs — stateful flows, signed
webhooks, and deterministic fault injection — for testing integrations without
touching a vendor sandbox.

> **Unofficial.** Not affiliated with, endorsed by, or connected to any vendor
> named here. Every behaviour is derived from publicly published API
> documentation. Vendor names are used only to identify which public API a
> module imitates.

Three vendors ship today. **Square (Connect v2)** is complete — OAuth2 (code +
PKCE flows), orders with a real lifecycle and fulfillments, external payments
that tender those orders, the merchant, locations, catalog and inventory
counts, a loyalty program, and webhook subscriptions whose deliveries are
signed the way Square signs them and retried on Square's documented schedule;
the full route list is under [The Square surface](#the-square-surface).
**Clover (REST v3)** ships the
same shape: OAuth v2 (authorize, token exchange, single-use refresh rotation,
the documented 401-for-everything auth behaviour), orders and line items with
client-owned totals, the atomic order/checkout calculators with taxes,
inventory with modifier groups, the merchant's employees/tenders/order
types/default service charge, customers, external-tender payments that lock
the order, print events, webhooks in Clover's aggregate-payload shape with
the static `X-Clover-Auth` header and the dashboard verification handshake,
and a seeded scenario — see [Clover quickstart](#clover-quickstart) and [the
webhook an order fires](#the-webhook-an-order-fires). **Toast (REST v2/v3)**
is the consumer-driven slice a restaurant-ordering integration calls: the
machine-client login with a JWT, the V3 menu and the configuration lists,
`/prices` and orders whose amounts are computed server-side from the
restaurant's tax rates (money as decimal dollars, the documented 8.99 → 9.55
example reproduced), OTHER and pre-authorised CREDIT payments, tips, voids,
discounts, stock, and webhooks in Toast's envelope with the documented headers,
the `Toast-Signature` HMAC and the documented five-then-ten-minute retry — see
[Toast quickstart](#toast-quickstart).

Because several vendors are installed, every command names one: `--vendor
square` (or `--vendor clover`, `--vendor toast`), or set `VENDORFAKE_VENDOR`. With no selector the
command refuses and lists what it found — it never guesses.

## Install

Not yet on PyPI. Until v0.1 is published, install from source:

```sh
git clone https://github.com/konyklabs/vendorfake
cd vendorfake
uv run vendorfake serve --vendor square   # or: uv sync && .venv/bin/vendorfake serve --vendor square
```

Once v0.1 is out this becomes `pip install vendorfake` / `uv add vendorfake`.
Python ≥ 3.11.

## Quickstart

Serve the Square unit (defaults to the `full` profile on port 8080):

```sh
vendorfake serve --vendor square      # or: VENDORFAKE_VENDOR=square vendorfake serve
```

The default scenario is pre-seeded — a merchant, two locations, a small
catalog, two orders, and a full-scope access token
`EAAAl-unit-seeded-access-token-full-scopes` — so the first call needs no
setup at all:

```sh
curl -s http://localhost:8080/v2/locations \
  -H "Authorization: Bearer EAAAl-unit-seeded-access-token-full-scopes"
# -> {"locations": [{"id": "18YC4JDH91E1H", "name": "Grant Park", ...
```

### The OAuth dance

Or mint your own token. The unit's application credentials are
`sandbox-sq0idb-unit-square-application` / `sandbox-sq0csb-unit-square-secret`:

```sh
curl -si "http://localhost:8080/oauth2/authorize?client_id=sandbox-sq0idb-unit-square-application&scope=ORDERS_READ+ORDERS_WRITE&state=xyz" \
  | grep -i '^location'
# location: https://example.test/oauth/callback?code=sq0cgb-CciusANvZ6RxELNSs76az9&response_type=code&state=xyz
```

Exchange the `code` from that redirect:

```sh
curl -s -X POST http://localhost:8080/oauth2/token -H 'Content-Type: application/json' -d '{
  "client_id": "sandbox-sq0idb-unit-square-application",
  "client_secret": "sandbox-sq0csb-unit-square-secret",
  "grant_type": "authorization_code",
  "code": "sq0cgb-CciusANvZ6RxELNSs76az9"
}'
# -> {"access_token": "EAAAzVuGgPQbUMl08bIkmyfSIp8...", "token_type": "bearer",
#     "expires_at": "2026-09-27T22:19:12Z", "merchant_id": "MLQW2MYBY81PZ", ...}
```

### An order, and the webhook it fires

Register a subscriber (this needs the seeded full-scope token — webhook
subscriptions require a scope the two-scope token above was not granted), then
create and pay an order:

```sh
SEED=EAAAl-unit-seeded-access-token-full-scopes

curl -s -X POST http://localhost:8080/v2/webhooks/subscriptions \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' -d '{
  "idempotency_key": "sub-quickstart-1",
  "subscription": {"name": "quickstart", "event_types": ["order.created", "order.updated"],
                   "notification_url": "http://localhost:9999/webhooks"}
}'
# -> {"subscription": {"id": "wbhk_8b4735a2...", "signature_key": "CM2erR8ajwYT9objd79byt", ...}}

curl -s -X POST http://localhost:8080/v2/orders \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' -d '{
  "idempotency_key": "order-quickstart-1",
  "order": {"location_id": "18YC4JDH91E1H",
            "line_items": [{"catalog_object_id": "2TZFAOHWGG7PAK2QEXWYPZSP", "quantity": "1"}]}
}'
# -> {"order": {"id": "CAIShCa1UcfqSiyfCVPNUIknxWD", "state": "OPEN", "version": 1,
#     "line_items": [{"name": "Tea", "variation_name": "Mug", ...

curl -s -X POST http://localhost:8080/v2/orders/CAIShCa1UcfqSiyfCVPNUIknxWD/pay \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' \
  -d '{"idempotency_key": "pay-quickstart-1", "order_version": 1}'
# -> {"order": {"id": "CAIShCa1UcfqSiyfCVPNUIknxWD", "state": "COMPLETED", "version": 2, ...
```

Both transitions fired real deliveries at the notification URL — signed,
enveloped, and (since nothing listens on :9999) retried on Square's documented
schedule. The control plane records every attempt:

```sh
curl -s http://localhost:8080/__unit/webhooks/deliveries
# -> {"count": 17, "deliveries": [
#      {"event_type": "order.created", "attempt": 1, "status": "failed",
#       "headers": {"x-square-hmacsha256-signature": "uis2mxg+I7AgGDNhwFbvranI+sI0lgR0wd2aLEMPHcE=",
#                   "square-environment": "Sandbox", ...},
#       "body": {"merchant_id": "MLQW2MYBY81PZ", "type": "order.created", ...
```

Point a real receiver at `notification_url` and you get the same bytes,
verifiable against the subscription's `signature_key` exactly as the vendor
documents (`GET /__unit/info` describes the signing scheme and cites it).

### Deterministic chaos

Faults are rules, not dice. Make the next two `POST /v2/orders` calls fail as
rate-limited, then watch a retrying client succeed on the third attempt:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "flaky-create-order",
  "scope": "request",
  "fault": "rate_limit",
  "match": {"route": "POST /v2/orders"},
  "when": {"nth": [1, 2]},
  "params": {"retry_after_seconds": 2}
}'

curl -si -X POST http://localhost:8080/v2/orders -H "Authorization: Bearer $SEED" ...
# attempt 1 -> HTTP/1.1 429 Too Many Requests   retry-after: 2
#   {"errors": [{"category": "RATE_LIMIT_ERROR", "code": "RATE_LIMITED", ...
# attempt 2 -> 429
# attempt 3 -> 200, order created, idempotency key honoured
```

`GET /__unit/chaos` lists every built-in fault: `rate_limit`, `server_error`,
`unavailable`, `timeout`, `token_expiry`, and the delivery-scope
`webhook.duplicate` / `webhook.delay` / `webhook.out_of_order` /
`webhook.drop_ack` / `webhook.drop`. Rules match on route, capability, event
type, header or body content; `when` selects the nth call, every-nth, a window,
or seeded probability. Same seed, same profile → same faults, every run.

#### Rehearsing a 401 deactivation

Integrations commonly deactivate a connection the moment a call answers 401,
so that path deserves a rehearsal. The `token_expiry` fault answers one
request with Square's documented `401 ACCESS_TOKEN_EXPIRED` **without
touching the stored token** — the next call succeeds again, which is exactly
the transient case a deactivate-on-401 handler gets wrong:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "expire-on-next-read",
  "scope": "request",
  "fault": "token_expiry",
  "match": {"route": "GET /v2/orders/{order_id}"},
  "when": {"nth": [1]}
}'
# the next GET /v2/orders/{id} -> 401 {"errors": [{"category": "AUTHENTICATION_ERROR",
#                                                   "code": "ACCESS_TOKEN_EXPIRED", ...
# the one after it            -> 200
```

The shipped `chaos-demo` profile carries the same rule on the fourth read. For
a *permanent* 401, revoke the token instead (`POST /oauth2/revoke` with the
application secret → every later call answers `ACCESS_TOKEN_REVOKED`), or
advance a virtual clock past the token's `expires_at` as shown below.

### The control plane

Every route under `/__unit/*` is the operator's side channel — state,
time and observability. The tour:

```sh
curl -s http://localhost:8080/__unit/health
# -> {"status": "ok", "vendor": "square", "profile": "full", ...}

curl -s "http://localhost:8080/__unit/journal?since=19"
# every state mutation, in order, with what changed and which operation did it

curl -s http://localhost:8080/__unit/state/snapshot   # full state + digest
curl -s -X POST http://localhost:8080/__unit/state/reset -H 'Content-Type: application/json' -d '{}'
# -> back to the seed: {"entities": {"orders": 2, "tokens": 2, ...}, "digest": "594a6c28..."}
```

Time is controllable when the unit starts with a virtual clock:

```sh
VENDORFAKE_CLOCK=virtual vendorfake serve --vendor square

curl -s -X POST http://localhost:8080/__unit/clock/advance \
  -H 'Content-Type: application/json' -d '{"ms": 2592000000}'    # 30 days
# -> {"now": "2026-09-27T22:20:54.388Z", ...}

curl -s http://localhost:8080/v2/locations -H "Authorization: Bearer $SEED"
# -> {"errors": [{"category": "AUTHENTICATION_ERROR", "code": "ACCESS_TOKEN_EXPIRED", ...
```

The rest is discoverable, not memorised: `GET /__unit/routes` lists all 65
routes with summaries, `GET /__unit/info` (or `vendorfake info --vendor square`)
describes the whole unit — capabilities, auth, signing scheme, fault catalogue,
retry schedule — and `vendorfake openapi --vendor square` prints an OpenAPI 3.1
document.

## Clover quickstart

Serve the Clover unit (defaults to the `full` profile):

```sh
vendorfake serve --vendor clover
```

The scenario is pre-seeded — merchant `HRVSTRYE12345` ("Harvest & Rye"),
three items, a modifier group, two employees, two tenders, two order types,
two tax rates, the default service charge, a customer, one open order, a
full-permission bearer `unit-seeded-clover-access-token-full-permissions`
(and a read-only one), and a pre-verified webhook subscriber whose auth code
is `unit-seeded-clover-webhook-auth-code` — so the first call needs no setup.
Every `/v3` path is scoped to the merchant:

```sh
SEED=unit-seeded-clover-access-token-full-permissions
M=HRVSTRYE12345

curl -s http://localhost:8080/v3/merchants/$M/items -H "Authorization: Bearer $SEED"
# -> {"elements":[{"href":"https://apisandbox.dev.clover.com/v3/merchants/HRVSTRYE12345/items/CRAFTBEER0750",
#     "id":"CRAFTBEER0750","hidden":false,"available":true,"name":"Craft Beer","price":750,
#     "priceType":"FIXED","defaultTaxRates":false,"isRevenue":false,"modifiedTime":1755786102000}, ...
```

### The OAuth v2 dance

The unit's app credentials are `UNITCLOVERAPP` / `unit-clover-app-secret`.
Clover's redirect names the merchant and echoes the app:

```sh
curl -si "http://localhost:8080/oauth/v2/authorize?client_id=UNITCLOVERAPP" | grep -i '^location'
# location: https://example.test/oauth/callback?merchant_id=HRVSTRYE12345&client_id=UNITCLOVERAPP&code=6299bf64-a9b0-4939-9cdb-6b095853ee99
```

Exchange the `code`; expirations are Unix **seconds** (access tokens live 30
minutes, as documented):

```sh
curl -s -X POST http://localhost:8080/oauth/v2/token -H 'Content-Type: application/json' -d '{
  "client_id": "UNITCLOVERAPP", "client_secret": "unit-clover-app-secret",
  "code": "6299bf64-a9b0-4939-9cdb-6b095853ee99"
}'
# -> {"access_token":"bc63dfc1-ecd2-455a-8681-6c46385f398c","access_token_expiration":1788100237,
#     "refresh_token":"cea1ad66-d073-4345-bd1f-72ccbf4e25a6","refresh_token_expiration":1819634437}
```

Refresh takes no client secret, and the refresh token is single use — send
it twice and the second call is a 401:

```sh
curl -s -X POST http://localhost:8080/oauth/v2/refresh -H 'Content-Type: application/json' -d '{
  "client_id": "UNITCLOVERAPP", "refresh_token": "cea1ad66-d073-4345-bd1f-72ccbf4e25a6"
}'
# -> {"access_token":"ecf2ddac-34fa-4794-9458-98c0850294fb","access_token_expiration":1788100237,
#     "refresh_token":"9fa9acdf-604b-4feb-a89e-c6ec5766c617","refresh_token_expiration":1819634437}
```

### An atomic order, and the payment that locks it

Plain `POST /orders` never totals an order (Clover leaves that to the app —
`total` is yours to set). The atomic endpoint does, taxes and the merchant's
default service charge included:

```sh
curl -s -X POST http://localhost:8080/v3/merchants/$M/atomic_order/orders \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' -d '{
  "orderCart": {
    "orderType": {"id": "KFRPRVCZ73JHM"},
    "lineItems": [
      {"item": {"id": "ESPRESSO00300"}, "modifications": [{"modifier": {"id": "MODIFIEROAT01"}}]},
      {"item": {"id": "CROISSANT0450"}}
    ],
    "serviceCharge": {"id": "SVCCHARGE0001"}
  }
}'
# -> {"id":"240Q4JZPXN595","currency":"USD","total":1002,"state":"open","paymentState":"OPEN", ...
#     "serviceCharge":{"id":"SVCCHARGE0001","name":"Service","percentageDecimal":180000,"enabled":true},
#     "subtotal":800,"totalTaxAmount":58,"taxSummaries":[{"id":"TAXDEFAULT001","name":"Sales Tax","rate":725000,"amount":58}]}

curl -s -X POST http://localhost:8080/v3/merchants/$M/orders/240Q4JZPXN595/payments \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' -d '{
  "tender": {"id": "TENDEREXTRN01"}, "employee": {"id": "EMPLBARISTA01"}, "amount": 1002, "offline": false
}'
# -> {"id":"HXKE0KIJDFK14","order":{"id":"240Q4JZPXN595"},
#     "tender":{"href":"https://apisandbox.dev.clover.com/v3/merchants/HRVSTRYE12345/tenders/TENDEREXTRN01","id":"TENDEREXTRN01"},
#     "amount":1002,"cashbackAmount":0,"employee":{"id":"EMPLBARISTA01"},"createdTime":1788098437885, ...,
#     "offline":false,"result":"SUCCESS"}

curl -s http://localhost:8080/v3/merchants/$M/orders/240Q4JZPXN595 -H "Authorization: Bearer $SEED"
# -> {"id":"240Q4JZPXN595","currency":"USD","total":1002,"state":"locked","paymentState":"PAID", ...}
```

A wrong bearer, an expired one, a token without the permission, or another
merchant's `{mId}` all answer the same `{"message":"401 Unauthorized"}` —
Clover documents no 403 — and the `unit_error` sidecar says which it was.

### The webhook an order fires

Clover has no subscription API — callbacks are configured in the developer
dashboard — so the unit ships one pre-verified subscriber as a template
(`wbhk_seed_quickstart`, pointed at `https://example.test/webhooks/clover`
where nothing listens, and therefore **disabled**) and two ways to add your
own: the control plane, pre-verified with the auth code you choose, or the
dashboard stand-in at `POST /__clover/webhooks/subscriptions`, which runs the
documented verification handshake. With a receiver on `localhost:19999`:

```sh
curl -s -X POST http://localhost:8080/__unit/webhooks/subscriptions -H 'Content-Type: application/json' -d '{
  "notification_url": "http://localhost:19999/webhooks",
  "event_types": ["O:*"], "signature_key": "my-local-auth-code"
}'
# -> {"subscription":{"id":"wbhk_ctl_02","notification_url":"http://localhost:19999/webhooks","event_types":["O:*"], ...}}

curl -s -X POST http://localhost:8080/v3/merchants/$M/orders \
  -H "Authorization: Bearer $SEED" -H 'Content-Type: application/json' \
  -d '{"currency": "USD", "total": 1500, "state": "open", "title": "Table 4"}'
# -> {"id":"NEUU09PKXV0AV","currency":"USD","total":1500,"state":"open","paymentState":"OPEN","title":"Table 4", ...}
```

What the receiver saw — the documented aggregate payload, authenticated by
the documented static header and nothing else (no HMAC, no timestamp):

```
X-Clover-Auth: my-local-auth-code
{"appId":"UNITCLOVERAPP","merchants":{"HRVSTRYE12345":[{"objectId":"O:NEUU09PKXV0AV","type":"CREATE","ts":1788100424155}]}}
```

Retries follow a schedule Clover does not publish (`30s, 2m, 10m, 30m, 2h`,
labelled JUDGMENT in `clover/retry.py`), compressed on the shipped profiles
so a test can watch the whole cascade.

The same six profiles ship for Clover (`--vendor clover --profile <name>`);
`chaos-demo` rate-limits every third order create and expires the token on
the fourth order read. Clover clients usually configure the OAuth host and
the `/v3` API host separately; the unit serves both on one origin, so point
both settings at it.

## Toast quickstart

Serve the Toast unit (defaults to the `full` profile):

```sh
vendorfake serve --vendor toast
```

The scenario is pre-seeded — restaurant `e6a4a8d2-0000-4000-8000-000000000001`
("Harvest & Rye — Toast", `America/New_York`, closeout at 4 am), a V3 menu
whose Tomato Soup is the documented 8.99 pricing example, the 6.25% default
tax rate, an "External" alternate payment type, two dining options, tables,
discounts, stock for every item, one open order, a pre-authorised card
payment, a full-scope bearer `unit-seeded-toast-access-token-full-scopes`
(and a read-only one) and a disabled webhook subscriber as a template — so
the first call needs no setup. Every restaurant-scoped path needs the
documented `Toast-Restaurant-External-ID` header beside the bearer.

### The login

The unit's partner credentials are `unit-toast-client-id` /
`unit-toast-client-secret`. The answer is the documented document, the token
a JWT carrying `partner_guid`, and `expiresIn` the documented 19168 seconds;
there is no refresh — log in again:

```sh
curl -s -X POST http://localhost:8080/authentication/v1/authentication/login -H 'Content-Type: application/json' -d '{
  "clientId": "unit-toast-client-id", "clientSecret": "unit-toast-client-secret", "userAccessType": "TOAST_MACHINE_CLIENT"
}'
# -> {"@class":".SuccessfulResponse","token":{"tokenType":"Bearer","scope":null,"expiresIn":19168,
#     "accessToken":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODgxMzU2NjYsImlhdCI6...","idToken":null,"refreshToken":null},"status":"SUCCESS"}

TOKEN=...            # the accessToken above
R=e6a4a8d2-0000-4000-8000-000000000001
```

### The menu, priced before it is ordered

```sh
curl -s http://localhost:8080/menus/v3/menus -H "Authorization: Bearer $TOKEN" -H "Toast-Restaurant-External-ID: $R"
# -> {"restaurantGuid":"e6a4a8d2-0000-4000-8000-000000000001","lastUpdated":"2025-08-21T14:21:42.000+0000",
#     "restaurantTimeZone":"America/New_York","menus":[{"name":"Dinner", ... "menuItems":[{"name":"Tomato Soup",
#     "guid":"3c9a1f00-0000-4000-8000-00000000c201","multiLocationId":"100000000171238879","price":8.99, ...
#     "modifierGroupReferences":{"2":{"referenceId":2,"name":"Sides", ... "modifierOptionReferences":[6,7]}}, ...
```

"Before you POST the order, you must retrieve the check prices from the
/prices endpoint" — and the two endpoints compute the same amounts from the
same body. Money is decimal dollars; `/prices` persists nothing and answers
`"guid": null`:

```sh
BODY='{"entityType":"Order","diningOption":{"guid":"5d0e2b11-0000-4000-8000-00000000d002","entityType":"DiningOption"},
       "checks":[{"entityType":"Check","selections":[{"entityType":"MenuItemSelection",
       "item":{"guid":"3c9a1f00-0000-4000-8000-00000000c201","entityType":"MenuItem"},"quantity":1}]}]}'

curl -s -X POST http://localhost:8080/orders/v2/prices -H "Authorization: Bearer $TOKEN" -H "Toast-Restaurant-External-ID: $R" \
  -H 'Content-Type: application/json' -d "$BODY"
# -> {"guid":null,"entityType":"Order", ... "checks":[{"guid":null, ... "selections":[{"guid":null, ...
#     "preDiscountPrice":8.99,"price":8.99,"tax":0.56, ... "appliedTaxes":[{... "rate":0.0625,"taxAmount":0.56,"type":"PERCENT"}], ...}],
#     ... "amount":8.99,"taxAmount":0.56,"totalAmount":9.55,"payments":[],"paymentStatus":"OPEN", ...}]}

curl -s -X POST http://localhost:8080/orders/v2/orders -H "Authorization: Bearer $TOKEN" -H "Toast-Restaurant-External-ID: $R" \
  -H 'Content-Type: application/json' -d "$BODY"
# -> {"guid":"5102953b-57d6-4f51-8611-44bdd4647830","entityType":"Order","externalId":null,
#     "openedDate":"2026-08-30T19:02:03.533+0000", ... "businessDate":20260830, ...
#     "checks":[{"guid":"5fb250f9-f62d-4c5b-9186-156dbad338c8", ... "totalAmount":9.55,"paymentStatus":"OPEN", ...}],
#     "source":"API","approvalStatus":"APPROVED","guestOrderStatus":"RECEIVED","voided":false, ... "displayNumber":"2", ...}
```

### A payment, and the void

Orders API payments are `OTHER` (naming an alternate payment type) or a
pre-authorised `CREDIT`. Paying the check marks it `PAID`; a second payment on
a paid check is refused; an empty amount is the one documented error code:

```sh
O=5102953b-57d6-4f51-8611-44bdd4647830; C=5fb250f9-f62d-4c5b-9186-156dbad338c8
curl -s -X POST http://localhost:8080/orders/v2/orders/$O/checks/$C/payments -H "Authorization: Bearer $TOKEN" \
  -H "Toast-Restaurant-External-ID: $R" -H 'Content-Type: application/json' \
  -d '[{"type":"OTHER","amount":9.55,"tipAmount":1.00,"otherPayment":{"guid":"5d0e2b11-0000-4000-8000-00000000d101"}}]'
# -> {"guid":"5102953b-...","paidDate":"2026-08-30T19:02:03.582+0000", ... "checks":[{... "paymentStatus":"PAID",
#     "payments":[{"guid":"3968e77f-f2d4-49f2-8afa-07c38c198e99","entityType":"OrderPayment","type":"OTHER","amount":9.55,
#     "tipAmount":1.0,"amountTendered":9.55,"paymentStatus":"CAPTURED","refundStatus":"NONE",
#     "otherPayment":{"guid":"5d0e2b11-...d101","entityType":"AlternatePaymentType"}, ...}]}]}

curl -s -X POST http://localhost:8080/orders/v2/orders/$O/void -H "Authorization: Bearer $TOKEN" \
  -H "Toast-Restaurant-External-ID: $R" -H 'Content-Type: application/json' \
  -d '{"selections":{"voidAll":true},"payments":{"voidAll":true}}'
# -> {"guid":"5102953b-...","voided":true,"voidDate":"2026-08-30T19:02:03.599+0000","voidBusinessDate":20260830,
#     "guestOrderStatus":"VOIDED", ... "checks":[{"paymentStatus":"VOIDED", ... "payments":[{... "paymentStatus":"VOIDED",
#     "voidInfo":{"voidDate":"2026-08-30T19:02:03.599+0000","voidBusinessDate":20260830}}]}]}

# again -> 400 {"status":400,"code":10015,"message":"Once an order has been voided, it can not be updated.", ...}
```

Every refusal is the documented `ErrorMessage` — `status`, `code`,
`message`, `requestId` and the documented nulls — with the documented status:
a missing scope is a **403** (unlike Clover's 401), a malformed guid a 400
with "The GUID was malformed", a 429 carries `X-Toast-RateLimit-By` /
`-Remaining` / `-Reset` and `Retry-After`. The `unit_error` sidecar names the
kind and whether the status is `documented` or `judgment`:

```sh
curl -si -X POST http://localhost:8080/orders/v2/prices -H 'Authorization: Bearer unit-seeded-toast-access-token-read-only' \
  -H "Toast-Restaurant-External-ID: $R" -H 'Content-Type: application/json' -d "$BODY" | sed -n '1p;/^{/p'
# HTTP/1.1 403 Forbidden
# {"status":403,"code":10010,"message":"The access token is missing the required permission(s): orders:write.",
#  "messageKey":null,"fieldName":null,"link":null,"requestId":"ea47429c-b794-4c8d-9710-5ac5a15b0ab0",
#  "developerMessage":null,"errors":[],"canRetry":null,"unit_error":{"missing":["orders:write"], ... "status_provenance":"documented"}}
```

### The webhooks an order fires

Toast has no subscription API — subscriptions come from the developer portal
or the integrations team — so the unit ships one disabled template
(`sub_seed_quickstart`, secret `unit-seeded-toast-webhook-secret`) and two
ways to add your own: the control plane, or the portal stand-in at
`POST /__toast/webhooks/subscriptions` (HTTPS only, as documented, unless the
vendor config's `allow_insecure_callbacks` is set). With a receiver on
`localhost:19999`, registered through the control plane before the order
above was created:

```sh
curl -s -X POST http://localhost:8080/__unit/webhooks/subscriptions -H 'Content-Type: application/json' -d '{
  "notification_url": "http://localhost:19999/webhooks",
  "event_types": ["order_updated", "low_quantity", "out_of_stock", "in_stock"], "signature_key": "my-local-secret"
}'
```

What the receiver saw for the create — the documented envelope, `details.order`
the full Order as `GET` returns it, and the documented headers:

```
Toast-Attempt-Number: 1
Toast-Event-Type: order_updated
Toast-Event-Category: order_updated
Toast-Restaurant-External-ID: e6a4a8d2-0000-4000-8000-000000000001
Toast-Signature: HJVTAaZGw/qVU42uBitXEjGNtgE5HvvQpiPUxjseOYA=
Content-Type: application/json

{"timestamp":"2026-08-30T19:02:03.533Z","eventCategory":"order_updated","eventType":"order_updated",
 "guid":"f2da7247-e9b6-439c-00c8-b764809d7967","details":{"restaurantGuid":"e6a4a8d2-0000-4000-8000-000000000001",
 "order":{"guid":"5102953b-57d6-4f51-8611-44bdd4647830","entityType":"Order", ...}}}
```

The signature is the documented `Base64(HMAC-SHA256(secret, body + timestamp))`.
Toast does not say which timestamp string is appended and sends no timestamp
header, so the unit appends the envelope's own `timestamp` field, exactly as
spelled in the body — a JUDGMENT labelled in `toast/signer.py` and at
`GET /__unit/info`. Verified in five lines against the delivery above:

```python
import base64, hashlib, hmac, json

body = b'{"timestamp":"2026-08-30T19:02:03.533Z",...}'  # the exact bytes received
timestamp = json.loads(body)["timestamp"]
expected = base64.b64encode(hmac.new(b"my-local-secret", body + timestamp.encode(), hashlib.sha256).digest()).decode()
hmac.compare_digest(expected, "HJVTAaZGw/qVU42uBitXEjGNtgE5HvvQpiPUxjseOYA=")  # True
```

(`vendorfake.toast.verify_toast_signature(secret, raw_body, signature)` is the
same check, shipped for copying.) Payments and the void are `order_updated`
too — "A new order is also considered an update" — and a stock update that
drops the Lemonade to 2 is a `low_quantity` in the `stock` category. Retries
follow the documented schedule — five minutes, then ten, then stop — within
the documented 2-second window, compressed on the shipped profiles so a test
can watch the cascade. One documented rule is not reproduced yet: Toast
resends only on a timeout, a 404, a 429 or a 5xx and never on another 4xx,
while this fake retries on **any** non-2xx until the core grows the seam
(konyklabs/roadmap#40) — a receiver answering 400 is retried here where Toast
would stop.

The same six profiles ship for Toast (`--vendor toast --profile <name>`);
`chaos-demo` rate-limits every third order create, expires the token on the
fourth order read, duplicates the first `order_updated` and reorders the first
`out_of_stock`. The surface, by capability:

| Capability | Routes |
|---|---|
| `auth` | `POST /authentication/v1/authentication/login` |
| `orders` | `POST /orders/v2/prices`, `POST /orders/v2/orders`, `GET /orders/v2/orders` (deprecated guid list), `GET /orders/v2/ordersBulk`, `GET /orders/v2/orders/{guid}`, `POST …/{guid}/void`, `PATCH …/{guid}/deliveryInfo`, `POST …/checks/{c}/selections`, `POST …/checks/{c}/appliedDiscounts`, `POST …/checks/{c}/selections/{s}/appliedDiscounts`, `POST /orders/v2/applicableDiscounts` |
| `payments` | `POST …/checks/{c}/payments`, `PATCH …/checks/{c}/payments/{p}` (tip), `GET /orders/v2/payments?paidBusinessDate`, `GET /orders/v2/payments/{guid}` |
| `menus` | `GET /menus/v3/menus`, `GET /menus/v3/metadata` |
| `config` | `GET /config/v2/<resource>[/{guid}]` for diningOptions, alternatePaymentTypes, taxRates, revenueCenters, serviceAreas, tables, restaurantServices, discounts, serviceCharges, menuItems, menuGroups, menus, voidReasons — `lastModified`, `Toast-Next-Page-Token` |
| `restaurants` | `GET /restaurants/v1/restaurants/{guid}`, `GET /restaurants/v1/groups/{guid}/restaurants` |
| `partners` | `GET /partners/v1/connectedRestaurants`, `GET /partners/v1/restaurants` |
| `stock` | `GET /stock/v1/inventory`, `POST /stock/v1/inventory/search`, `PUT /stock/v1/inventory/update` |
| `webhooks` | the `/__toast/webhooks/subscriptions` stand-in (not a Toast endpoint) |

Two state machines are published at `GET /__unit/machines`: `check`
(`paymentStatus`) and `order` (`guestOrderStatus`).

## Profiles

A profile decides which capabilities a unit serves. Ship-with-the-package
choices (`vendorfake serve --vendor square --profile <name>`, or a path to your own JSON):

| Profile | What it is |
|---|---|
| `full` | Every capability on. The default. |
| `no-faults` | Fault injection off entirely. For happy-path CI. |
| `no-chaos` | Delivery faults off: a webhook that is sent is sent honestly, once. |
| `orders-only` | Orders and payments plus the reference data they point at. No OAuth dance, no webhooks, no loyalty or inventory: authenticate with a seeded token. |
| `oauth-only` | Only the OAuth dance, for testing token handling alone. |
| `chaos-demo` | Full surface with a preloaded fault set: rate limits, mid-flow token expiry, duplicate and reordered delivery. |

## The Square surface

Every route is documented-or-JUDGMENT in its module docstring: where Square
publishes the behaviour the code cites the page, and where it does not the
choice is labelled. `GET /__unit/routes` publishes the same list with
summaries; `GET /__unit/capabilities` says which profile serves which.

| Capability | Routes |
|---|---|
| `oauth` | `GET /oauth2/authorize`, `POST /oauth2/token`, `POST /oauth2/revoke`, `POST /oauth2/token/status` |
| `order-lifecycle` | `POST /v2/orders`, `POST /v2/locations/{location_id}/orders` (the pre-2019 create path), `GET /v2/orders/{order_id}`, `PUT /v2/orders/{order_id}` (sparse update under `version`, including `fulfillments[].state`), `POST /v2/orders/search` (filters, `query.sort`, cursor), `POST /v2/orders/batch-retrieve`, `POST /v2/orders/{order_id}/pay` |
| `merchant-directory` | `GET /v2/merchants`, `GET /v2/merchants/{merchant_id}` (`me` works), `GET /v2/locations`, `GET /v2/catalog/list` (`types`, cursor), `GET /v2/catalog/object/{object_id}`, `POST /v2/catalog/search` (`begin_time` → `latest_time`, `prefix_query` / `exact_query` on `name`), `POST /v2/catalog/object` (upsert ITEM / ITEM_VARIATION under the catalog `version`) |
| `payments` | `POST /v2/payments` (`source_id: "EXTERNAL"` + `external_details`; `autocomplete` default true; tenders and completes its `order_id`), `GET /v2/payments/{payment_id}`, `POST /v2/payments/{payment_id}/complete`, `POST /v2/payments/{payment_id}/cancel` |
| `inventory` | `POST /v2/inventory/changes/batch-create` (physical counts and adjustments to `IN_STOCK`), `POST /v2/inventory/counts/batch-retrieve`, `GET /v2/inventory/{catalog_object_id}` |
| `loyalty` | `GET /v2/loyalty/programs/main`, `POST /v2/loyalty/accounts/search` (by `mappings[].phone_number`), `POST /v2/loyalty/accounts` (E.164 phone), `POST /v2/loyalty/accounts/{account_id}/accumulate` (points from the seeded SPEND rule, or stated) |
| `webhooks` | `GET /v2/webhooks/event-types`, `POST` / `GET` / `DELETE` `/v2/webhooks/subscriptions[/{id}]`, `POST /v2/webhooks/subscriptions/{id}/test` |

Webhook event types: `order.created`, `order.updated`, `payment.created`,
`payment.updated`, `catalog.version.updated` (every catalog upsert),
`inventory.count.updated` (every count change) — all delivered with Square's
URL-bound HMAC signature. Three state machines are published at
`GET /__unit/machines`: `order`, `fulfillment` and `payment`.

## Why this exists

Integration code against third-party vendors is hard to exercise in CI. Vendor
sandboxes are rate-limited, network-bound and behaviourally incomplete — some
require a human to advance a state machine by hand, and several cannot produce
the failure modes that actually break integrations: duplicate webhooks,
out-of-order delivery, retries, expired tokens mid-flow.

Generic mocks don't help, because the thing worth testing is precisely the
behaviour a generic stand-in doesn't have. `vendorfake` aims at the opposite:
few vendors, modelled properly, with the awkward parts reproducible on demand.

The word *fake* is precise rather than modest. In the standard test-double
taxonomy a *mock* is assertion-focused and usually stateless, while a **fake**
has a working implementation — real state, real transitions, real
consequences. Create an order and it exists; pay it and the webhook fires,
signed the way the vendor signs it. Where the public documentation is silent
and a behaviour had to be decided, the wire says so: error bodies carry a
`status_provenance` field distinguishing `documented` from `judgment`.

## Status

Pre-release, built in the open. The Square, Clover and Toast surfaces above are implemented and
tested (`uv run pytest --collect-only -q` prints the current test count);
nothing is published to a registry yet. Treat interfaces as subject to change
until v0.1 is tagged.

## Design

Two architectural decisions are recorded as ADRs in the
[roadmap](https://github.com/konyklabs/roadmap/tree/main/decisions):

- **D-001** — the unit architecture: stateful vendor units with a shared core,
  a journal-backed state engine, capability profiles, and deterministic chaos.
- **D-002** — Python on FastAPI, the `vendorfake` naming schema, and a single
  distribution with vendors as modules.

The invariant those decisions turn on: the stateful machinery — journal, state
store, capability registry, chaos engine, webhook dispatcher — stays
framework-free, and the web framework lives only in the transport adapter.
CI enforces the boundary with import-linter and an AST-level check.

## Licence

Apache-2.0.
