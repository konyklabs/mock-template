# vendorfake

High-fidelity **fakes** of third-party vendor APIs — stateful flows, signed
webhooks, and deterministic fault injection — for testing integrations without
touching a vendor sandbox.

> **Unofficial.** Not affiliated with, endorsed by, or connected to any vendor
> named here. Every behaviour is derived from publicly published API
> documentation. Vendor names are used only to identify which public API a
> module imitates.

Two vendors ship today. **Square (Connect v2)** is complete — OAuth2 (code +
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
webhook an order fires](#the-webhook-an-order-fires).

Because two vendors are installed, every command names one: `--vendor square`
(or `--vendor clover`), or set `VENDORFAKE_VENDOR`. With no selector the
command refuses and lists what it found — it never guesses.

## For consumers

You have an integration to test and no wish to learn how the fake is built.
This section is the whole path: install, run, point your tests at it. Every
command below was run as written.

### Install

Not yet on PyPI. Install straight from the repository (Python ≥ 3.11):

```sh
pip install "vendorfake @ git+https://github.com/konyklabs/vendorfake"
# or, in a uv project:
uv add "vendorfake @ git+https://github.com/konyklabs/vendorfake"

vendorfake vendors            # -> clover, square
vendorfake serve --vendor square
```

That resolves `main`. `vendorfake.testing`, the container and the examples
arrive with the change that ships this README, so if the `main` you are
reading predates it, pin the branch or tag that carries it
(`...vendorfake@<ref>`). From a checkout, `uv sync && uv run vendorfake serve
--vendor square`. Once v0.1 is out this becomes `pip install vendorfake`.

### Run it as a container

One image serves every vendor; the vendor is chosen when the container
starts, by `VENDORFAKE_VENDOR`, and the profile by `VENDORFAKE_PROFILE`
(default `full`). Nothing is published to a registry yet, so build it:

```sh
docker build -t vendorfake .

docker run --rm -p 8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
docker run --rm -p 8081:8080 -e VENDORFAKE_VENDOR=clover -e VENDORFAKE_PROFILE=chaos-demo vendorfake
# equivalent: docker run --rm -p 8080:8080 vendorfake serve --vendor square --profile no-faults

curl -s http://localhost:8080/__unit/health
# -> {"status":"ok","vendor":"square","profile":"full","uptime_ms":221,"framework_answered":0}
```

The image runs as a non-root user, listens on 8080, and carries a
`HEALTHCHECK` on `/__unit/info` so `docker ps` (and any orchestrator) reports
`healthy` only once the unit has hydrated its seed and is answering. With no
vendor set it refuses and lists what it found — it never guesses.
`tools/verify_image_build.sh` is the build's own proof: it builds, serves each
vendor, waits for the healthcheck, and fails if the Dockerfile names a vendor.

### pytest

`vendorfake.testing` is the fixture layer. A unit is built in your test
process in a few milliseconds and driven through `httpx.Client` with no
socket; webhooks still go out over real HTTP, so a receiver on loopback sees
signed bytes. The seeded credentials and ids are attributes, so nothing is
typed twice:

```python
from vendorfake.testing import unit, webhook_receiver
from vendorfake.square.signer import verify_square_signature


def test_an_order_is_paid_and_the_webhook_verifies():
    with unit("square") as square, webhook_receiver() as receiver:
        seed = square.seed  # SquareSeed: tokens, ids, app credentials
        square.subscribe(receiver.url, ["order.created"], signature_key="k")

        created = square.client.post(
            "/v2/orders",
            headers=seed.auth,
            json={
                "idempotency_key": "ticket-1",
                "order": {
                    "location_id": seed.location_id,
                    "line_items": [{"catalog_object_id": seed.tea_mug_variation_id, "quantity": "1"}],
                },
            },
        )
        assert created.status_code == 200
        square.drain()  # wait for deliveries, retries included

        (delivery,) = receiver.received
        assert verify_square_signature(
            "k", receiver.url, delivery.body, delivery.header("x-square-hmacsha256-signature") or ""
        )
```

Clover is the same shape with its own vocabulary — event types are
`<object>:<change>` (`O:CREATE`, `P:UPDATE`, ...; a glob such as `O:*` is
fine), the "signature key" is the `X-Clover-Auth` code, and every `/v3`
path lives under the merchant, which `seed.path()` fills in:

```python
from vendorfake.testing import unit, webhook_receiver
from vendorfake.clover.signer import verify_clover_auth


def test_a_clover_order_fires_a_webhook_with_the_auth_code():
    with unit("clover") as clover, webhook_receiver() as receiver:
        seed = clover.seed  # CloverSeed
        clover.subscribe(receiver.url, ["O:*"], signature_key="auth-code-from-the-dashboard")

        created = clover.client.post(
            seed.path("/orders"), headers=seed.auth, json={"currency": "USD", "total": 1500, "state": "open"}
        )
        assert created.status_code == 200
        clover.drain()

        (delivery,) = receiver.received
        assert verify_clover_auth(delivery.headers, "auth-code-from-the-dashboard")
```

`subscribe` checks the event types against the vendor's vocabulary and
refuses one it will never send — a Square type on a Clover unit would
otherwise register happily and never fire.

Ids are deterministic per unit: two `unit("square")` blocks mint the same
order ids, tokens and codes in the same order, from separate stores. That is
what makes an id assertion stable run to run; it also means ids are not
unique *across* units. Pass `unit("square", seed=2)` when a test needs two
units to diverge.

When your service needs a URL, `served("square")` runs the shipped
`vendorfake serve` in a child process and yields one, and
`serve_in_thread(started)` gives a URL onto an in-process unit. Every driver
wraps the control plane: `add_chaos_rule`, `reset_chaos`, `reset`,
`deliveries`, `advance_clock`.

[`examples/pytest-consumer`](examples/pytest-consumer) is a complete
standalone project — ten tests, both vendors, about a second — plus a
Testcontainers variant against the image. `uv sync && uv run pytest` inside it.

### Vitest

[`examples/vitest-consumer`](examples/vitest-consumer) is the same suite in
TypeScript, sharing nothing with the fake but HTTP. Its `globalSetup` starts
the container through testcontainers when `VENDORFAKE_IMAGE` is set, or
`vendorfake serve` as a child process otherwise, and a webhook receiver the
tests read raw bytes from. `npm install && npm test`.

### Seeded credentials

Every profile ships the same scenario, so a fresh unit needs no setup. The
values are readable and obviously fake by design.

| | Square | Clover |
|---|---|---|
| App credentials | `sandbox-sq0idb-unit-square-application` / `sandbox-sq0csb-unit-square-secret` | `UNITCLOVERAPP` / `unit-clover-app-secret` |
| Redirect URI the app registered | `https://example.test/oauth/callback` | `https://example.test/oauth/callback` |
| Full-access bearer | `EAAAl-unit-seeded-access-token-full-scopes` | `unit-seeded-clover-access-token-full-permissions` |
| Read-only bearer | `EAAAl-unit-seeded-access-token-read-only` | `unit-seeded-clover-access-token-read-only` |
| Merchant | `MLQW2MYBY81PZ` | `HRVSTRYE12345` ("Harvest & Rye") |
| Location / order type | location `18YC4JDH91E1H` (Grant Park), kiosk `057P5VYJ4A5X1` | order types `KFRPRVCZ73JHM` (dine-in), `ORDTYPETAKE01` |
| Catalog | Tea `W62UWFY35CWMYGVWK6TWJDNI` with variations Mug `2TZFAOHWGG7PAK2QEXWYPZSP` (150) and Pot; Cold Brew `BJNQCF2FJ6S6UIDT65ABHLRX` | items `CRAFTBEER0750` (750), `ESPRESSO00300` (300, modifier group `MODGROUPMILK1`: oat `MODIFIEROAT01`, soy `MODIFIERSOY01`), `CROISSANT0450` (450) |
| Orders | open `CAISENgvlJ6jLWAzERDzjyHVybY`, completed `CAISEM82RcpmcFBM0TfOyiHV3es` | open `SEEDORDER0001` |
| Payment plumbing | `POST /v2/payments` with `source_id: "EXTERNAL"` | tender `TENDEREXTRN01` (external), `TENDERCASH001`; employees `EMPLBARISTA01`, `OWNERHRVST001`; service charge `SVCCHARGE0001` (18%) |
| Webhooks | register with the full-access bearer; the `signature_key` comes back | pre-verified subscriber `wbhk_seed_quickstart` (auth code `unit-seeded-clover-webhook-auth-code`), **disabled**; register your own through `POST /__unit/webhooks/subscriptions` |
| Event types | `order.created`, `order.updated`, `payment.created`, `payment.updated`, `catalog.version.updated`, `inventory.count.updated` (`GET /v2/webhooks/event-types`) | `O:`, `I:`, `C:`, `P:` (orders, inventory items, customers, payments) × `CREATE`, `UPDATE`, `DELETE` — e.g. `O:CREATE`; globs like `O:*` accepted |

In Python these are `square.seed.*` / `clover.seed.*` on a started unit
(`vendorfake.testing.SquareSeed`, `CloverSeed`).

**Clover has two hosts in your configuration** — the OAuth host
(`sandbox.dev.clover.com`) and the API host (`apisandbox.dev.clover.com`). The
unit serves both on one origin, so point both settings at it.

### Rehearsing failures

Faults are rules, not dice: same seed, same profile, same faults every run.
Arm one through the control plane (or `square.add_chaos_rule({...})` in
Python), then make the call your retry loop has to survive:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "flaky-create-order", "scope": "request", "fault": "rate_limit",
  "match": {"route": "POST /v2/orders"}, "when": {"nth": [1, 2]},
  "params": {"retry_after_seconds": 2}
}'
# the next two POST /v2/orders -> 429 with Retry-After; the third succeeds and
# the idempotency key still returns the same order
```

The faults every vendor supports: `rate_limit`, `server_error`,
`unavailable`, `timeout`, `token_expiry` (one 401 without touching the stored
token — the transient case a deactivate-on-401 handler gets wrong), and on
deliveries `webhook.duplicate`, `webhook.delay`, `webhook.out_of_order`,
`webhook.drop_ack`, `webhook.drop`. Clover routes are matched with the
tenant placeholder, e.g. `"route": "POST /v3/merchants/{mId}/orders"`.
`GET /__unit/chaos` lists the catalogue; `POST /__unit/chaos/reset` disarms
everything. The `chaos-demo` profile ships a preloaded set. Details and the
401 rehearsal are under [Deterministic chaos](#deterministic-chaos).

### Running the conformance suite against your unit

The contracts the fake holds itself to — determinism, byte-identical bindings,
signature properties, the catch-all — ship in the wheel, and so do targets for
both vendors:

```sh
vendorfake-conformance --target vendorfake.testing.conformance:square_target
vendorfake-conformance --target vendorfake.testing.conformance:clover_target --transport http --profile full
pytest --pyargs vendorfake.conformance --conformance-target vendorfake.testing.conformance:square_target

vendorfake-conformance --base-url http://localhost:8080     # a unit already running, e.g. the container
```

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

Pre-release, built in the open. The Square surface above is implemented and
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
