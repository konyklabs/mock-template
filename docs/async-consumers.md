# Async consumers

For a service that takes an `httpx.AsyncClient` — a FastAPI-style application,
an async worker, anything whose fixtures are `async def`.
[Which binding to use → In-process, async](start/bindings.md#in-process-async)
is the short version; this is the whole surface, plus what a deliberate delay
does on each binding.

Nothing here needs `vendorfake.asgi`. That package is where the web framework
lives, its shape exists to keep the core framework-free, and it is internal:
not a supported surface, and free to change without a major version.

## The three entry points

| | What it gives you | When |
|---|---|---|
| `unit(...)` + `.async_client` | An `httpx.AsyncClient` on the same unit as `.client` | Your test is `async def`, your fixture is not |
| `async_unit(...)` | The same `StartedUnit`, as `async with` | Your fixture is `async def` too |
| `vendorfake_async_unit` | The same `StartedUnit`, as a pytest fixture | You would rather write a marker than a `with` block |

All three yield the same object, `vendorfake.testing.StartedUnit`, so `seed`,
`client`, `async_client`, `unit` and every control-plane helper
(`add_chaos_rule`, `reset`, `reset_chaos`, `drain`, `advance_clock`,
`subscribe`, `deliveries`) are in the same place whichever one you used.

### `async_client` on a synchronous block

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

Built on first access and reused after that, on the same transport instance as
`client` and against the same base URL. Requests through either are the same
call into the unit, so state written on one is visible on the other with no
socket in between — which is what makes a synchronous set-up call followed by
async code under test the ordinary shape rather than a workaround.

### `async_unit()` when your fixture is async

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

`async_unit` takes exactly the arguments `unit` takes — `vendor`, `profile`,
`sink`, `env`, `logger`, `seed` — and delegates to it, so there is one
description of how a unit is built and two ways in. What it adds is the exit:
the async client's `aclose()` is awaited. The synchronous `unit()` cannot await
inside a running event loop, so it leaves the client for the loop; nothing
leaks either way, because this transport owns no socket, no connection pool and
no thread.

### The pytest fixture

Installed with the wheel, through the `pytest11` entry point. No `conftest.py`,
no import:

```python
import pytest


@pytest.mark.anyio
@pytest.mark.vendorfake("square")
async def test_a_marked_test_gets_a_unit(vendorfake_async_unit):
    answered = await vendorfake_async_unit.async_client.get("/v2/locations", headers=vendorfake_async_unit.seed.auth)
    assert answered.status_code == 200
```

The marker takes the same arguments as `unit()`:
`@pytest.mark.vendorfake("square", "oauth-only")`, or
`@pytest.mark.vendorfake("square", profile="oauth-only", env={"VENDORFAKE_CLOCK": "virtual"}, seed=11)`.
A test that asks for the fixture without the marker fails, with a message
saying to add one; it is not skipped, because a skip is a green run in which
the test never happened.

The fixture is function-scoped and that is a contract, not a default. Ids are
deterministic *per unit*, so two tests sharing one would see the second
continue the first's id stream and its store.

It is an ordinary synchronous fixture that yields an object owning an async
client, so it works under `pytest-asyncio` in strict mode, under
`pytest-asyncio` in auto mode, under `anyio`'s plugin, and in a plain
synchronous test that drives the client with `anyio.run` — vendorfake depends
on none of them and does not guess which you have.

## Timeouts, and which binding waits

A `timeout` chaos rule asks for a delay. The unit decides *whether* to delay;
the binding decides *how*, because only the binding knows whose clock is being
spent and what timeout that caller set.

```python
square.add_chaos_rule(
    {
        "id": "slow",
        "scope": "request",
        "fault": "timeout",
        "match": {"route": "GET /v2/locations"},
        "params": {"delay_ms": 5000},
    }
)
```

| Binding | Delay longer than the caller's read timeout | Delay within it |
|---|---|---|
| `async_client` / `client` (in process) | `httpx.ReadTimeout` raised at once, nothing waits | Waited for real, then the 504 |
| `served()`, `serve_in_thread()` (a socket) | Server waits; your client times out on its own | Waited for real, then the 504 |
| File drop | n/a — no caller to time out | The response document appears after the delay |
| `InProcessClient` (the raw seam) | n/a — a function call has no timeout | Not waited; the delay is on `.raw.delay_ms` |

The first row is the point. Rehearsing "my client times out and my retry runs"
used to need a real server; in process the client's read timeout is now
consulted, and a five-second rule proves the retry path in about a
millisecond:

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

`timeout=None` is a setting and not a missing one: a caller who said they would
wait for anything waits, and gets the 504.

On a virtual clock (`VENDORFAKE_CLOCK=virtual`) the delay moves scenario time
instead and the answer comes back immediately — no binding is asked to wait at
all, which is what makes an uncompressed retry schedule drivable.

## What the async client does not change

Everything else is the seam the README already describes. Webhooks still leave
over real HTTP, so a `webhook_receiver()` on loopback sees signed bytes; ids
are still deterministic per unit; `reset()` still drops subscribers a test
registered. `drain()` is synchronous on both entry points, because it waits on
the unit's own delivery machinery rather than on a client.
