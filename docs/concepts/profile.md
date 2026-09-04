# Profile

A **profile** decides which capabilities a [unit](unit.md) serves, and
carries the seed document, retry policy, and every other resolved setting a
unit starts with. Every vendor ships the same six names, and — since
konyklabs/roadmap#70 — a name means the same *shape* of thing whichever
vendor answers it, checked by conformance clauses C34/C35 on all three:

See [the generated profile reference](../reference/profiles.md) for the
exact capability set and summary each vendor's copy of these six ships with.

| Profile | What it is |
|---|---|
| `full` | Every capability on. The default. |
| `no-faults` | Fault injection off entirely. For happy-path CI. |
| `no-chaos` | Delivery faults off: a webhook that is sent is sent honestly, once. Request-scope chaos (role `chaos`) stays enabled — the name promises no *delivery* chaos, not none at all. |
| `orders-only` | Orders and payments plus the reference data they point at. No OAuth dance, no webhooks: authenticate with a seeded token. |
| `oauth-only` | Only the OAuth dance, for testing token handling alone. |
| `chaos-demo` | Full surface with a preloaded fault set: rate limits, mid-flow token expiry, duplicate and reordered delivery. |

## Precedence

Four layers, each beating the one before it — ported unchanged from the
reference implementation and pinned by test:

```
built-in defaults  <  caller defaults (a vendor's retry schedule)  <  profile document  <  environment
```

`vendor.retry_defaults` sits **under** the profile document, not over it, so
a profile can override a vendor default and an operator can override both
through `VENDORFAKE_*` variables — see
[the generated environment-variable reference](../reference/env.md). Every
binding resolves them the same way: the exported process environment first,
then the `env=` mapping a test passes, then explicit arguments, each layer
beating the one before. `create_unit()` itself takes `env` as a parameter
defaulting to `{}`; `unit()`, `served()` and the CLI hand it the ambient
`VENDORFAKE_*` variables through one function, `registry.ambient_env()`.

## Choosing one

Pass a name (`--profile chaos-demo`, or `unit("square", profile="chaos-demo")`),
a path to your own JSON document, or — since konyklabs/roadmap#70 — a
**capability request** instead:

```python
with unit("toast", capabilities=["auth"]) as driver:  # -> resolves to "oauth-only"
    ...
with unit("square", capabilities=["oauth", "payments"]) as driver:  # -> "no-faults" (narrowest superset)
    ...
```

`capabilities=` takes either a [role name](capability-and-roles.md) or a
vendor's own capability name, and resolves to the narrowest shipped profile
that is a superset of the request — or `full` plus that exact set, through
the environment layer, when no shipped profile qualifies. Passing both
`profile=` and `capabilities=` is a `ValueError`; so is an empty
`capabilities=[]`, because the empty set is a subset of every profile and
resolving it the way a real request resolves would silently pick the
smallest one. `GET /__unit/info` echoes back both `profile` (what actually
started) and `requested_capabilities` (what was asked for), so a consumer
never has to guess which one a running unit resolved to.

## A custom profile

Any JSON document matching the same schema (`name`, `summary`,
`capabilities`, `seed`, `webhooks`, `chaos`, `clock`, `requests`,
`unmatched`, `errors`) works as a path: `vendorfake serve --vendor square
--profile ./my-profile.json`. Fields you omit fall through to the vendor's
built-in defaults, field by field — not replaced wholesale — which is what
lets a profile that sets only `webhooks.retry.time_scale` still inherit the
vendor's own retry schedule underneath it.
