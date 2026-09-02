# The seeded scenario

Every profile ships the same scenario, so a fresh unit needs no setup. The
values are readable and obviously fake by design. In Python they are
`.seed.*` attributes on a started unit (`vendorfake.testing.SquareSeed`,
`CloverSeed`, `ToastSeed`), which is the form to use in a test.

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
