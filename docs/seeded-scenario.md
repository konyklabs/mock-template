# The seeded scenario

Every profile ships the same scenario, so a fresh unit needs no setup. The
values are readable and obviously fake by design. In Python they are
`.seed.*` attributes on a started unit (`vendorfake.testing.SquareSeed`,
`CloverSeed`, `ToastSeed`, `LightspeedSeed`), which is the form to use in a
test. The vendor
name narrows the type: `unit("toast").seed` is a `ToastSeed` to a type
checker, so a field belonging to another vendor is a type error rather than
a runtime surprise.

## The three fields every vendor has

A test parametrized over vendors reads the seed through
`vendorfake.testing.Seed`, which is what the four have in common:
`credentials`, `auth`, `read_only_auth` and `event_types`. Everything else in
the tables below is vendor-specific and reached through the vendor's own seed
type.

`credentials` is the app credential from the first row of each table, under
names that do not change per vendor — Square spells it `application_id` and
Clover and Toast spell it `client_id`:

| | `app_id` | `app_secret` | `grant` |
|---|---|---|---|
| Square | `application_id` | `application_secret` | `refresh_token` |
| Clover | `client_id` | `client_secret` | `refresh_token` |
| Toast | `client_id` | `client_secret` | `client_credentials` |
| Lightspeed | `client_id` | `client_secret` | `refresh_token` |

`grant` names the token lifecycle, which is the one difference a consumer's
session handling genuinely has to branch on: Square, Clover and Lightspeed
issue a refresh token and rotate it, Toast issues a bearer with no refresh and
expects a fresh login when it expires. That is also why `refresh_token` is
not on the shared type — Toast has none. Lightspeed rotates hardest: a refresh
revokes the access token that was issued *with* the consumed refresh token, so
the old bearer is dead the instant the new one arrives.

The values come from the profile's `vendor` block, so a profile that
overrides the app credentials is reported as it actually ran rather than as
the default below.

## Square

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

## Clover

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

## Toast

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

The guids in the Toast table above are truncated to their last four characters. They come
in four families, each with its own fixed prefix and the same
`-0000-4000-8000-` middle: `e6a4a8d2…` the restaurant and its management
group, `3c9a1f00…` the menu and everything on it, `5d0e2b11…` restaurant
configuration (dining options, payment types, tax rates), `9a7b6c5d…`
orders and checks. So the dine-in dining option in full is
`5d0e2b11-0000-4000-8000-00000000d001`.

## Lightspeed Retail X-Series

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
on the [Lightspeed page](vendors/lightspeed.md). Every value is also an
attribute on the seed object, which is the form to use in a test.
