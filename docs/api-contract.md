# The public API contract

What vendorfake promises to keep working, what it reserves the right to change,
and how a change that has to happen is announced.

This page answers one question: **if I import it, will it still be there next
release?** Everything called public below is covered by the deprecation policy
at the bottom. Everything called internal can change in any release, without a
deprecation and without a changelog entry.

The reason the line is drawn explicitly rather than left to whatever happens to
be importable: a fake is only worth using if a test written against it survives
an upgrade, and a surface that became public by accident — reachable, so
somebody imported it — is a surface nobody can ever change. So the line is
written here in prose and pinned in `tests/unit/test_public_api.py`, which
holds the exported names of every public module as a checked-in list. Widening
or narrowing the surface fails that test until the list is edited, and that
edit is the review trigger.

## Public

### `vendorfake` — the package root

Five names, re-exported from `vendorfake.registry` because discovering what
exists and building one are a single task:

| Name | What it is |
| --- | --- |
| `available_vendors()` | Every vendor name that can actually be loaded |
| `available_profiles(vendor)` | Every profile a vendor ships, with its summary, capabilities and seed |
| `routes(vendor, profile)` | The route table a profile serves |
| `create_unit(...)` | The one constructor: a name and a profile in, a running `Unit` out |
| `resolve_vendor(name)` | A name to a `VendorDefinition`, refusing a typo by listing the real ones |

`__version__` is the version of the code that is imported, which is not always
what `importlib.metadata` reports — the two disagree in a source checkout.

Both spellings work and neither is deprecated: `from vendorfake import
create_unit` and `from vendorfake.registry import create_unit` are the same
function.

### `vendorfake.testing` — what most consumers import

The binding layer: `unit()`, `async_unit()`, `served()`, `serve_in_thread()`
and `webhook_receiver()`; the `Driver`, `StartedUnit`, `ServedUnit`,
`WebhookReceiver` and `Delivery` handles; `UnitTransport`; the
`UnmatchedRequest` assertion error and `checked_unmatched`, the validation
`unit()` applies to its `unmatched=` argument; `ClockInfo` and `RouteInfo`;
the `Seed` protocol, the four per-vendor seed types, `Credentials` and
`Token`; the four per-vendor seed-overlay types (`SquareSeedOverlay`,
`CloverSeedOverlay`, `ToastSeedOverlay`, `LightspeedSeedOverlay`) and the
untyped `SeedOverlay`;
the `SeedT` type variable; and the tuning constants `CLIENT_TIMEOUT_S`, `DRAIN_TIMEOUT_S`,
`DEFAULT_REQUEST_LIMIT`, `LOG_LINES`, `SERVE_COMMAND` and `NO_SEED_HINT`.

`vendorfake.testing.seeds` publishes the same seed types and `seed_for`
directly, for a caller who wants a seed without building a unit, plus
`seed_collections_for` and the `SEED_COLLECTIONS_ATTR` name it reads — which
seed collections a vendor's `.seed` is built from, and how a third-party
vendor declares its own.

**`served()` takes `env=`.** A `VENDORFAKE_*` mapping layered onto the
child's inherited `os.environ` — an entry beats the ambient variable of the
same name, `clock_start=` layers beneath it exactly as in `unit()`, and the
parent-resolved `.seed` reads the same `VENDORFAKE_VENDOR_*` layer. Additive:
a call without it behaves as before. Entries for what `served()` passes as a
flag (`VENDORFAKE_PROFILE`, `VENDORFAKE_HOST`, `VENDORFAKE_PORT`,
`VENDORFAKE_LOG_LEVEL`), for `VENDORFAKE_TRANSPORT` and
`VENDORFAKE_TRANSPORT_DIR`, for `VENDORFAKE_SEED` and for
`VENDORFAKE_SEED_OVERLAY` are refused with
`ValueError` before the child is spawned — the first four because the flag
would beat them (the message names the parameter to use), the transport pair
because `serve` only ever binds HTTP and there is no parameter to use, the
seed because `.seed` could not describe it, and the overlay because
`seed_overlay=` is the parameter for it and only the parameter's path checks
the document in the calling process. There is still no `capabilities=`.

