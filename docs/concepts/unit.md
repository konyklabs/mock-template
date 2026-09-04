# Unit

A **unit** is one running instance of a vendor fake: one vendor's route
table, one profile's worth of capabilities, one seeded state store, one
chaos engine, one webhook dispatcher, one clock. Everything else in this
documentation — [profiles](profile.md), [capabilities](capability-and-roles.md),
the [seed](seed.md), [chaos rules](chaos-rules-and-faults.md) — is a
property of a unit, not a separate thing you wire up yourself.

## Building one

`vendorfake.registry.create_unit` is the single constructor everything else
calls — the CLI's `serve`/`info`/`openapi` subcommands, and
`vendorfake.testing.unit()`/`async_unit()`/`served()` underneath their
context-manager sugar:

```python
from vendorfake.registry import create_unit

unit = create_unit(vendor="square", profile="full")
unit.start()  # hydrates the store from the seed document
...
unit.stop()
```

In order: resolve the vendor (its profile directory and retry defaults are
properties of it), load the profile (vendor defaults, under the profile
document, under the environment — see [Profile](profile.md)), construct the
unit with its control plane, then start it. `vendorfake.testing.unit()`
wraps exactly this in a context manager and narrows the return type by
vendor name — reach for it in a test rather than calling `create_unit`
directly; see [Driver](driver.md).

## What it exposes

- `unit.routes` — every registered `Route`, vendor surface and
  `/__unit/*` control plane together, as the router built them from.
- `unit.control` — the `ControlBinding` the `/__unit/*` handlers close
  over: `list_routes()`, and the callbacks that let the control plane reach
  into the store, the chaos engine and the clock without a route handler
  ever needing that access.
- `unit.requests` — the bounded [request log](journal-and-request-log.md).
- `unit.webhooks` — the delivery dispatcher.
- `unit.context` — vendor, resolved config, clock, logger: everything a
  route handler or the control plane needs to answer one request.
- `unit.handle(req)` — the one entry point every binding calls: an
  in-process client, the ASGI adapter, and the file-drop transport all end
  up here. A route this unit does not serve, an unknown capability, and a
  disabled one are indistinguishable from `handle`'s point of view — the
  difference is in *what* gets raised, not in a second code path.

## Bindings are one property, not four units

[Which binding to use](../start/bindings.md) — in-process sync, in-process
async, served, or the container — changes how a client reaches a unit's
`handle`, never what the unit itself does. The same seed produces the same
ids, the same chaos rule fires the same way, and `GET /__unit/info` reports
the same facts, whichever binding is in front of it.

## Unmatched requests

A path this unit does not serve is, in-process, an assertion failure by
default (`vendorfake.testing.UnmatchedRequest`) rather than the vendor's own
404 — because in that binding the unit is standing in for a *test double*,
and a request nobody wrote a handler for is almost always a typo, not
something worth a passing 404. A served unit never raises; it answers as
the vendor would, because there the unit stands in for the *vendor itself*.
Pass `unmatched="vendor-404"` to `unit()`, or set `VENDORFAKE_UNMATCHED`, to
get the vendor's answer in-process too. Every unmatched response — either
binding — carries a `Vendorfake-Near-Miss` header naming the closest routes,
scored deterministically, so the same mistake prints the same diagnosis
every run.
