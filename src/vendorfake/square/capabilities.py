"""What this vendor can be asked to do, and what switching each off removes. Declared once so the
route table, the control plane's capability index and the core's own gating all read the same list.

INVARIANT: every capability the core gates on is accounted for here or excused in ``not_supported``
with a reason -- Square implements chaos, webhooks and webhooks.chaos, so
:data:`SQUARE_NOT_SUPPORTED` is empty. ``chaos`` is not a Square concept; it is the core's gate for
request-scope fault injection. ``webhooks.chaos`` gates delivery-scope faults separately.
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
        name="inventory",
        summary="Stock counts per variation and location: physical counts, adjustments, retrieval.",
    ),
    CapabilityDecl(
        name="loyalty",
        summary="The seller's loyalty program: find or enrol a buyer by phone, accumulate points for an order.",
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
"""Empty, deliberately: everything the core gates on is declared above rather than excused here."""
