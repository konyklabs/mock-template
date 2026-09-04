# Chaos: rules, faults and provenance

Faults are **rules, not dice**: the same seed, the same profile and the same
rule fire the same fault on the same call, every run, which is what makes "the
third create fails" assertable rather than flaky.

## Arming a rule

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "flaky-create-order", "scope": "request", "fault": "rate_limit",
  "match": {"route": "POST /v2/orders"}, "when": {"nth": [1, 2]},
  "params": {"retry_after_seconds": 2}
}'
# the next two POST /v2/orders -> 429 with Retry-After; the third succeeds
```

or, in Python, `driver.add_chaos_rule({...})` — see
[Driver](unit.md#driver). `GET /__unit/chaos` lists the active catalogue,
each fault's `scope` and `provenance` included; `POST /__unit/chaos/reset`
disarms everything. `driver.reset_chaos()` is the same call. The
`chaos-demo` [profile](unit.md#profile) ships a preloaded set.

## The grammar

A rule has an `id`, a `scope` (`request` or `webhook`), a `fault` name,
and two optional clauses:

- **`match`** — which subjects the rule applies to: `route`
  (`"POST /v2/orders"`, `*`-glob'd, e.g. `"POST /v2/orders*"`), `path`,
  `method`, `capability`, `event_type` (webhook scope: a vendor event type,
  glob'd — `"O:*"`), `header` (compared lower-cased), `body_contains`.
  Every condition given is ANDed; an absent `match` applies to every
  subject in its scope — `{"scope": "request", "fault": "server_error"}`
  with no `match` means "fail everything".
- **`when`** — when a *matching* subject actually fires: `nth` (fire on
  these 1-based occurrences), `every` (fire on every Nth match, `>= 1` —
  `% 0` is refused at submission rather than left to produce a `NaN` or a
  500), `after` (skip this many matches first), `times` (stop firing after
  this many), `probability` (seeded-random, so even that run is replayable
  from `GET /__unit/info`). Conditions are ANDed; an absent `when` fires on
  every match.

Clover routes are matched with the tenant placeholder in the template,
e.g. `"route": "POST /v3/merchants/{mId}/orders"`.

## The fault catalogue

[The generated fault reference](../reference/faults.md) is the exact, current
list — every fault's scope, provenance, **phase**, parameters and description.
Three families:

**Vendor faults** (`provenance: vendor`) reproduce something the vendor
itself documents: `rate_limit`, `server_error`, `unavailable`, `timeout`,
`token_expiry` (one 401 without touching the stored token — the transient
case a deactivate-on-401 handler gets wrong).

**Delivery faults** (`webhook` scope, `provenance: vendor`):
`webhook.duplicate`, `webhook.delay`, `webhook.out_of_order`,
`webhook.drop_ack`, `webhook.drop`.

**Transport faults** (`provenance: transport`) reproduce "the network
returned garbage", which no vendor documents: `malformed_body` (invalid JSON,
an HTML error page, an empty or truncated body), `body_mutation` (RFC 6901
JSON-pointer edits to a successful response — remove a field, blank it,
retype it), and three that are not really a *response*: `connection_reset`,
`empty_response`, `slow_body`.

Every faulted response carries `Vendorfake-Fault` and `Vendorfake-Rule`
headers, so a test can tell a faulted answer from a real one without parsing
the body.

## Phase: does the handler commit?

Provenance says where a fault's behaviour comes from; **phase** says *when*
it fires, and the two are independent axes. Every fault publishes its
phase (`GET /__unit/chaos`, `GET /__unit/info`, `vendorfake faults`,
`vendorfake explain fault <name>`):

- `phase: request` — fires **instead of** the handler. `rate_limit`,
  `server_error`, `unavailable`, `timeout`, `token_expiry`. Nothing is
  committed; a retry starts clean.
- `phase: response` — fires **on the answer, after the handler ran and
  committed**. All five transport faults. The store keeps the mutation and
  the journal has it; with four of the five the caller never saw it succeed
  (`slow_body` delivers the answer intact, only late).
- `phase: delivery` — a webhook delivery, not a request: the `webhook.*`
  faults.

**A response-phase fault against a single-use rotation strands the
credential**: `malformed_body` on Clover's `POST /oauth/v2/refresh` rotates
the refresh token, then hands the caller an HTML 502, so the next refresh with
the stored token is a 401 — exactly as behind a real gateway that mangled the
response after the write. The request-log entry for such a call carries
`discarded_mutation: true` and `committed_journal_seq` (see
[Journal and request log](unit.md#the-journal-and-the-request-log)). Bound the
rule with `when: {"nth": [1]}` and re-seed the token, or use a request-phase
fault for the failure the retry ladder is meant to recover from.

## Transport faults: what each binding raises

The three transport faults that act on the connection rather than the body
have no single wire form, so each binding raises the exception a real client
would see in its position. What to catch:

| Fault | In process (`unit()`, `async_unit()`, the pytest fixtures) | Served (`served()`, `serve_in_thread()`, a container) |
| --- | --- | --- |
| `connection_reset` | `httpx.RemoteProtocolError`, raised without waiting | the server closes mid-body; httpx raises a `TransportError` (`RemoteProtocolError` with the pinned uvicorn and Starlette — an observation, not a promise; the repository's own served tests catch `TransportError`) |
| `empty_response` | `httpx.ReadError`, raised without waiting | the server closes before any byte the framework lets it withhold; httpx raises a `TransportError` (`ReadError` or `RemoteProtocolError`, by server version) |
| `slow_body` | delivered whole after the aggregate gap; `httpx.ReadTimeout` without waiting if one gap exceeds the read timeout | streamed in chunks; the client's own read timeout applies per chunk |

`malformed_body` and `body_mutation` are ordinary responses with a bad body
on both. Catch `httpx.TransportError` to cover the first two on either
binding. A rule-authoring mistake (a pointer that is not in *this* answer, a
mode that does not exist) is not a fault at all: it answers a 400 carrying
`Vendorfake-Rule-Error: <rule id>` and no `Vendorfake-Fault` header, so "your
rule did not apply" reads differently from "the vendor failed".

## Rehearsing a timeout without waiting

A `timeout` rule costs a test a millisecond rather than the delay it names.
See
[The clock and the timeout fault](unit.md#the-clock-and-the-timeout-fault).

## Rehearsing a 401 deactivation

`token_expiry` answers one request with the vendor's documented "expired
token" error **without touching the stored token**, so the next call succeeds
again — the transient case a deactivate-on-401 handler gets wrong:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "expire-on-next-read", "scope": "request", "fault": "token_expiry",
  "match": {"route": "GET /v2/orders/{order_id}"}, "when": {"nth": [1]}
}'
```

