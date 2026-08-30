"""What this vendor can be asked to do, and what switching each off removes.

FOR: declaring the capability set once, so that the route table, the control
plane's capability index and the core's own gating all read the same list.

INVARIANT: **every capability the core gates on is accounted for.** The core
fails at construction when one of its gated capabilities is neither declared
here nor excused in ``not_supported`` with a reason -- otherwise a behaviour
the core silently skips is indistinguishable from a behaviour a vendor never
had. Square implements all three, so :data:`SQUARE_NOT_SUPPORTED` is empty and
says why it is empty rather than being absent.

``chaos`` is not a Square concept and is declared anyway. It is the core's
behaviour gate for request-scope fault injection from every source -- standing
rules, in-band magic values, forced headers -- and it owns no routes, because
there is no endpoint whose absence would represent it. ``webhooks.chaos`` keeps
its own gate for delivery-scope faults: a profile that wants request faults but
honest delivery, or the reverse, is a real configuration that one gate could
not express.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.kernel.types import CapabilityDecl

__all__ = ["SQUARE_CAPABILITIES", "SQUARE_NOT_SUPPORTED"]

SQUARE_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    CapabilityDecl(
        name="oauth",
        summary="Authorization-code flow, token refresh, revocation and token status.",
    ),
    CapabilityDecl(
        name="order-lifecycle",
        summary="Create, retrieve, update, search and pay orders, with state persisting across calls.",
    ),
    CapabilityDecl(
        name="merchant-directory",
        summary="Merchant, locations and catalog -- the reference data orders point at.",
    ),
    CapabilityDecl(
        name="payments",
        summary="External payments against orders: create, complete, cancel, retrieve.",
    ),
    CapabilityDecl(
        name="webhooks",
        summary="Signed event delivery to subscribers, with the documented retry schedule.",
    ),
    CapabilityDecl(
        name="chaos",
        summary="Request-scope fault injection: rate limits, timeouts, server errors, token expiry.",
        kind="behavior",
    ),
    CapabilityDecl(
        name="webhooks.chaos",
        summary="Delivery faults: duplication, reordering, dropped acknowledgements, delay.",
        kind="behavior",
        requires=("webhooks", "chaos"),
    ),
)

SQUARE_NOT_SUPPORTED: Mapping[str, str] = {}
"""Empty, and deliberately so.

The core gates on ``chaos``, ``webhooks`` and ``webhooks.chaos``; this vendor
declares all three above. A capability listed here would be one the core gates
on and this vendor does not implement, recorded with the reason it does not
apply -- "this vendor has no webhook mechanism at all" and "webhooks are on the
roadmap" being different facts that a bare set would lose.
"""
