# vendorfake

High-fidelity **fakes** of third-party vendor APIs, for testing an
integration without a vendor sandbox: real state (orders have a lifecycle,
tokens expire, refresh tokens rotate), webhooks signed the way the vendor
signs them and retried on the vendor's schedule, and fault injection that is
deterministic, so a retry loop is rehearsed the same way every run.

!!! warning "Unofficial"
    Not affiliated with, endorsed by, or connected to any vendor named here.
    Every behaviour is derived from publicly published API documentation.
    Vendor names are used only to identify which public API a module
    imitates.

The word *fake* is precise rather than modest. In the standard test-double
taxonomy a *mock* is assertion-focused and usually stateless, while a
**fake** has a working implementation — real state, real transitions, real
consequences. Create an order and it exists; pay it and the webhook fires,
signed the way the vendor signs it. Where the public documentation is silent
and a behaviour had to be decided, the wire says so — see
[Provenance labels](concepts/provenance-labels.md).

## Four vendors, modelled properly

| Vendor | What the fake covers |
|---|---|
| **Square** (Connect v2) | OAuth2 with code and PKCE flows; orders with a real lifecycle and fulfillments; external payments; merchant, locations, catalog, inventory counts, a loyalty program; webhook subscriptions signed and retried on Square's documented schedule |
| **Clover** (REST v3) | OAuth v2 with single-use refresh rotation and the documented 401-for-everything auth; orders and line items with client-owned totals; atomic order and checkout calculators with taxes; inventory with modifier groups; employees, tenders, order types, customers; external-tender payments that lock the order; webhooks in Clover's aggregate shape with the `X-Clover-Auth` header and the verification handshake |
| **Toast** (REST v2/v3) | The consumer-driven slice an ordering integration calls: machine-client login with a JWT; the V3 menu and configuration lists; `/prices` and orders priced server-side from the tax rates; OTHER and pre-authorised CREDIT payments, tips, voids, discounts, stock; webhooks in Toast's envelope with the `Toast-Signature` HMAC and its documented retry schedule |
| **[Lightspeed Retail X-Series](vendors/lightspeed.md)** (API 2026-07) | The token endpoint with both grants and the rotation that revokes the access token it was returned with; the retailer, outlets, registers and payment types behind Lightspeed's per-retailer version cursor (`after`/`before`/`page_size`, `{"data": …, "version": {max, min}}`); the register open/close actions, with a close synthesising the closure the `register_closure.create` webhook carries; the documented fixed-window rate limiter (`300 × registers + 50` per five minutes, with an RFC 1123 `Retry-After`); products with inline variants, inventory records and levels with the 1-1000 stock-adjustment batch, and customers — each firing its documented webhook; sales, with line items and payments inline on the sale, a declared `parked | pending | voided | closed` lifecycle, computed totals, payment refusals in the vendor's `PaymentErrorResponse` shape, the return action, and a close that draws the outlet's stock; webhook CRUD, and form-encoded delivery signed with `X-Signature` |

## Sixty seconds to a first request

```sh
pip install "vendorfake @ git+https://github.com/konyklabs/vendorfake@v0.1.0"
vendorfake vendors                       # -> clover, lightspeed, square, toast
vendorfake serve --vendor square         # http://127.0.0.1:8080
```

```sh
curl -s http://127.0.0.1:8080/__unit/health
# -> {"status":"ok","vendor":"square","profile":"full","uptime_ms":221,"framework_answered":0}
```

The full walkthrough, including the container and the pytest/Vitest
fixtures, is under [Start here](start/install.md).

## Where to go next

- **[Start here](start/install.md)** — install it, make the first request,
  and pick the binding (in-process sync, in-process async, served, or
  container) that fits your test suite.
- **[Recipes](pytest-plugin.md)** — runnable patterns for pytest (sync and
  async), Vitest, Playwright, docker compose, and CI.
- **[Concepts](concepts/unit.md)** — the vocabulary: unit, profile,
  capability and roles, seed, driver, journal and request log, clock, chaos
  rules and faults, provenance labels.
- **[Reference](reference/routes-square.md)** — generated from the code:
  every route per vendor, every profile, every fault, every environment
  variable, the control plane, and the CLI's own `--help`.
- **[Vendors](vendors/lightspeed.md)** — a page per vendor where the surface
  has one: what it covers, transcripts from a served unit, the vendor's own
  inconsistencies it reproduces, and every judgment call with the page that is
  silent about it. Lightspeed Retail X-Series has the first.
- **[For agents](for-agents.md)** — the agent-facing surface: `vendorfake
  agent-setup`, `vendorfake explain`, `AGENTS.md`, `llms.txt`.
- **[Contract](api-contract.md)** — what is public, what is internal, and
  the deprecation policy.
- **[Changelog](changelog.md)**.

## Why this exists

Integration code against third-party vendors is hard to exercise in CI.
Vendor sandboxes are rate-limited, network-bound and behaviourally
incomplete — some require a human to advance a state machine by hand, and
several cannot produce the failure modes that actually break integrations:
duplicate webhooks, out-of-order delivery, retries, expired tokens mid-flow.

Generic mocks don't help, because the thing worth testing is precisely the
behaviour a generic stand-in doesn't have. `vendorfake` aims at the
opposite: few vendors, modelled properly, with the awkward parts
reproducible on demand.

Two architectural decisions are recorded as ADRs in the
[roadmap](https://github.com/konyklabs/roadmap/tree/main/decisions):
**D-001** (the unit architecture — stateful vendor units with a shared core,
a journal-backed state engine, capability profiles, and deterministic
chaos) and **D-002** (Python on FastAPI, the `vendorfake` naming schema, and
a single distribution with vendors as modules). The invariant those
decisions turn on: the stateful machinery stays framework-free, and the web
framework lives only in the transport adapter — enforced in CI with
import-linter and an AST-level boundary check.

v0.1.0 is tagged and built in the open; not yet on PyPI or a container
registry. Treat interfaces as subject to change before v1. Apache-2.0 — see
`LICENSE`.
