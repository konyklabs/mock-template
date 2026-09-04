"""What this vendor can be asked to do, and what it deliberately does not model.

FOR: declaring the capability set once, so that the route table, the control
plane's capability index and the core's own gating all read the same list --
and recording, with reasons, every documented Lightspeed behaviour this fake
chooses not to implement.

INVARIANT: **every capability the core gates on is accounted for.** The core
fails at construction when one of its gated capabilities (``chaos``,
``webhooks``, ``webhooks.chaos``) is neither declared here nor excused in
``VendorDefinition.not_supported`` with a reason.

SCOPE. Two slices of konyklabs/roadmap#94 have landed: the foundation (the
token endpoint and the authorize stand-in, the retailer, outlets, registers,
payment types and webhooks) and products/inventory/customers. Sales is in the
issue's scoped surface and arrives in the next slice; it is NOT listed in
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
        name="products",
        summary=(
            "Products: the version-cursor list with its sku/name/family_name overrides, one product by id, "
            "create with inline variants, update, and a soft delete. Every write fires product.update."
        ),
    ),
    CapabilityDecl(
        name="inventory",
        summary=(
            "Inventory: the four documented reads (two of them POSTs whose query is the body, all four "
            "answering a bare array) and the 1-1000 stock-adjustment batch. A level change fires "
            "inventory.update."
        ),
    ),
    CapabilityDecl(
        name="customers",
        summary=(
            "Customers: the version-cursor list, one by id, create (201), replace, and a soft delete (204). "
            "Every write fires customer.update. One seeded, read-only customer group."
        ),
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
    "product-family-delete": (
        "DELETE /products/{product_id}/all (DeleteProductFamily) removes a parent and every variant in one "
        "call. Issue #94's scoped surface names list/get/create/update/delete for the Products tag and not "
        "the family delete, so DELETE /products/{product_id} here removes exactly the product it addresses, "
        "which is what the delete operation's own description says a variant id does."
    ),
    "product-reference-tags": (
        "Product embeds a BrandSample, a SupplierSample and a ProductTypeSample, each {id, name, version}. "
        "The Brands, Suppliers and Product Types tags are outside issue #94's scoped surface, so there is no "
        "entity here to resolve an id against: brand, supplier and type are always the empty object the "
        "vendor's own example prints for a product with none, while brand_id, supplier_id and "
        "product_type_id carry through whatever the caller set."
    ),
    "product-categories-and-tags": (
        "Product.categories is an array of Tag and Product.tag_ids is an array of tag ids. The Tags and "
        "Product Categories tags are excluded by issue #94, so tag_ids is stored and echoed verbatim and "
        "categories is always empty -- there is nothing to expand an id into."
    ),
    "composite-products": (
        "ProductCreateBody.composite, Product.composite_bom, Product.is_composite and the includes[] value "
        "'composite_products' describe a product assembled from other products. Nothing in issue #94's "
        "scoped surface consumes a bill of materials, so is_composite is always false, composite and "
        "includes[] are accepted and change nothing, and no composite_bom is ever emitted."
    ),
    "variant-attributes": (
        "The Variant Attributes tag (5 operations) owns the attribute vocabulary a variant's "
        "{attribute_id, value} pairs refer to, and is outside issue #94's scoped surface. Variants "
        "themselves ARE modelled -- ProductCreateBody.variants creates one child product per payload -- but "
        "an attribute_id cannot be resolved to a display name here, so each variant_options row carries the "
        "attribute_id verbatim as its name."
    ),
    "product-sku-uniqueness": (
        "Nothing in the specification says a SKU is unique: the list parameter is documented as loading 'a "
        "product by one of its SKUs' and the sku member carries no uniqueness annotation. So two products "
        "may share one here and no 409 is invented for it -- the sku filter answers every match."
    ),
    "reorder-points": (
        "POST /inventory/reorder_points (SetReorderPoints) is an Inventory-tag operation that configures "
        "replenishment rather than reading or adjusting stock, and the build contract scopes in 'the "
        "inventory read + adjustment operations'. A product's reorder_point, reorder_amount, reorder_target "
        "and reorder_method are still modelled: the create body's inventory payload sets the first two."
    ),
    "custom-adjustment-reason-crud": (
        "The three custom_inventory_adjustment_reasons operations (list, create, update) are deferred with "
        "the rest of the out-of-scope surface, but a CUSTOM stock adjustment needs a reason to point at -- "
        "so the scenario seeds two, one POSITIVE and one NEGATIVE, and a CUSTOM adjustment naming anything "
        "else is a 422. JUDGMENT, and the reason there is no route to create a third."
    ),
    "quantity-to-procure": (
        "Inventory.quantity_to_procure and InventoryLevel.quantity_to_procure are how much of a product a "
        "replenishment run says to order. Purchase Orders and Consignments -- the two tags that would "
        "compute it -- are outside issue #94's scoped surface, so it is always 0 here rather than a number "
        "this unit made up."
    ),
    "inventory-level-report-filters": (
        "InventoryLevelsRequest declares group_variants, include_composites, supplier_ids, sort_type and "
        "to_be_procured_only. This unit has no composites, no supplier entity and no per-column sort to "
        "apply them to, so all five are accepted and change nothing -- recorded here rather than silently."
    ),
    "customer-groups": (
        "The Customer Groups tag (7 operations) is outside issue #94's scoped surface. The scenario seeds "
        "the retailer's one default group so that every customer has one to belong to, a create or update "
        "naming a group that does not exist is a 422, and there is no route to read, create or delete one."
    ),
    "customer-addresses": (
        "The Customer Addresses tag (5 operations) is a sub-resource of a customer and is excluded by issue "
        "#94. The flat physical_* and postal_* members ON the customer -- which are what CustomerBase "
        "carries -- are fully modelled; the addressable CustomerAddress records are not."
    ),
    "customer-balances": (
        "Customer.balance, loyalty_balance and year_to_date are format: double on the response and absent "
        "from CustomerBase, so nothing a consumer can send moves one. Store credit, loyalty adjustments and "
        "on-account sales -- the three things that would -- are all outside issue #94's scoped surface, so "
        "these three stay wherever the scenario put them."
    ),
    "stock-adjustment-user": (
        "StockAdjustment.user_id is required and the Users tag (7 operations) is outside issue #94's scoped "
        "surface, so there is no user entity to attribute an adjustment to. Every adjustment this unit "
        "records carries the retailer's own id. JUDGMENT."
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
