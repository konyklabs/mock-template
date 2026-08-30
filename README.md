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
retried on Square's documented schedule. **Clover (REST v3)** is in progress.
At this commit the `clover` unit implements OAuth v2 (authorize, token
exchange, single-use refresh rotation, the documented 401-for-everything auth
behaviour), orders and line items with client-owned totals, the atomic
order/checkout calculators with taxes, inventory with modifier groups, the
merchant's employees/tenders/order types/default service charge, customers,
external-tender payments that lock the order, and print events. The shipped
`full` profile seeds **one merchant** (`HRVSTRYE12345`, "Harvest & Rye"), so
out of the box `GET /oauth/v2/authorize?client_id=UNITCLOVERAPP` redirects
with a code, `POST /oauth/v2/token` with `client_secret`
`unit-clover-app-secret` mints a bearer, and every
`/v3/merchants/HRVSTRYE12345/…` route then accepts it — but the store holds
nothing else yet: no items, employees, tenders, order types, tax rates or
seeded token, so the reference lists come back empty and a line item must
carry its own `price` until the full scenario lands (the next PR). The test
harness seeds that scenario itself today. Webhooks arrive with it.

Because two vendors are installed, every command names one: `--vendor square`
(or `--vendor clover`), or set `VENDORFAKE_VENDOR`. With no selector the
command refuses and lists what it found — it never guesses.

Clover clients usually configure the OAuth host and the `/v3` API host
separately; the `clover` unit serves both prefixes (`/oauth/v2/*` and
`/v3/merchants/*`) on one origin, so point both settings at the unit.

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
