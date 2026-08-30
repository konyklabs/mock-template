"""What this vendor can be asked to do, and what it deliberately does not model.

FOR: declaring the capability set once, so that the route table, the control
plane's capability index and the core's own gating all read the same list --
and recording, with reasons, every documented Toast behaviour this fake
chooses not to implement.

INVARIANT: **every capability the core gates on is accounted for.** The core
fails at construction when one of its gated capabilities (``chaos``,
``webhooks``, ``webhooks.chaos``) is neither declared here nor excused in
``VendorDefinition.not_supported`` with a reason.

The build lands in steps, and the declaration follows the seams: ``webhooks``
and ``webhooks.chaos`` are *excused* until the signer, the event mapper and
the subscription stand-in exist, because a capability declared while the
dispatcher would silently no-op on a missing seam is the enabled-but-dead
state the declaration system exists to prevent.

``not_supported`` may not name anything the core does not gate on, so the
documented Toast features this fake omits live in :data:`TOAST_NOT_MODELED`, an
informational map the surfaces cite in refusals.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.kernel.types import CapabilityDecl

__all__ = ["TOAST_CAPABILITIES", "TOAST_NOT_MODELED", "TOAST_NOT_SUPPORTED"]

TOAST_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    CapabilityDecl(
        name="auth",
        summary="The machine-client login: clientId/clientSecret for a Bearer JWT, no refresh.",
    ),
    CapabilityDecl(
        name="menus",
        summary="Menus API V3: the published menu document and its metadata.",
    ),
    CapabilityDecl(
        name="config",
        summary="Configuration API v2: thirteen reference lists, by guid, with lastModified and page tokens.",
    ),
    CapabilityDecl(
        name="restaurants",
        summary="Restaurants API: one restaurant, and a management group's restaurants.",
    ),
    CapabilityDecl(
        name="partners",
        summary="Partners API: the restaurants connected to this client, in the documented page envelope.",
    ),
    CapabilityDecl(
        name="orders",
        summary="Orders v2: prices, create, read, bulk list, selections, void, discounts, delivery info.",
    ),
    CapabilityDecl(
        name="payments",
        summary="Payments on a check: OTHER and pre-authorised CREDIT, tips, and the payment reads.",
    ),
    CapabilityDecl(
        name="chaos",
        summary="Request-scope fault injection: rate limits, timeouts, server errors, token expiry.",
        kind="behavior",
    ),
)

TOAST_NOT_SUPPORTED: Mapping[str, str] = {
    "webhooks": (
        "Not yet: the Toast-Signature signer, the order_updated/stock/menus_updated mapper and the "
        "subscription stand-in arrive together in a later step of this build (konyklabs/roadmap#39)."
    ),
    "webhooks.chaos": "Not yet: follows webhooks.",
}
"""Core-gated capabilities this step does not implement, each with its reason."""

TOAST_NOT_MODELED: Mapping[str, str] = {
    "hostnames": (
        "Toast never publishes API hostnames ('You receive the hostname for the sandbox environment "
        "from the Toast integrations team', apiEnvironments.html); the unit serves everything on one "
        "origin and emits no absolute URLs."
    ),
    "token-refresh": (
        "There is no refresh flow: idToken and refreshToken are 'for internal use only' and a client "
        "logs in again (authentication.html)."
    ),
    "management-group-accounts": (
        "Only partner API accounts are modelled (partner_guid in the JWT, restaurants opt in "
        "individually); restaurant management group accounts (management_set_guid) are not."
    ),
    "credit-card-authorization": (
        "The credit-cards API (PUT /merchants/{m}/payments/{p} with encrypted card data) is not "
        "modelled; CREDIT payments on an order are accepted only when they name a pre-authorised "
        "payment guid the scenario seeded (authorizingCcPayments.html)."
    ),
    "refunds": "No refund endpoint is documented; refund fields on a payment are read-only and never set here.",
    "pos-side-order-flow": (
        "Approval (approvalStatus), kitchen fulfilment (fulfillmentStatus), delivery state and closing an "
        "order are POS-side; API orders are APPROVED on create and only void moves them. The control "
        "plane can drive the published machines for anything else."
    ),
    "rate-limit-accounting": (
        "No request counting against the documented limits (20 rps, 10,000 per 15 minutes; one per "
        "second on GET /menus; five per second on GET /ordersBulk -- apiRateLimiting.html). Every 429 "
        "is chaos-injected, with the documented X-Toast-RateLimit-* headers."
    ),
    "menus-v2": "Only Menus API V3 is served; 'Ordering integrations should use menus API V3'.",
    "loyalty-and-service-charges-on-orders": (
        "appliedLoyaltyInfo, appliedServiceCharges, appliedPackagingInfo and marketplace facilitator tax "
        "info are accepted on the wire and stored verbatim, never computed."
    ),
}
"""Documented Toast behaviour this fake deliberately omits, each with its why."""
