# vendorfake

High-fidelity **fakes** of third-party vendor APIs — stateful flows, signed
webhooks, and deterministic fault injection — for testing integrations without
touching a vendor sandbox.

> **Unofficial.** Not affiliated with, endorsed by, or connected to any vendor
> named here. Every behaviour is derived from publicly published API
> documentation. Vendor names are used only to identify which public API a
> module imitates.

Two vendors ship today. **Square (Connect v2)** is complete — OAuth2 (code +
PKCE flows), orders with a real lifecycle, locations and catalog, and webhook
subscriptions whose deliveries are signed the way Square signs them and
retried on Square's documented schedule. **Clover (REST v3)** ships the
same shape: OAuth v2 (authorize, token exchange, single-use refresh rotation,
the documented 401-for-everything auth behaviour), orders and line items with
client-owned totals, the atomic order/checkout calculators with taxes,
inventory with modifier groups, the merchant's employees/tenders/order
types/default service charge, customers, external-tender payments that lock
the order, print events, and a seeded scenario — see [Clover
quickstart](#clover-quickstart). Webhooks land with the next PR.

Because two vendors are installed, every command names one: `--vendor square`
(or `--vendor clover`), or set `VENDORFAKE_VENDOR`. With no selector the
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

The rest is discoverable, not memorised: `GET /__unit/routes` lists all 45
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
dashboard — so the unit ships a pre-verified subscriber (pointed at
`https://example.test/webhooks/clover`, where nothing listens) and two ways
to add your own: the control plane, pre-verified with the auth code you
choose, or the dashboard stand-in at `POST /__clover/webhooks/subscriptions`,
which runs the documented verification handshake. With a receiver on
`localhost:19999`:

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

## Profiles

A profile decides which capabilities a unit serves. Ship-with-the-package
choices (`vendorfake serve --vendor square --profile <name>`, or a path to your own JSON):

| Profile | What it is |
|---|---|
| `full` | Every capability on. The default. |
| `no-faults` | Fault injection off entirely. For happy-path CI. |
| `no-chaos` | Delivery faults off: a webhook that is sent is sent honestly, once. |
| `orders-only` | Orders plus the reference data they point at. No OAuth dance, no webhooks: authenticate with a seeded token. |
| `oauth-only` | Only the OAuth dance, for testing token handling alone. |
| `chaos-demo` | Full surface with a preloaded fault set: rate limits, mid-flow token expiry, duplicate and reordered delivery. |

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

Pre-release, built in the open. The Square surface above is implemented and
tested (1371 tests at the time of writing); nothing is published to a registry
yet. Treat interfaces as subject to change until v0.1 is tagged.

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
