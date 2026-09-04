# AGENTS.md

For an agent working *in this repository* -- building, fixing, or extending
vendorfake itself. If instead you are writing tests **against** an installed
vendorfake in a consumer repository, this is the wrong file: run `vendorfake
agent-setup` there (or `vendorfake explain <kind> <name>` for one answer at a
time), or read
https://github.com/konyklabs/vendorfake/blob/main/docs/for-agents.md.

## Layout

```
src/vendorfake/
  core/         the framework-free kernel: state store, state machine,
                capability registry, chaos engine, webhook dispatcher, retry,
                clock, rng. Imports no web framework, ever.
  asgi/         the only place FastAPI is imported. Adapts ASGI to core's
                UnitRequest/UnitResponse and back, byte for byte.
  conformance/  the vendor-independent contracts (C01, C02, ...) every vendor
                module must pass, plus the runner, the pytest plugin, and the
                committed manifest.json.
  testing/      the fixture layer a consumer's test suite imports:
                unit()/async_unit()/served()/serve_in_thread(), Driver,
                StartedUnit, seeds.
  agent/        this surface: agent-setup's rules-file template and
                explain's lookups.
  square/ clover/ toast/
                one vendor surface each: routes, error vocabulary, signature
                scheme, retry schedule, seed.
  cli.py        the vendorfake command; the only module that reads
                os.environ.
  registry.py   vendor discovery and the one create_unit() constructor.
tests/
  unit/         fast, no server, no vendor-specific fixtures required.
  integration/  needs a running server (marker: integration).
  conformance/  the suite that exercises tests/conformance's own harness
                against the checks in src/vendorfake/conformance/checks/.
tools/          self-test.sh, boundary_check.py, boundary.toml, and the
                other scripts self-test.sh's steps call.
docs/           the docs site's source (see mkdocs.yml if present).
```

## The one command

```sh
uv sync --all-groups
tools/self-test.sh
```

Every step is printed in a summary table at the end; a failure anywhere is a
red row, not a stack trace to scroll back to. Paste the command and its
output as evidence for any change -- "tests pass" is not evidence.

## Boundary rules

`tools/boundary.toml` is the whole policy, read by `tools/boundary_check.py`;
`pyproject.toml`'s `[tool.importlinter]` contracts are the declarative half of
the same invariant. Both run as steps in `tools/self-test.sh`. The rules that
matter most:

- `vendorfake.core` imports no web framework (`fastapi`, `starlette`,
  `uvicorn`, `multipart`) -- enforced by import-linter, with `vendorfake.asgi`
  the sole named exception.
- A module under `src/vendorfake/core/` may import first-party code only from
  `src/vendorfake/core/`; the same rule holds for `conformance/` and `asgi/`
  against their own prefix plus `core/`. A vendor package and `cli.py`/`agent/`
  are unrestricted by this rule, but still may not import `fastapi` et al.
  outside `asgi/`.
- No vendor slug (`square`, `clover`, `toast`, ...) appears as a literal
  string or token under `core/` or `conformance/` -- discovered from the
  source tree, not hand-listed, so a new vendor cannot silently widen this.
- Pydantic is permitted in `core/` only in the three files
  `tools/boundary.toml` names, because it parses an external document there;
  everywhere else in `core/` an entity stays a plain dict.
- `cli.py` is the only module that resolves a unit's config from `os.environ`;
  every first-party import in it happens inside a function body so `vendorfake
  --help` never pays for importing a web framework. `vendorfake.testing.served()`
  is the one documented exception, because it spawns `cli.py`'s own `serve`
  subcommand as a child that inherits the real environment regardless -- see
  its docstring in `src/vendorfake/testing/__init__.py`.

## Provenance labels

Every behaviour this project reproduces carries a label at the site, because
this is a *fake* and the label is the difference between "the vendor really
does this" and "we decided to". Two contexts, three words:

- **`DOCUMENTED`** (in comments and docstrings) / **`"documented"`** (in the
  `Vendorfake-Status-Provenance` header and `GET /__unit/errors`'
  `provenance` field) -- the vendor's own published documentation says so,
  cited at the site.
- **`JUDGMENT`** / **`"judgment"`** -- this project chose the behaviour
  because no vendor page settles it.
- **`"transport"`** -- `core/chaos/rules.py`'s `FaultProvenance`, for a fault
  that is about the HTTP transport rather than any vendor's documented
  behaviour (a dropped connection, a truncated body): no vendor page could
  settle it because it is not a vendor's decision to make.

A citation with no working URL is worse than no citation -- check it before
committing it.

## How streams and conformance ids are allocated

**Conformance checks** are C-numbered (`C01`, `C02`, ...) and registered with
`@check(id=...)` in whichever module under
`src/vendorfake/conformance/checks/` matches their subsystem (`auth`,
`capabilities`, `chaos`, `control_plane`, `discovery`, `errors`, `state`,
`transport`, `webhooks`); a new check takes the next free id. C01–C35 are
all allocated (C24–C32 by the conformance-coverage stack of roadmap #15,
#46 and #42; C33 by stream S and C34–C35 by stream C of the 0.2 batch).
**Next free id: C36.** `checks/__init__.py`
imports every module and sorts the registry into id order, so report order
never depends on import order. `manifest.json` is the committed
id-to-name-and-expected-skips record; `tests/conformance/test_manifest.py`
fails if the live registry and the file disagree, which is what makes
removing, renaming or re-gating a check a reviewable diff rather than a
silent deletion -- update the manifest in the same change.

**Work streams** are lettered or named by the driving task's slug, each on
its own branch (`<type>/<issue>-<slug>`) and worktree, merged onto an
integration branch in review order. That convention belongs to the workspace
this repository lives in, not to vendorfake itself; nothing in this repo
enforces it mechanically the way the C-id manifest does.

## No employer-identifying terms

Never write a real company's name, an internal codename, or any other
employer-identifying term into any commit, branch, file name, issue, or PR in
this repository -- vendor names used to describe a publicly documented API
(`square`, `clover`, `toast`) are the one deliberate exception this project
exists to make. Describe a consumer generically ("a consumer", "an async
FastAPI-style service", "a service under test") rather than naming the real
system a test or an example is modelled on.
