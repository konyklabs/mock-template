# Unit

A **unit** is one running instance of a vendor fake: one vendor's route
table, one profile's worth of capabilities, one seeded state store, one
chaos engine, one webhook dispatcher, one clock. Everything on this page is
a property of a unit, not a separate thing to wire up.

## Vocabulary

| Term | What it is |
|---|---|
| **vendor** | Which API is faked: `square`, `clover`, `toast`, `lightspeed`. `vendorfake vendors` lists what is installed. |
| **profile** | A named JSON document choosing which capabilities are on, the seed document, the clock mode, retry timing and the request-log capacity. [Below](#profile). |
| **capability** | One named slice of a vendor's surface a profile can switch on or off. **Role** is the vendor-neutral spelling. [Below](#capabilities-and-roles). |
| **seed** | The scenario a unit starts with: ids, credentials, an order or two, already there. [Seed](seed.md). |
| **driver** | The object every binding hands back: `.client`, `.seed`, and the control-plane helpers. [Below](#driver). |
| **journal** | The append-only record of committed *mutations*. [Below](#the-journal-and-the-request-log). |
| **request log** | Every request the unit handled, matched or not, 2xx or 4xx. [Below](#the-journal-and-the-request-log). |
| **clock** | Real or virtual. Virtual time moves only on `POST /__unit/clock/advance`. [Below](#clock). |
| **chaos rule** | A document saying which requests get which fault, how often, deterministically. [Chaos](chaos.md). |
| **fault** | One specific failure a chaos rule can arm. [Chaos](chaos.md#the-fault-catalogue). |
| **provenance** | Whether a behaviour is `documented`, `judgment`, or — for a fault — `transport`. [Chaos](chaos.md#provenance). |

`vendorfake explain <route|fault|profile|error|header> <name>` answers "what
is this" from the command line: `vendorfake explain fault timeout`,
`vendorfake explain route CreateOrder --vendor square`.

## Building one

`vendorfake.registry.create_unit` is the single constructor everything else
calls — the CLI's `serve`/`info`/`openapi` subcommands, and
`vendorfake.testing.unit()`/`async_unit()`/`served()` underneath their
context-manager sugar:

```python
from vendorfake.registry import create_unit

unit = create_unit(vendor="square", profile="full")  # built AND started: the store is hydrated
...
unit.stop()
```

In order: resolve the vendor, load the profile (see
[Precedence](#precedence)), construct the unit with its control plane, then
start it. In a test use `vendorfake.testing.unit()` instead: it wraps exactly
this in a context manager and narrows the return type by vendor name.

## What it exposes

- `unit.routes` — every registered `Route`, vendor surface and `/__unit/*`
  control plane together.
- `unit.control` — the `ControlBinding` the `/__unit/*` handlers close over:
  `list_routes()`, and the callbacks that reach the store, the chaos engine
  and the clock without a route handler needing that access.
- `unit.requests` — the bounded
  [request log](#the-journal-and-the-request-log).
- `unit.webhooks` — the delivery dispatcher.
- `unit.context` — vendor, resolved config, clock, logger.
- `unit.handle(req)` — the one entry point every binding calls.

[Which binding to use](../start/bindings.md) changes how a client reaches
`handle`, never what the unit does: the same seed produces the same ids, the
same chaos rule fires the same way, and `GET /__unit/info` reports the same
facts on all four.

## Unmatched requests

A path this unit does not serve answers the vendor's own 404 on the wire, on
every binding, with a `Vendorfake-Near-Miss` header naming the closest
routes. The Python drivers (`unit()`, `served()`, `serve_in_thread()`) turn
that answer into `vendorfake.testing.UnmatchedRequest`, an `AssertionError`,
so pytest reports it as a *failure* and a retry loop under test that catches
`httpx.HTTPError` does not swallow it. Pass `unmatched="vendor-404"` to any
of them for the 404 instead. The policy is the driver's, never the unit's: a
container or a non-Python consumer sees the 404 and the header.

A 404 from a route that *did* match — an id that does not exist — is a real
answer and never raises.

## Profile

A **profile** decides which capabilities a unit serves, and carries the seed
document, retry policy and every other resolved setting a unit starts with.
Every vendor ships the same six names, and a name means the same *shape* of
thing whichever vendor answers it (conformance clauses C34/C35).
[The generated profile reference](../reference/profiles.md) has the exact
capability set each vendor's copy ships with.

| Profile | What it is |
|---|---|
| `full` | Every capability on. The default. |
| `no-faults` | Fault injection off entirely. For happy-path CI. |
| `no-chaos` | Delivery faults off: a webhook that is sent is sent honestly, once. Request-scope chaos (role `chaos`) stays enabled — the name promises no *delivery* chaos, not none at all. |
| `orders-only` | Orders and payments plus the reference data they point at. No OAuth dance, no webhooks: authenticate with a seeded token. |
| `oauth-only` | Only the OAuth dance, for testing token handling alone. |
| `chaos-demo` | Full surface with a preloaded fault set: rate limits, mid-flow token expiry, duplicate and reordered delivery. |

### Precedence

Four layers, each beating the one before it:

```
built-in defaults  <  caller defaults (a vendor's retry schedule)  <  profile document  <  environment
```

`vendor.retry_defaults` sits **under** the profile document, so a profile can
override a vendor default and an operator can override both through
`VENDORFAKE_*` variables — see
[the environment-variable reference](../reference/env.md). Within the
environment layer: the exported process environment first, then the keyword
arguments that spell a variable (`seed=`, `clock_start=`, `seed_overlay=`),
then the `env=` mapping a test passes. `profile=` and `capabilities=` are
resolved ahead of that layer and beat `VENDORFAKE_PROFILE`. `create_unit()`
takes `env` defaulting to `{}`; `unit()`, `served()` and the CLI hand it the
ambient variables through `registry.ambient_env()`.

### Choosing one

Pass a name (`--profile chaos-demo`, or
`unit("square", profile="chaos-demo")`), a path to your own JSON document, or
a **capability request**:

```python
with unit("toast", capabilities=["auth"]) as driver:  # -> resolves to "oauth-only"
    ...
with unit("square", capabilities=["oauth", "payments"]) as driver:  # -> "no-faults" (narrowest superset)
    ...
```

`capabilities=` takes either a [role name](#capabilities-and-roles) or a
vendor's own capability name, and resolves to the narrowest shipped profile
that is a superset of the request — or `full` plus that exact set, through
the environment layer, when no shipped profile qualifies. Passing both
`profile=` and `capabilities=` is a `ValueError`; so is an empty
`capabilities=[]`, because the empty set is a subset of every profile and
would silently pick the smallest one. `GET /__unit/info` echoes back both
`profile` (what started) and `requested_capabilities` (what was asked for).

### A custom profile

Any JSON document matching the same schema (`name`, `summary`,
`capabilities`, `seed`, `webhooks`, `chaos`, `clock`, `requests`,
`unmatched`, `errors`) works as a path: `vendorfake serve --vendor square
--profile ./my-profile.json`. Fields you omit fall through to the vendor's
built-in defaults field by field, not replaced wholesale, so a profile that
sets only `webhooks.retry.time_scale` still inherits the vendor's own retry
schedule underneath it.

## Capabilities and roles

A **capability** is a named slice of a vendor's surface — `oauth`,
`order-lifecycle`, `payments`, `webhooks`, and so on; see
[the generated route reference](../reference/routes-square.md) for which
routes each covers. Every route declares exactly one.

A **role** is the vendor-neutral vocabulary above capability names: `auth`,
`orders`, `webhooks`, `chaos`, each mapped to one of every shipped vendor's
own capabilities and published at `GET /__unit/info` under `vendor.roles`. A
test written once (`capabilities=["auth"]`) means the same request whichever
vendor it runs against, without knowing that Square calls it `oauth`.

### Toggling

A capability that is off is **not hidden**: a route whose capability is
disabled answers an explicit `capability_disabled` error naming the
capability, what is blocking it and the profile, rather than a 404
indistinguishable from "this vendor has no such endpoint at all".

```sh
curl -s http://localhost:8080/__unit/capabilities
# -> {"enabled": ["oauth", "order-lifecycle", ...], "declared": [...]}

curl -s -X POST http://localhost:8080/__unit/capabilities \
  -H 'Content-Type: application/json' -d '{"enable": ["loyalty"], "disable": ["inventory"]}'
```

`VENDORFAKE_CAPABILITIES` sets this at start-up instead — an absolute list,
or a `+add,-remove` delta against the profile's own list.

A name may be dotted (`webhooks.chaos`). Usability consults the **immediate
parent only**, one level: if `a.b` is enabled while `a` is off, `a.b` still
reads as usable. Disabling a parent removes every dotted descendant with it.

An unknown name — a typo in a profile's `capabilities` list, or in
`VENDORFAKE_CAPABILITIES` — is a startup failure naming the declared set,
never a silent no-op.

`/__unit/*` carries its own capability, `__control`: auto-declared, always
enabled, filtered out of every listing, and impossible to switch off, because
doing so would remove the one channel that could turn it back on.

## Driver

`vendorfake.testing.Driver` is the base class every handle a test holds is
built on: `StartedUnit` (in-process — adds `.unit`, `.client`,
`.async_client`) and `ServedUnit` (a real process — adds `.pid`, `.logs`,
`.base_url`). Every method below is expressed against the control plane,
which every [binding](../start/bindings.md) serves identically, so they work
the same on whichever handle `unit()`, `async_unit()` or `served()` returned.

**Discovery**

- `driver.health()`, `driver.info()` — the documents `GET /__unit/health` and
  `GET /__unit/info` serve.
- `driver.route_for(operation_id)` / `driver.path_for(operation_id)` — a
  route by its `operation_id` rather than a hand-typed path. Every vendor
  also ships an `<vendor>.paths` module (`vendorfake.square.paths`,
  `vendorfake.clover.paths`, `vendorfake.toast.paths`,
  `vendorfake.lightspeed.paths`) of `UPPER_SNAKE` constants for the same
  purpose at import time.
- `driver.clock()` — the clock's mode and current instant.

**Requests and assertions**

- `driver.requests(operation_id=, route=, unmatched=, limit=)` — the
  [request log](#the-request-log-what-was-called), filtered.
- `driver.assert_called(operation_id, times=)` — raises with every operation
  the unit actually saw, and its count, in the failure message.
- `driver.clear_requests()` — draws a line under setup.

**Webhooks**

- `driver.subscribe(url, event_types, signature_key=)` — checks the event
  types against the vendor's own vocabulary and refuses one it will never
  send.
- `driver.drain(timeout_s=)` — sleeps the retry timers for real, scaled by
  the profile's `time_scale`, until every delivery has settled or the bound
  is hit; raises rather than let the next assertion run against deliveries
  that never happened. For an uncompressed schedule, drive a
  [virtual clock](#virtual) with `advance_clock` instead.
- `driver.deliveries()` — every delivery attempt the dispatcher recorded.
- `driver.pending_webhook_timers()` — retries still scheduled.

**Chaos and reset**

- `driver.add_chaos_rule(rule)` / `driver.reset_chaos()` — arm and disarm
  [chaos rules](chaos.md) without hand-rolling the `POST /__unit/chaos/rules`
  body.
- `driver.advance_clock(ms)` — move a [virtual clock](#virtual) forward.
- `driver.reset()` — return to the seed scenario. It also drops every
  subscriber a test registered, so `subscribe()` again *after* a reset. A
  unit shared across tests needs this per test when the vendor keeps
  single-use state; the fixture is in
  [Chaos → Sharing one unit across tests](chaos.md#sharing-one-unit-across-tests).

## The journal and the request log

Two records of "what happened", easy to reach for the wrong one.

### The journal: what changed

`GET /__unit/journal?since=N` lists every **state mutation**, in order, with
what changed and which operation did it. It is written by the state store
itself, so only a *committed* mutation appears: a read, a 4xx and a call that
matched no route leave no trace here at all.

`GET /__unit/state`, `GET /__unit/state/snapshot` and
`POST /__unit/state/reset` read and reset the store the journal describes;
`POST /__unit/state/restore` replays a snapshot.

### The request log: what was called

`GET /__unit/requests` (filters: `operation_id`, `route`, `unmatched`,
`limit`), `DELETE /__unit/requests` and
`GET /__unit/requests/unmatched/near-misses` answer what actually landed on
this unit, matched or not, 2xx or 4xx.

```python
with unit("square") as square:
    place_an_order(base_url=square.base_url)  # the code under test

    square.assert_called("CreateOrder", times=1)  # fails listing what WAS called
    (call,) = square.requests(operation_id="CreateOrder")
    assert call["status"] == 200
    assert call.get("fault") is None  # a key with nothing to say is absent

    square.requests(unmatched=True)  # anything that landed nowhere
    square.clear_requests()  # draw a line under setup
```

Two fields tie a row back to the journal. `committed_journal_seq` is the last
journal `seq` this request committed, present only when it committed
something. `discarded_mutation` (always present) is `true` when the handler
committed *and* the caller still did not get its clean answer: a
response-phase fault corrupted it, or the fault's own params were bad and the
caller got the 400 naming the rule instead (`slow_body` delivers the answer
intact, only late, so it does not count). The mutation stands in the store;
it is discarded only from the caller's point of view — which, against a
single-use rotation, means a credential spent by a call that looked like it
failed. See [Chaos → Phase](chaos.md#phase-does-the-handler-commit).

```python
(call,) = clover.requests(route="POST /oauth/v2/refresh")
if call["discarded_mutation"]:
    since = call["committed_journal_seq"] - 1
    committed = clover.client.get("/__unit/journal", params={"since": since}).json()
```

The log is a ring holding the last 10,000 requests by default (`requests:
{"capacity": N}` in a profile, or `VENDORFAKE_REQUEST_LOG_CAPACITY`; zero
switches it off). It records no bodies and no headers, control-plane calls
never appear in it, and `reset()` clears it with the state store.

### Near misses

An unmatched request scores every route in the *active* capability set
against the path and method actually called, deterministically, so the same
typo prints the same diagnosis every run. In-process that diagnosis is the
message on `UnmatchedRequest`; over HTTP the same ranking rides on every
unmatched response as `Vendorfake-Near-Miss`, so a served unit or the
container reports it too:

```sh
curl -si http://localhost:8080/oauth2/tokens -X POST | grep -i near-miss
# vendorfake-near-miss: [{"route":"POST /oauth2/token","score":0.7,"operation_id":"ObtainToken"}, ...]
```

`GET /__unit/requests/unmatched/near-misses` is the same diagnosis read back
from the request log; `GET /__unit/routes` (or `vendorfake explain route
<operation_id>`) is the ground truth behind the ranking.

### Which one answers your question

| Question | Answer |
|---|---|
| "Did the order actually get created?" | Journal (a committed mutation) |
| "Did my client call the right path?" | Request log |
| "Why did my client get a 404 / near-miss?" | Request log, `unmatched=True`, or the `Vendorfake-Near-Miss` header |
| "What is the state right now?" | `GET /__unit/state` / `state/snapshot` |

## Clock

Every unit runs on a `Clock`, in one of two modes, chosen at start-up and
fixed for the unit's lifetime.

### Real (the default)

Time moves the way it always does: a `timeout` fault that stalls 250ms stalls
250ms, and a token that expires in an hour expires an hour from when the unit
started. The right mode for most tests, and the only one a
[served](../start/bindings.md#served) unit's timing behaves intuitively
under.

### Virtual

```sh
VENDORFAKE_CLOCK=virtual vendorfake serve --vendor square
```

Time moves only when told to, through `POST /__unit/clock/advance` (or
`driver.advance_clock(ms)`) — enough to drive an uncompressed retry schedule
to completion, or to jump past a token's `expires_at`, without waiting:

```sh
curl -s -X POST http://localhost:8080/__unit/clock/advance \
  -H 'Content-Type: application/json' -d '{"ms": 2592000000}'    # 30 days
# -> {"now": "2026-09-27T22:20:54.388Z", ...}

curl -s http://localhost:8080/v2/locations -H "Authorization: Bearer $SEED"
# -> {"errors": [{"category": "AUTHENTICATION_ERROR", "code": "ACCESS_TOKEN_EXPIRED", ...
```

`VENDORFAKE_CLOCK` alone leaves the *start instant* to wall-clock luck, so an
`expires_at` assertion is deterministic within one run and different the
next. `VENDORFAKE_CLOCK_START` (an RFC 3339 instant or a timezone-aware
`datetime`; requires `VENDORFAKE_CLOCK=virtual`, refused rather than silently
switching modes) pins it, so two units started from the same value agree on
every expiry to the second:

```python
with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start="2026-01-01T00:00:00Z") as square:
    ...
```

### The clock and the timeout fault

The `timeout` fault is the one place clock mode changes what a *client*
observes, not just what the unit's own state does:

- **Real clock, in-process**: if `delay_ms` exceeds the calling client's read
  timeout, the client raises `httpx.ReadTimeout` **without actually
  waiting** — a millisecond test proves a five-second retry path. A shorter
  delay is waited out for real. Served mode always waits for real, because
  over a socket the timeout is the client's own to enforce.
- **Virtual clock, in-process**: the delay advances scenario time on the
  calling thread and the response comes back immediately, so no
  elapsed-wall-time assertion is meaningful. The client's read timeout is
  still honoured: the answer carries `Vendorfake-Delay-Ms` (the delay the
  rule asked for), and if that exceeds the read timeout the client raises
  `httpx.ReadTimeout` without waiting. One rule means one thing on both
  clocks — past the client's timeout it is a client-side timeout, under it a
  504 — and only the wait differs.
- **Virtual clock, served**: answers the 504 immediately. Over a socket only
  a real wait can time a client out, and a virtual clock never waits for
  real.

## Discovering what a unit serves, without a server

These work from a checkout or an installed wheel with no unit running:
`vendorfake vendors`, `vendorfake profiles --vendor <v>`, `vendorfake routes
--vendor <v>` (add `--internal` for the `/__unit/*` control plane),
`vendorfake faults`, `vendorfake openapi --vendor <v>`. Every one accepts
`--json`, before or after the subcommand name. `vendorfake explain <kind>
<name>` narrows any of them to one row, and refuses an unknown name by
listing the real ones rather than returning nothing.
