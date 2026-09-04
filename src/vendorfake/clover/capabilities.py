"""What this vendor can be asked to do, and what it deliberately does not
model -- declares the capability set once, so the route table, the control
plane and the core's gating all read the same list.

INVARIANT: every capability the core gates on (``chaos``, ``webhooks``,
``webhooks.chaos``) must be declared here or excused in
``VendorDefinition.not_supported``, which may not name anything it doesn't
gate on -- so documented Clover features this fake omits outright live in
:data:`CLOVER_NOT_MODELED` instead, an informational map surfaces cite in
refusals.
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
        summary="Orders, line items, print events and the atomic order/checkout calculators, with client-owned totals.",
    ),
    CapabilityDecl(
        name="inventory",
        summary="Inventory items (with tax rates and modifier groups) and modifiers -- what a line item points at.",
    ),
    CapabilityDecl(
        name="merchant",
        summary="The merchant record and its configuration: employees, tenders, order types, default service charge.",
    ),
    CapabilityDecl(
        name="customers",
        summary="Customer records: list, filter and create.",
    ),
    CapabilityDecl(
        name="payments",
        summary="External-tender payment records on an order; paying locks the order.",
    ),
    CapabilityDecl(
        name="webhooks",
        summary=(
            "Event delivery with the documented aggregate payload and X-Clover-Auth header, the dashboard "
            "stand-in for registering and verifying a callback, and a JUDGMENT retry schedule."
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

CLOVER_NOT_SUPPORTED: Mapping[str, str] = {}
"""Empty: every core-gated capability is declared above; see :data:`CLOVER_NOT_MODELED`."""

CLOVER_NOT_MODELED: Mapping[str, str] = {
    "card-payments": (
        "Only external-tender payment records (POST .../orders/{orderId}/payments) are modelled -- "
        "'This endpoint references external tenders and logs them for bookkeeping purposes. This is not "
        "for Clover credits/debit tenders.' Card processing, refunds, voids and the Ecommerce API are not."
    ),
    "customer-contact-details": (
        "Customers carry firstName, lastName and addresses. Email addresses, phone numbers and cards are not modelled."
    ),
    "employee-management": "Employees, tenders and order types are read-only reference data from the seed.",
    "tax-exemption-rules": (
        "Tax rates are applied per item (explicit associations or the merchant's defaults) by the atomic "
        "calculator only. Tax exemption rules, VAT-inclusive pricing and flat taxAmount rates are not modelled."
    ),
    "modifier-management": (
        "Modifier groups and modifiers are read-only reference data from the seed; line-item "
        "modifications are priced but groups cannot be created or edited."
    ),
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
"""Documented Clover behaviour this fake omits, with why -- informational,
cited by surface refusals, never handed to the core."""
