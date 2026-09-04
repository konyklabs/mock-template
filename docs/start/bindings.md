# Which binding to use

Four ways to run a unit, from fastest-and-most-isolated to
most-like-production. All four are the same [unit](../concepts/unit.md) —
same state machine, same seed, same faults — differing only in how a client
reaches it.

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
of tests: it is the fastest binding, and by default an unmatched request
raises `vendorfake.testing.UnmatchedRequest` (an `AssertionError`) rather
than answering the vendor's 404, which turns "my client hit the wrong path"
into a loud test failure instead of a quiet one. See
[Recipes → Sync pytest](../pytest-plugin.md).

## In-process, async

```python
from vendorfake.testing import unit

with unit("square") as square:
    response = await square.async_client.get("/v2/locations", headers=square.seed.auth)
```

The same started unit exposes `async_client` — built on first access, over
the same transport — so synchronous set-up (seed reads, chaos rules) and an
async client for the code under test coexist without a second fixture.
`async_unit()` is the async-context-manager form when your own fixtures are
`async def`. Works under `pytest-asyncio` (strict or auto) and under
`anyio`'s plugin, without vendorfake depending on either. See
[Recipes → Async pytest](../async-consumers.md).

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
these when the code under test needs an actual `host:port` it can point an
HTTP client library at — a service configured by URL, a language binding
with no custom-transport seam, or a webhook subscriber that must receive a
real POST over loopback. Served units never raise on an unmatched request;
they answer the vendor's own 404, the same as production would, because a
served unit is standing in for the vendor rather than acting as a test
double.

`served(..., env={...})` is the `VENDORFAKE_*` layer for that one child,
on top of the environment it inherits — `env={"VENDORFAKE_CLOCK":
"virtual"}` with `clock_start=`, or a `VENDORFAKE_VENDOR_*` credential
override for a second, deliberately misconfigured child — so two
differently-seeded children can run in one process with nothing written to
`os.environ`. The parent-resolved `.seed` reads the same
`VENDORFAKE_VENDOR_*` layer, so its credentials agree with the child's.
Entries for what `served()` passes as a flag — `VENDORFAKE_PROFILE`,
`VENDORFAKE_HOST`, `VENDORFAKE_PORT`, `VENDORFAKE_LOG_LEVEL` — are refused
with a `ValueError` naming the parameter to use, rather than silently
beaten by the flag. `VENDORFAKE_SEED` is refused because `.seed` is
derived from the vendor's constants and could not describe a child hydrated
from another document, and `VENDORFAKE_SEED_OVERLAY` because
[`seed_overlay=`](../concepts/seed.md#seed-overlays) is the parameter for it —
the parameter takes the document as a mapping, encodes it for the child, and
refuses an unknown collection here, where the caller can see it, rather than
as a child that exited before announcing a port. It refuses one more thing
here: an overlay naming `tokens` or the vendor's identity collection, which
`.seed` is built from and cannot follow — see
[the credentials and the identity](../concepts/seed.md#the-credentials-and-the-identity-cannot-be-overlaid).

A served child shared across tests needs `reset()` between them when the
vendor keeps single-use state — see
[Chaos rules and faults → Sharing one unit across tests](../concepts/chaos-rules-and-faults.md#sharing-one-unit-across-tests).

## Container

```sh
docker build -t vendorfake .
docker run --rm -p 127.0.0.1:8080:8080 -e VENDORFAKE_VENDOR=square vendorfake
```

One image, every vendor; which one it serves is chosen at run time, never
baked into the image. Reach for this when the consumer under test is not a
Python process at all — a service in another language, a browser
end-to-end suite, or anything driven through
[docker compose](../recipes/docker-compose.md) or
[Testcontainers](https://testcontainers.com/). It is also the binding
[CI](../recipes/ci.md) reaches for when the suite under test is not Python.

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
