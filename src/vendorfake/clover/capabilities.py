"""What this vendor can be asked to do, and what it deliberately does not model.

FOR: declaring the capability set once, so that the route table, the control
plane's capability index and the core's own gating all read the same list --
and recording, with reasons, every documented Clover behaviour this fake
chooses not to implement.

INVARIANT: **every capability the core gates on is accounted for.** The core
fails at construction when one of its gated capabilities (``chaos``,
``webhooks``, ``webhooks.chaos``) is neither declared here nor excused in
``VendorDefinition.not_supported`` with a reason. This vendor declares
``chaos`` and excuses the two webhook gates: with no signer and no event
mapper the dispatcher would silently no-op
(``WebhookDispatcher`` returns before preparing anything when either seam is
``None``), and a capability that is *declared* while structurally undeliverable
is exactly the enabled-but-dead state the declaration system exists to
prevent. PR D ships the surface, the signer and the mapper together and moves
both names back into :data:`CLOVER_CAPABILITIES`.

The core is also strict the other way: ``not_supported`` may not name anything
the core does *not* gate on ("not_supported names {name}, which the core does
not gate on" is a startup failure). So the list of documented Clover features
this fake deliberately omits cannot live on the protocol property; it lives in
:data:`CLOVER_NOT_MODELED`, an informational map this package exports and its
surfaces cite in refusals. The distinction: ``not_supported`` answers the
*core* ("do you have webhooks?"), ``CLOVER_NOT_MODELED`` answers the
*consumer* ("why is there no payments endpoint?").
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.kernel.types import CapabilityDecl

__all__ = ["CLOVER_CAPABILITIES", "CLOVER_NOT_MODELED", "CLOVER_NOT_SUPPORTED"]

CLOVER_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    CapabilityDecl(
        name="oauth",
        summary="OAuth v2 authorization-code flow with expiring access tokens and single-use refresh rotation.",
    ),
    CapabilityDecl(
        name="orders",
        summary="Orders, line items and the atomic order/checkout calculators, with client-owned totals.",
    ),
    CapabilityDecl(
        name="inventory",
        summary="Inventory items and the merchant record -- the reference data orders point at.",
    ),
    CapabilityDecl(
        name="chaos",
        summary="Request-scope fault injection: rate limits, timeouts, server errors, token expiry.",
        kind="behavior",
    ),
)

CLOVER_NOT_SUPPORTED: Mapping[str, str] = {
    "webhooks": (
        "The webhook surface, the X-Clover-Auth signer and the event mapper ship together in PR D of "
        "konyklabs/roadmap#34; declaring the capability before the seams exist would be enabled-but-dead."
    ),
    "webhooks.chaos": (
        "Delivery-scope faults need a delivery to inject into; arrives with the webhook surface in PR D "
        "of konyklabs/roadmap#34."
    ),
}
"""The two webhook gates, excused until PR D ships the seams that make them
deliverable. Both names are core-gated, which is what lets them live here; the
documented Clover features this fake omits outright are recorded in
:data:`CLOVER_NOT_MODELED` instead -- see the module docstring.
"""

CLOVER_NOT_MODELED: Mapping[str, str] = {
    "payments": (
        "No payments surface. Orders carry paymentState as a plain field; nothing moves it. "
        "Modelling Clover's payment/tender flows is out of scope for this build."
    ),
    "customers": "The customers API is not modelled; orders here never reference a customer.",
    "employees": "The employees API is not modelled; orders here never reference an employee.",
    "tax-rates": (
        "Tax rates are not modelled. Items keep their documented defaultTaxRates flag, but no tax "
        "is ever computed -- atomic-order totals cover line items, discounts and service charge only."
    ),
    "modifier-groups": "Modifier groups and line-item modifiers are not modelled.",
    "token-migration": (
        "POST /oauth/token/migrate_v2 and the legacy v1 (non-expiring token) flow are not modelled; "
        "this unit speaks only the v2 expiring-token flow."
    ),
    "rate-limit-accounting": (
        "No real request counting against the documented 50 req/s per-app / 16 req/s per-token limits "
        "(https://docs.clover.com/dev/docs/api-usage-rate-limits). Every 429 this unit sends is "
        "chaos-injected, so a consumer can rehearse handling one without generating real load."
    ),
    "90-day-filter-restriction": (
        "Clover documents that several order list filters are 'restricted to the last 90 days'. "
        "Deliberately not enforced: a fake that hides seeded data by age helps nobody, and a scenario "
        "must stay fully queryable however old its timestamps are. JUDGMENT -- a consumer must not "
        "learn from this fake that old orders are filterable on the real API."
    ),
}
"""Documented Clover behaviour this fake deliberately omits, each with its why.

Informational -- published for consumers and cited by surface refusals, never
handed to the core (see the module docstring). "This vendor has no payments
surface" and "payments are on the roadmap" are different facts, and a bare set
would lose both.
"""
