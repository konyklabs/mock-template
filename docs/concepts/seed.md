# Seed

Every profile ships the same scenario, so a fresh [unit](unit.md) needs no
setup: a merchant or restaurant, locations, a small catalog, seeded orders,
tenders, application credentials, and bearer tokens — obviously fake, and
readable on sight. The concrete values for all three vendors are in
[Seeded scenario](../seeded-scenario.md); this page is the vocabulary
around them.

## `.seed` on a started unit

In Python the seed is a typed attribute on whatever
[`unit()`/`served()`](driver.md) handed back —
`vendorfake.testing.SquareSeed`, `CloverSeed`, `ToastSeed` — not a JSON blob
to parse:

```python
with unit("square") as square:
    seed = square.seed  # SquareSeed
    seed.location_id  # "18YC4JDH91E1H"
    seed.tea_mug_variation_id  # a seeded catalog object id
```

**The vendor name narrows the type.** `unit("clover").seed` is a
`CloverSeed` to a type checker, not a union — `seed.merchant_id`
type-checks and a field belonging to another vendor does not, with no
`isinstance`, no cast, no per-vendor helper. `served()` narrows the same
way. A vendor passed as a plain `str` (a parametrized test, or an
entry-point vendor this distribution never heard of) gets
`vendorfake.testing.Seed`, the structural type every seed satisfies. The
seed is never `None` — a vendor that publishes none is refused where the
unit is built, not discovered later as a missing attribute.

## What every seed has in common

Four fields on `vendorfake.testing.Seed`, useful for a test parametrized
across vendors:

- `credentials` — the app credential from the seeded scenario, under names
  that do not change per vendor (`app_id`, `app_secret`, `grant`) even
  though the vendor's own field names do (Square: `application_id`; Clover
  and Toast: `client_id`). `grant` names the token lifecycle worth
  branching on: Square and Clover issue a refresh token and rotate it,
  Toast issues a bearer with no refresh and expects a fresh login —
  `refresh_token` is deliberately *not* on the shared type, because Toast
  has none and faking one would be a lie the type system helped tell.
- `auth` — headers for the full-scope seeded bearer, ready to pass to a
  client. For Toast this carries the bearer **and** the
  `Toast-Restaurant-External-ID` header together, because a
  restaurant-scoped call needs both.
- `read_only_auth` — the same, for the read-only seeded bearer.
- `event_types` — the webhook event vocabulary this vendor actually sends;
  `subscribe()` on a [driver](driver.md) checks a request against it and
  refuses a name that will never fire, rather than registering it silently.

## Ids are deterministic, not unique

Two `unit("square")` blocks mint the same order ids, tokens and codes in
the same order, from separate stores — what makes an id assertion stable
run to run. It also means ids are **not** unique *across* units in the same
process; pass `unit("square", seed=2)` when a test genuinely needs two
units to diverge.

## A third-party vendor's own seed

`VendorDefinition` accepts an optional `seed` hook — a callable (or
attribute) returning a `Seed`-protocol object for a built unit — so an
entry-point vendor outside this distribution gets a real, typed seed from
the `str` overload of `unit()` too, not just the three shipped here.
