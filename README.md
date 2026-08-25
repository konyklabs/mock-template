# Square vendor mock unit

A stateful, high-fidelity simulator of Square's public API, built from public
documentation only — and, more importantly, a demonstration of the **template**
it was forked from. The Square-specific part of this repository is one package;
everything that makes it a *usable* mock (state engine, capability gating, chaos
injection, webhook dispatch, control plane, conformance suite, packaging, CI,
freshness job) is vendor-agnostic core that the next vendor inherits untouched.

Runnable evidence for every claim on this page is in [EVIDENCE.md](EVIDENCE.md).
The exercise of forking this template for a second vendor is worked through in
[SECOND-VENDOR.md](SECOND-VENDOR.md).

---

## Quickstart

```bash
npm ci
npm run build

# Run the whole gate: build, conformance, both test suites, image, freshness.
bash tools/self-test.sh

# Or start the unit and talk to it.
npm start                       # http://127.0.0.1:8080, profile "full"
docker build -t vendor-unit-square:test . && docker run --rm -p 8080:8080 vendor-unit-square:test
```

```bash
# Seeded token, so you can skip the OAuth dance while exploring.
TOKEN=EAAAl-unit-seeded-access-token-full-scopes

curl -s localhost:8080/__unit/health
curl -s localhost:8080/v2/orders/CAISENgvlJ6jLWAzERDzjyHVybY -H "Authorization: Bearer $TOKEN"
curl -s localhost:8080/v2/orders -H "Authorization: Bearer $TOKEN" \
  -d '{"idempotency_key":"demo-1","order":{"location_id":"18YC4JDH91E1H",
       "line_items":[{"catalog_object_id":"2TZFAOHWGG7PAK2QEXWYPZSP","quantity":"2"}]}}'

# What is this unit, exactly?
curl -s localhost:8080/__unit/info | jq '{profile, capabilities: [.capabilities[].name], state}'
```

Other entry points:

| Command | What it does |
| --- | --- |
| `npm test` | The fork's behavioural suite: 55 tests, in process, under a second |
| `npm run test:integration` | Consumer-side Vitest against the container (or a spawned process) |
| `npm run test:py` | The same, in Python, with pytest |
| `npm run conformance` | Runs the template's conformance suite against this fork |
| `npm run template:check` | Proves this fork has not edited template code |
| `npm run spec:freshness` | Reconciles the unit against Square's published spec and changelog |
| `npm run demo:chaos` | Prints a reproducible chaos transcript |

---

## The template boundary

This is the claim the whole design rests on, so here it is as a file listing
rather than a diagram. `npm run template:check` enforces it mechanically:
template-owned files are checksummed in `template.manifest.json`, and editing
one is an error, not a style opinion.

```
packages/core/            @vendor-unit/core — TEMPLATE. 22 files, 2734 lines of code.
  kernel/                   the unit contract and the request pipeline
    types.ts                  UnitRequest/UnitResponse, VendorDefinition, the 20 core error kinds
    unit.ts                   the pipeline: route -> capability -> chaos -> auth -> idempotency -> handler
    router.ts                 segment router with :params
    magic.ts                  in-band fault triggering from ordinary request fields
    reply.ts, bindings.ts     response helpers, control-plane wiring
  state/
    store.ts                  collections, optimistic concurrency, the journal, cursors, idempotency records
    machine.ts                declarative lifecycle: states, legal transitions, terminality
  capability/registry.ts    capability declaration, profile resolution, the disabled-capability error
  chaos/engine.ts           rules, counters, deterministic triggering, the built-in fault set
  webhooks/
    dispatcher.ts             journal subscription, at-least-once delivery, the retry schedule
    sink.ts                   outbound transport: HTTP, in-memory, file
  control/plane.ts          the 21-route /__unit/* control API
  transport/                http.ts, inprocess.ts, filedrop.ts — three bindings over one Unit
  config/profile.ts         profile documents and their environment overrides
  conformance/index.ts      the 10 contracts every fork must satisfy
  time/clock.ts             real and virtual clocks, and the scheduler
  rand/rng.ts               seeded RNG (used only where randomness is explicitly asked for)
  util/json.ts              canonical JSON, digests, dot-path access

packages/square/          @vendor-unit/square — FORK. 18 files, 1798 lines of code.
  vendor.ts                 the VendorDefinition: 111 lines that turn the template into a Square unit
  surface/                  the endpoints, and the only genuinely bespoke work
    oauth.ts (337)  orders.ts (426)  webhooks.ts (177)  directory.ts (117)  common.ts (60)
  errors.ts (115)           one table: 20 core error kinds -> Square {category, code, status}
  auth.ts (83)              Bearer and `Client {secret}` schemes
  signer.ts (51)            the HMAC-SHA256 webhook signature
  events.ts (68)            journal entry -> Square webhook envelope
  machine.ts (34)           the order lifecycle, declared as data
  entities.ts (121)         stored shapes; model/order.ts (106) projects them to Square's JSON
  ids.ts (87)               Square-shaped, seeded identifier generation
  hydrate.ts (257)          the seed-document loader
  profiles/*.json           five capability subsets — configuration, not code
  seed/default.seed.json    the scenario a unit starts in
  freshness.json            what to watch on the vendor, for the shared freshness runner
```

