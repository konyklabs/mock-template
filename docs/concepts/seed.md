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

Five fields on `vendorfake.testing.Seed`, useful for a test parametrized
across vendors:

- `token` — the seeded credential a consumer *stores* per tenant, under
  names that do not change per vendor: `access_token`, `refresh_token`
  (`None` exactly when `credentials.grant` is `client_credentials`) and
  `tenant_id` (Clover's `merchant_id`, Square's `merchant_id` — the seller
  the token belongs to, not a location — and Toast's `restaurant_guid`).
- `credentials` — the app credential from the seeded scenario, under names
  that do not change per vendor (`app_id`, `app_secret`, `grant`) even
  though the vendor's own field names do (Square: `application_id`; Clover
  and Toast: `client_id`). `grant` names the token lifecycle worth
  branching on: Square and Clover issue a refresh token and rotate it,
  Toast issues a bearer with no refresh and expects a fresh login — a bare
  `refresh_token` field is deliberately *not* on the shared type, because
  Toast has none and faking one would be a lie the type system helped tell;
  `token.refresh_token` is where it lives, honestly typed `str | None`.
- `auth` — headers for the full-scope seeded bearer, ready to pass to a
  client. For Toast this carries the bearer **and** the
  `Toast-Restaurant-External-ID` header together, because a
  restaurant-scoped call needs both.
- `read_only_auth` — the same, for the read-only seeded bearer.
- `event_types` — the webhook event vocabulary this vendor actually sends;
  `subscribe()` on a [driver](driver.md) checks a request against it and
  refuses a name that will never fire, rather than registering it silently.

## Seed overlays

A scenario that is *almost* right is the common case: the shipped merchant and
catalog, but one extra order, or a loyalty program on different terms, or no
orders at all. `seed_overlay=` takes a **partial** seed document and merges it
over the profile's before the store is hydrated, so the unit answers from the
merged scenario on its very first request:

```python
with unit("square", seed_overlay={"loyalty_program": {"terminology_one": "Star"}}) as square:
    ...  # every other collection is exactly what the profile ships
```

It is accepted by `unit()`, `async_unit()` and `served()`, as an inline
mapping or as a `str`/`os.PathLike` naming a JSON file, and by
`VENDORFAKE_SEED_OVERLAY` — a path, or the JSON itself inline when the value
starts with `{`. The parameter *is* that variable's layer, so an explicit
`VENDORFAKE_SEED_OVERLAY` entry in a shared `env=` mapping wins, exactly as
`seed=` and `clock_start=` behave. `served(env=)` refuses the variable and
names the parameter: only the parameter's path checks the overlay in the
calling process, where the refusal is visible.

### The merge rule

Stated here and nowhere else; implemented once, in
`vendorfake.core.config.overlay.merge_seed`.

| In the overlay | Result |
| --- | --- |
| An object, where the base has an object | Merged key by key, recursively; the overlay's keys win |
| `null` | The key is **removed** from the result, not set to null |
| An array | Replaces the base array whole — never concatenated, never merged by index or id |
| Anything else | Replaces |

`null` deletes because a seed carrying `"orders": null` and a seed carrying no
`orders` are different documents, and "remove it" has to produce the second —
the same *absent means absent* rule the state store follows. An array replaces
because a seed's `orders` is a list a reader can see: an overlay that lists two
orders means two.

**The merged document is still a whole seed document, and hydration still
validates it.** The merge rule above says what comes out; whether that
document *loads* is the vendor's own question, asked exactly as it is for a
seed with no overlay. So a deletion that leaves a dangling reference is
refused with hydration's message, not the overlay's:

```python
# Starts: nothing in the seed points at an order.
unit("square", seed_overlay={"orders": None})

# UnitError: Seed loyalty_accounts need a loyalty_program to belong to.
unit("square", seed_overlay={"loyalty_program": None})

# Starts: the accounts that pointed at the program are gone in the same overlay.
unit("square", seed_overlay={"loyalty_program": None, "loyalty_accounts": None})
```

Removing a collection means removing what references it, in the same overlay.

### A collection you invent is refused when the unit starts

A top-level key that is not one of the seed document's collections fails the
unit at construction, before any request, naming the offending key and the
collections that exist:

```text
seed overlay names 'merchants', which the seed document for profile 'full'
does not have. ... Valid collections: catalog, inventory_counts, locations,
loyalty_accounts, loyalty_program, merchant, orders, tokens.
```

