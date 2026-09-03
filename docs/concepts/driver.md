# Driver

`vendorfake.testing.Driver` is the base class every handle a test holds is
built on: `StartedUnit` (in-process — adds `.unit`, `.client`,
`.async_client`) and `ServedUnit` (a real process — adds `.pid`, `.logs`,
`.base_url`) both extend it, so the control-plane methods below work
identically whichever [binding](../start/bindings.md) produced the handle.
`unit()`, `async_unit()` and `served()` are what hand one back — see
[Recipes → Sync pytest](../pytest-plugin.md) and
[Recipes → Async pytest](../async-consumers.md) for the fixtures built on
top.

## Discovery

- `driver.health()`, `driver.info()` — the same documents
  `GET /__unit/health` / `GET /__unit/info` serve.
- `driver.route_for(operation_id)` / `driver.path_for(operation_id)` — look
  up a route by its `operation_id` rather than a hand-typed path string.
  Every vendor also ships an `<vendor>.paths` module (`vendorfake.square.paths`,
  `vendorfake.clover.paths`, `vendorfake.toast.paths`) of `UPPER_SNAKE`
  constants for the same purpose at import time, kept honest against the
  router by a drift test in this repository.
- `driver.clock()` — the clock's mode and current instant; see
  [Clock](clock.md).

## Requests and assertions

- `driver.requests(operation_id=, route=, unmatched=, limit=)` — the
  [request log](journal-and-request-log.md), filtered.
- `driver.assert_called(operation_id, times=)` — raises with every
  operation the unit actually saw, and its count, printed in the failure
  message rather than left for a second query.
- `driver.clear_requests()` — draws a line under setup, before the
  assertions that matter.

## Webhooks

- `driver.subscribe(url, event_types, signature_key=)` — checks the event
  types against the vendor's own vocabulary and refuses one it will never
  send.
- `driver.drain(timeout_s=)` — sleeps the retry timers for real, scaled by
  the profile's `time_scale`, until every delivery has settled or the
  bound is hit; raises rather than let the next assertion run against
  deliveries that never happened. For an uncompressed schedule, drive a
  [virtual clock](clock.md) with `advance_clock` instead of draining.
- `driver.deliveries()` — every delivery attempt the dispatcher recorded.
- `driver.pending_webhook_timers()` — retries still scheduled.

## Chaos and reset

- `driver.add_chaos_rule(rule)` / `driver.reset_chaos()` — arm and disarm
  [chaos rules](chaos-rules-and-faults.md) without hand-rolling the
  `POST /__unit/chaos/rules` body.
- `driver.advance_clock(ms)` — move a [virtual clock](clock.md) forward.
- `driver.reset()` — return to the seed scenario. This also drops every
  subscriber a test registered during the run — `subscribe()` again after a
  reset, not before it. A unit shared across tests needs this per test
  when the vendor keeps single-use state (Clover's refresh rotation) —
  the fixture is in
  [Chaos rules and faults → Sharing one unit across tests](chaos-rules-and-faults.md#sharing-one-unit-across-tests).

## Why a base class, not one class per binding

Every method above is expressed against the control plane, which every
binding serves identically. Writing them once on `Driver` is what makes
"works the same in-process and served" a fact about the code rather than a
convention two implementations have to be kept in step with by hand.