Two more boundaries worth naming:

- **Tooling.** `tools/spec-freshness.mjs`, `tools/template-check.mjs`,
  `tools/self-test.sh` and `tools/verify-image-build.sh` are template code and
  contain no vendor knowledge. The Square-specific part of the freshness job is
  `packages/square/freshness.json` — a config file, not a script.
- **Tests.** `tests/support/` (TypeScript) and `tests/pytest/launch.py` +
  `subscriber.py` are template harness. The assertions in
  `tests/vitest/square-unit.test.ts` and `tests/pytest/test_square_unit.py` are
  the fork's.

`template.manifest.json` also names a third class, **seeded**: files the
template supplies once and the fork owns thereafter (the root `package.json`,
the two workflows). Divergence there is reported, not punished, because a fork
*should* customise them.

### The measurement

| | files | code | comment | total |
| --- | ---: | ---: | ---: | ---: |
| Template core | 22 | 2734 | 418 | 3524 |
| Square fork | 18 | 1798 | 334 | 2312 |
| Fork tests | 8 | 1047 | 89 | 1287 |

Runtime dependencies: **zero**. `@vendor-unit/core` has an empty `dependencies`
block and the server is written against `node:` builtins only. The dev
dependencies are TypeScript, Vitest, Testcontainers and `@types/node`. That is
deliberate — see "Solo maintenance" below.

---

## Design choices

Every choice below carries a citation to Square's public documentation or a
maintained OSS repository, or is labelled **JUDGMENT** where the docs are
silent. Where Square publishes nothing, this unit says so rather than inventing
fidelity it cannot back up.

### 1. The unit contract is not an HTTP contract

`Unit.handle(UnitRequest) -> UnitResponse` is the entire inbound contract, and
`DeliverySink.send()` the entire outbound one. `transport` on a request names
whichever binding produced it, and `rawBody` is kept as bytes rather than a
parsed object because signature schemes cover the exact bytes.

Three bindings ship: `transport/http.ts` (a `node:http` server),
`transport/inprocess.ts` (direct calls — used by the conformance suite and by
the fork's own tests) and `transport/filedrop.ts` (reads
`<dir>/in/*.request.json`, writes `<dir>/out/*.response.json`).

The file-drop binding exists specifically to keep this claim honest. It is
exercised by a test (`packages/square/test/transport.test.ts`), and the state it
creates is then read back over HTTP — so if a future change let an HTTP
assumption leak into the kernel, that test is what breaks. **JUDGMENT**: no
non-HTTP *vendor* is built here; the brief asks for the interfaces plus a
demonstration that the core does not assume HTTP, and this is that
demonstration rather than an assertion in a README.

`UNIT_TRANSPORT=filedrop UNIT_TRANSPORT_DIR=/data/exchange` starts the unit on
that binding instead of a port.

### 2. Not spec-generation-reliant

`github.com/square/connect-api-specification` (Apache-2.0) is used for exactly
one thing: drift detection. No type, route, validator or response in this
repository is generated from it, and the unit runs with no network and no spec
file present. The spec is a *signal that something moved*, checked by
`tools/spec-freshness.mjs` — which is the right use for it, because the entire
value of a vendor mock is the behaviour a spec cannot express (state
transitions, optimistic concurrency, idempotent replay, retry schedules,
signature schemes).