For a *permanent* revocation, use the vendor's own revoke endpoint instead;
for an expiry a client's own clock would notice, advance a
[virtual clock](unit.md#virtual) past `expires_at`.

## From an SDK: in-band triggers

A consumer talking to the unit through a vendor's own SDK often cannot add a
header or call `/__unit/chaos/rules` at all, but it can set a reference id. So
a vendor declares which **ordinary request fields** are scanned for a magic
prefix, and a value of `chaos:<fault>` (or `chaos:<fault>:k=v` for the fault's
parameters) in one of them arms that fault **for that request only**:

```python
square.client.post(
    "/v2/orders",
    headers=square.seed.auth,
    json={"idempotency_key": "t-1", "order": {"reference_id": "chaos:rate_limit", ...}},
)
# -> 429, exactly as a standing rate_limit rule would have answered
```

The rules of the mechanism:

- It is reached **only under the `chaos` capability**, from the same choke
  point that evaluates standing rules. A profile with `chaos` off ignores
  the value entirely, so a magic string cannot become a second arming path.
- An in-band trigger **wins over a standing rule** and touches no rule
  counter, so an `every`/`nth` sequence is not perturbed by it. The fire is
  still recorded in the chaos history under rule id `magic`.
- Candidate order is a contract: declared body paths first, then query
  parameters, then headers. Only `str` values are candidates, and a later
  candidate's parameters overwrite an earlier one's under the same key. At
  most one fault is armed per request — the first found.
- `chaos:` alone names no fault and is skipped rather than rejected.
- Body paths are read through a content-type-general body reader, so a
  vendor's declared paths are reachable on a form-encoded request too.
- The mechanism is `provenance: judgment`. Square's own sandbox drives
  faults from magic values in ordinary fields
  ([Square sandbox testing](https://developer.squareup.com/docs/devtools/sandbox/testing)),
  which is the prior art; the other three vendors publish no equivalent, so
  the `chaos:` prefix is this project's own, chosen so that no real value
  would carry it.

### Where each vendor declares its fields

| Vendor | Body paths | Query parameter |
|---|---|---|
| Square | `order.reference_id`, `idempotency_key`, `subscription.name` | `state` |
| Clover | `note`, `title`, `externalReferenceId` | `state` |
| Toast | `externalId`, `deliveryInfo.notes` | `pageToken` |
| Lightspeed | `url` (a webhook's) | `state` |

Each is a `MagicTriggerSpec` in that vendor's `vendor.py`, returned from its
`magic` property; none declares a header. The prefix is `chaos:` for all
four.

## Sharing one unit across tests

A unit built once for a whole session is cheap, and it is where the second
test lies to you. **A vendor with single-use or rotating state needs an
explicit `reset()` between tests.** Clover retires a refresh token the moment
it is used, so the second test in a session to refresh gets Clover's real
`401` for a reason unrelated to what it tests; minted tokens, created orders
and armed rules accumulate the same way, more quietly. Under
`pytest-randomly` *which* test pays changes run to run, which reads as a flake
and is not one.

`POST /__unit/state/reset` — [`driver.reset()`](unit.md#driver) — re-hydrates
the store from the seed document with no restart, putting the seeded
single-use token back, and clears the request log and the journal with it.
Armed chaos rules are the one thing it leaves in place. The per-test fixture
is two calls on the way in and one on the way out:

```python
import pytest
from vendorfake.testing import served


@pytest.fixture(scope="session")
def clover():
    with served("clover", "oauth-only") as child:
        yield child


@pytest.fixture(autouse=True)
def fresh(clover):
    clover.reset()  # the seed scenario again: single-use token back, request log empty
    clover.reset_chaos()  # no rule from a previous test, whatever order ran
    yield
    clover.reset_chaos()  # a rule this test leaked cannot blame the next one
```

The same two calls over HTTP, for a suite in another language or a
container: `POST /__unit/state/reset` and `POST /__unit/chaos/reset`
(`DELETE /__unit/requests` — `clear_requests()` — draws a line under setup
*without* a reset). `reset()` also drops every webhook subscriber a test
registered — subscribe *after* the reset, not in a session fixture above it.

**A virtual clock is not rewound.** `reset()` re-hydrates against the
clock as it stands, and no control-plane route sets it back, so every
absolute expiry in the seed moves forward by whatever an earlier test
advanced — an assertion on an exact `expires_at` passes or fails on test
order. On a shared [virtual clock](unit.md#virtual), assert relative to
`clock()` or give the advancing test its own unit.

The [pytest plugin](../start/bindings.md#the-pytest-plugin)'s
`vendorfake_unit` is function-scoped for exactly this reason: a fresh
in-process unit costs milliseconds, so it builds one per test and none of the
above applies. Reach for the shared shape only when the unit is a process or
a container and starting it per test is what costs.

## Provenance

Every behaviour in this project is either something a vendor documents, or
something this project decided because the vendor's documentation is
silent. Provenance is the label that says which, published in three places
rather than left as something only the source code remembers.

### In the source

A behaviour reproducing something a vendor publishes cites the page and is
marked `DOCUMENTED`. Anything invented because the vendor's documentation is
silent is marked `JUDGMENT` and explains the choice. Transport-level
behaviour no vendor documents is labelled `provenance: transport`. Grep any
vendor surface module for `JUDGMENT` to see every place this project decided
something the vendor never told it.

### On an error response: `status_provenance`

Every shaped error carries a `status_provenance` field — `"documented"` or
`"judgment"` — alongside `kind` and any extra `info`. By default it rides
as the `Vendorfake-Status-Provenance` response header (and
`Vendorfake-Error-Kind`, `Vendorfake-Error-Info`); a profile's
`errors.sidecar` (or `VENDORFAKE_ERROR_SIDECAR`) can move it into the body
instead, or carry it in both places:

```sh
curl -si -X POST http://localhost:8080/orders/v2/prices \
  -H 'Authorization: Bearer unit-seeded-toast-access-token-read-only' \
  -H "Toast-Restaurant-External-ID: $R" -d '...' | grep -iE '^(HTTP|vendorfake-)'
# HTTP/1.1 403 Forbidden
# vendorfake-error-kind: forbidden_scope
# vendorfake-status-provenance: documented
```

A `documented` status is the vendor's own answer for this failure. A
`judgment` status is this project's choice where the vendor never said. On
Toast a missing scope answers **403**, which the vendor documents; whether a
malformed guid answers 400 with a specific message is documented nowhere and
is therefore `judgment`, labelled at the site in `toast/errors.py`.

### On a fault: `provenance: vendor | transport`

`GET /__unit/chaos` and `GET /__unit/info` publish a provenance per fault,
and [the generated fault reference](../reference/faults.md) lists it for every
built-in one:

- `provenance: vendor` — reproduces a failure mode the vendor itself
  documents (`rate_limit`, `timeout`, `token_expiry`, ...).
- `provenance: transport` — reproduces something no vendor documents because
  it isn't a vendor behaviour: an HTML error page behind a 502, a response
  missing a documented field, a connection that drops mid-transfer.

A consumer whose retry logic branches on a vendor's documented error code
needs to know whether this fake's answer for an edge case is something the
vendor promised or something this project guessed at reasonably.