**All three bindings take `seed_overlay=`.** A partial seed document merged
over the profile's before the store is hydrated — an inline mapping, or a
`str`/`os.PathLike` naming a JSON file. Narrowed on the vendor literal through
the per-vendor `TypedDict`s above. It is the `VENDORFAKE_SEED_OVERLAY` layer,
so an explicit entry in `env=` wins, exactly as `seed=` and `clock_start=`
behave; `served(env=)` refuses that variable and names the parameter. Additive:
a call without it behaves as before. The merge rule and the refusal for a
collection the seed does not have are in
[Seed](concepts/seed.md#seed-overlays); `GET /__unit/info` publishes
`seed_overlay: {active, digest}` and never the contents.

**An overlay may not name the collections `.seed` is built from.** `tokens`,
and the vendor's identity collection (`merchant` on Square and Clover,
`restaurant` on Toast), are refused with `UnitError` when the unit starts —
on all three bindings, and in the parent process before `served()` spawns a
child. `.seed` carries the shipped credentials and tenant id from this
distribution's constants rather than from the loaded document, so an overlay
of those two would make `.seed.auth` 401 against a unit that started
perfectly. A vendor from the entry-point group declares the same set with a
`seed_collections` attribute on its `VendorDefinition`, beside its
`SeedingVendor.seed` hook; declaring nothing refuses nothing. See
[Seed](concepts/seed.md#seed-overlays).

**`served()`'s startup failures are eager, in the parent process.** An
unknown vendor, a nonexistent or malformed profile, and a vendor with no seed
are all refused with `ValueError`, `UnitError` or `LookupError` before
`served()` ever spawns a child — the vendor is resolved and the profile is
loaded to build the seed exactly as `unit()` builds it, and both failures
surface there. This is a behaviour change for a bad profile name specifically:
before this release, `served()` did no profile resolution of its own, so a
typo reached the caller only as whatever the spawned child's own startup or
health-check path produced. A caller written against that older, slower
failure mode — catching a connection or startup-timeout error around
`with served(...)` — now sees an unhandled `UnitError` instead.

The parent resolves that profile from the layers the **child** will resolve
from, not from the profile document alone: the ambient `VENDORFAKE_VENDOR_*`
block, and `VENDORFAKE_SEED` where it is set. So a seed overlay is checked
against the document the child will actually load — a house scenario exported
for a whole suite included — rather than against the profile's own seed.
`VENDORFAKE_PROFILE` is deliberately not among them, because the child is
given `--profile` as a flag and the CLI prefers a flag to the variable.

### `vendorfake.registry` — discovery and construction

The five names above, plus `ProfileInfo`, `RouteInfo`, `ROLE_NAMES`,
`ENTRY_POINT_GROUP`, `VENDOR_ENV_VAR`, and the two protocols a third-party
vendor implements — `VendorDefinition` and `SeedingVendor`, re-exported here
from `vendorfake.core.kernel.types` for the reason given under *Publishing a
vendor of your own* below.

### `vendorfake.pytest` — the plugin the wheel auto-loads

The `vendorfake` marker (`MARKER`) and the `vendorfake_unit`,
`vendorfake_async_unit` and `vendorfake_webhook_receiver` fixtures. Installing
vendorfake loads this and nothing else; the conformance suite's pytest form is
loaded explicitly with `-p vendorfake.conformance.plugin`.

### The per-vendor path constants

`vendorfake.square.paths`, `vendorfake.clover.paths` and
`vendorfake.toast.paths` — one `UPPER_SNAKE` constant per route carrying an
`operation_id`, named after that `operation_id`, which is the same identifier
`registry.routes` and `GET /__unit/routes` publish.
`tests/unit/test_paths_drift.py` asserts every constant against the live route
table in both directions, so a value here cannot drift from what the router
serves.

### Publishing a vendor of your own

A distribution publishes a vendor through the `vendorfake.vendors` entry-point
group — `square = "vendorfake.square:VENDOR"` is the shape. Two things are
public for that purpose, both re-exported from `vendorfake.registry` so that
writing a vendor needs no import into an internal package:

- `vendorfake.registry.VendorDefinition`, the protocol such an object
  satisfies. There is no way to write a vendor without it.
- `vendorfake.registry.SeedingVendor`, its optional extension: a vendor that
  implements `seed(vendor_config)` gets a real `.seed` out of
  `unit("<its name>")` instead of the `LookupError` a seedless vendor draws.
  The object it returns must satisfy `vendorfake.testing.Seed`; one that does
  not is refused by name when the unit is built.

Both are defined in `vendorfake.core.kernel.types`, which stays internal —
every other name that module exports can change in any release. Only the two
re-exported through `vendorfake.registry` are pinned.

Adding a member to `VendorDefinition` is a breaking change for a third-party
vendor and is announced as one.

### The control plane

Every `/__unit/*` route, its query parameters, and the JSON bodies it accepts
and returns. `vendorfake routes --internal --json` lists them for a given
profile, and `vendorfake openapi` prints the document. These are as public as
the vendor surfaces are: the conformance suite asserts a vendor's behaviour
entirely through them, which is what lets an implementation in another
language be checked against the same contract.

### The command line

Every subcommand, every flag, and the JSON document `--json` prints. `--json`
is accepted on either side of the subcommand name and means the same thing.

### The profile document

Every key a profile JSON document accepts, and every `VENDORFAKE_*`
environment variable that overrides one. `GET /__unit/info` publishes the
resolved result.

### The `Vendorfake-*` response headers

| Header | Carries |
| --- | --- |
| `Vendorfake-Near-Miss` | The closest routes to a request nothing matched, ranked |
| `Vendorfake-Error-Kind` | The neutral error kind behind a vendor-shaped error |
| `Vendorfake-Status-Provenance` | Whether the vendor documents that status, or it is a judgment |
| `Vendorfake-Error-Field` | The offending field, percent-encoded |
| `Vendorfake-Error-Info` | The error's structured detail, as ASCII-safe JSON |
| `Vendorfake-Fault` | The chaos fault kind that shaped this response |
| `Vendorfake-Rule` | The id of the chaos rule that fired |
| `Vendorfake-Rule-Error` | The id of a chaos rule whose fault payout refused its own params (a 400; no `Vendorfake-Fault`, because no fault fired) |
| `Vendorfake-Delay-Ms` | On a `timeout`-faulted answer, the delay the rule asked for, on either clock |

Header names are case-insensitive on the wire, and the package is not
consistent about the case it sets: the four error-sidecar headers
(`Vendorfake-Error-Kind`, `Vendorfake-Status-Provenance`,
`Vendorfake-Error-Field`, `Vendorfake-Error-Info`) go out capitalised, and the
near-miss and chaos headers (`Vendorfake-Near-Miss`, `Vendorfake-Fault`,
`Vendorfake-Rule`) go out lower case. Read either with a case-insensitive
lookup, which is what every HTTP client here already does; this table writes
every name capitalised in prose regardless of which one a given response uses.

## Internal

These may change in any release, in any way, without notice. Import them and an
upgrade may move them under you.

- **`vendorfake.asgi`** — the only place a web framework is imported. A public
  module never re-exports anything from it: `tests/unit/test_public_api.py`
  asserts that no public module's `__all__` hands back a name defined in
  `vendorfake.asgi`, and that no public module imports it at module scope.
  That check does not follow a public module's own imports transitively —
  `tools/boundary_check.py` and the `import-linter` contract in
  `pyproject.toml` are what enforce the deeper property, that no chain of
  imports starting from a public module reaches a web framework at all. If
  you want a real socket, use `served()`, `serve_in_thread()` or the
  container.
- **`vendorfake.core`** — the whole stateful machinery: the kernel, the store,
  the chaos engine, the webhook dispatcher, the clock, the control plane's
  implementation. `VendorDefinition` and `SeedingVendor` are defined here but
  are not an exception to this: they are public through their re-export at
  `vendorfake.registry`, named under *Publishing a vendor of your own* above,
  and the rest of this package — everything reached only through
  `vendorfake.core.*` directly — is not pinned.
- **`vendorfake.conformance` internals.** The suite is meant to be *run* —
  `vendorfake-conformance`, `python -m vendorfake.conformance`, or
  `-p vendorfake.conformance.plugin`. Its clause ids and their published
  results are stable; the modules that implement them are not.
- **Vendor packages other than `paths`** — `vendorfake.<vendor>.surface`,
  `.config`, `.errors`, `.seed`, `.model`, and the names their `__init__`
  re-exports. What a vendor surface *does* is pinned by the conformance suite;
  where it lives is not.
- **`vendorfake.agent`** — the machinery behind `vendorfake agent-setup` and
  `vendorfake explain`: the rules-file template, the `.mcp.json` merge, and
  the lookups `explain` renders. `vendorfake.agent.__init__` declares
  `__all__ = []` and is reached only from `vendorfake.cli`'s two subcommand
  bodies. The command line those two subcommands are part of is pinned — see
  *The command line*, above — this package's internal shape is not; a test
  or an agent reaches this surface through the `vendorfake` command, never by
  importing `vendorfake.agent` directly.

## White-box handles

`started.unit` and `started.unit.context.store` are documented and supported.
They are the intended way to assert against state a vendor surface does not
publish, and reaching for them is not a workaround.

They are **not frozen**. They may change between minor releases, with a
changelog entry, but without a deprecation period. The reason for the weaker
promise is that they expose the shape of the machinery rather than a designed
interface: freezing them would freeze the internals they are a window onto,
which is exactly what the internal list above exists to avoid.

## The deprecation policy

When a public symbol has to go:

1. **It keeps working for one minor release.** The release that deprecates it
   still ships it, behaving as it did.
2. **It warns with `DeprecationWarning`, naming its replacement**, so the
   notice arrives in a test run rather than in a release note nobody read.
3. **It is listed under Deprecations in `CHANGELOG.md`** for the release that
   deprecates it, and again under Breaking changes for the release that
   removes it.

One honest limitation. A module-level constant cannot warn when it is read —
there is nothing to intercept — so for those the changelog entry and a note in
the docstring at the site are the whole notice. Functions, classes and methods
warn.

A behaviour change to a symbol that keeps its name is not a deprecation and
does not get a grace release; it is announced under Behaviour changes or
Breaking changes with a migration note saying what to do instead. The 0.2.0
entries are written that way, and they are the model.
