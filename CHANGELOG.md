# Changelog

## Unreleased

A placeholder heading for release-please to fold into the release notes; the
entries below are hand-written because the behaviour change needs more than a
commit subject.

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
  loud refusal rather than a silent no-op. `Driver.clock() -> ClockInfo` reads
  a unit's current mode and instant off `/__unit/info`.
* **core:** the `unit_error` sidecar now defaults to riding as response
  headers (`Vendorfake-Error-Kind`, `Vendorfake-Status-Provenance`,
  `Vendorfake-Error-Field`, `Vendorfake-Error-Info`) instead of a
  `unit_error` body key, so a vendor's default-profile body is byte-for-byte
  what the real vendor would send (konyklabs/roadmap#71). `errors.sidecar`
  (profile) / `VENDORFAKE_ERROR_SIDECAR` (env) selects `"headers"` (default),
  `"body"` or `"both"`.
* **testing:** a new, minimal `pytest11` entry point, `vendorfake` (module
  `vendorfake.pytest`), replaces `vendorfake_conformance` as what installing
  the wheel auto-loads into a consumer's pytest run. It exposes only the
  `vendorfake` marker and the `vendorfake_unit` / `vendorfake_webhook_receiver`
  fixtures -- no `--conformance-*` options, no session hook. The conformance
  suite's pytest form still exists; run it with `-p vendorfake.conformance.plugin`
  or `pytest --pyargs vendorfake.conformance` (konyklabs/roadmap#71).


* **testing:** `UnitTransport` now implements both `httpx.BaseTransport` and
  `httpx.AsyncBaseTransport`, so one instance drives an `httpx.Client` and an
  `httpx.AsyncClient` over the same unit. `StartedUnit.async_client` is that
  client, built on first access; `vendorfake.testing.async_unit()` is `unit()`
  as an async context manager. An async consumer no longer writes ASGI wiring
  per vendor against the internal `vendorfake.asgi`.
* **pytest:** a `vendorfake_async_unit` fixture, registered through the
  `pytest11` entry point and driven by `@pytest.mark.vendorfake(vendor, ...)`.
  It is a synchronous fixture yielding an object that owns an async client, so
  it works under pytest-asyncio (strict and auto) and under anyio's plugin
  without vendorfake depending on either.
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
  `Vendorfake-Fault` and `Vendorfake-Rule` headers.


* **vendorfake:** discover profiles and routes by code — `registry.available_profiles`, `registry.routes`, `Driver.route_for`/`path_for`, and a per-vendor `paths` module of hand-written path constants kept honest against the router by `tests/unit/test_paths_drift.py` (konyklabs/roadmap#70)
* **vendorfake:** add `VendorDefinition.roles`, the neutral capability-role vocabulary (`auth`, `orders`, `webhooks`, `chaos`) every vendor maps to its own capability names, published at `GET /__unit/info` under `vendor.roles` (konyklabs/roadmap#70)
* **vendorfake:** `create_unit`/`unit()` accept `capabilities=[...]` — role names or a vendor's own capability names — and resolve to the narrowest shipped profile that is a superset, or `full` plus an absolute list when none qualifies; passing `profile=` and `capabilities=` together, or an empty `capabilities=[]`, is a `ValueError`. `GET /__unit/info` echoes the request back under `requested_capabilities` (konyklabs/roadmap#70)
* **cli:** add `--json`, accepted both before and after the subcommand (`vendorfake --json profiles` and `vendorfake profiles --json` are the same request; a no-op where a subcommand already prints JSON) and three subcommands — `vendorfake profiles`, `vendorfake routes`, `vendorfake faults` — plus `vendorfake vendors --json` (konyklabs/roadmap#70)
* **conformance:** add C34 (every vendor maps all four capability roles to a declared capability) and C35 (the profile-name contract holds: every vendor ships all six of `full`, `oauth-only`, `orders-only`, `no-chaos`, `no-faults`, `chaos-demo`, published at `GET /__unit/info` under `vendor.profiles`, and the profile a unit was built on honours what its name promises) (konyklabs/roadmap#70)

### Behaviour changes



* **testing:** `Driver.seed` is no longer `Optional`. It was `None` for any
  vendor with no seed, which every consumer paid for with a guard on a value
  that is present for all three shipped vendors. `unit()` and `served()` now
  raise `LookupError` naming the vendor and profile instead. **Breaking for a
  consumer relying on `seed is None`**: a vendor from the entry-point group
  that publishes no seed must be driven with `create_unit()` rather than
  `unit()`.


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

### Breaking changes



* **testing:** an in-process unit (`unit()`) now raises
  `vendorfake.testing.UnmatchedRequest` — an `AssertionError` — for a request no
  route matched, where v0.1.0 returned the vendor's 404. A test that
  deliberately calls an unmodelled path opts out with
  `unit(..., unmatched="vendor-404")`, with `VENDORFAKE_UNMATCHED=vendor-404`,
  or with `unmatched: {"policy": "vendor-404"}` in its profile. Served units
  (`served()`, `serve_in_thread()`, the container) are unaffected and never
  raise: they stand in for the vendor and answer as the vendor would. A 404
  from a route that did match — an id that does not exist — is unaffected on
  every binding.
* **core:** a consumer reading `unit_error` out of a vendor's response body
  under the default profile no longer finds it there -- it is in the
  `Vendorfake-*` headers instead. Set `"errors": {"sidecar": "body"}` in your
  profile, or `VENDORFAKE_ERROR_SIDECAR=body`, to keep the v0.1 body key for
  this minor release; `"both"` keeps both. The body-riding form is
  **DEPRECATED** and may be removed in a future minor release.
* **testing:** a consumer whose pytest run relied on the `vendorfake_conformance`
  `pytest11` entry point being auto-loaded (its `--conformance-*` options, or
  its `pytest_sessionfinish` cross-profile check) must now load it explicitly:
  `-p vendorfake.conformance.plugin`. `vendorfake-conformance` (the CLI) and
  `python -m vendorfake.conformance` are unaffected.

### Dependencies



* `anyio` is now declared directly (it was already installed as an
  unconditional dependency of `httpx`); `vendorfake.testing.transport` imports
  it so the async wait works under trio as well as asyncio.
* `pytest-asyncio` added to the dev group, used only to run a consumer's suite
  under it inside `pytester`.

### Bug Fixes


* **vendorfake:** `unit()`'s `profile` argument now resolves the same three-step precedence `create_unit` documents — an explicit `profile=` argument, then `VENDORFAKE_PROFILE` in the `env=` mapping given to that call, then `full` — where v0.1.0 passed the literal string `"full"` and so never read `VENDORFAKE_PROFILE` from an `env=` mapping passed to `unit()` at all. **Behaviour change:** a caller who builds one `env` mapping for a whole test module and passes it to both `served()` (a real environment) and `unit()` will now see `unit()` honour `VENDORFAKE_PROFILE` in it too, where v0.1.0 silently ignored the variable for this call (konyklabs/roadmap#70)

### Documentation


* **vendorfake:** the profile-name contract, as it actually holds across all three shipped vendors: `orders-only` does NOT enable role `auth` (every shipped profile of that name promises "no OAuth dance, authenticate with a seeded token", pinned by each vendor's own tests) and `no-chaos` keeps role `chaos` enabled, switching off only `webhooks.chaos` (`no-faults` is the profile that switches off both). Documented in `src/vendorfake/conformance/checks/discovery.py` and the README's new "Discovering profiles and routes" section (konyklabs/roadmap#70)


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
