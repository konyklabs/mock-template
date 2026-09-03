# Clock

Every [unit](unit.md) runs on a `Clock`, in one of two modes, chosen at
start-up and fixed for the unit's lifetime.

## Real (the default)

Time moves the way it always does. A `timeout` fault that stalls 250ms
stalls 250ms; a token that expires in an hour expires an hour from when the
unit started. This is the right mode for most tests and the only mode a
[served](../start/bindings.md#served) unit's timing behaves intuitively
under.

## Virtual

```sh
VENDORFAKE_CLOCK=virtual vendorfake serve --vendor square
```

Time only moves when told to, through `POST /__unit/clock/advance` (or
`driver.advance_clock(ms)`):

```sh
curl -s -X POST http://localhost:8080/__unit/clock/advance \
  -H 'Content-Type: application/json' -d '{"ms": 2592000000}'    # 30 days
# -> {"now": "2026-09-27T22:20:54.388Z", ...}

curl -s http://localhost:8080/v2/locations -H "Authorization: Bearer $SEED"
# -> {"errors": [{"category": "AUTHENTICATION_ERROR", "code": "ACCESS_TOKEN_EXPIRED", ...
```

Use this to drive an uncompressed retry schedule to completion, or to jump
straight past a token's `expires_at`, without a test that actually waits.

`VENDORFAKE_CLOCK` alone leaves the *start instant* to wall-clock luck, so
an `expires_at` assertion is deterministic within one run and different the
next. `VENDORFAKE_CLOCK_START` (an RFC 3339 instant; requires
`VENDORFAKE_CLOCK=virtual`, refused rather than silently switching modes
otherwise) pins it, so two units started from the same value agree on
every expiry to the second:

```python
with unit("square", env={"VENDORFAKE_CLOCK": "virtual"}, clock_start="2026-01-01T00:00:00Z") as square:
    ...
```

A timezone-aware `datetime` works too. `driver.clock()` reads the mode and
the current instant back.

## The clock and the `timeout` fault

The `timeout` fault (see [Chaos rules and faults](chaos-rules-and-faults.md))
is the one place clock mode changes what a *client* observes, not just what
the unit's own state does:

- **Real clock, in-process**: if `delay_ms` exceeds the calling client's
  read timeout, the client raises `httpx.ReadTimeout` **without actually
  waiting** — a millisecond test proves a five-second retry path. A delay
  shorter than the read timeout is waited out for real. Served mode always
  waits for real, because over a socket the timeout is the client's own to
  enforce.
- **Virtual clock**: the delay advances scenario time on the calling
  thread and the response comes back immediately — an elapsed-wall-time
  assertion is meaningless here and none is made.

An earlier design routed every `timeout` fault through the clock
unconditionally, on the reasoning that a fake should never really sleep.
That deadlocks a virtual-clock unit: the pipeline holds one lock for the
duration of a request, and the only thing that can fire a virtual timer is
itself a request (`POST /__unit/clock/advance`) — a request parked on a
virtual timer would hold the unit while the one call that could release it
waited for the same lock. Splitting the fault by clock mode is what keeps
neither half ever waiting on another request.
