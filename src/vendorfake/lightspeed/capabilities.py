"""What this vendor can be asked to do, and what it deliberately does not model.

FOR: declaring the capability set once, so that the route table, the control
plane's capability index and the core's own gating all read the same list --
and recording, with reasons, every documented Lightspeed behaviour this fake
chooses not to implement.

INVARIANT: **every capability the core gates on is accounted for.** The core
fails at construction when one of its gated capabilities (``chaos``,
``webhooks``, ``webhooks.chaos``) is neither declared here nor excused in
``VendorDefinition.not_supported`` with a reason.

SCOPE. This is the foundation slice of konyklabs/roadmap#94: the token
endpoint and the authorize stand-in, the retailer, outlets, registers, payment
types and webhooks. Products, inventory, customers and sales are in the
issue's scoped surface and arrive in later slices; they are NOT listed in
:data:`LIGHTSPEED_NOT_MODELED`, because that map is for behaviour this fake has
decided against, not for work not yet done.

``not_supported`` may not name anything the core does not gate on, so the
documented Lightspeed features this fake omits live in
:data:`LIGHTSPEED_NOT_MODELED`, an informational map the surfaces cite.
"""

from __future__ import annotations

from collections.abc import Mapping

from vendorfake.core.kernel.types import CapabilityDecl

__all__ = ["LIGHTSPEED_CAPABILITIES", "LIGHTSPEED_NOT_MODELED", "LIGHTSPEED_NOT_SUPPORTED"]

LIGHTSPEED_CAPABILITIES: tuple[CapabilityDecl, ...] = (
    CapabilityDecl(
        name="auth",
        summary=(
            "POST /api/1.0/token for both documented grants, with refresh rotation; a stand-in GET /connect "
            "that issues the single-use code."
        ),
    ),
    CapabilityDecl(
        name="retailer",
        summary="GET /retailer: the one retailer this unit serves, its currency, timezone and domain prefix.",
    ),
    CapabilityDecl(
        name="outlets",
        summary="Outlets: the version-cursor list and one outlet by id.",
    ),
    CapabilityDecl(
        name="registers",
        summary=(
            "Registers: the list, one by id, the open and close actions, and the payments summary. Closing "
            "synthesises a register closure and fires register_closure.create."
        ),
    ),
    CapabilityDecl(
        name="payment_types",
        summary="Payment types: the version-cursor list, excluding internal types unless asked for.",
    ),
    CapabilityDecl(
        name="webhooks",
        summary=(
            "The five documented webhook operations, and delivery: form-encoded payload=<JSON>, X-Signature "
            "(HMAC-SHA256), the 5-second timeout and the 20-attempt exponential retry inside 48 hours."
        ),
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

LIGHTSPEED_NOT_SUPPORTED: Mapping[str, str] = {}
"""Empty: every core-gated capability is declared above."""

LIGHTSPEED_NOT_MODELED: Mapping[str, str] = {
    "consignment-events": (
        "consignment.send and consignment.receive are two of the seven values in the spec's WebhookType enum "
        "and a subscription to either is accepted, but nothing in this unit ever fires one: consignments "
        "(Consignments, Consignment Products and Purchase Orders, 16 operations) are outside issue #94's "
        "scoped surface, and an event with no mutation behind it would be a fake event."
    ),
    "product-images": (
        "POST /products/{product_id}/actions/image_upload is multipart/form-data and the Product Images tag's "
        "three operations serve binary image data; issue #94 excludes images explicitly."
    ),
    "price-books": (
        "The Price Books tag (10 operations) and the products:read:price_books / products:write:price_books "
        "scopes are excluded by issue #94's scoped surface."
    ),
    "second-rate-limit-counter": (
        "The rate-limiting page describes two independent counters -- 'per retailer per application' and 'per "
        "retailer for all users' -- so that integrated traffic cannot starve in-store transactions. This unit "
        "has no POS user interface and models the application counter only "
        "(https://x-series-api.lightspeedhq.com/docs/rate_limiting)."
    ),
    "webhook-black-holing": (
        "A subscriber that fails >95% of requests in an hour AND has >1000 unprocessed events has its backlog "
        "erased and receives nothing for 24 hours. This unit retries on the documented ladder and then stops; "
        "it never suspends a subscription (https://x-series-api.lightspeedhq.com/docs/webhooks)."
    ),
    "retry-only-on-some-outcomes": (
        "'3xx and 4xx will not trigger retries'. The core dispatcher retries every non-2xx outcome and offers "
        "no vendor hook to say otherwise, so a subscriber answering 400 is retried here where Lightspeed would "
        "stop. The seam is konyklabs/roadmap#40; recorded in retry.py."
    ),
    "payload-shape-is-2026-07": (
        "'The payload objects you'll find in webhook requests are the same as those you'll receive from API "
        "1.0'. This unit sends the 2026-07 entity it stores, because it does not model API 1.0 at all. How "
        "large a drift that is, is UNVERIFIED (https://x-series-api.lightspeedhq.com/docs/webhooks)."
    ),
    "personal-token-lifecycle": (
        "Personal tokens are 'available to retailers on the Plus plan' and admins 'create them directly in the "
        "web application'; there is no API to mint or rotate one. The scenario seeds one, and nothing in this "
        "unit can create a second (https://x-series-api.lightspeedhq.com/docs/authorization)."
    ),
    "refresh-token-expiry": (
        "The authorization page states no refresh-token lifetime and this pass found none anywhere, so a "
        "refresh token here never expires -- it is only ever retired by being used. Recorded as a documentation "
        "gap rather than answered with an invented number."
    ),
    "real-authorize-host": (
        "The authorize redirect lives on the fixed host secure.retail.lightspeed.app, not on the retailer's own "
        "subdomain. A unit serves one origin, so GET /connect here is a stand-in at the documented path and "
        "approval is automatic: there is nobody to click a consent screen."
    ),
    "button-layouts": (
        "GET /button_layouts and GET /button_layouts/{id} carry the registers:read scope and are part of the "
        "Registers tag, but they describe the POS's own quick-key grid rather than the till lifecycle issue "
        "#94 scopes in."
    ),
}
"""Documented Lightspeed behaviour this fake deliberately omits, each with its why."""
