# Changelog

## Unreleased

### Features

* **lightspeed:** a fourth vendor -- Lightspeed Retail X-Series, API 2026-07
  (konyklabs/roadmap#94). This is the foundation slice: the token endpoint
  (`POST /api/1.0/token`) with both documented grants and the rotation that
  retires the consumed refresh token *and* revokes the access token it was
  returned with; a stand-in `GET /connect` issuing the single-use code; the
  retailer, outlets, registers and payment types; the five documented webhook
  operations with the 409 on a duplicate type-and-url pair; and delivery of
  `register_closure.create` end to end. Products, inventory, customers and
  sales are in issue #94's scoped surface and arrive in later slices.

  Three cross-cutting mechanics come with it, all vendor-side because the core
  has no seam for any of them:

  - the **retailer-global version counter** -- one monotonically increasing
    integer per retailer across every resource type, stamped on every entity
    and bumped by every mutation -- and the list envelope built on it
    (`{"data": [...], "version": {"max": …, "min": …}}`, ascending by version,
    with `after`/`before`/`page_size`/`deleted`). The store's own `version` is
    per-entity optimistic concurrency and its `paginate` is an opaque expiring
    cursor; both are the right model for a different vendor.
  - the **documented fixed-window rate limiter**: `300 × registers + 50` per
    retailer per application over five minutes, `X-RateLimit-Limit` and
    `X-RateLimit-Remaining` on every response, and a 429 whose `Retry-After`
    is an RFC 1123 HTTP-date rather than delta-seconds. It is vendor
    behaviour, not chaos, so no profile switches it off; the quota is a config
    knob instead.
  - **form-encoded webhook delivery**: `payload=<JSON>` plus `domain_prefix`
    and `environment`, signed `X-Signature: signature=<hex>,algorithm=HMAC-SHA256`.

* **core:** `vendorfake.core.webhooks.models.BodyEncodingSigner` -- an
  optional, structurally discovered protocol (the same shape as
  `SeedingVendor`) letting a vendor whose delivery body is not JSON encode it
  itself. The dispatcher's default is unchanged; a signer that does not
  implement it is declaring "JSON" exactly as before. Without it a vendor
  could set a content type through `DeliveryHeaderProvider` and then send
  bytes contradicting it, which is the one shape a fake must not ship.

Hardening round after 0.3 (konyklabs/roadmap#105), landed with the reviewed
2026-09-01 batch (konyklabs/roadmap#53: the conformance-coverage stack #15,
#46, #42 and the fidelity legs #55, #56).

### Bug fixes

* **examples:** the Vitest example's Toast signature helper now mirrors
  `toast/signer.py` exactly -- the timestamp is appended only when the body is
  a JSON object carrying one as a string; anything else is signed alone. It
  used to throw on a body without one. Four parity vectors are pinned in both
  suites, and the full self-test now runs the Vitest example as well as the
  pytest one, so the two cannot drift again without a step going red (#49).
* **testing:** `served(env=)` refuses an entry for `VENDORFAKE_PROFILE`,
  `VENDORFAKE_HOST`, `VENDORFAKE_PORT` or `VENDORFAKE_LOG_LEVEL` with a
  `ValueError` naming the parameter to use -- the child gets each as an
  explicit flag, so the entry changed nothing and was documented as silently
  beaten -- and a `VENDORFAKE_TRANSPORT` or `VENDORFAKE_TRANSPORT_DIR` entry
  with its own message: `serve` only ever binds HTTP, and there is no
  parameter to use instead (#105).

* **examples:** both consumer examples now assert the documented status a
  Toast check lands in after an OTHER payment covering its total: `CLOSED`,
  as the payment walkthrough's own result shows (`PAID` is a card charge
  whose tip is still unadjusted). The fidelity corpus corrected the unit in
  0.3.x (#56); the examples had asserted the old answer and nothing ran them
  (#105).

### Tooling

* `tools/self-test.sh` runs the pytest consumer example as its own uv project
  against the checkout, in full mode -- the step that would have caught the
  example regression above (#105).
* `tools/self-test.sh --quick` skips the fidelity pin and report of a vendor
  whose extract is fetched rather than committed (Toast), and says so: the
  fetch step does not run under `--quick`, and a pull request's check must
  not depend on a vendor's documentation site answering. The first quick run
  after the fidelity legs landed failed exactly there (#105).
* `tools/self-test.sh` runs `pip-audit` over the runtime dependencies and
  `bandit -ll` over the package in its full mode (main and a laptop before a
  push; not `--quick`). Bandit's two findings -- the wildcard-bind comparison
  in `webhook_receiver` and a `yaml.load` on a `SafeLoader` subclass in the
  fidelity extractor -- are annotated at the site with their reasons, and a
  unit test pins that the loader refuses a python-object tag (#105).
* `uv.lock` carries the 0.3.0 version `pyproject.toml` already had, aligned
  by hand on vendorfake#43: release-please bumps only the latter, so every
  `uv run` since v0.3.0 had been rewriting the lockfile in the working tree.
  The root-cause fix -- the lockfile in the release config's extra-files --
  is still pending, as #105's item 2 (it needs a workflow change).

---

Consumer feedback round 3 (konyklabs/roadmap#102, items 20–21, from an
end-to-end spike that ran the fake as a real out-of-process child), round 2
(konyklabs/roadmap#101, items 15–19) and the four non-blocking findings from
the 0.2 gate approval (konyklabs/roadmap#99).

### Features

* **testing:** `served(env=)` — the `VENDORFAKE_*` layer for that one child,
  on top of the environment it inherits: an entry beats the ambient variable
  of the same name, `clock_start=` layers beneath it exactly as in `unit()`,
  and the parent-resolved `.seed` reads the same `VENDORFAKE_VENDOR_*` layer
  so its credentials agree with the child's. Two differently-seeded children
  can now run in one process with nothing written to `os.environ`, which
  makes them safe under `pytest-xdist`. Entries for what `served()` passes
  as a flag (`VENDORFAKE_PROFILE`, `VENDORFAKE_HOST`, `VENDORFAKE_PORT`,
  `VENDORFAKE_LOG_LEVEL`) are beaten by the flag; a `VENDORFAKE_SEED` entry
  is refused before the child is spawned, since `.seed` could not describe
  it; there is still no `capabilities=`. Additive — a call without `env=`
  behaves as before (#102, item 20).
* **docs:** *Sharing one unit across tests* in `docs/concepts/chaos-rules-and-faults.md`:
  a session-scoped `served()` or `unit()` against a vendor with single-use
  state (Clover's refresh rotation) needs `reset()` per test, with the
  fixture, what a reset clears (the request log too) and what it does not
  (armed rules, a virtual clock); linked from the served binding, the driver's reset
  bullet, the Playwright and compose recipes and the pytest-plugin page, and
  from `Driver.reset()`'s and `served()`'s own docstrings (#102, item 21).
* **testing:** `seed.token` — the seeded credential a consumer *stores* per
  tenant, under neutral names on every vendor: `Token(access_token,
  refresh_token, tenant_id)`, with `refresh_token` `None` exactly when
  `credentials.grant` is `client_credentials`. `tenant_id` is Clover's
  `merchant_id`, Toast's `restaurant_guid` and Square's `merchant_id` (the
  seller the token belongs to; a location is a parameter of a call, not of
  the credential). On the `Seed` protocol, so a parametrized cross-vendor
  test needs no `Any` escape to build a stored-credential row. **Migration
  for a third-party `SeedingVendor`:** a published seed must now carry
  `token` as well; the hook check names the missing member at unit build
  (#101, item 16).
* **chaos:** every fault publishes its `phase` — `request` (fires instead of
  the handler; nothing commits), `response` (fires on the answer after the
  handler committed; the five transport faults) or `delivery` (the
  `webhook.*` faults) — at `GET /__unit/chaos`, `GET /__unit/info`,
  `vendorfake faults` and `vendorfake explain fault`. The set the pipeline
  applies after the handler is now derived from the catalogue's phase rather
  than written out beside it, so the two cannot disagree (#101, item 17a).
* **kernel:** the request log ties a row back to the journal.
  `committed_journal_seq` is the last journal seq the request committed
  (absent when it committed nothing); `discarded_mutation` (always present)
  is `true` when the handler committed and the caller still did not receive
  its clean answer — a response-phase fault corrupted it, or the fault's own
  params were bad and the caller got the 400 naming the rule. This is how a
  test sees that a `malformed_body` against Clover's refresh spent the
  single-use rotation, without diffing journal sequence numbers (#101, item
  17b).
* **kernel:** a rule-authoring refusal a fault payout raises (a
  `body_mutation` pointer that is not in *this* answer, a `malformed_body`
  mode that does not exist) answers its 400 with `Vendorfake-Rule-Error:
  <rule id>` and, as before, no `Vendorfake-Fault` header — so a consumer
  that ignores bodies can tell "your rule did not apply" from "the vendor
  failed" (#101, item 19).
* **testing:** `unmatched=` on `unit()` / `async_unit()` and on
  `@pytest.mark.vendorfake(...)` is validated against the two policies at
  the call, refusing with a message naming both. Before, any other value —
  `"raise"`, or `True` (a likely slip: `Driver.requests()` has a boolean
  keyword of the same name) — was stored verbatim and silently meant
  `vendor-404`, turning strict mode *off* while the caller believed they had
  turned it on. `vendorfake.testing.checked_unmatched` is the check (#99,
  item 1).

### Behaviour changes

* **testing:** the `timeout` fault means one thing on both clocks. A
  `timeout`-faulted answer carries `Vendorfake-Delay-Ms`, the delay the rule
  asked for, on either clock, and the in-process transport races it against
  the client's read timeout. So `delay_ms: 120000` against a 10 s client
  raises `httpx.ReadTimeout` on a **virtual** clock now, where before it
  answered the 504 the real clock never produced — and a consumer's
  "network error" assertion failed naming an HTTP status. Under the
  threshold a virtual clock still answers at once; a *served* unit on a
  virtual clock still answers the 504, because only a real wait can time a
  client out over a socket (#101, item 18). **Migration:** a virtual-clock
  test that armed a `timeout` past its client's read timeout and asserted a
  504 now sees `ReadTimeout` — which is what the same test asserted on a
  real clock. That includes `started.client` with no `timeout=` of its own:
  the bundled client's read timeout is `CLIENT_TIMEOUT_S` (30 s), so a
  `delay_ms` above 30000 on a virtual clock now raises where it answered.

### Documentation

* **vendorfake:** "Transport faults: what each binding raises" in
  `docs/concepts/chaos-rules-and-faults.md` — the table of the exception each
  binding surfaces for `connection_reset`, `empty_response` and `slow_body`,
  in process and served; the four source comments that cited a README
  "Transport faults" section that never existed now point there (#99, item 2).
  "Phase: does the handler commit?" on the same page, with the single-use
  rotation hazard stated once (#101, item 17c).
* **vendorfake:** the conformance package docstring shows the `-p
  vendorfake.conformance.plugin` flag beside `--pyargs vendorfake.conformance`,
  as every other site already did; `tests/unit/test_docs_claims.py` fails if
  any file names the selection without the flag (#99, item 3).
* **vendorfake:** `AGENTS.md` records the conformance-id allocation — C24
  held by `fix/46`, C25–C32 margin, next free id C36 (#99, item 4).
* **vendorfake:** `docs/start/install.md` — pin tags, not commits, and why a
  commit pin reports the previous release's version (#101, item 15).
  `docs/pytest-plugin.md` — narrowing the marker fixtures' `Seed` under a
  type checker (#101, item 16).

## [0.4.0](https://github.com/konyklabs/vendorfake/compare/v0.3.0...v0.4.0) (2026-09-04)


### Features

* **conformance:** land the coverage stack — C24–C32, mutants M32–M53, inert control-plane reads (konyklabs/roadmap[#15](https://github.com/konyklabs/vendorfake/issues/15)) ([#43](https://github.com/konyklabs/vendorfake/issues/43)) ([81ba77b](https://github.com/konyklabs/vendorfake/commit/81ba77ba935e3cae4b56cfdd00b24e9beb513447))
* **fidelity:** the Square and Toast fidelity legs under D-006 — contract extracts, corpus, pins (konyklabs/roadmap[#55](https://github.com/konyklabs/vendorfake/issues/55)) ([#44](https://github.com/konyklabs/vendorfake/issues/44)) ([1966529](https://github.com/konyklabs/vendorfake/commit/196652984f1afa12bfd301bdf38cb20a56ad24cf))


### Bug Fixes

* **square:** a recorded-empty approval survives the round trip and refuses, never re-grants (konyklabs/roadmap[#28](https://github.com/konyklabs/vendorfake/issues/28)) ([#40](https://github.com/konyklabs/vendorfake/issues/40)) ([f42182b](https://github.com/konyklabs/vendorfake/commit/f42182bf3622648ae5182b1691875f3084287cb9))
* **testing:** hardening round after 0.3 — signer parity, loud served(env=) refusals, scanners and both examples in the self-test (konyklabs/roadmap[#105](https://github.com/konyklabs/vendorfake/issues/105)) ([#45](https://github.com/konyklabs/vendorfake/issues/45)) ([ce97f1a](https://github.com/konyklabs/vendorfake/commit/ce97f1ada092ca2e28cb4d88d6c7539c4f3dce38))
* **toast:** caller-supplied extremes answer the documented 400, not a 500 (konyklabs/roadmap[#41](https://github.com/konyklabs/vendorfake/issues/41)) ([#41](https://github.com/konyklabs/vendorfake/issues/41)) ([eb32dbd](https://github.com/konyklabs/vendorfake/commit/eb32dbd1f5089dfc4ddb37157cdd94c6ea69b4f0))

## [0.3.0](https://github.com/konyklabs/vendorfake/compare/v0.2.0...v0.3.0) (2026-09-03)


### Features

* **testing:** consumer feedback round 2 — fault phase, seed.token, timeout on a virtual clock, rule-error header (konyklabs/roadmap[#101](https://github.com/konyklabs/vendorfake/issues/101)) ([#36](https://github.com/konyklabs/vendorfake/issues/36)) ([45a55e4](https://github.com/konyklabs/vendorfake/commit/45a55e4de0fbcac73e1a741a7d0b40d525577c3b))
* **testing:** served(env=) and the shared-unit reset recipe (konyklabs/roadmap[#102](https://github.com/konyklabs/vendorfake/issues/102)) ([#38](https://github.com/konyklabs/vendorfake/issues/38)) ([efa8f6d](https://github.com/konyklabs/vendorfake/commit/efa8f6d73de806fd06f7ac5fcfd62be701e49145))

## [0.2.0](https://github.com/konyklabs/vendorfake/compare/v0.1.0...v0.2.0) (2026-09-03)

The 0.2.0 notes, hand-written because a release this size needs more than its
commit subjects, and because six of them are migrations a consumer has to act
on rather than facts they can skim. (They sat under an `Unreleased` heading
until 0.2.0 was cut; release-please appended its own 0.2.0 section below them
rather than folding them in, so they were moved here by hand.)

**If you are upgrading from 0.1.0, read these six first.** Each is written out
in full under the heading named beside it.

| What changed | Where |
| --- | --- |
| A bare `StartedUnit` annotation now fails `mypy --strict` | Features, typed seeds |
| A `timeout` fault past the client's read timeout raises instead of answering 504 | Behaviour changes |
| `unit()` now honours `VENDORFAKE_PROFILE` in the `env=` you pass it | Behaviour changes |
| The `unit_error` sidecar moved from the response body to headers | Breaking changes |
| An unmatched in-process request now raises instead of returning the vendor's 404 | Breaking changes |
| The conformance plugin is no longer auto-loaded into your pytest run | Breaking changes |

### Features

* **testing:** the vendor name narrows the seed. `unit()` and `served()` are
  overloaded on the vendor literal, so `unit("clover")` yields a
  `StartedUnit[CloverSeed]` rather than a union a consumer had to unpick with
  an `isinstance` per vendor. `Driver`, `StartedUnit` and `ServedUnit` are
  generic in their seed; written bare they are `[Any]` under mypy's default
  settings and under pyright/basedpyright in standard mode, which is what a
  v0.1.0 fixture annotation already meant. **Migration for a consumer running
  `mypy --strict`:** `disallow_any_generics` turns a bare annotation into a
  hard error -- `def f(s: StartedUnit) -> None` now fails with `Missing type
  arguments for generic type "StartedUnit"  [type-arg]` -- on a fixture the
  consumer did not change. Parameterise it (`Iterator[StartedUnit[SquareSeed]]`)
  or write `StartedUnit[Any]` to keep the old, unnarrowed meaning explicitly;
  either way nothing about the object handed back at runtime changes, only
  what a strict checker will accept as its declared type.
* **testing:** `seed.credentials` reports the application credential under
  neutral names (`app_id`, `app_secret`) plus the token lifecycle the vendor
  runs (`grant`: `refresh_token` for Square and Clover, `client_credentials`
  for Toast), so one parametrized test can authenticate against all three. No
  existing seed field was renamed or removed.
* **testing:** `vendorfake.testing.Seed` is the structural type all three
  seeds satisfy — `credentials`, `auth`, `read_only_auth`, `event_types` —
  and the seed type a vendor that is a plain `str` yields.
* **testing:** a vendor from the `vendorfake.vendors` entry-point group can
  publish its own seed. Implement `seed(vendor_config)` — the optional
  `vendorfake.core.kernel.types.SeedingVendor` protocol, discovered
  structurally, so no existing `VendorDefinition` has to change — and
  `unit("<its name>").seed` hands back that object instead of raising
  `LookupError`. It must satisfy `vendorfake.testing.Seed`; one that does not
  is refused by name when the unit is built, rather than surfacing as an
  `AttributeError` inside a consumer's test. `seed_for` takes an optional
  `definition=` for a caller that already holds one (konyklabs/roadmap#74)
* **core:** record every request in a bounded in-memory log, distinct from the
  journal, and publish it at `GET /__unit/requests` (filters: `operation_id`,
  `route`, `unmatched`, `limit`), `DELETE /__unit/requests` and
  `GET /__unit/requests/unmatched/near-misses`. The journal records committed
  mutations by design, so a read, a 4xx or a call that matched no route left no
  trace anywhere. Capacity comes from the profile (`requests: {capacity: N}`,
  default 10,000) or `VENDORFAKE_REQUEST_LOG_CAPACITY`; bodies and headers are
  not stored; control-plane requests are not recorded; `reset()` clears it.
* **core:** answer an unmatched request with a `Vendorfake-Near-Miss` header
  naming the three closest routes of the active capability set, ranked
  deterministically. The vendor-shaped 404 body is unchanged.
* **testing:** `Driver.requests()`, `Driver.assert_called(operation_id, times=,
  at_least=)` — whose failure lists every operation that *was* called, with
  counts — and `Driver.clear_requests()`.
* **testing:** `VENDORFAKE_CLOCK_START` and `unit()`/`served()`'s `clock_start=`
  pin the virtual clock's start instant, so two units built from the same
  `clock_start` agree on every expiry to the second (konyklabs/roadmap#71).
  Requires `clock.mode="virtual"`; setting it against a real clock is now a
  loud refusal rather than a silent no-op, and the value must carry a timezone
  — a naive instant or a bare date is refused, because neither names the same
  moment on two machines. `Driver.clock() -> ClockInfo` reads a unit's current
  mode and instant off `/__unit/info`.
* **core:** the `unit_error` sidecar now defaults to riding as response
  headers (`Vendorfake-Error-Kind`, `Vendorfake-Status-Provenance`,
  `Vendorfake-Error-Field`, `Vendorfake-Error-Info`) instead of a
  `unit_error` body key, so a vendor's default-profile body is byte-for-byte
  what the real vendor would send (konyklabs/roadmap#71). `errors.sidecar`
  (profile) / `VENDORFAKE_ERROR_SIDECAR` (env) selects `"headers"` (default),
  `"body"` or `"both"`. Header values are ASCII-safe: `Vendorfake-Error-Info`
  escapes non-ASCII, `Vendorfake-Error-Field` is percent-encoded. See
  **Breaking changes** for what a consumer reading the body must do.
* **testing:** a new, minimal `pytest11` entry point, `vendorfake` (module
  `vendorfake.pytest`), replaces `vendorfake_conformance` as what installing
  the wheel auto-loads into a consumer's pytest run. It exposes only the
  `vendorfake` marker and the `vendorfake_unit`, `vendorfake_async_unit` and
  `vendorfake_webhook_receiver` fixtures -- no `--conformance-*` options, no
  session hook. The marker takes `vendor`, `profile`, `env`, `seed`,
  `clock_start`, `unmatched` and `capabilities`. The conformance suite's
  pytest form still exists, and now needs both flags together:
  `pytest --pyargs vendorfake.conformance -p vendorfake.conformance.plugin`.
  The two are not alternatives — `-p` is what loads the plugin (and so
  registers `--conformance-target` and the rest), while `--pyargs
  vendorfake.conformance` only selects the tests, which without the plugin
  fail at fixture resolution. This is the form README and `tools/self-test.sh`
  both use (konyklabs/roadmap#71).
* **pytest:** the `vendorfake_async_unit` fixture, driven by the same
  `@pytest.mark.vendorfake(vendor, ...)` marker. It is a *synchronous* fixture
  yielding an object that owns an async client, which is what makes it work
  under pytest-asyncio (strict and auto) and under anyio's plugin without
  vendorfake depending on either: an `async def` fixture would need each
  runner's own decorator, and picking one would break the other's users at
  collection time.
* **testing:** `UnitTransport` now implements both `httpx.BaseTransport` and
  `httpx.AsyncBaseTransport`, so one instance drives an `httpx.Client` and an
  `httpx.AsyncClient` over the same unit. `StartedUnit.async_client` is that
  client, built on first access; `vendorfake.testing.async_unit()` is `unit()`
  as an async context manager, with the same four overloads. An async consumer
  no longer writes ASGI wiring per vendor against the internal
  `vendorfake.asgi`.
* **core:** `UnitResponse` gains `delay_ms` (default `0`, additive).
* **core:** five transport-fidelity faults -- `malformed_body`,
  `body_mutation`, `connection_reset`, `empty_response`, `slow_body` -- for
  "the vendor returned garbage", which no vendor documents and no other fault
  can produce: an HTML error page behind a 502, invalid JSON, a 200 missing
  its token, a documented field retyped to something else, a connection that
  drops mid-response, a body that streams in slowly. `UnitResponse` gains
  `transport: TransportDirective | None` (default `None`, additive) for the
  three that are not a response at all; the in-process transport and the ASGI
  binding each interpret it in the terms of the caller they hold.
  `core/chaos/rules.py`'s `FaultSpec` gains `provenance: "vendor" | "transport"`,
  published at `GET /__unit/chaos` and `GET /__unit/info` (and so in
  `vendorfake info`'s output), distinguishing these five from every fault that
  came before them. Every faulted response, old kinds included, now carries
  `Vendorfake-Fault` and `Vendorfake-Rule` headers. `provenance` is
  keyword-only with a default of `"vendor"`, so it is purely additive: a
  fork's existing three- or four-positional `FaultSpec(...)` construction
  (`name, scope, summary[, params]`) keeps its v0.1.0 meaning rather than
  silently binding its `params` prose to `provenance`. `slow_body` races a
  client's read timeout on the single gap between two chunks, not their sum,
  because that is what httpx's read timeout actually measures — a client that
  tolerates each gap never times out however long the whole transfer takes,
  and the in-process transport matches the served one so a test green in one
  is green in the other. When a response-phase fault hits an idempotent
  request, the key stores the handler's clean answer, recorded before the
  fault touches it, so a retry replays the payment the vendor already took
  instead of taking a second one. A response-phase fault armed for a request applies to
  the replay of an idempotent key too, exactly as it does to a fresh answer: a
  vendor's network does not know a request is a retry, and a second dropped
  connection is what a robust client has to survive. So a rule's `times`
  budget counts only the faults the caller actually observed, and the request
  log's `fault` column matches the response the caller got rather than the
  decision the pipeline drew. The stored idempotency record is still never
  touched by a fault, because a replay does not store.
* **vendorfake:** discover profiles and routes by code — `registry.available_profiles`, `registry.routes`, `Driver.route_for`/`path_for`, and a per-vendor `paths` module of hand-written path constants kept honest against the router by `tests/unit/test_paths_drift.py` (konyklabs/roadmap#70)
* **vendorfake:** add `VendorDefinition.roles`, the neutral capability-role vocabulary (`auth`, `orders`, `webhooks`, `chaos`) every vendor maps to its own capability names, published at `GET /__unit/info` under `vendor.roles`. It is a required member
  of the `VendorDefinition` protocol, so it is also a **breaking change for a
  third-party entry-point vendor built against 0.1.0** — see **Breaking
  changes** (konyklabs/roadmap#70)
* **vendorfake:** `create_unit`/`unit()` accept `capabilities=[...]` — role names or a vendor's own capability names — and resolve to the narrowest shipped profile that is a superset, or `full` plus an absolute list when none qualifies; passing `profile=` and `capabilities=` together, or an empty `capabilities=[]`, is a `ValueError`. `GET /__unit/info` echoes the request back under `requested_capabilities` (konyklabs/roadmap#70)
* **vendorfake:** the package root re-exports `available_profiles` and
  `routes` alongside `available_vendors`, `create_unit` and `resolve_vendor`,
  so discovering what a vendor ships and building a unit from it are one
  import. `from vendorfake.registry import ...` is unchanged and is not
  deprecated (konyklabs/roadmap#74)
* **cli:** add `--json`, accepted both before and after the subcommand (`vendorfake --json profiles` and `vendorfake profiles --json` are the same request; a no-op where a subcommand already prints JSON) and three subcommands — `vendorfake profiles`, `vendorfake routes`, `vendorfake faults` — plus `vendorfake vendors --json` (konyklabs/roadmap#70)
* **cli:** add `vendorfake agent-setup`, which writes a Claude Code rules file (`<dir>/.claude/rules/vendorfake.md`, scoped by `paths:` to `--tests-glob`, default `tests/**`) naming how to start a unit, the pytest fixtures, and the vocabulary an agent writing or fixing a consumer's tests needs — `--dir`, `--force`, and `--mcp`/`--allow-future` to also merge a `vendorfake` entry into `<dir>/.mcp.json` (a notice only, until the `vendorfake mcp` server itself ships in 0.4). See `docs/for-agents.md` for the same contract in full (konyklabs/roadmap#74)
* **cli:** add `vendorfake explain <route|fault|profile|error|header> <name>`, which answers one question about a unit's surface — a route's method/path/auth by `operation_id`, a chaos fault's params and provenance, a profile's capabilities and seed, a core error kind's status and body, or a `Vendorfake-*` response header's meaning — through the same control-plane and catalogue lookups `vendorfake routes`/`faults`/`profiles` already use, with `--vendor`, `--profile` and `--json` (konyklabs/roadmap#74)
* **conformance:** add C33 (an unmatched request is named and recorded), C34
  (every vendor maps all four capability roles to a declared capability) and
  C35 (the profile-name contract holds: every vendor ships all six of `full`,
  `oauth-only`, `orders-only`, `no-chaos`, `no-faults`, `chaos-demo`,
  published at `GET /__unit/info` under `vendor.profiles`, and the profile a
  unit was built on honours what its name promises)
  (konyklabs/roadmap#70, konyklabs/roadmap#72)

### Behaviour changes

* **testing:** `Driver.seed` is no longer `Optional`. It was `None` for any
  vendor with no seed, which every consumer paid for with a guard on a value
  that is present for all three shipped vendors. `unit()` and `served()` now
  raise `LookupError` naming the vendor and profile instead. **Breaking for a
  consumer relying on `seed is None`**: a vendor from the entry-point group
  that publishes no seed can now publish one (see the `SeedingVendor` hook
  under Features), and until it does, drive it with `create_unit()` rather
  than `unit()`.
* **core:** the `timeout` chaos fault no longer calls `time.sleep` inside the
  kernel. On a real clock it reports the delay on the response and each
  binding carries it out: the in-process transport raises `httpx.ReadTimeout`
  **without waiting** when the delay exceeds the client's read timeout, and
  waits for real when it does not; the ASGI application awaits it, so served
  mode is unchanged from a client's point of view; the file-drop binding waits
  before writing the response document. Virtual-clock mode is unchanged --
  scenario time moves and the answer is immediate.

  What a consumer gains is the case that was previously unreachable in
  process: a client-side timeout, and therefore a rehearsal of their retry
  path, without starting a server. What changes for an existing consumer,
  stated plainly: a `timeout` fault whose `delay_ms` exceeds the client's read
  timeout now raises `httpx.ReadTimeout` instead of answering a 504. For the
  built-in client -- `StartedUnit.client` and `StartedUnit.async_client`, what
  `unit()` and `async_unit()` hand back -- that threshold is
  `vendorfake.testing.CLIENT_TIMEOUT_S` (30 s), now passed to both explicitly
  rather than left to httpx's own 5 s default. A consumer who needs the 504
  answer for a longer delay passes a client built with a longer timeout, or
  arms a shorter `delay_ms`. The raw in-process seam
  (`vendorfake.core.transport.inprocess.InProcessClient`) holds no caller and
  so takes no delay at all; the delay is readable there on
  `response.raw.delay_ms`. Status, body and headers are unchanged everywhere.
* **testing:** `UnitTransport` now reads `request.extensions["timeout"]`. It
  is still consulted for nothing except a deliberate `delay_ms`; an ordinary
  in-process call still cannot be interrupted by a timeout.
* **testing:** `unit()`'s `profile` argument now resolves the same three-step
  precedence `create_unit` documents — an explicit `profile=` argument, then
  `VENDORFAKE_PROFILE` in the `env=` mapping given to that call, then `full`
  — where v0.1.0 passed the literal string `"full"` and so never read
  `VENDORFAKE_PROFILE` from an `env=` mapping passed to `unit()` at all.
  **Migration:** a caller who builds one `env` mapping for a whole test module
  and passes it to both `served()` (a real environment) and `unit()` will now
  see `unit()` honour `VENDORFAKE_PROFILE` in it too. If that mapping was
  meant only for the served unit, pass `profile=` explicitly to `unit()` or
  drop the key from the mapping it gets. `served()` is deliberately
  asymmetric: it keeps `profile: str = "full"`, takes no `capabilities=`, and
  does not read `VENDORFAKE_PROFILE` out of an `env=` mapping
  (konyklabs/roadmap#70)
* **cli:** a startup failure is now a one-line refusal rather than a
  traceback, for every subcommand that builds a unit (`serve`, `info`,
  `openapi`, `routes`, `profiles`). A nonexistent `--profile` raised a raw
  `UnitError` out of the profile loader while the adjacent `--vendor` flag —
  the same kind of typo — was already a clean message; both now read the same
  way, and the loader's message already names every profile the vendor ships.
  The exit code is 1, which is what every other refusal in the CLI already
  uses (konyklabs/roadmap#74)

### Breaking changes

* **testing:** an in-process unit (`unit()`) now raises
  `vendorfake.testing.UnmatchedRequest` — an `AssertionError` — for a request no
  route matched, where v0.1.0 returned the vendor's 404. **Migration:** a test
  that deliberately calls an unmodelled path opts out with
  `unit(..., unmatched="vendor-404")`, with `VENDORFAKE_UNMATCHED=vendor-404`,
  or with `unmatched: {"policy": "vendor-404"}` in its profile. Served units
  (`served()`, `serve_in_thread()`, the container) are unaffected and never
  raise: they stand in for the vendor and answer as the vendor would. A 404
  from a route that did match — an id that does not exist — is unaffected on
  every binding.
* **core:** a consumer reading `unit_error` out of a vendor's response body
  under the default profile no longer finds it there -- it is in the
  `Vendorfake-*` headers instead. **Migration:** read
  `response.headers["Vendorfake-Error-Kind"]` and its three siblings, or set
  `"errors": {"sidecar": "body"}` in your profile or
  `VENDORFAKE_ERROR_SIDECAR=body` to keep the v0.1 body key for this minor
  release; `"both"` keeps both while you move. `Vendorfake-Error-Info` is
  JSON, ASCII-escaped; `Vendorfake-Error-Field` is percent-encoded and needs
  `urllib.parse.unquote`.
* **testing:** a consumer whose pytest run relied on the `vendorfake_conformance`
  `pytest11` entry point being auto-loaded (its `--conformance-*` options, or
  its `pytest_sessionfinish` cross-profile check) must now load it explicitly:
  **`-p vendorfake.conformance.plugin`**, in the command line or in
  `addopts`. `vendorfake-conformance` (the CLI) and
  `python -m vendorfake.conformance` are unaffected. What installing the wheel
  auto-loads is now the small `vendorfake` plugin instead.
* **core:** `VendorDefinition.roles` is a new **required** member of the
  `VendorDefinition` protocol (see Features). **Breaking for a third-party
  vendor registered through the `vendorfake.vendors` entry-point group and
  written against v0.1.0**, which has no `roles` attribute at all. **How it
  shows up:** `GET /__unit/info` now publishes `vendor.roles`, and the CLI's
  `vendorfake info`, `Driver.clock()` and the conformance runner all call that
  route as a matter of course; `create_unit(capabilities=[...])` translates
  role names through the same mapping. **Migration:** implement `roles` on the
  vendor definition — a `Mapping[str, str]` taking each of `auth`, `orders`,
  `webhooks` and `chaos` to one of that vendor's own declared capability
  names, as the three shipped vendors do (`square/vendor.py`,
  `clover/vendor.py`, `toast/vendor.py`). Conformance C34 checks the mapping is
  complete and that every value names a capability the vendor really declares.
  Until then the two runtime read sites are tolerant rather than fatal:
  `GET /__unit/info` publishes `vendor.roles` as `{}` (so C34 reports the real
  defect against a unit that still answers) and `create_unit(capabilities=...)`
  raises a `ValueError` naming the vendor and the role, instead of an
  `AttributeError` from inside the registry. Vendors written against 0.2 are
  unaffected, and no consumer-facing call signature changed.

### Deprecations

* **core:** the body-riding `unit_error` sidecar — `"errors": {"sidecar":
  "body"}` and `VENDORFAKE_ERROR_SIDECAR=body`. It keeps working for this
  minor release so a consumer has somewhere to stand while migrating, and may
  be removed in a future one. The headers are the supported form; see
  **Breaking changes** above for how to read them.
* **toast:** `vendorfake.toast.surface.auth.LOGIN_PATH` is a deprecated alias
  of `vendorfake.toast.paths.LOGIN`. The alias is a courtesy for v0.1.0 code
  that imported it, and it emits no warning because a module-level constant
  cannot; `vendorfake.toast.surface` is internal either way (see
  `docs/api-contract.md`), and `vendorfake.toast.paths` is the public home for
  path constants.

### Dependencies

* `anyio` is now declared directly (it was already installed as an
  unconditional dependency of `httpx`); `vendorfake.testing.transport` imports
  it so the async wait is meant to work under any anyio backend rather than
  tying this transport to one of them; exercised on asyncio in this suite.
* `pytest-asyncio` added to the dev group, used only to run a consumer's suite
  under it inside `pytester`.

### Documentation

* **vendorfake:** `docs/api-contract.md` states the public API contract: which
  modules and surfaces are public, which are internal and may change in any
  release, the stability of the white-box handles (`started.unit`,
  `started.unit.context.store` — documented, may change between minors), and
  the deprecation policy the Deprecations heading above follows.
  `tests/unit/test_public_api.py` pins the exported names of every public
  module against a checked-in list, so widening or narrowing the surface is an
  edit a reviewer sees (konyklabs/roadmap#74)
* **vendorfake:** the profile-name contract, as it actually holds across all three shipped vendors: `orders-only` does NOT enable role `auth` (every shipped profile of that name promises "no OAuth dance, authenticate with a seeded token", pinned by each vendor's own tests) and `no-chaos` keeps role `chaos` enabled, switching off only `webhooks.chaos` (`no-faults` is the profile that switches off both). Documented in `src/vendorfake/conformance/checks/discovery.py` and the README's new "Discovering profiles and routes" section (konyklabs/roadmap#70)

### Commits (release-please)

#### Features

* **testing:** the 0.2 consumer-experience batch — async seam, typed seeds, discovery, hygiene, strict mode, transport faults (konyklabs/roadmap[#67](https://github.com/konyklabs/vendorfake/issues/67)) ([#35](https://github.com/konyklabs/vendorfake/issues/35)) ([be0f6aa](https://github.com/konyklabs/vendorfake/commit/be0f6aad681880e96cca9b748c54698a13e81c8a))


#### Documentation

* **readme:** a first screen that starts in sixty seconds; seeded matrix moved to docs (konyklabs/roadmap[#59](https://github.com/konyklabs/vendorfake/issues/59)) ([#33](https://github.com/konyklabs/vendorfake/issues/33)) ([50c5efa](https://github.com/konyklabs/vendorfake/commit/50c5efaae9f388ae78774abe949f4668eb0483d1))

## 0.1.0 (2026-09-01)


### Features

* **asgi:** add the FastAPI transport adapter as the only framework importer (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#10](https://github.com/konyklabs/vendorfake/issues/10)) ([cc138f2](https://github.com/konyklabs/vendorfake/commit/cc138f2af90305062a69e8366eb7914aa2114fd4))
* **clover:** add the Clover vendor foundation and OAuth v2 surface (konyklabs/roadmap[#34](https://github.com/konyklabs/vendorfake/issues/34)) ([#22](https://github.com/konyklabs/vendorfake/issues/22)) ([58f5e32](https://github.com/konyklabs/vendorfake/commit/58f5e32d62d6858989d3bb23b4eeec3169ffdd8d))
* **clover:** ship the Clover vendor — orders, inventory, customers, payments, webhooks, seed and profiles (konyklabs/roadmap[#34](https://github.com/konyklabs/vendorfake/issues/34)) ([#25](https://github.com/konyklabs/vendorfake/issues/25)) ([dc95be2](https://github.com/konyklabs/vendorfake/commit/dc95be236f664421945aace86310e47d81b746c4))
* **core:** add the control plane and the file-drop binding (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#9](https://github.com/konyklabs/vendorfake/issues/9)) ([41c8c8c](https://github.com/konyklabs/vendorfake/commit/41c8c8cfe8d9fd8e1e3c5fd3ec84fd58455afe56))
* **core:** add the router, the request pipeline and the in-process binding (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#7](https://github.com/konyklabs/vendorfake/issues/7)) ([69ff62e](https://github.com/konyklabs/vendorfake/commit/69ff62eb3c0475c0d127be9657fb4f73a1a1be59))
* **core:** add the webhook dispatcher with vendor-neutral delivery metadata (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#8](https://github.com/konyklabs/vendorfake/issues/8)) ([ca29fa2](https://github.com/konyklabs/vendorfake/commit/ca29fa235b3621dca597ed4d5140fbc689927c72))
* **square:** add the OAuth surface and retire the first test's xfail (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#12](https://github.com/konyklabs/vendorfake/issues/12)) ([b3d0c96](https://github.com/konyklabs/vendorfake/commit/b3d0c963779bbe4a7b81dab68e735b2af3dc9e5b))
* **square:** add the orders surface with sparse-update semantics (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#13](https://github.com/konyklabs/vendorfake/issues/13)) ([68917bb](https://github.com/konyklabs/vendorfake/commit/68917bb57f8bce8207d13b161799d2041f1db99e))
* **square:** add the vendor foundation, error table and order model (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#11](https://github.com/konyklabs/vendorfake/issues/11)) ([2e7c7a6](https://github.com/konyklabs/vendorfake/commit/2e7c7a635274491f9805ac59d3703f0901445bcc))
* **square:** close the consumer-driven endpoint gaps — merchants, catalog, payments, loyalty, inventory (konyklabs/roadmap[#36](https://github.com/konyklabs/vendorfake/issues/36)) ([#24](https://github.com/konyklabs/vendorfake/issues/24)) ([a6038d6](https://github.com/konyklabs/vendorfake/commit/a6038d69bb29375645b3237a4007239ee7c199a5))
* **testing:** ship the consumer path — fixtures, container and runnable examples (konyklabs/roadmap[#14](https://github.com/konyklabs/vendorfake/issues/14), [#17](https://github.com/konyklabs/vendorfake/issues/17)) ([#29](https://github.com/konyklabs/vendorfake/issues/29)) ([d701995](https://github.com/konyklabs/vendorfake/commit/d70199535ceec3d17c5541bdc77aa3754e99c572))
* **testing:** ship Toast's conformance target and seed, and correct drain's contract (konyklabs/roadmap[#14](https://github.com/konyklabs/vendorfake/issues/14)) ([#31](https://github.com/konyklabs/vendorfake/issues/31)) ([d2df5b6](https://github.com/konyklabs/vendorfake/commit/d2df5b692b1c58d17d1148f1a3195017a7170fdf))
* **toast:** add the Toast vendor — auth, menus, orders, payments, stock and webhooks (konyklabs/roadmap[#39](https://github.com/konyklabs/vendorfake/issues/39)) ([#30](https://github.com/konyklabs/vendorfake/issues/30)) ([eca40ad](https://github.com/konyklabs/vendorfake/commit/eca40addf551beed568e170b01987c2c894149ba))
* **vendorfake:** add the capability registry and deterministic chaos engine (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#6](https://github.com/konyklabs/vendorfake/issues/6)) ([33ccba0](https://github.com/konyklabs/vendorfake/commit/33ccba0f2b8f7d5813d5a570e05eddcce140294b))
* **vendorfake:** add the conformance suite, hardened against an adversarial pass (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#17](https://github.com/konyklabs/vendorfake/issues/17)) ([e56e42f](https://github.com/konyklabs/vendorfake/commit/e56e42f049501d89033562817ce1442246c4ed21))
* **vendorfake:** add the core foundation and journal-backed state engine (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#5](https://github.com/konyklabs/vendorfake/issues/5)) ([1ae5be0](https://github.com/konyklabs/vendorfake/commit/1ae5be03d4ece5bfae04ad1a02ef975c24f0c302))
* **vendorfake:** add the Square seed, profiles and end-to-end wiring (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#19](https://github.com/konyklabs/vendorfake/issues/19)) ([88ebaae](https://github.com/konyklabs/vendorfake/commit/88ebaae04c53ce7a5259632ef286b9764f8812b0))
* **vendorfake:** add the Square webhook surface, signer and retry schedule (konyklabs/roadmap[#10](https://github.com/konyklabs/vendorfake/issues/10)) ([#14](https://github.com/konyklabs/vendorfake/issues/14)) ([a04ec17](https://github.com/konyklabs/vendorfake/commit/a04ec17c7477563cfe39048a2d0e2ea728d5e1b0))


### Bug Fixes

* **core:** expose repeated query parameters to handlers (konyklabs/roadmap[#37](https://github.com/konyklabs/vendorfake/issues/37)) ([#23](https://github.com/konyklabs/vendorfake/issues/23)) ([8c393d5](https://github.com/konyklabs/vendorfake/commit/8c393d57f9f2a298dfae5ffe5b73b96ff74b4a60))


### Documentation

* **readme:** add verified quickstart and release plumbing (konyklabs/roadmap[#33](https://github.com/konyklabs/vendorfake/issues/33)) ([#20](https://github.com/konyklabs/vendorfake/issues/20)) ([8615347](https://github.com/konyklabs/vendorfake/commit/86153478f1c03ff206d94c41110b9a25285e6422))
* **vendorfake:** add README with unofficial disclaimer and design pointers (konyklabs/roadmap[#7](https://github.com/konyklabs/vendorfake/issues/7)) ([#2](https://github.com/konyklabs/vendorfake/issues/2)) ([dd292c9](https://github.com/konyklabs/vendorfake/commit/dd292c934dafe919f58227a2fa39bb06c66c2f27))
