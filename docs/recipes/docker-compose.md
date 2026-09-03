# Docker compose

Build the image once ([Install → As a container](../start/install.md#as-a-container)),
then run it alongside the service under test:

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
`vendorfake-clover`, `vendorfake-toast` — each with its own
`VENDORFAKE_VENDOR`. The image already carries a `HEALTHCHECK` on
`GET /__unit/info`; reusing it in compose (rather than re-checking
liveness some other way) means `depends_on: condition: service_healthy`
only unblocks the app once the unit has actually hydrated its seed and is
answering, not merely once the process started.

Bind vendorfake's port to loopback on the host (`127.0.0.1:8080:8080`) as
shown — the control plane is unauthenticated by design, so a fake reachable
from outside the compose network is an outbound-request primitive for
anyone who can route to it. The `app` service reaches it over the compose
network's internal DNS (`http://vendorfake-square:8080`) without that port
needing to be published at all; publishing it here is only for a developer
who wants `curl localhost:8080` from the host.

## Resetting between test runs

`POST /__unit/state/reset` returns a unit to its seed scenario without a
container restart — cheaper than tearing the stack down between suites:

```sh
curl -s -X POST http://localhost:8080/__unit/state/reset -H 'Content-Type: application/json' -d '{}'
# -> {"entities": {..., "orders": 2, "tokens": 2}, "journal_seq": 17, "digest": "3fafd03a5ffa1120..."}
```

See [Concepts → Journal and request log](../concepts/journal-and-request-log.md)
for what `reset()` clears and what it does not (subscribers you registered
during the run are dropped too — re-subscribe after a reset, not before it),
and [Sharing one unit across tests](../concepts/chaos-rules-and-faults.md#sharing-one-unit-across-tests)
for why one container shared across a suite needs it *between tests*, not
only between runs.

## Testcontainers, as an alternative

[`examples/pytest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/pytest-consumer)
and
[`examples/vitest-consumer`](https://github.com/konyklabs/vendorfake/tree/main/examples/vitest-consumer)
both ship a Testcontainers variant of the same idea — starting the image
from inside the test process rather than from a compose file — for a suite
that would rather manage the container's lifecycle in code. Compose is the
better fit for a stack with several services wired together ahead of time;
Testcontainers is the better fit for a single test process that wants to own
the container itself.
