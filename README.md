# vendorfake

High-fidelity **fakes** of third-party vendor APIs — stateful flows, signed
webhooks, and deterministic fault injection — for testing integrations without
touching a vendor sandbox.

> **Unofficial.** Not affiliated with, endorsed by, or connected to any vendor
> named here. Every behaviour is derived from publicly published API
> documentation. Vendor names are used only to identify which public API a
> module imitates.

## Status

Early. Nothing is published to any registry yet, and the implementation is
being built in the open. Treat anything here as subject to change until a
first release is tagged.

## Why this exists

Integration code against third-party vendors is hard to exercise in CI. Vendor
sandboxes are rate-limited, network-bound and behaviourally incomplete — some
require a human to advance a state machine by hand, and several cannot produce
the failure modes that actually break integrations: duplicate webhooks,
out-of-order delivery, retries, expired tokens mid-flow.

Generic mocks don't help, because the thing worth testing is precisely the
behaviour a generic stand-in doesn't have. `vendorfake` aims at the opposite:
few vendors, modelled properly, with the awkward parts reproducible on demand.

## What "fake" means here

The word is precise rather than modest. In the standard test-double taxonomy a
*mock* is assertion-focused and usually stateless, while a **fake** has a
working implementation — real state, real transitions, real consequences. That
is what this is: create an order, and it exists; complete it, and the webhook
fires, signed the way the vendor signs it.

## Design

Two architectural decisions are recorded as ADRs in the
[roadmap](https://github.com/konyklabs/roadmap/tree/main/decisions):

- **D-001** — the unit architecture: stateful vendor units with a shared core,
  a journal-backed state engine, capability profiles, and deterministic chaos.
  Chosen by building three independent implementations and measuring them
  against a fixed rubric.
- **D-002** — Python on FastAPI, the `vendorfake` naming schema, and a single
  distribution with vendors as modules.

The invariant those decisions turn on: the stateful machinery — journal, state
store, capability registry, chaos engine, webhook dispatcher — stays
framework-free, and the web framework lives only in the transport adapter. A
framework's parsing assumptions leaking into shared code is a measured failure
mode, not a hypothetical one.

## Licence

Apache-2.0.
