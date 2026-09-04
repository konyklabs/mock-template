# For agents

The consumer-side version: writing or fixing tests against an *installed*
vendorfake, in a repository that is not this one. If you are working inside
the vendorfake repository itself, read its `AGENTS.md` instead.

One shortcut before the detail below: `vendorfake explain
<route|fault|profile|error|header> <name>` answers "what is this" from the
command line, without opening this document or the source: `vendorfake
explain fault timeout`, `vendorfake explain route CreateOrder --vendor
square`.

## Starting a unit

Four ways to hold one, described in full elsewhere -- linked, not repeated:

| way | when | more |
|---|---|---|
| `unit(vendor)` | the default: sync, in-process, no socket, milliseconds to build | [Seeded scenario](seeded-scenario.md) for what it starts with |
| `async_unit(vendor)`, or `.async_client` on `unit(...)` | your test or fixture is `async def` | [Async consumers](async-consumers.md) |
| `served(vendor, env={...})` | a service under test needs a real base URL; `env=` is that child's `VENDORFAKE_*` layer | [Which binding to use → Served](start/bindings.md#served) |
| a container (`docker run ... vendorfake`) | the service under test runs out-of-process too, or in CI | [Which binding to use → Container](start/bindings.md#container) |

`vendorfake.testing.serve_in_thread(started)` adds a real server on a
background thread in front of a unit `unit()` already built, for a test that
needs both an in-process driver and a URL onto the *same* state. The pytest
plugin ([pytest plugin](pytest-plugin.md)) wraps the same four behind a
marker and three fixtures (`vendorfake_unit`, `vendorfake_async_unit`,
`vendorfake_webhook_receiver`), for a suite that would rather write
`@pytest.mark.vendorfake` than a `with` block.

## Vocabulary

- **vendor** -- which API is faked: `square`, `clover`, `toast`.
  `vendorfake vendors` lists what is installed; `vendorfake vendors --json`
  for a script.
- **profile** -- a named JSON document choosing which capabilities are on,
  the seed document, the clock mode, retry timing, and the request-log
  capacity. `vendorfake profiles --vendor <name>` lists what a vendor ships;
  `full` (every capability on) is the default. `vendorfake explain profile
  <name> --vendor <vendor>` gives one profile's summary, capabilities and
  seed.
- **capability** -- one named slice of a vendor's surface (`orders`,
  `webhooks.chaos`, ...) a profile can switch on or off; a disabled one
  answers a documented refusal (`capability_disabled`) rather than 404, so a
  test can assert on the refusal itself. **Role** is the vendor-neutral
  spelling four roles map to -- `auth`, `orders`, `webhooks`, `chaos` -- so a
  request for `capabilities=["orders"]` (or `VENDORFAKE_CAPABILITIES`) reads
  the same whichever vendor a parametrized test is currently running against.
- **seed** -- the scenario a unit starts with: ids, credentials, an order or
  two, already there, no setup call needed. `driver.seed` (a `SquareSeed`,
  `CloverSeed` or `ToastSeed`) is where the fields live;
  `vendorfake.testing.Seed` is the structural type all three share, for a
  test parametrized over vendors. Full field tables: [Seeded
  scenario](seeded-scenario.md).
- **driver** -- the object every binding above yields: `.client` (or
  `.async_client`) speaks the vendor surface with no socket in the in-process
  case, `.seed` names the scenario, and its methods (`subscribe`, `drain`,
  `reset`, the chaos-rule helpers, `requests(...)`) wrap the `/__unit/*`
  control plane so a test says what it means rather than which route does
  it.
- **journal** -- the append-only record of committed *mutations*
  (`GET /__unit/journal`). A read, or a request that was refused, leaves no
  entry -- that is what the request log is for.
- **request log** -- every request the unit handled, matched or not, 2xx or
  4xx (`driver.requests(...)`, `GET /__unit/requests`). It records no bodies
  and no headers, and control-plane calls never appear in it. Read this, not
  the journal, when a call under test never committed anything -- a wrong
  path, a capability that was off, a 4xx.
