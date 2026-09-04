# Seed

Every profile ships the same scenario, so a fresh [unit](unit.md) needs no
setup: a merchant or restaurant, locations, a small catalog, seeded orders,
tenders, application credentials, and bearer tokens — obviously fake, and
readable on sight. [The tables below](#the-shipped-scenario) hold the concrete
values for all four vendors.

## `.seed` on a started unit

In Python the seed is a typed attribute on whatever
[`unit()`/`served()`](unit.md#driver) handed back —
`vendorfake.testing.SquareSeed`, `CloverSeed`, `ToastSeed`,
`LightspeedSeed` — not a JSON blob to parse:

```python
with unit("square") as square:
    seed = square.seed  # SquareSeed
    seed.location_id  # "18YC4JDH91E1H"
    seed.tea_mug_variation_id  # a seeded catalog object id
```

**The vendor name narrows the type.** `unit("clover").seed` is a `CloverSeed`
to a type checker, not a union: `seed.merchant_id` type-checks and a field
belonging to another vendor does not, with no `isinstance` and no cast.
`served()` narrows the same way. A vendor passed as a plain `str` gets
`vendorfake.testing.Seed`, the structural type every seed satisfies. The seed
is never `None` — a vendor that publishes none is refused where the unit is
built.

## What every seed has in common

Five fields on `vendorfake.testing.Seed`, for a test parametrized across
vendors:

- `token` — the credential a consumer *stores* per tenant: `access_token`,
  `refresh_token` (`None` exactly when `credentials.grant` is
  `client_credentials`) and `tenant_id` (Square's and Clover's
  `merchant_id` — the seller, not a location — Toast's `restaurant_guid`,
  Lightspeed's `retailer_id`).
- `credentials` — `app_id`, `app_secret` and `grant`, whatever the vendor's
  own field names are. `grant` names the token lifecycle worth branching on:
  Square, Clover and Lightspeed issue a refresh token and rotate it, Toast
  issues a bearer with no refresh and expects a fresh login. A bare
  `refresh_token` is deliberately *not* on the shared type, because Toast has
  none; it lives on `token.refresh_token`, typed `str | None`.
- `auth` — headers for the full-scope seeded bearer. For Toast this carries
  the bearer **and** the `Toast-Restaurant-External-ID` header, because a
  restaurant-scoped call needs both.
- `read_only_auth` — the same, for the read-only seeded bearer.
- `event_types` — the webhook event vocabulary this vendor actually sends;
  `subscribe()` on a [driver](unit.md#driver) refuses a name that will never
  fire rather than registering it silently.

## Ids are deterministic, not unique

Two `unit("square")` blocks mint the same order ids, tokens and codes in
the same order, from separate stores — what makes an id assertion stable
run to run. It also means ids are **not** unique *across* units in the same
process; pass `unit("square", seed=2)` when a test genuinely needs two
units to diverge.

## Seed overlays

`seed_overlay=` takes a **partial** seed document and merges it over the
profile's before the store is hydrated, so the unit answers from the merged
scenario on its very first request — the shipped merchant and catalog, but one
extra order, or no orders at all:

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
names the parameter, because only the parameter's path checks the overlay in
the calling process where the refusal is visible.

### The merge rule

Stated here and nowhere else; implemented once, in
`vendorfake.core.config.overlay.merge_seed`.

| In the overlay | Result |
| --- | --- |
| An object, where the base has an object | Merged key by key, recursively; the overlay's keys win |
| `null` | The key is **removed** from the result, not set to null |
| An array | Replaces the base array whole — never concatenated, never merged by index or id |
| Anything else | Replaces |

`null` deletes because a seed carrying `"orders": null` and one carrying no
`orders` are different documents, and "remove it" has to produce the second.
An array replaces because a seed's `orders` is a list a reader can see: an
overlay that lists two orders means two.

**The merged document is still a whole seed document, and hydration still
validates it.** A deletion that leaves a dangling reference is refused with
hydration's message, not the overlay's:

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

This is the one mistake an overlay has no other symptom for: `{"order":
[...]}` for a vendor whose collection is `orders` merges cleanly, hydrates
nothing, and reads an hour later as "the fake ignored my scenario".
Conformance clause **C36** asserts the refusal on every vendor.

On a vendor named as a literal a type checker says so first, from the
per-vendor `TypedDict`s `SquareSeedOverlay`, `CloverSeedOverlay`,
`ToastSeedOverlay` and `LightspeedSeedOverlay`, whose keys are that vendor's
collections, all optional, with untyped values. A vendor passed as a plain
`str` gets `vendorfake.testing.SeedOverlay` (`Mapping[str, Any]`); the unit
still refuses an unknown collection at start.

### The credentials and the identity cannot be overlaid

The collections a vendor's `.seed` is built from are refused when the unit
starts, on every binding: its credentials, and its identity collection. On
Square and Clover that is `tokens` and `merchant`; on Toast, `tokens` and
`restaurant`; on Lightspeed the three collections its credentials live in
(`tokens`, `personal_tokens`, `refresh_tokens`) and `retailer`.

```text
seed overlay names 'tokens', which is what square's .seed is built from. The seed
handed back by unit(), async_unit() and served() describes the SHIPPED credentials
and identity … so it cannot follow an overlay.
```

`.seed` is built from this distribution's own constants and the profile's
`vendor` block, not from the seed document that was loaded. Overlaying those
two would change what the unit authenticates against while `.seed` still
reported the shipped values, so `.seed.auth` would answer 401 against a unit
that started perfectly. `served()` refuses it before the child is spawned.

Every other collection may be overlaid, `.seed`'s catalog, order and location
ids included: those diverge visibly, as a 404 on the entity you replaced.
To run a unit on different credentials or a different tenant, point the
profile at a whole seed document of your own — its `seed` key, or
`VENDORFAKE_SEED` — and read the values from that document.

### What is published, and what is not

`GET /__unit/info` and `vendorfake info` carry a `seed_overlay` block:

```json
{"seed_overlay": {"active": true, "digest": "sha256:3233fbc3…"}}
```

The **contents are never published** — an inline overlay may carry a
consumer's own credentials. The digest is over the overlay's canonical JSON
(keys sorted at every depth, no whitespace), so two callers who wrote the same
overlay with their keys in a different order pin the same value. A unit built
with no overlay reports `{"active": false, "digest": null}`. Ids stay
deterministic under an overlay.

## A third-party vendor's own seed

A vendor from the `vendorfake.vendors` entry-point group can publish its own
seed by implementing `SeedingVendor`, a separate `runtime_checkable` protocol
rather than a required member of `VendorDefinition`, because "this vendor has
no seed" is a legitimate permanent answer:

```python
class SeedingVendor(Protocol):
    def seed(self, vendor_config: Mapping[str, object]) -> object: ...
```

`seed_for` discovers it structurally and asks it before falling back to the
vendors shipped here, so a vendor implementing it gets a real, typed seed from
the `str` overload of `unit()` too. The return type is `object` because the
core layer that declares the protocol may not import `vendorfake.testing`;
what comes back is checked structurally against `vendorfake.testing.Seed` at
the point the unit is built, so a hook returning the wrong shape is named as a
hook defect there rather than as a later `AttributeError`.

## The shipped scenario

The values are readable and obviously fake by design, and every one is an
attribute on the seed object, which is the form to use in a test. They come
from the profile's `vendor` block, so a profile that overrides the app
credentials is reported as it actually ran rather than as the default below.
`credentials` is the app credential from the first row of each table, spelled
`application_id`/`application_secret` on Square and `client_id`/`client_secret`
on the other three.

### Square

| | |
|---|---|
| App credentials | `sandbox-sq0idb-unit-square-application` / `sandbox-sq0csb-unit-square-secret` |
| OAuth shape | authorize redirect (`https://example.test/oauth/callback`) + code exchange |
| Full-access bearer | `EAAAl-unit-seeded-access-token-full-scopes` |
| Read-only bearer | `EAAAl-unit-seeded-access-token-read-only` |
| Tenant, and how requests name it | merchant `MLQW2MYBY81PZ` (implicit in the token) |
| Location / order type | location `18YC4JDH91E1H` (Grant Park), kiosk `057P5VYJ4A5X1` |
| Catalog | Tea `W62UWFY35CWMYGVWK6TWJDNI` with variations Mug `2TZFAOHWGG7PAK2QEXWYPZSP` (150) and Pot; Cold Brew `BJNQCF2FJ6S6UIDT65ABHLRX` |
| Orders | open `CAISENgvlJ6jLWAzERDzjyHVybY`, completed `CAISEM82RcpmcFBM0TfOyiHV3es` |
| Payment plumbing | `POST /v2/payments` with `source_id: "EXTERNAL"` |
| Webhooks | register with the full-access bearer; the `signature_key` comes back |
| Event types | `order.created`, `order.updated`, `payment.created`, `payment.updated`, `catalog.version.updated`, `inventory.count.updated` (`GET /v2/webhooks/event-types`) |

### Clover

| | |
|---|---|
| App credentials | `UNITCLOVERAPP` / `unit-clover-app-secret` |
| OAuth shape | authorize redirect (same URI) + code exchange, single-use refresh |
| Full-access bearer | `unit-seeded-clover-access-token-full-permissions` |
| Read-only bearer | `unit-seeded-clover-access-token-read-only` |
| Tenant, and how requests name it | merchant `HRVSTRYE12345` ("Harvest & Rye") in every `/v3` **path** |
| Location / order type | order types `KFRPRVCZ73JHM` (dine-in), `ORDTYPETAKE01` |
| Catalog | items `CRAFTBEER0750` (750), `ESPRESSO00300` (300, modifier group `MODGROUPMILK1`: oat `MODIFIEROAT01`, soy `MODIFIERSOY01`), `CROISSANT0450` (450) |
| Orders | open `SEEDORDER0001` |
| Payment plumbing | tender `TENDEREXTRN01` (external), `TENDERCASH001`; employees `EMPLBARISTA01`, `OWNERHRVST001`; service charge `SVCCHARGE0001` (18%) |
| Webhooks | pre-verified subscriber `wbhk_seed_quickstart` (auth code `unit-seeded-clover-webhook-auth-code`), **disabled**; register through `POST /__unit/webhooks/subscriptions` |
| Event types | `O:`, `I:`, `C:`, `P:` (orders, inventory items, customers, payments) × `CREATE`, `UPDATE`, `DELETE` — e.g. `O:CREATE`; globs like `O:*` accepted |

### Toast

| | |
|---|---|
| App credentials | `unit-toast-client-id` / `unit-toast-client-secret` |
| OAuth shape | no redirect: machine-client `POST /authentication/v1/authentication/login` |
| Full-access bearer | `unit-seeded-toast-access-token-full-scopes` |
| Read-only bearer | `unit-seeded-toast-access-token-read-only` |
| Tenant, and how requests name it | restaurant `e6a4a8d2-0000-4000-8000-000000000001` ("Harvest & Rye — Toast") in the `Toast-Restaurant-External-ID` **header** |
| Location / order type | dining options `…d001` (dine-in), `…d002` (take-out) |
| Catalog | menu `…c001`: Soup `…c201` (899), Burger `…c202` (sides modifier group `…c301`), Lemonade `…c203` |
| Orders | open `9a7b6c5d-0000-4000-8000-00000000f001` with one check `…f101` |
| Payment plumbing | alternate payment type `…d101` on `POST …/checks/{c}/payments` |
| Webhooks | subscriber `sub_seed_quickstart` (secret `unit-seeded-toast-webhook-secret`, `Toast-Signature` HMAC), **disabled**; register through `POST /__unit/webhooks/subscriptions` |
| Event types | `order_updated`, `in_stock`, `out_of_stock`, `low_quantity`, `menus_updated` |

The guids above are truncated to their last four characters. They come in
four families, each with its own fixed prefix and the same
`-0000-4000-8000-` middle: `e6a4a8d2…` the restaurant and its management
group, `3c9a1f00…` the menu and everything on it, `5d0e2b11…` restaurant
configuration (dining options, payment types, tax rates), `9a7b6c5d…`
orders and checks. So the dine-in dining option in full is
`5d0e2b11-0000-4000-8000-00000000d001`.

### Lightspeed Retail X-Series

| | |
|---|---|
| App credentials | `unit-lightspeed-client-id` / `unit-lightspeed-client-secret` |
| OAuth shape | authorize stand-in `GET /connect` + form-encoded code exchange at `POST /api/1.0/token`; refresh rotates AND revokes the access token it came with |
| Full-access bearer | `seedfullscopeaccesstoken000000000000001A` (refresh `seedfullscoperefreshtoken00000000000001A`) |
| Read-only bearer | `seedreadonlyaccesstoken0000000000000001A` |
| Personal token | `seedpersonalaccesstoken0000000000000001A` — full scopes, never expires, Plus-plan only at the real vendor |
| Tenant, and how requests name it | retailer `…0001` ("Ridgeline Provisions"), `domain_prefix` `unit-lightspeed` (implicit in the token; the real API puts it in the host) |
| Outlets and registers | outlets `…0101` (main), `…0102` (quay); registers `…0201` (open) and `…0202` (closed), one per outlet |
| Payment types | cash `…0301`, credit card `…0302`, and one **internal** type `…0303` that `payment_types:read` excludes |
| Catalog | Trail Mix 500g `…0701` (`TRAIL-500`, 12.50), Merino Socks `…0702` (24.90), Insulated Bottle 1L `…0703` (`BOTL-1L`, inactive), and the Ridgeline Tee family `…0704` with variants `…0705` (small) and `…0706` (large) |
| Inventory | ten records across the two outlets, plus two adjustment reasons (`…0921` found, `…0922` spoiled) and two logged adjustments |
| Customers | Ada Whitcombe `…0911`, Blake `…0912`, Noor `…0913` (a null `last_name`, which is legal), in group `…0901` |
| Sales | parked `…0a01`, closed `…0a02` (card payment, invoice `MAIN-1042-NZ`), layby `…0a03` |
| Payment plumbing | payments are **inline on the sale**; there is no payment operation anywhere in this API |
| Rate limit | `300 × registers + 50` = 650 per five minutes, on every profile, with `x-ratelimit-limit`/`-remaining` on every response |
| Webhooks | subscriber `…0401` on `register_closure.create` to `https://consumer.example/hooks/lightspeed`, **enabled**; register through the vendor's own `POST /api/2026-07/webhooks` |
| Event types | `sale.update`, `product.update`, `customer.update`, `inventory.update`, `register_closure.create`, and the two consignment events, which are declared and never fired |

Lightspeed's ids are one block: `1a000000-0000-1000-8000-0000000000NN`, in the
version-1 UUID layout the vendor's own examples use, numbered by entity kind —
`01` the retailer, `01xx` outlets, `02xx` registers, `03xx` payment types,
`04xx` webhooks, `05xx` tokens, `07xx` products, `09xx` customers, `0axx`
sales. So the main register in full is
`1a000000-0000-1000-8000-000000000201`. The whole surface, with transcripts, is
on the [Lightspeed page](../vendors/lightspeed.md).
