# Lightspeed Retail X-Series (API 2026-07)

The fourth vendor, the second whose specification is **vendored**, and the first
with a documentation page of its own: Lightspeed publishes `api-2026-07.yaml`
under Apache 2.0, so a structural extract of it is committed beside the code and
every fidelity check runs offline, as Square's already does.

!!! warning "Unofficial"
    Not affiliated with, endorsed by, or connected to Lightspeed. Every
    behaviour below is derived from publicly published documentation, and every
    behaviour that is *not* documented is labelled JUDGMENT here and at its site
    in the code.

Thirty-seven routes on one retailer: the token endpoint and an authorize
stand-in, the retailer, its outlets, registers and payment types, products,
inventory, customers, sales, and webhook CRUD. Everything else the vendor
publishes is deferred — see [Deferred surface](#deferred-surface).

## Quickstart

Every transcript on this page was produced by running the commands as written
against a served unit on the `full` profile. Ids are the shipped scenario's;
tokens are minted per unit and will differ on yours. Every value shown is the
value the unit answered, but the longer bodies are **abridged** to the members
under discussion; each such block says so underneath, and a block with no such
note is the whole response.

```sh
vendorfake serve --vendor lightspeed --port 8124
```

```sh
curl -s http://127.0.0.1:8124/__unit/health
```

```json
{"status":"ok","vendor":"lightspeed","profile":"full","uptime_ms":19,"framework_answered":0}
```

### 1. Authorize, then exchange the code

The authorize page lives on a fixed host at the real vendor
(`secure.retail.lightspeed.app/connect`). A unit serves one origin and has
nobody to click a consent screen, so `GET /connect` is a **stand-in** that
approves automatically and redirects with the code.

```sh
curl -s -o /dev/null -D - "http://127.0.0.1:8124/connect\
?response_type=code&client_id=unit-lightspeed-client-id\
&redirect_uri=https%3A%2F%2Fconsumer.example%2Fcallback&state=xyz\
&scope=products%3Aread%20sales%3Awrite%20webhooks%20registers%3Aread%20register%3Aclose%20payment_types%3Aread%20customers%3Aread%20inventory%3Aread"
```

```http
HTTP/1.1 302 Found
location: https://consumer.example/callback?code=v6a3M8gVkAyYJtYMedyNgYwu3jGzty2q&state=xyz
```

The exchange is **form-encoded**, under a different version segment from the
whole rest of the API (`/api/1.0/token`, not `/api/2026-07/...`):

```sh
curl -s -X POST http://127.0.0.1:8124/api/1.0/token \
  -H 'content-type: application/x-www-form-urlencoded' \
  --data "grant_type=authorization_code&code=$CODE\
&client_id=unit-lightspeed-client-id&client_secret=unit-lightspeed-client-secret\
&redirect_uri=https%3A%2F%2Fconsumer.example%2Fcallback"
```

```json
{"access_token":"PxJJ26G40shgdeokHYiCkBN5mT5ppGxv2WNAWEw3","token_type":"Bearer",
 "expires":1788587325,"expires_in":86400,
 "refresh_token":"NKGQn7mudbjOwcYJ4MBQOPOlmWd2Zhiir3N7du0P",
 "domain_prefix":"unit-lightspeed",
 "scope":"products:read sales:write webhooks registers:read register:close payment_types:read customers:read inventory:read"}
```

Those seven members are the whole documented response. JSON is accepted on this
route as well as the documented form encoding, so a consumer fails on the thing
under test rather than on a content type.

### 2. A list, and the version cursor

```sh
curl -s -D - -H "Authorization: Bearer $AT" \
  "http://127.0.0.1:8124/api/2026-07/products?page_size=2"
```

```http
HTTP/1.1 200 OK
x-ratelimit-limit: 650
x-ratelimit-remaining: 643
```

```json
{
  "data": [
    {"id": "1a000000-0000-1000-8000-000000000701", "name": "Trail Mix 500g",
     "sku": "TRAIL-500", "price_including_tax": 12.5,
     "price_excluding_tax": 10.86957, "version": 1000008},
    {"id": "1a000000-0000-1000-8000-000000000702", "name": "Merino Socks",
     "sku": "SOCK-MER", "price_including_tax": 24.9,
     "price_excluding_tax": 21.65217, "version": 1000009}
  ],
  "version": {"max": 1000009, "min": 1000008}
}
```

*Abridged: each `data` row carries about thirty members — `active`,
`attributes`, `brand`, `button_order`, `categories`, `created_at`,
`customizations`, `family_id`, `handle`, `has_inventory`, … `variant_options` —
of which six are shown. The envelope is complete.*

`version` is one per-retailer monotonically increasing integer, bumped on every
mutation of any resource, and it is the cursor: ask for the next page with
`after=<the previous response's version.max>` and stop when `data` comes back
empty, at which point `version.max` and `version.min` are both `null`.

### 3. A sale, with its payments inline

There is no payment operation anywhere in this API. A sale carries its line
items *and* its payments as members of itself.

```sh
curl -s -X POST http://127.0.0.1:8124/api/2026-07/sales \
  -H "Authorization: Bearer $AT" -H 'content-type: application/json' -d '{
  "state": "closed",
  "source": {"author_id": "1a000000-0000-1000-8000-000000000001",
             "register_id": "1a000000-0000-1000-8000-000000000201"},
  "customer_id": "1a000000-0000-1000-8000-000000000911",
  "line_items": [{"product": {"id": "1a000000-0000-1000-8000-000000000701"},
                  "quantity": 2, "pricing": {"price": 10.87},
                  "tax": {"id": "1a000000-0000-1000-8000-0000000000a1", "amount": 1.63}}],
  "payments": [{"type": {"config_id": "1a000000-0000-1000-8000-000000000301"}, "amount": 25.0}]
}'
```

```json
{"data": {
  "id": "ad801b0c-df35-1056-a623-6af007c4eedf", "state": "closed",
  "customer_id": "1a000000-0000-1000-8000-000000000911",
  "invoice_number": "MAIN-1044-NZ", "receipt_number": "MAIN-1044-NZ",
  "source": {"outlet_id": "1a000000-0000-1000-8000-000000000101",
             "register_id": "1a000000-0000-1000-8000-000000000201",
             "author": {"id": "1a000000-0000-1000-8000-000000000001"}},
  "line_items": [{"id": "49d2a169-5414-1c99-bf8a-b45ef5e3a87c",
                  "product": {"id": "1a000000-0000-1000-8000-000000000701"},
                  "quantity": 2.0,
                  "pricing": {"price": 10.87, "total": 21.74},
                  "tax": {"id": "1a000000-0000-1000-8000-0000000000a1",
                          "amount": 1.63, "total": 3.26},
                  "_metadata": {"sequence": 0}}],
  "payments": [{"id": "25997883-3633-122e-b1e9-3a31a565e6cc", "amount": 25.0,
                "date": "2026-09-04T05:48:57Z",
                "type": {"config_id": "1a000000-0000-1000-8000-000000000301", "name": "Cash"},
                "source": {"register_id": "1a000000-0000-1000-8000-000000000201"}}],
  "taxes": [{"id": "1a000000-0000-1000-8000-0000000000a1", "tax": 3.26}],
  "totals": {"price": 21.74, "price_incl_tax": 25.0, "tax": 3.26,
             "loyalty": 0.0, "surcharge": 0.0},
  "return": {"is_return": false},
  "date": "2026-09-04T05:48:57Z", "version": 1000035}}
```

*Abridged: `data` also carries `attributes`, `created_at`, `updated_at` and
`_metadata`.*

Aim the same sale at the **second** register, which the scenario seeds closed,
and the refusal comes back in `PaymentErrorResponse` — the one error schema this
vendor's document names anywhere:

```json
HTTP 409
{"error":{"code":1001,"message":"Register 1a000000-0000-1000-8000-000000000202 is not open; a closed register takes no payments."}}
```

### 4. Register a webhook, and see a delivery

```sh
curl -s -X POST http://127.0.0.1:8124/api/2026-07/webhooks \
  -H "Authorization: Bearer $AT" -H 'content-type: application/json' \
  -d '{"active":true,"type":"sale.update","url":"https://consumer.example/hooks/sales"}'
```

```json
HTTP 201
{"data":{"id":"dafd6487-b2b9-1b0d-b091-ebcc407cc5d3",
         "retailer_id":"1a000000-0000-1000-8000-000000000001",
         "type":"sale.update","url":"https://consumer.example/hooks/sales","active":true}}
```

The same `type` and `url` again is the documented conflict, and its body has
exactly one member:

```json
HTTP 409
{"error":"A webhook with this type and URL already exists."}
```

A delivery is `application/x-www-form-urlencoded` with the entity JSON inside a
`payload` field, and it is signed:

```sh
curl -s -X POST http://127.0.0.1:8124/__unit/webhooks/drain -d '{}'
curl -s http://127.0.0.1:8124/__unit/webhooks/deliveries
```

```json
{"count": 20,
 "deliveries": [
   {"id": "dlv_00001",
    "event_id": "807bc150-9af1-e247-e132-63a163d58a6a",
    "event_type": "sale.update",
    "entity_id": "ad801b0c-df35-1056-a623-6af007c4eedf",
    "subscription_id": "dafd6487-b2b9-1b0d-b091-ebcc407cc5d3",
    "url": "https://consumer.example/hooks/sales",
    "attempt": 1, "retry_number": 0, "status": "failed", "response_status": 0,
    "headers": {"content-type": "application/x-www-form-urlencoded",
                "x-vendorfake-attempt-number": "1",
                "X-Signature": "signature=8bba5207797c02829c18100ad95173f8386adb385202787c0c068d446b7bf360,algorithm=HMAC-SHA256"},
    "body_preview": "payload=%7B%22id%22%3A%22ad801b0c-df35-1056-a623-6af007c4eedf%22%2C%22state%22%3A%22closed%22…"},
   …
 ]}
```

*Abridged: one of the twenty rows is shown, its `body_preview` truncated. A row
also carries `at`, `body_hash`, `error` and `next_attempt_in_ms`. The `{"count":
…, "deliveries": [ … ]}` envelope is the whole shape — assert against it, not
against a bare delivery object.*

`status: failed` because `consumer.example` does not resolve — which is what
makes the retry ladder visible: `drain` reported 40 deliveries for two events,
twenty attempts each, the documented bound. Point a subscription at a URL that
answers 2xx and the first attempt is the only one.

### 5. Refreshing revokes the token it was issued with

This is the behaviour most worth rehearsing, because a consumer that keeps the
pre-refresh access token works against a naive fake and fails in production.

```sh
curl -s -X POST http://127.0.0.1:8124/api/1.0/token \
  -H 'content-type: application/x-www-form-urlencoded' \
  --data "grant_type=refresh_token&refresh_token=$RT\
&client_id=unit-lightspeed-client-id&client_secret=unit-lightspeed-client-secret"
```

```json
{"access_token":"1ESWUXnoXXTCgkQ7GJP4Q2MP1nbfzgDzmUrtD6Vk","token_type":"Bearer",
 "expires":1788587378,"expires_in":86400,
 "refresh_token":"h0te9cU8Wy9MUeXhofRupwXx8hzHkDjKnxUt4aP7",
 "domain_prefix":"unit-lightspeed","scope":"products:read sales:write webhooks ..."}
```

The old access token, immediately afterwards:

```json
HTTP 401
{"error":"Unauthorized","message":"The access token was revoked: using a refresh token revokes the access token that was returned with it."}
```

And the scope table is real. The token above never asked for `retailer:read`,
and `GET /retailer` needs it *alongside* `payment_types:read` — one of three
operations whose description names a pair:

```json
HTTP 403
{"error":"Forbidden","message":"The access token is missing the required permission(s): retailer:read."}
```

## The vendor's own inconsistencies, reproduced

Five places where `api-2026-07.yaml` disagrees with itself. None of them is a
defect here; each is a fact a consumer will meet against the real API, and each
has a fidelity corpus case or a test that pins it.

| # | The inconsistency | Where it bites |
|---|---|---|
| 1 | **Money is a JSON number on one surface and a JSON string on another.** `Product.price_excluding_tax` is `type: number` and prints `110`, `126.5`, `2.63158`; `RegisterClosePaymentType.total` is `type: string` and prints `"255.00"`. | A consumer with one money parser. Both spellings ship. |
| 2 | **The four inventory reads answer a bare array**, with no `data` wrapper and no `version` pair — unlike every other list on the API — and two of the four are POSTs whose paging travels in the body under names of their own (`size`, `offset`). `GET /stock_adjustments`, on the same tag, *does* answer the envelope. | A generic list walker. Corpus case `inventory.bare-array`. |
| 3 | **Status codes differ per tag.** `POST /customers` is 201 and `DELETE /customers/{id}` is 204; `POST /products` is an empty-bodied 200 that answers an **array of ids**, and `DELETE /products/{id}` is an empty 200. | Anything asserting one convention. Corpus case `customers.status-codes`. |
| 4 | **`GET /stock_adjustments` is gated on `inventory:write`.** A read behind a write scope, in the operation's own annotation. Reproduced, not corrected: the scenario's read-only token cannot see the adjustment log at all. | Least-privilege token design. |
| 5 | **`include_images=false` produces a body the vendor's own schema rejects.** The parameter is documented ("Whether to include product image fields in the response. Defaults to true"), and `images` and `skuImages` are two of `Product`'s twenty-one **required** members. Both cannot hold. This unit follows the parameter. | Schema validation against the published document. Pinned by `test_include_images_false_answers_a_body_the_vendors_own_schema_rejects`, which asserts the violation *happens*. |

And one thing the vendor documents that this unit does **not** reproduce:

> **"3xx and 4xx will not trigger retries."** The core dispatcher retries every
> non-2xx outcome and offers a vendor no hook to say otherwise, so a subscriber
> answering 400 or 404 is retried here where Lightspeed would stop. Tracked as
> konyklabs/roadmap#40; stated in `lightspeed/retry.py` at the site.

## Capabilities

| Capability | Kind | Routes | What it covers |
|---|---|---|---|
| `auth` | surface | 2 | `POST /api/1.0/token` for both documented grants with rotation; a stand-in `GET /connect` that issues the single-use code |
| `retailer` | surface | 1 | The one retailer, its currency, timezone and domain prefix |
| `outlets` | surface | 2 | The version-cursor list and one outlet by id |
| `registers` | surface | 5 | The list, one by id, the open and close actions, and the payments summary |
| `payment_types` | surface | 1 | The list, excluding internal types unless asked for |
| `products` | surface | 5 | List with `sku`/`name`/`family_name` overrides, get, create with inline variants, update, soft delete |
| `inventory` | surface | 6 | The four documented reads and the 1–1000 stock-adjustment batch |
| `customers` | surface | 5 | List, get, create (201), replace, soft delete (204) |
| `sales` | surface | 5 | List, get, create, update, and the return action; payments inline |
| `webhooks` | surface | 5 | The five documented operations, and signed form-encoded delivery |
| `chaos` | behavior | — | Request-scope faults: rate limits, timeouts, server errors, token expiry |
| `webhooks.chaos` | behavior | — | Delivery faults: duplication, reordering, dropped acknowledgements, delay |

Profiles: `full`, `no-chaos`, `no-faults`, `chaos-demo`, `oauth-only` (the token
endpoint and the stand-in, nothing else) and `orders-only` (the till surface with
the catalogue, stock and customers it reads, and no token endpoint). The
generated tables are in [Lightspeed routes](../reference/routes-lightspeed.md)
and [Profiles](../reference/profiles.md).

## The JUDGMENT list

Everything below is a place the vendor's published documentation does not
settle a behaviour a fake must nevertheless have. Each is labelled `JUDGMENT` at
its site in the code; the citation is the page that is *silent*, which is the
discipline described under
[Provenance labels](../concepts/provenance-labels.md).

| Decision | Why it is a judgment | Cited page |
|---|---|---|
| **The error envelope** — `{"error": "<Title>", "message": "<detail>"}` for every refusal | The vendor publishes none. Of 373 component schemas exactly one is an error schema (`PaymentErrorResponse`, payments only); most operations declare a 4xx with a bare description and no content; the documentation site has no error-codes page. This generalises the one body the vendor prints verbatim, the 429's. | [rate_limiting](https://x-series-api.lightspeedhq.com/docs/rate_limiting) |
| **Which status each refusal gets** — 403 for a missing scope, 422 for a bad field, 400 for malformed JSON, 409 for an invalid transition | The vocabulary is the vendor's (401 on 29 operations, 403 on 24, 404 on 39, 409 on 12, 422 on 10), but no page connects a cause to a status. 404, 409 on `POST /webhooks`, 429 and 401 are `documented`; the rest are not. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **Every `PaymentErrorResponse.code` value** (1001–1004) | `code` is declared `type: integer` with no enum, example or range, and there is no error-codes page. The four are deliberately synthetic — a dense four-digit block no real vendor's sparse numbering resembles — and live in one table so nothing hard-codes one. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **The webhook signing recipe** — HMAC-SHA256 over the raw form bytes, hex | "Generate a signature by hashing the webhook request body" is genuinely ambiguous over a form body with JSON inside a field, and the page's own sample value is neither hex nor base64. This unit takes the literal reading and ships the other one as `lightspeed_signature_over_payload`. Both readings are published at `GET /__unit/info`. | [webhooks](https://x-series-api.lightspeedhq.com/docs/webhooks) |
| **The retry intervals** — doubling from 30 s, capped at 4 h, 19 intervals summing to 44 h 15 m | "Exponential", "up to 20 times", "over 48h" is three constraints and no numbers. The 48-hour bound is checked at import as a raise. | [webhooks](https://x-series-api.lightspeedhq.com/docs/webhooks) |
| **Access-token lifetime 86 400 s** | The page's own *example* response shows it; nothing states a standard lifetime. | [authorization](https://x-series-api.lightspeedhq.com/docs/authorization) |
| **The refresh token never expires** | No lifetime is stated anywhere. This unit refuses to fill the gap with an invented number, and documents the gap instead. | [authorization](https://x-series-api.lightspeedhq.com/docs/authorization) |
| **The authorization code is single-use and lasts ten minutes** | Carried from the roadmap#75 spike and not re-quoted by the deeper documentation pass. UNCONFIRMED. | [authorization](https://x-series-api.lightspeedhq.com/docs/authorization) |
| **`client_secret` is required on the refresh call** | The page lists the secret for the initial exchange and does not repeat the parameter list for the refresh. Taken in the direction that cannot teach a consumer a weaker rule than the real API's. | [authorization](https://x-series-api.lightspeedhq.com/docs/authorization) |
| **Repeating a register action is a 409** | The schema documents `is_open` and the two actions and nothing about repeating one. Answering 200 would let an end-of-day close run twice and report success both times. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **`payments_summary` is a 404 before the first close** | Every member of the documented example names a closure; a body of nulls would be a worse answer. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **Both deletes are soft** | A hard delete would leave nothing for the documented `deleted` list parameter to include, and nothing for a `customer.update` tombstone to carry. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **`PUT /customers/{id}` replaces rather than merges** | It declares the same `CustomerBase` body the create does, with no partial-update variant anywhere. The shape is documented; what an absent member means is not. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **The tax rate deriving the price the caller did not send** (`product_tax_rate`, default 0.15) | The create body may carry `price_including_tax` or `price_excluding_tax` and not both, so the other has to come from somewhere, and the Taxes tag is out of scope. 0.15 is the seeded retailer's own country's rate, and it is a config knob. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **The webhook secret is the application's `client_secret`** | `WebhookRequest` carries no per-subscription secret, so there is nothing else to sign with. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |
| **The payload of a delivery is the 2026-07 entity** | The webhooks page says payloads "are the same as those you'll receive from API 1.0" — an older shape this project has no document for. UNVERIFIED drift; recorded rather than guessed at. | [webhooks](https://x-series-api.lightspeedhq.com/docs/webhooks) |
| **`environment` is `production`** | The page names the field and no value for it. A config knob. | [webhooks](https://x-series-api.lightspeedhq.com/docs/webhooks) |
| **Id shape** — version-1-layout lowercase UUIDs for entities, opaque non-UUID strings for tokens and codes | The vendor's examples are v1-shaped; the version nibble is a judgment either way. Tokens are deliberately not UUID-shaped so nothing invites parsing one. | [api-2026-07.yaml](https://x-series-api.lightspeedhq.com/openapi/api-2026-07.yaml) |

`GET /__unit/errors` renders the whole error table with a `provenance` field per
row, so the documented/judgment split is readable from a running unit and not
only from this page.

## Fidelity

Lightspeed is the second **vendored** vendor, after Square: `info.license` reads
Apache 2.0, so `fidelity/extract.json` — the scoped, prose-stripped cut of
`api-2026-07.yaml` — is committed beside the declaration, and `pin.json` ties it
to the upstream bytes it was cut from (sha256 `5660c174…`, 519 895 bytes,
version `2026-07`, fetched 2026-09-04). Both fidelity steps therefore run
offline; there is no `fetch` to pay for, unlike Toast.

```sh
uv run python -m vendorfake.fidelity pin --check --offline \
  --target vendorfake.testing.fidelity:lightspeed_target
uv run python -m vendorfake.fidelity report \
  --target vendorfake.testing.fidelity:lightspeed_target
```

The report joins two legs. **Contract**: every response the corpus produces is
validated against the vendor's schema for that operation and status — and so is
every response the unit's own test suite produces, because the test harness
drives the same validating client. **Behaviour**: thirteen corpus cases, all
`provenance: documented`, each naming the page it was read from and the date it
was fetched.

Two entries in the declaration are worth knowing about.

**`error_schema: PaymentErrorResponse`**, with a deviation. It is the only error
schema in the document, so it is what an undeclared 4xx is validated against —
and the generalised `{"error": "<Title>", ...}` body fails it, because
`PaymentErrorResponse.error` is an object. One narrow deviation excuses exactly
that: keyword `type`, pointer `/error`, and a pattern that admits only a status
reason phrase. A payment refusal answers the declared object shape and is
validated against it unexcused.

**`annotations`**, which is how the scope table stays honest. Lightspeed states
the scope an operation requires as a line of its *description* —
`🔒 Requires: ` `` `products:write` `` ` scope` — and not as an OAuth2 security
scheme: `components.securitySchemes` holds one flat bearer scheme with no scopes
at all. Prose is what a fidelity cut strips, so the declaration asks the cutter
to lift those lines out first and record them per route in the extract, under
the sha256 the pin covers. `tests/unit/lightspeed/test_fidelity_scopes.py` then
compares that record against the `scopes=(...)` on every route the unit serves:
all thirty-five modeled operations carry an annotation, three of them name a
pair, and the only two routes without one are `GET /connect` and
`POST /api/1.0/token` — neither of which is in the document at all, both excused
for that reason, and neither of which demands a scope, because one issues the
credential and the other exchanges it.

## Deferred surface

The scoped surface of konyklabs/roadmap#94 is what is above. Everything else the
2026-07 document publishes — consignments, price books, product images, taxes,
brands, suppliers, product types, variant attributes, users, customer groups as
a writable resource, the loyalty and account surfaces, and the reorder-point
write — is deferred and tracked in **konyklabs/roadmap#107**.

Deferred is not silent: `vendorfake.lightspeed.LIGHTSPEED_NOT_MODELED` maps
thirty-two deferred keys to the reason each is not modelled, so the boundary is
written down beside the code rather than left to a 404. It is a Python constant
the surfaces cite; `GET /__unit/capabilities` answers the twelve capabilities
this unit *has*, which is a different list.
