# Capability and roles

A **capability** is a named slice of a vendor's surface — `oauth`,
`order-lifecycle`, `payments`, `webhooks`, and so on; see
[the generated route reference](../reference/routes-square.md) for which
routes each vendor's capabilities cover. Every route declares exactly one.
A **role** is the small, vendor-neutral vocabulary that sits above
capability names: `auth`, `orders`, `webhooks`, `chaos` — the four things
every shipped vendor maps to one of its own capabilities, published at
`GET /__unit/info` under `vendor.roles`. Roles exist so a test written once
(`capabilities=["auth"]`) means the same request whichever vendor it runs
against, without the test needing to know that Square calls it `oauth` and
Toast calls it `auth`.

## Toggling

A capability that is off is **not hidden**. A route whose capability is
disabled answers an explicit `capability_disabled` error — naming the
capability, what is blocking it, and the profile — rather than a 404 that
is indistinguishable from "this vendor has no such endpoint at all". That
distinction is the entire point: hiding the route would turn "you switched
this off" into a debugging session.

```sh
curl -s http://localhost:8080/__unit/capabilities
# -> {"enabled": ["oauth", "order-lifecycle", ...], "declared": [...]}

curl -s -X POST http://localhost:8080/__unit/capabilities \
  -H 'Content-Type: application/json' -d '{"enable": ["loyalty"], "disable": ["inventory"]}'
```

`VENDORFAKE_CAPABILITIES` sets this at start-up instead — an absolute list,
or a `+add,-remove` delta against the profile's own list; see
[the environment-variable reference](../reference/env.md).

## Dotted names

A capability name may be dotted (`webhooks.chaos` is the delivery-scope
chaos sub-capability of `webhooks`). Whether a dotted capability is usable
consults its **immediate parent only**, one level — not every ancestor: if
`a.b` is enabled while `a` is off, `a.b` still reads as usable. Disabling a
parent, on the other hand, removes every dotted descendant with it, so "a
grandparent is off and a child is on" is a state the registry's own write
path never produces — it can only be observed by hand-editing state
underneath it, which nothing in this project does.

## An unknown name is refused, not ignored

A typo in a profile's `capabilities` list, or in `VENDORFAKE_CAPABILITIES`,
is a startup failure naming the declared set — never a silent no-op that
leaves a consumer wondering why nothing changed.

## The control plane's own capability

`/__unit/*` carries its own capability, `__control` — auto-declared, always
enabled, and filtered out of every listing a unit publishes. It cannot be
switched off, because doing so would remove the one channel that could turn
it back on.