This is the one mistake an overlay has no other symptom for. A partial
document has nothing to be wrong *against*: `{"order": [...]}` for a vendor
whose collection is `orders` merges cleanly, hydrates nothing, and reads an
hour later as "the fake ignored my scenario". Conformance clause **C36**
asserts the refusal on every vendor.

On a vendor named as a literal a type checker says so first, from the
per-vendor `TypedDict`s `SquareSeedOverlay`, `CloverSeedOverlay` and
`ToastSeedOverlay` — whose keys are that vendor's collections, all optional,
with untyped values. A vendor passed as a plain `str` gets
`vendorfake.testing.SeedOverlay` (`Mapping[str, Any]`), the honest answer when
the call site does not know which vendor's collections apply; the unit still
refuses an unknown collection at start.

### The credentials and the identity cannot be overlaid

Two collections are refused when the unit starts, on every binding: `tokens`,
and the vendor's identity collection — `merchant` on Square and Clover,
`restaurant` on Toast.

```text
seed overlay names 'tokens', which is what square's .seed is built from. The seed
handed back by unit(), async_unit() and served() describes the SHIPPED credentials
and identity … so it cannot follow an overlay.
```

`.seed` is built from this distribution's own constants and the profile's
`vendor` block, not from the seed document that was loaded. An overlay of
those two would change what the unit authenticates against and which tenant it
answers for while `.seed.access_token` and `.seed.merchant_id` still reported
the shipped values — so `.seed.auth`, the documented way to call the unit,
would answer 401 against a unit that started perfectly, with nothing anywhere
naming the overlay. `served()` refuses it in the calling process, before the
child is spawned.

Every other collection may be overlaid, `.seed`'s catalog, order and location
ids included: those diverge visibly, as a 404 on the entity you replaced,
rather than as an authentication failure on everything.

To run a unit on different credentials or a different tenant, point the
profile at a whole seed document of your own — its `seed` key, or
`VENDORFAKE_SEED` — and read the values from that document rather than from
`.seed`.

### What is published, and what is not

`GET /__unit/info` and `vendorfake info` carry a `seed_overlay` block:

```json
{"seed_overlay": {"active": true, "digest": "sha256:3233fbc3…"}}
```

The **contents are never published** — an inline overlay may carry a
consumer's own credentials. The digest is over the overlay's canonical JSON
(keys sorted at every depth, no whitespace), so two callers who wrote the same
overlay with their keys in a different order pin the same value, and a report
can say which scenario a run was on. A unit built with no overlay reports
`{"active": false, "digest": null}`.

Ids stay deterministic under an overlay: two units on the same profile and the
same overlay hold the same entities, exactly as two units on the shipped
scenario do.

## Ids are deterministic, not unique

Two `unit("square")` blocks mint the same order ids, tokens and codes in
the same order, from separate stores — what makes an id assertion stable
run to run. It also means ids are **not** unique *across* units in the same
process; pass `unit("square", seed=2)` when a test genuinely needs two
units to diverge.

## A third-party vendor's own seed

Landing with this batch's API contract (stream F3, konyklabs/roadmap#74): a
vendor from the `vendorfake.vendors` entry-point group can publish its own
seed by implementing `SeedingVendor`, a separate `runtime_checkable`
protocol — not a required member of `VendorDefinition`, because "this vendor
has no seed" is a legitimate, permanent answer, unlike the required members a
vendor writer must always supply something for. Its shape:

```python
class SeedingVendor(Protocol):
    def seed(self, vendor_config: Mapping[str, object]) -> object: ...
```

`seed_for` discovers it structurally — `isinstance(definition, SeedingVendor)`
— and asks it before falling back to the three vendors shipped here, so a
vendor implementing it gets a real, typed seed from the `str` overload of
`unit()` too. The return type is `object` because the core layer that
declares the protocol may not import `vendorfake.testing`; what comes back is
checked structurally against `vendorfake.testing.Seed` (`credentials`, `token`,
`auth`, `read_only_auth`, `event_types`) at the point the unit is built, and a
hook that returns the wrong shape is named as a hook defect there rather than
surfacing later as an `AttributeError` on `started.seed.credentials`. A
vendor that implements nothing is refused exactly as it was before this hook
existed.
