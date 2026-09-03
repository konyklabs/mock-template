# Chaos rules and faults

Faults are **rules, not dice**: the same seed, the same profile and the
same rule fire the same fault on the same call, every run. That is what
makes "the third create fails" something a test can assert rather than
something that flakes.

## Arming a rule

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "flaky-create-order", "scope": "request", "fault": "rate_limit",
  "match": {"route": "POST /v2/orders"}, "when": {"nth": [1, 2]},
  "params": {"retry_after_seconds": 2}
}'
# the next two POST /v2/orders -> 429 with Retry-After; the third succeeds
```

or, in Python, `driver.add_chaos_rule({...})` — see [Driver](driver.md).
`GET /__unit/chaos` lists the active catalogue, each fault's `scope` and
`provenance` included; `POST /__unit/chaos/reset` disarms everything.
`driver.reset_chaos()` is the same call. The `chaos-demo`
[profile](profile.md) ships a preloaded set.

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

[The generated fault reference](../reference/faults.md) is the exact,
current list — every fault's scope, provenance, **phase**, parameters and
description, from the same tables the control plane reads. Three families:

**Vendor faults** (`provenance: vendor`) reproduce something the vendor
itself documents: `rate_limit`, `server_error`, `unavailable`, `timeout`,
`token_expiry` (one 401 without touching the stored token — the transient
case a deactivate-on-401 handler gets wrong).

**Delivery faults** (`webhook` scope, `provenance: vendor`):
`webhook.duplicate`, `webhook.delay`, `webhook.out_of_order`,
`webhook.drop_ack`, `webhook.drop`.

**Transport faults** (`provenance: transport`) reproduce something no
vendor documents because it isn't a vendor behaviour at all — "the network
returned garbage": `malformed_body` (invalid JSON, an HTML error page, an
empty or truncated body), `body_mutation` (RFC 6901 JSON-pointer edits to
a successful response — remove a field, blank it, retype it), and three
that are not really a *response*: `connection_reset`, `empty_response`,
`slow_body`. See [Provenance labels](provenance-labels.md) for why this
distinction is published rather than left implicit. Every faulted
response, all three families included, carries `Vendorfake-Fault` and
`Vendorfake-Rule` headers, so a test can tell a faulted answer from a real
one without parsing the body.

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

The response phase is the one to know about before arming a rule against
a write. **A response-phase fault against a single-use rotation strands the
credential**: `malformed_body` on Clover's `POST /oauth/v2/refresh` rotates
the refresh token, then hands the caller an HTML 502 — the next refresh with
the stored token is a 401, exactly as it would be behind a real gateway that
mangled the response after the write. That is a valuable rehearsal, and it
is also a surprise the first time. Two things make it readable without
opening the journal: the request-log entry for such a call carries
`discarded_mutation: true` and `committed_journal_seq` (see
[Journal and request log](journal-and-request-log.md)), and a rule that
plays a fault sequence ending "clean" cannot end clean against a rotation —
bound the response-phase rule with `when: {"nth": [1]}` and re-seed the
token, or use a request-phase fault for the failure the retry ladder is
meant to recover from.

## Transport faults: what each binding raises

The three transport faults that act on the connection rather than the body
have no single wire form — a socket and an in-process call cannot fail the
same way — so each binding raises the exception a real client would see in
its position. What to catch:

| Fault | In process (`unit()`, `async_unit()`, the pytest fixtures) | Served (`served()`, `serve_in_thread()`, a container) |
| --- | --- | --- |
| `connection_reset` | `httpx.RemoteProtocolError`, raised without waiting | the server closes mid-body; httpx raises a `TransportError` (`RemoteProtocolError` with the pinned uvicorn and Starlette — an observation, not a promise; the repository's own served tests catch `TransportError`) |
| `empty_response` | `httpx.ReadError`, raised without waiting | the server closes before any byte the framework lets it withhold; httpx raises a `TransportError` (`ReadError` or `RemoteProtocolError`, by server version) |
| `slow_body` | delivered whole after the aggregate gap; `httpx.ReadTimeout` without waiting if one gap exceeds the read timeout | streamed in chunks; the client's own read timeout applies per chunk |

`malformed_body` and `body_mutation` are ordinary responses with a bad body,
on both. Catch `httpx.TransportError` to cover the first two on either
binding; the in-process choice of subclass mirrors what httpx itself raises
for the same event on a socket. A rule-authoring mistake in a
`body_mutation` or `malformed_body` rule (a pointer that is not in *this*
answer, a mode that does not exist) is not a fault at all: it answers a
400 carrying `Vendorfake-Rule-Error: <rule id>` and no `Vendorfake-Fault`
header, so a consumer that reads status codes and headers can tell "your
rule did not apply" from "the vendor failed".

## Rehearsing a timeout without waiting

The `timeout` fault's relationship to the [clock](clock.md) is its own
worked example — a real client-side timeout that costs a test a
millisecond rather than the delay named in the rule. See
[Clock → The clock and the timeout fault](clock.md#the-clock-and-the-timeout-fault).

## Rehearsing a 401 deactivation

`token_expiry` answers one request with the vendor's documented "expired
token" error **without touching the stored token** — the next call
succeeds again, which is exactly the transient case a
deactivate-on-connection-on-401 handler gets wrong:

```sh
curl -s -X POST http://localhost:8080/__unit/chaos/rules -H 'Content-Type: application/json' -d '{
  "id": "expire-on-next-read", "scope": "request", "fault": "token_expiry",
  "match": {"route": "GET /v2/orders/{order_id}"}, "when": {"nth": [1]}
}'
```

For a *permanent* revocation, use the vendor's own revoke endpoint instead;
for an expiry a client's own clock would notice, advance a
[virtual clock](clock.md) past `expires_at`.

## Sharing one unit across tests

A unit built once for a whole session — a session-scoped `served()` or
`unit()` fixture, a container started in `globalSetup` — is cheap, and
it is where the second test lies to you. **A vendor with single-use or
rotating state needs an explicit `reset()` between tests.** Clover
retires a refresh token the moment it is used, so the second test in a
session to refresh gets Clover's real `401` for a reason unrelated to what
it tests; every vendor's minted tokens, created orders and armed rules
accumulate the same way, more quietly. Under `pytest-randomly` *which*
test pays changes run to run, which reads as a flake and is not one.

`POST /__unit/state/reset` — [`driver.reset()`](driver.md#chaos-and-reset)
— re-hydrates the store from the seed document with no restart, putting
the seeded single-use token back. The per-test fixture is three calls on
the way in and one on the way out:

```python
import pytest
from vendorfake.testing import served


@pytest.fixture(scope="session")
def clover():
    with served("clover", "oauth-only") as child:
        yield child


@pytest.fixture(autouse=True)
def fresh(clover):
    clover.reset()  # the seed scenario again: the single-use token is back
    clover.reset_chaos()  # no rule from a previous test, whatever order ran
    clover.clear_requests()  # assert_called counts only this test's calls
    yield
    clover.reset_chaos()  # a rule this test leaked cannot blame the next one
```

The same three calls over HTTP, for a suite in another language or a
container: `POST /__unit/state/reset`, `POST /__unit/chaos/reset`,
`DELETE /__unit/requests`. `reset()` also drops every webhook subscriber a
test registered — subscribe *after* the reset, not in a session fixture
above it ([Driver → Chaos and reset](driver.md#chaos-and-reset)).

The [pytest plugin](../pytest-plugin.md)'s `vendorfake_unit` is
function-scoped for exactly this reason: a fresh in-process unit costs
milliseconds, so it builds one per test and none of the above applies.
Reach for the shared shape only when the unit is a process or a container
and starting it per test is what costs.