- **clock** -- real or virtual (a profile's `clock.mode`, or
  `VENDORFAKE_CLOCK`). Virtual time only moves on `POST
  /__unit/clock/advance`, which is how a token-expiry or a webhook-retry test
  runs without a real sleep, and how it stays deterministic across runs.
- **chaos rule** -- a document saying which requests (by route, method,
  header, event type, body substring) get which fault, how often
  (`every`/`times`), deterministically (a seeded RNG,
  `VENDORFAKE_CHAOS_SEED`), so the same suite fires the same faults on every
  run.
- **fault** -- one specific failure a chaos rule can arm: `rate_limit`,
  `timeout`, `server_error`, `unavailable`, `token_expiry`, five
  `webhook.*` delivery faults, and five transport-fidelity faults
  (`malformed_body`, `body_mutation`, `connection_reset`, `empty_response`,
  `slow_body`). `vendorfake faults` lists every one with its parameters;
  `vendorfake explain fault <name>` gives one, plus its provenance.
- **provenance** -- on nearly everything this project asserts, a tag saying
  whether the behaviour is `documented` (the vendor's own docs say so),
  `judgment` (this project decided, because no vendor page settles it), or
  -- for a fault -- `transport` (about the HTTP transport, not any vendor's
  decision to make). `vendorfake explain error <kind> --vendor <vendor>` and
  `vendorfake explain fault <name>` both report it; read it before treating
  an assertion as a fact about the real vendor rather than about this fake.

## Discovering what a unit serves, without a server

Every one of these works from a checkout or an installed wheel, no unit
running: `vendorfake vendors`, `vendorfake profiles --vendor <v>`,
`vendorfake routes --vendor <v>` (add `--internal` for the `/__unit/*`
control plane too), `vendorfake faults`, `vendorfake openapi --vendor <v>`.
Every one accepts `--json` (before or after the subcommand name) for a
script rather than a human. `vendorfake explain <kind> <name>` narrows any of
these to one row, in whichever form is asked for, and refuses an unknown name
by listing the real ones rather than returning nothing.

## When a request matches nothing

In process, the default is a raised `vendorfake.testing.UnmatchedRequest` (an
`AssertionError`, so pytest reports it as a *failure*, and a retry loop under
test that catches `httpx.HTTPError` does not swallow it) naming the closest
routes by a path/method similarity score -- read the message, it says what
the unit *does* serve, and `GET /__unit/routes` (or `vendorfake explain route
<operation_id>`) is the ground truth behind it. A 404 from a route that *did*
match -- an id that does not exist -- is a real answer and never raises.

Served and container units never raise; they stand in for the vendor and
answer as it would. The same diagnosis rides on every unmatched HTTP response
instead, as the `Vendorfake-Near-Miss` header -- a compact JSON array of the
same candidates (`route`, `score`, `operation_id`). Pass
`unit(vendor, unmatched="vendor-404")`, or set `VENDORFAKE_UNMATCHED` /
a profile's `unmatched.policy`, for a test or a suite that probes an
unmodelled path on purpose.

## The evidence habit

Paste the command and its output. "Tests pass" is not evidence; a pasted
`pytest` failure, or a pasted `vendorfake explain` answer, is. If a step was
skipped, say so rather than omitting it.

## What not to import

`vendorfake.asgi` and `vendorfake.core` are internal: not a supported
surface, and free to change without a major version. A consumer imports
`vendorfake.testing`, `vendorfake.registry`, `vendorfake.pytest`, or a
vendor's `paths` module (`vendorfake.square.paths`, `vendorfake.clover.paths`,
`vendorfake.toast.paths`) -- the one per-vendor import
[api-contract.md](api-contract.md) pins. Each vendor's webhook signature
scheme is documented and independently verifiable there too; the module that
implements it (`vendorfake.square.signer`, `vendorfake.clover.signer`,
`vendorfake.toast.signer`) is not one of the pinned imports, so verify a
delivery against the documented algorithm rather than depending on that
module's path.

## More

[the site home](index.md) for install and the full quickstarts,
[Seeded scenario](seeded-scenario.md) for exact field tables per vendor,
[Async consumers](async-consumers.md) for every async entry point and what a
delay does on each binding, [The pytest plugin](pytest-plugin.md) for the
marker and fixtures, [the changelog](changelog.md) for what changed and when.
