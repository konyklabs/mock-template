# Which binding to use

Four ways to run a unit, from fastest-and-most-isolated to
most-like-production. All four are the same [unit](../concepts/unit.md) — same
state machine, same seed, same faults — differing only in how a client reaches
it.

## In-process, sync

```python
from vendorfake.testing import unit

with unit("square") as square:
    seed = square.seed
    response = square.client.get("/v2/locations", headers=seed.auth)
```

`unit()` builds the unit in your test process, in milliseconds, and drives
it through an `httpx.Client` over a transport that calls the router
directly — no socket, no event loop. Use this for the overwhelming majority
of tests. By default an unmatched request raises
`vendorfake.testing.UnmatchedRequest` (an `AssertionError`) rather than
answering the vendor's 404, which turns "my client hit the wrong path" into a
loud test failure; `served()` does the same from a response hook.

## In-process, async

For a service that takes an `httpx.AsyncClient` — an async FastAPI-style
application, an async worker, anything whose fixtures are `async def`. Three
entry points, all yielding the same `vendorfake.testing.StartedUnit`: `.async_client`
on a `unit(...)` block when your test is `async def` but your fixture is not,
`async_unit(...)` when your fixture is `async def` too, and the
`vendorfake_async_unit` [fixture](#the-fixtures) when you would rather write a
marker than a `with` block.

```python
import pytest
from vendorfake.testing import unit


@pytest.mark.anyio
async def test_the_service_refreshes_its_token():
    with unit("square") as square:
        seed = square.seed
        answered = await square.async_client.post(
            "/oauth2/token",
            json={
                "client_id": seed.application_id,
                "client_secret": seed.application_secret,
                "grant_type": "refresh_token",
                "refresh_token": seed.refresh_token,
            },
        )
        assert answered.status_code == 200
```

`async_client` is built on first access and reused, on the same transport
instance as `client` and against the same base URL. Requests through either
are the same call into the unit, so state written on one is visible on the
other with no socket in between: synchronous set-up followed by async code
under test is the ordinary shape, not a workaround.

`async_unit()` is the `async with` form, for a fixture that is `async def`:

```python
import pytest_asyncio
from vendorfake.testing import async_unit


@pytest_asyncio.fixture
async def clover():
    async with async_unit("clover") as started:
        yield started


async def test_items_are_readable(clover):
    answered = await clover.async_client.get(clover.seed.path("/items"), headers=clover.seed.auth)
    assert answered.status_code == 200
```

It takes exactly the arguments `unit()` takes and delegates to it. What it
adds is the exit: the async client's `aclose()` is awaited. The synchronous
`unit()` cannot await inside a running event loop, so it leaves the client for
the loop; nothing leaks either way, because this transport owns no socket, no
connection pool and no thread.

All of this works under `pytest-asyncio` (strict or auto) and under `anyio`'s
plugin, without vendorfake depending on either. Nothing here needs
`vendorfake.asgi`, which is internal.

### Rehearsing a client timeout in a millisecond

A `timeout` chaos rule asks for a delay. The unit decides *whether* to delay;
the binding decides *how*, because only the binding knows what timeout the
caller set. In process the client's read timeout is consulted, so a
five-second rule proves the retry path in about a millisecond:

```python
import time

import httpx
import pytest
from vendorfake.testing import UnitTransport, unit


def test_my_retry_path_survives_a_timeout():
    with unit("square") as square:
        square.add_chaos_rule(
            {
                "id": "slow",
                "scope": "request",
                "fault": "timeout",
                "match": {"route": "GET /v2/locations"},
                "params": {"delay_ms": 5000},
            }
        )
        client = httpx.Client(
            transport=UnitTransport(square.unit),
            base_url=square.base_url,
            timeout=httpx.Timeout(0.2),
        )
        begun = time.monotonic()
        with pytest.raises(httpx.ReadTimeout):
            client.get("/v2/locations", headers=square.seed.auth)
        assert (time.monotonic() - begun) < 0.1
```

`timeout=None` is a setting and not a missing one: a caller who said they
would wait for anything waits, and gets the 504. Over a socket the server
waits and the client times out on its own. The full matrix, including what a
virtual clock changes, is in
[The clock and the timeout fault](../concepts/unit.md#the-clock-and-the-timeout-fault).

Webhooks leave over real HTTP on every binding, so a `webhook_receiver()` on
loopback sees signed bytes; `drain()` is synchronous on both entry points,
because it waits on the unit's delivery machinery rather than on a client.

## Served

```python
from vendorfake.testing import served, unit, serve_in_thread

with served("square") as square:
    square.base_url  # a real http://127.0.0.1:PORT

with unit("square") as started:
    with serve_in_thread(started) as driver:
        driver.base_url  # a real http://127.0.0.1:PORT
```

`served()` runs the shipped `vendorfake serve` in a child process and hands
back a real URL; `serve_in_thread()` puts an ASGI server on a background
thread in front of a unit you already built in-process. Reach for one of
these when the code under test needs an actual `host:port` — a service
configured by URL, a language binding with no custom-transport seam, or a
webhook subscriber that must receive a real POST over loopback. On the wire a
served unit answers the vendor's own 404 to an unmatched request; the
`served()` driver raises `UnmatchedRequest` from that answer by default, and
`unmatched="vendor-404"` hands the 404 through.

`served(..., env={...})` is the `VENDORFAKE_*` layer for that one child, on
top of the environment it inherits, so two differently-seeded children can run
in one process with nothing written to `os.environ`. The parent-resolved
`.seed` reads the same `VENDORFAKE_VENDOR_*` layer, so its credentials agree
with the child's. Four variables behave specially:

- `VENDORFAKE_HOST`, `VENDORFAKE_PORT`, `VENDORFAKE_LOG_LEVEL` — refused with
  a `ValueError` naming the parameter to use, rather than silently beaten by
  the flag `served()` passes.
- `VENDORFAKE_PROFILE` — honoured; an explicit `profile=` beats it, and
  `served(capabilities=)` resolves the way `unit()`'s does.
- `VENDORFAKE_SEED` — refused, because `.seed` is derived from the vendor's
  constants and could not describe a child hydrated from another document.
- `VENDORFAKE_SEED_OVERLAY` — refused in favour of
  [`seed_overlay=`](../concepts/seed.md#seed-overlays), which encodes the
  document for the child and refuses an unknown collection here, where the
  caller can see it, rather than as a child that exited before announcing a
  port. It also refuses an overlay naming `tokens` or the vendor's identity
  collection — see
  [the credentials and the identity](../concepts/seed.md#the-credentials-and-the-identity-cannot-be-overlaid).

`served(validate=True)` puts the fidelity response check behind the socket;
see [Fidelity](../concepts/fidelity.md).

A served child shared across tests needs `reset()` between them when the
vendor keeps single-use state — see
[Chaos → Sharing one unit across tests](../concepts/chaos.md#sharing-one-unit-across-tests).

## Container

```sh
docker build -t vendorfake .
docker run --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
```

One image, every vendor; which one it serves is chosen at run time, never
baked into the image. Reach for this when the consumer under test is not a
Python process — a service in another language, a browser end-to-end suite, or
anything driven through [Testcontainers](https://testcontainers.com/) or
docker compose. `POST /__unit/state/reset` returns a unit to its seed scenario
without a container restart.

Bind the port to loopback (`-p 127.0.0.1:...`): the control plane is
unauthenticated by design, so a fake reachable from outside is an
outbound-request primitive for anyone who can route to it.

### Docker compose

```yaml
# docker-compose.yml
services:
  vendorfake-square:
    build: https://github.com/konyklabs/vendorfake.git
    environment:
      VENDORFAKE_VENDOR: square
      VENDORFAKE_PROFILE: full
    ports:
      - "127.0.0.1:8080:8080"
    healthcheck:
      test: ["CMD", "python", "-c",
             "import urllib.request as u; exit(0 if u.urlopen('http://127.0.0.1:8080/__unit/info', timeout=2).status == 200 else 1)"]
      interval: 5s
      timeout: 3s
      retries: 5

  app:
    build: .
    environment:
      SQUARE_BASE_URL: http://vendorfake-square:8080
    depends_on:
      vendorfake-square:
        condition: service_healthy
```

One service per vendor your suite needs — `vendorfake-square`,
`vendorfake-clover`, `vendorfake-toast`, `vendorfake-lightspeed` — each with
its own `VENDORFAKE_VENDOR`. The image already carries a `HEALTHCHECK` on
`GET /__unit/info`, so `depends_on: condition: service_healthy` unblocks the
app only once the unit has hydrated its seed and is answering. The `app`
service reaches it over the compose network's internal DNS; publishing the
port is only for a developer who wants `curl localhost:8080` from the host.

[`examples/pytest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/pytest-consumer)
ships a Testcontainers variant, for a test process that would rather own the
container's lifecycle in code.

### CI

Most consumer suites need nothing beyond installing vendorfake and running
pytest: the in-process bindings need no extra service, no port and no
container, and `served()` starts and stops its own child:

```yaml
# .github/workflows/test.yml
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
      - uses: astral-sh/setup-uv@v7
      - run: uv sync --frozen
      - run: uv run pytest
```

When the consumer is not Python, build the image and run it as a service
container:

```yaml
      - uses: actions/checkout@v6
        with:
          repository: konyklabs/vendorfake
          path: vendorfake
      - run: docker build -t vendorfake ./vendorfake
      - run: docker run -d --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
      - run: for i in $(seq 1 30); do curl -sf http://127.0.0.1:8080/__unit/health && break; sleep 1; done
      - name: Test
        env:
          SQUARE_BASE_URL: http://127.0.0.1:8080
        run: <your suite's command>
```

The suite under test needs nothing from vendorfake but a base URL; the
control plane is plain HTTP for the setup and teardown a test needs.

## TLS

`vendorfake serve` speaks plain HTTP, and nothing in vendorfake does TLS. A
service that pins `https://` to the vendor talks to a reverse proxy that
terminates TLS in front of the unit — with Caddy:

```sh
vendorfake serve --vendor square                       # 127.0.0.1:8080, plain HTTP
caddy reverse-proxy --from https://square.local --to 127.0.0.1:8080
```

Caddy mints a locally trusted certificate for `square.local` from its own
local CA. The service under test must trust that CA — `caddy trust` installs
it in the system store, and a containerised consumer needs the root copied
in and its language's trust store pointed at it. Point `square.local` at
loopback in `/etc/hosts` (or the container's `extra_hosts`). Any other
terminating proxy works the same way; the unit behind it is unchanged.

## The pytest plugin

Installing vendorfake registers exactly one `pytest11` entry point,
`vendorfake` (module `vendorfake.pytest`): one marker and three fixtures, no
options, no session hook, no autouse fixture. A suite that uses none of the
fixtures runs identically whether the plugin is loaded or disabled with
`-p no:vendorfake`. The conformance suite's own pytest form is loaded
explicitly instead, with `-p vendorfake.conformance.plugin`; see
[the CLI reference](../reference/cli.md).

### The marker

```python
@pytest.mark.vendorfake(vendor, profile=None, env=None, seed=None, clock_start=None, unmatched=None, capabilities=None)
```

Every argument matches [`unit()`](#in-process-sync)'s own: `vendor` is
required and positional (`"square"`, `"clover"`, `"toast"` or
`"lightspeed"`); the rest are the same keyword arguments with the same
defaults and the same meaning. `clock_start` carries the same precondition
it does on `unit()` — it requires a virtual clock, and setting it against the
default real clock is a loud refusal at fixture setup, not a mode switch:

```python
@pytest.mark.vendorfake("square", clock_start="2026-01-01T00:00:00Z", env={"VENDORFAKE_CLOCK": "virtual"})
def test_a_token_expires_on_schedule(vendorfake_unit): ...
```

### The fixtures

`vendorfake_unit` is function-scoped and yields the
[`StartedUnit`](../concepts/unit.md#driver) the marker describes, built fresh
per test, which is what makes per-test `reset()` unnecessary here. Requesting
it without the marker fails loudly and names the fix rather than skipping:

```python
import pytest


@pytest.mark.vendorfake("square")
def test_an_order_is_created(vendorfake_unit):
    seed = vendorfake_unit.seed
    created = vendorfake_unit.client.post(
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
```

`vendorfake_async_unit` yields the same kind of `StartedUnit`, for a test
that drives it through `async_client` instead of `client`:

```python
import pytest


@pytest.mark.anyio
@pytest.mark.vendorfake("clover")
async def test_items_are_readable(vendorfake_async_unit):
    seed = vendorfake_async_unit.seed
    answered = await vendorfake_async_unit.async_client.get(seed.path("/items"), headers=seed.auth)
    assert answered.status_code == 200
```

Both fixture functions are plain synchronous `def`s yielding an object that
owns an async client, which is what lets them work under `pytest-asyncio`
(strict or auto) and under `anyio`'s plugin without this package depending on
either; the test function itself still needs its runner's own marker.

`vendorfake_webhook_receiver` is function-scoped and yields the same
`WebhookReceiver` object `vendorfake.testing.webhook_receiver()` does — a real
HTTP endpoint on loopback, for the other half of a webhook test:

```python
@pytest.mark.vendorfake("square")
def test_a_webhook_is_delivered_and_verifies(vendorfake_unit, vendorfake_webhook_receiver):
    vendorfake_unit.subscribe(vendorfake_webhook_receiver.url, ["order.created"], signature_key="k")
    ...
    vendorfake_unit.drain()
    (delivery,) = vendorfake_webhook_receiver.received
```

### Narrowing the seed under a type checker

Both fixtures are declared `StartedUnit[Seed]`: the vendor is a runtime
marker argument, so there is nothing for a checker to narrow on and
`vendorfake_unit.seed.merchant_id` is a type error even on a `"clover"`
marker. Three ways out, in order of preference:

- Stay on the structural `Seed`. `seed.credentials` and `seed.token` are the
  cross-vendor views, and a test parametrized over vendors should need
  nothing else.
- Narrow once with an assertion the checker understands and the runtime
  enforces: `assert isinstance(seed, CloverSeed)`.
- Call `unit("clover")` from a fixture of your own instead: the literal
  overload yields `StartedUnit[CloverSeed]` with no assertion at all.

`typing.cast` works too, but it is the one option that can be wrong without
anything failing.

### Choosing between the marker and `unit()`

Nothing about the fixtures is more capable than the function. Reach for the
marker when a test needs exactly one unit and no fixture composition of your
own; reach for `unit()` directly (typically from your own fixture, the way
[`examples/pytest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/pytest-consumer)
does) when a test needs two units, a non-default sink, or a fixture whose
scope you control.

## Choosing

| | Speed | Isolation | Needs a real URL | Language |
|---|---|---|---|---|
| In-process sync | fastest | one test process | no | Python |
| In-process async | fastest | one test process | no | Python |
| Served | fast | one child process/thread | yes | Python |
| Container | slower (image start) | full process/network | yes | any |

Default to in-process (sync unless the code under test is already async);
move to served only when something genuinely needs a socket; move to the
container only when the consumer is not Python.

## One contract, three bindings

What a consumer sees is the same on every binding, with the exceptions below,
each asserted by `tests/parity/`:

| Behaviour | In-process `unit()` | `served()` | CLI / container |
|---|---|---|---|
| Exported `VENDORFAKE_*` variables | honoured; `env=` and arguments beat them | honoured; `env=` and arguments beat them | honoured |
| Unmatched path, on the wire | 404 + `Vendorfake-Near-Miss` | 404 + `Vendorfake-Near-Miss` | 404 + `Vendorfake-Near-Miss` |
| Unmatched path, Python driver | raises `UnmatchedRequest`; `unmatched="vendor-404"` opts out | the same | no driver |
| `timeout` fault, real clock | the client's own `ReadTimeout`, raised without waiting when the delay exceeds it | the client's own `ReadTimeout`, after waiting | the same |
| `timeout` fault, virtual clock | `ReadTimeout` at once when the delay exceeds the client's read timeout, else 504 | 504 at once, `Vendorfake-Delay-Ms` carrying the asked delay: a socket cannot know the client's timeout | the same |
| Seeds, chaos rules, reset, the control plane | identical | identical | identical |