Proof that the dependency runs the right way round: the freshness job asks the
*unit* what it implements (via its route table) and looks each operation up in
the spec — not the reverse.

### 3. Errors: one table, and honesty about what Square publishes

The core raises 20 vendor-neutral error kinds. `packages/square/src/errors.ts`
maps each to a Square `{category, code}` and an HTTP status. The envelope and
the four `Error` fields are documented
([handling errors](https://developer.squareup.com/docs/build-basics/handling-errors),
[Error object](https://developer.squareup.com/reference/square/objects/Error)).

Square publishes HTTP statuses for the authentication codes and 429 only. Every
other status in that table is marked `provenance: 'judgment'` in the source and
is echoed back to the caller in the response, so a consumer can tell fidelity
from convention:

```json
{"errors":[{"category":"INVALID_REQUEST_ERROR","code":"VERSION_MISMATCH","detail":"…","field":"order.version"}],
 "unit_error":{"kind":"version_conflict","statusProvenance":"judgment","collection":"orders","supplied":1,"current":2}}
```

The `unit_error` sidecar is a **JUDGMENT** deviation from Square's wire format:
it is namespaced, ignored by any consumer that reads only `errors`, and
switchable off with `"errorSidecar": false` in a profile's vendor config. The
alternative — making a consumer parse prose to find out why the mock refused —
is worse.

Two specific fidelity notes:

- `VERSION_MISMATCH` is **not** in Square's published `ErrorCode` enum. The
  optimistic-concurrency guide names it in prose
  ([source](https://developer.squareup.com/docs/working-with-apis/optimistic-concurrency))
  and the only observed body comes from Square's developer forum, which reports
  HTTP 400 rather than 409. This unit follows the observed 400 and labels the
  choice, rather than reaching for the 409 that "should" be right.
- `/oauth2/token` and `/oauth2/revoke` have **no published error table at all**.
  Their failures use the v2 envelope (which the ObtainToken response schema does
  declare an `errors` array for) with `AUTHENTICATION_ERROR`/`UNAUTHORIZED`.
  Labelled JUDGMENT in `surface/oauth.ts`.

### 4. State: a journal, not just a map

`state/store.ts` keeps entities in named collections with `version`,
`createdAt`, `updatedAt`, and appends every committed mutation to a journal.
The journal is load-bearing, not diagnostic:

- The webhook dispatcher subscribes to it, so an event cannot exist for a
  mutation that did not commit, and a handler cannot forget to emit one.
- `GET /__unit/journal` is how a consumer debugging a test sees what actually
  changed.
- Conformance asserts it is monotonic in both sequence and entity version.

Mutations go through `update(id, {expectVersion}, draft => …)`: the mutator gets
a private copy, and nothing is written or journalled if it throws. Optimistic
concurrency is therefore a core feature that Square's `order.version`
([Order object](https://developer.squareup.com/reference/square/objects/Order))
simply plugs into.

Cursor pagination is core too, and reproduces three real behaviours consumers
get wrong: the cursor is opaque, it carries a fingerprint of the query it was
issued for (Square: "you must use the original query"), and it expires after
five minutes ([pagination](https://developer.squareup.com/docs/build-basics/common-api-patterns/pagination)).

Idempotency is core as well. A route declares
`idempotency: {keyPath: 'idempotency_key', scope: 'orders.create'}` and the
kernel handles replay and conflict. `UpdateOrder` additionally sets
`onMismatch: 'replay'`, because Square documents the surprising behaviour that
reusing an update key returns the stored response and silently drops the new
changes ([update orders](https://developer.squareup.com/docs/orders-api/manage-orders/update-orders)).

### 5. Chaos: deterministic by default, with two ways in

Rules fire on **counters**, never on a coin flip:

```json
{"id":"rate-limit-every-third-create","scope":"request","fault":"rate_limit",
 "match":{"route":"POST /v2/orders"},"when":{"every":3},"params":{"retryAfterSeconds":2}}
```

`when` supports `nth`, `every`, `after` and `times`; the conditions are ANDed,
and a rule with no `when` fires on every match (`always: true` spells that out).
A `probability` escape hatch exists and does use the seeded RNG — but the seed lives in the
profile and is reported by `/__unit/info`, so even that run is replayable from
its own report. Every matching rule's counter advances whether or not it fires,
so adding a rule cannot silently re-number the rules below it.

Two triggering mechanisms, because they serve different callers:

- **Control API** (`POST /__unit/chaos/rules`) for a suite that owns the mock.
- **Magic values** for a consumer driving the unit through a vendor SDK that
  cannot set a header or reach a control plane: `reference_id`, the OAuth
  `state` parameter, or an idempotency key beginning `chaos:` arms that fault
  for that request only. Prior art is Square's own Sandbox, which drives
  declines from magic values in ordinary payment fields such as
  `cnon:card-nonce-declined`
  ([test values](https://developer.squareup.com/docs/devtools/sandbox/testing)).

Faults: `rate_limit`, `server_error`, `unavailable`, `timeout`, `token_expiry`
on requests; `webhook.duplicate`, `webhook.out_of_order`, `webhook.drop_ack`,
`webhook.delay` on deliveries. `token_expiry` injects
`ACCESS_TOKEN_EXPIRED` mid-flow **without** touching the stored token, so the
next request succeeds — which is the situation a consumer's refresh logic
actually has to survive.

`npm run demo:chaos` prints the whole thing; two runs of it are byte-identical
(EVIDENCE.md).

### 6. Webhooks: at-least-once, with the documented schedule and signature

Signature: `base64(HMAC-SHA256(signature_key, notification_url + raw_body))` in
`x-square-hmacsha256-signature`. The header, algorithm and three inputs are
documented ([validate](https://developer.squareup.com/docs/webhooks/step3validate));
the docs do **not** state the concatenation order, so this follows Square's own
SDKs, which are what a consumer's verification code will be using:
[python](https://github.com/square/square-python-sdk/blob/master/src/square/utils/webhooks_helper.py),
[node](https://github.com/square/square-nodejs-sdk/blob/master/src/wrapper/WebhooksHelper.ts).
Both test suites verify it with an independent reimplementation of the
algorithm rather than by calling this unit's signer.

The signer declares what its scheme is bound to
(`properties: {urlBound, bodyBound, secretBound}`) and conformance checks each
direction. That exists because the first version of the check asserted a fact
about HMAC rather than about signing, which would have failed any vendor using a
static shared header — see SECOND-VENDOR.md, section 4.

Delivery headers `square-environment`, `square-initial-delivery-timestamp`,
`square-retry-number` and `square-retry-reason` (`http_timeout` / `http_error` /
`other_error`) follow
[build with webhooks](https://developer.squareup.com/docs/webhooks/build-with-webhooks).

Retry schedule: Square's documented 11 retries over 24 hours — 1, 2, 4, 8, 16,
32, 60 minutes then 2, 4, 8, 8 hours
([overview](https://developer.squareup.com/docs/webhooks/overview)). The table is
verbatim; a profile scales it (`webhooks.retry.timeScale`, default `0.000167`)
so a test observes the real *shape* in milliseconds. **JUDGMENT**: scaling
rather than shortening keeps the schedule reviewable against the docs — the
numbers in the code are Square's, and the compression is one visible factor.

At-least-once is real, not decorative: a delivery is retried until a 2xx or
schedule exhaustion, `webhook.drop_ack` reproduces the lost-acknowledgement case
where the subscriber succeeded but the vendor retried anyway, and every attempt
carries the same `event_id` — the dedup handle Square tells consumers to use
("Webhooks can be sent more than once… using the idempotency value included as
the `event_id`"). Square also documents that ordering is not guaranteed, which
is what `webhook.out_of_order` reproduces.

Event payloads carry a **summary, not the order** — `data.object.order_created`
holds `{created_at, location_id, order_id, state, version}`
([order.created](https://developer.squareup.com/reference/square/webhooks/order.created)).
Getting this wrong is the classic vendor-mock error, and it is the kind of thing
a spec-generated mock reproduces incorrectly.

### 7. Capabilities and profiles

Five capabilities. Four are the ones the slice names; `merchant-directory` is a
fifth, added to show the mechanism is not special-cased to the required list.

| Capability | Kind | Surface |
| --- | --- | --- |
| `oauth` | surface | authorize, token, revoke, token status |
| `order-lifecycle` | surface | create, retrieve, update, search, pay |
| `merchant-directory` | surface | locations, catalog |
| `webhooks` | surface | subscription management, event types, test |
| `webhooks.chaos` | behavior | delivery faults; requires `webhooks` |

A disabled capability answers **explicitly**:

```
HTTP/1.1 501 Not Implemented
x-unit-error: capability_disabled
x-unit-capability: order-lifecycle

{"errors":[{"category":"API_ERROR","code":"NOT_IMPLEMENTED",
  "detail":"Capability 'order-lifecycle' is disabled in profile 'oauth-only'. Enable it in the profile, in UNIT_CAPABILITIES, or with POST /__unit/capabilities."}],
 "unit_error":{"kind":"capability_disabled","capability":"order-lifecycle","profile":"oauth-only","route":"POST /v2/orders"}}
```

Never a 404, because a consumer cannot tell a 404 from "this vendor has no such
endpoint" — and a path this vendor genuinely does not serve *does* return 404,
so the two stay distinguishable. `NOT_IMPLEMENTED` is a real Square generic
error code (it appears in `api.json`'s `x-square-generic-error-codes`), which
keeps the answer inside the vendor's own vocabulary. The 501 status is
**JUDGMENT**.

`webhooks.chaos` is declared `kind: 'behavior'`: it gates conduct and owns no
routes. Its "disabled" answer surfaces where a consumer meets it — registering a
webhook-scoped chaos rule returns the same `capability_disabled` error.

**Consumer subsets are configuration.** Five profiles ship; none of them is code:

| Profile | Capabilities | For |
| --- | --- | --- |
| `full` | all five | the default |
| `oauth-only` | `oauth` | a consumer testing token handling and nothing else |
| `orders-only` | `order-lifecycle`, `merchant-directory` | skip the OAuth dance, use a seeded token |
| `no-chaos` | all but `webhooks.chaos` | a happy-path CI suite |
| `chaos-demo` | all five, plus four preloaded rules, virtual clock | the transcript |

One image serves all of them: `UNIT_PROFILE` picks the file,
`UNIT_CAPABILITIES='+webhooks,-webhooks.chaos'` adjusts it, `UNIT_WEBHOOK_URL`
registers a subscriber, `UNIT_CHAOS_SEED` and `UNIT_CLOCK` change determinism
and time. Runtime toggling via `POST /__unit/capabilities` needs no restart at
all.

### 8. Authentication is not part of the `oauth` capability

A deliberate split: `oauth` governs the four `/oauth2/*` endpoints, while Bearer
validation on the v2 surface is intrinsic. A consumer that does not test the
OAuth dance runs `orders-only` and authenticates with a seeded token; a consumer
that does test it gets the whole flow. Making authentication part of the
capability would have forced every profile to run the dance or turn off auth
entirely, and both are worse.

### 9. Time is controllable

`clock.mode: "virtual"` (or `UNIT_CLOCK=virtual`) plus
`POST /__unit/clock/advance {"ms": …}` makes vendor-scale time testable in
milliseconds: a 30-day token expiry, a five-minute authorization code, a
24-hour retry schedule. Timers are registered with the clock, so advancing it
fires the deliveries that became due. The default is a real clock, so nothing
about ordinary use depends on this.

### 10. Solo maintenance

Zero runtime dependencies is the load-bearing decision here. A vendor mock
maintained by one person competes for attention with everything else that
person owns; the failure mode is not a bad architecture, it is a Dependabot
queue and a transitive CVE in a framework the mock used for routing. The router
is 60 lines, the HTTP binding is 80, and neither will need attention when
someone else's major version lands.

The second decision is that CI runs `tools/self-test.sh` and nothing else, so
"green in CI" and "green on my laptop" cannot drift apart.

### 11. Stack

TypeScript on Node 22+, because the DX targets are Vitest and pytest regardless
of the server's language, and TypeScript makes the vendor-neutral core's
contracts (`VendorDefinition`, `ErrorShaper`, `WebhookSigner`, `EventMapper`)
enforceable at the template boundary rather than merely documented. Python
consumers are first-class — `tests/pytest/` talks to the same container over
the same HTTP, and knows nothing about the implementation language.

---

## The surface

17 vendor routes. The freshness job reconciles all 17 against Square's published
spec: 16 are matched to a spec operation and fingerprinted, and
`GET /oauth2/authorize` is recorded as a *documented absence* — it is a browser
redirect flow that Square describes in prose but does not put in `api.json`, so
its absence is expected rather than drift.

```
GET     /oauth2/authorize                                 oauth               -              Authorize
POST    /oauth2/token                                     oauth               -              ObtainToken
POST    /oauth2/revoke                                    oauth               client-secret  RevokeToken
POST    /oauth2/token/status                              oauth               bearer         RetrieveTokenStatus
POST    /v2/orders                                        order-lifecycle     bearer         CreateOrder
GET     /v2/orders/:order_id                              order-lifecycle     bearer         RetrieveOrder
PUT     /v2/orders/:order_id                              order-lifecycle     bearer         UpdateOrder
POST    /v2/orders/search                                 order-lifecycle     bearer         SearchOrders
POST    /v2/orders/:order_id/pay                          order-lifecycle     bearer         PayOrder
GET     /v2/locations                                     merchant-directory  bearer         ListLocations
GET     /v2/catalog/list                                  merchant-directory  bearer         ListCatalog
GET     /v2/webhooks/event-types                          webhooks            bearer         ListWebhookEventTypes
POST    /v2/webhooks/subscriptions                        webhooks            bearer         CreateWebhookSubscription
GET     /v2/webhooks/subscriptions                        webhooks            bearer         ListWebhookSubscriptions
GET     /v2/webhooks/subscriptions/:subscription_id       webhooks            bearer         RetrieveWebhookSubscription
DELETE  /v2/webhooks/subscriptions/:subscription_id       webhooks            bearer         DeleteWebhookSubscription
POST    /v2/webhooks/subscriptions/:subscription_id/test  webhooks            bearer         TestWebhookSubscription
```

Plus 21 control-plane routes under `/__unit/`. That prefix is a **JUDGMENT**
call: no real vendor serves a path segment starting with a double underscore, so
it cannot collide, and keeping the control plane inside the unit (rather than on
a second port) means a consumer's existing base URL already reaches it.

`GET /__unit/routes` returns this table at runtime, which is what the
conformance suite and the freshness job both read.

### Deliberate scope cuts

Prototype fidelity means shrinking surfaces, never faking behaviours. What is
missing, and why:

- **Orders**: `BatchRetrieveOrders`, `CalculateOrder`, `CloneOrder` — no new
  state behaviour over the five implemented. Taxes, discounts, service charges
  and fulfillments are not modelled; the roll-up fields are emitted as zero
  money so the full `Order` shape still deserializes.
- **Payments**: there is no Payments API, so `PayOrder` accepts `payment_ids` as
  opaque references and derives the tender total from the order.
- **Webhooks**: `UpdateWebhookSubscription` and signature-key rotation.
- **PKCE**: `code_challenge` is supported; `code_challenge_method` is not,
  because Square documents no such parameter.

Every one of these is a smaller surface, not a hollow one. The six slice
elements are all implemented end to end.

---

## Updating a fork from the template

The point of the boundary is that a core improvement reaches every fork without
a merge. The procedure is four commands, and three of them are checks:

```bash
npm run template:check                    # 1. prove this fork edited no template code
npm i @vendor-unit/core@<next>            # 2. take the new core
npm run conformance                       # 3. prove the fork still satisfies the contracts
npm test && npm run test:integration      # 4. prove behaviour is unchanged
npm run template:update                   # re-record the manifest, on purpose
```

Step 1 is what makes step 2 safe. `template.manifest.json` records a SHA-256 for
every template-owned file; `template:check` reports modifications, deletions and
*additions* inside template-owned paths (a new file there will be clobbered by
the next template merge, which is the failure nobody sees coming). It needs no
network and no git history.

Step 3 is the real fork-update story. The conformance suite ships **with the
core**, so a core upgrade brings its own new checks. It asserts, against a fork
it knows nothing about:

1. the control plane responds and `/__unit/info` is complete;
2. every route belongs to a declared capability, every surface capability owns
   at least one route, and no behavior capability owns any;
3. every capability, disabled, answers `capability_disabled` rather than 404 —
   and works again when re-enabled;
4. an unknown path and a wrong method are vendor-shaped;
5. all 20 core error kinds map to a 4xx/5xx vendor error with a non-empty body;
6. two freshly seeded units hash identically;
7. the journal is monotonic in sequence and in entity version;
8. the same chaos rule and traffic produce identical outcomes in two units;
9. webhook signing is deterministic and depends on exactly what the signer
   declares it depends on (`WebhookSigner.properties`) — so a vendor whose
   scheme is a static shared header is conformant, not merely tolerated;
10. the HTTP and in-process bindings return byte-identical responses.

A red check names the fork file to fix. The fork runs it in its own suite
(`packages/square/test/conformance.test.ts`, across three profiles), so a stale
fork fails on a developer's machine rather than in someone else's CI.

---

## Freshness

Two workflows, deliberately separate.

`.github/workflows/self-test.yml` runs `tools/self-test.sh` on every push and
nightly at 03:17 UTC: build, template purity, conformance across three profiles,
55 unit tests, both container-backed integration suites, a real `docker build`
plus a health check, and the freshness job. `workflow_dispatch` is enabled for a
demo run.

`.github/workflows/spec-freshness.yml` runs daily at 06:25 UTC and on demand. It
depends on a third party being reachable, so it is kept out of the build gate —
its failures mean something different. It:

1. downloads Square's `api.json` and compares each **operation this unit
   implements** against the pinned fingerprint in `packages/square/spec-pin.json`;
2. reads the changelog index and compares the latest published `Square-Version`
   against the version the unit declares;
3. opens (or comments on a single existing) tracking issue on drift.

Severity is the design. A byte change anywhere in a 3 MB spec is `info`; a
change to an operation this unit implements is `error`; a newer published API
version is `warn` (`--strict` promotes it). A job that cries wolf gets muted,
and a muted freshness job is worse than no job, because it looks like coverage.

The pin also records the one operation Square documents but does not put in its
spec — `GET /oauth2/authorize`, a browser redirect flow — so its absence reads
as expected rather than as drift.

---

## Configuration

| Variable | Default | Effect |
| --- | --- | --- |
| `UNIT_PROFILE` | `full` | Profile name in `packages/square/profiles/`, or a path |
| `UNIT_CAPABILITIES` | — | Absolute list, or a delta: `+webhooks,-webhooks.chaos` |
| `UNIT_PORT` / `UNIT_HOST` | `8080` / `0.0.0.0` | HTTP binding |
| `UNIT_TRANSPORT` | `http` | `http` or `filedrop` |
| `UNIT_TRANSPORT_DIR` | — | Exchange directory for the file-drop binding |
| `UNIT_SEED` | profile's | Path to a seed scenario |
| `UNIT_WEBHOOK_URL` | — | Registers a subscriber at startup |
| `UNIT_WEBHOOK_SIGNATURE_KEY` | `unit-signature-key` | Its signing key |
| `UNIT_WEBHOOK_TIME_SCALE` | `0.000167` | Scales the documented retry schedule |
| `UNIT_CHAOS_SEED` | profile's | Seeds the RNG and the id generator |
| `UNIT_CLOCK` | `real` | `real` or `virtual` |
| `UNIT_LOG_LEVEL` | `info` | `debug`, `info`, `warn`, `error` |
| `UNIT_VENDOR_*` | — | Any vendor config key, e.g. `UNIT_VENDOR_APPLICATION_ID` |

Seeded credentials, for exploring without the OAuth flow:

| | |
| --- | --- |
| Full-scope token | `EAAAl-unit-seeded-access-token-full-scopes` |
| Read-only token | `EAAAl-unit-seeded-access-token-read-only` |
| Application id | `sandbox-sq0idb-unit-square-application` |
| Application secret | `sandbox-sq0csb-unit-square-secret` |
| Merchant / location | `MLQW2MYBY81PZ` / `18YC4JDH91E1H` |
| Open seed order | `CAISENgvlJ6jLWAzERDzjyHVybY` |

---

## Provenance

Everything here comes from Square's public developer documentation, their
Apache-2.0 OpenAPI specification, and their MIT-licensed SDKs. Seed data uses
the example ids and values from Square's own documentation pages, so a consumer
reading the docs recognises what comes back. Each source file carries the URLs
for the behaviour it implements; the gaps where Square publishes nothing are
marked `JUDGMENT` in the same place, not quietly filled in.
