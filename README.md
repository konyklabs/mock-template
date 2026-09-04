# vendorfake

High-fidelity **fakes** of third-party vendor APIs for testing an integration
without a vendor sandbox: real state (orders have a lifecycle, tokens expire,
refresh tokens rotate), webhooks signed the way the vendor signs them and
retried on the vendor's schedule, and fault injection that is deterministic,
so a retry loop is rehearsed the same way every run.

> **Unofficial.** Not affiliated with, endorsed by, or connected to any vendor
> named here. Every behaviour is derived from publicly published API
> documentation. Vendor names are used only to identify which public API a
> module imitates.

Covers **Square** (Connect v2), **Clover** (REST v3), **Toast** (REST v2/v3)
and **Lightspeed Retail X-Series** (API 2026-07) — see [the docs
site](docs/index.md) for what each surface covers; the route reference lists
every route per vendor, and `vendorfake explain` describes one in place.
Lightspeed has a page of its own, with real transcripts, the vendor's own
inconsistencies it reproduces and every judgment call it makes:
[Lightspeed Retail X-Series](docs/vendors/lightspeed.md).

## Start in sixty seconds

Python 3.11 or newer. Not on PyPI yet — install from the tag:

```sh
pip install "vendorfake @ git+https://github.com/konyklabs/vendorfake@v0.1.0"
# or, in a uv project: uv add "vendorfake @ git+https://github.com/konyklabs/vendorfake@v0.1.0"
# or, from a checkout of this repository: uv sync

vendorfake vendors                       # -> clover, lightspeed, square, toast
vendorfake serve --vendor square         # http://127.0.0.1:8080
vendorfake serve --vendor lightspeed     # or any other installed vendor
```

```sh
curl -s http://127.0.0.1:8080/__unit/health
# -> {"status":"ok","vendor":"square","profile":"full","uptime_ms":221,"framework_answered":0}
```

Every command names a vendor (`--vendor square|clover|toast|lightspeed`, or
`VENDORFAKE_VENDOR`); with none installed it refuses and lists what it found.
Drop the `@v0.1.0` to track `main` instead of a release tag. A container image
is also available (one image, every vendor, chosen at run time) — see
[Install → As a container](docs/start/install.md#as-a-container).

## Documentation

Everything past the first request lives in the docs site under `docs/`:

- **[Start here](docs/start/install.md)** — install, the sixty-second
  quickstart above in full, and which binding to use for a test suite
  (in-process sync, in-process async, served, container).
- **[Recipes](docs/pytest-plugin.md)** — pytest (sync and async), docker
  compose, CI.
- **[Concepts](docs/concepts/unit.md)** — unit, profile, capability and
  roles, seed, driver, journal and request log, clock, chaos rules and
  faults, provenance labels.
- **[Reference](docs/reference/routes-square.md)** — generated from the
  code: every route per vendor, every profile, every fault, every
  environment variable, the control plane, the CLI's own `--help`.
- **[Vendors](docs/vendors/lightspeed.md)** — a page per vendor where the
  surface has one: what it covers, transcripts from a served unit, the
  vendor's own inconsistencies, and the judgment calls with their citations.
- **[For agents](docs/for-agents.md)** and **[Contract](docs/api-contract.md)**
  — the agent-facing surface and the public API contract.
- **[Changelog](docs/changelog.md)**.

Read the pages directly on GitHub, or render the site locally from a
checkout:

```sh
uv sync --group docs
uv run mkdocs serve   # -> http://127.0.0.1:8000
```

## Status

v0.1.0 is tagged and built in the open. Not yet on PyPI or a container
registry: install from the tag as shown above, and treat interfaces as
subject to change before v1. Why this project exists, and the two ADRs its
architecture turns on, are on [the docs site](docs/index.md#why-this-exists).

## Licence

Apache-2.0.
