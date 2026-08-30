"""Turning a validated scenario into store state.

FOR: the one function ``CloverVendor.hydrate`` calls -- at start and again on
``POST /__unit/state/reset`` -- and therefore the one place that decides what
a unit's world looks like.

INVARIANT: **a seeded mutation is marked as one.** Every insert carries
``{"seed": True}`` in its journal meta, which is what stops the webhook
dispatcher pushing an event for a record that has existed since before the
process started, and what lets a journal assertion in a test tell scenario
writes from request traffic.

SECOND INVARIANT: **seeded ids come from the document, never from the id
stream.** That is what makes the state digest identical across two processes
and across every profile: the stream is consumed only by requests, and a
scenario that drew from it would renumber itself whenever a rule was added.
The only hydrate-time values are the token expirations and the tokens'
``createdTime`` (stamped from the clock and the configured TTLs), and both
are volatile fields the digest ignores.
"""

from __future__ import annotations

from typing import Any

from vendorfake.clover.config import CloverConfig
from vendorfake.clover.entities import COL, ItemEntity, MerchantEntity, OrderEntity, TokenEntity
from vendorfake.clover.seed.document import SeedDocument, SeedOrder, parse_seed_document
from vendorfake.core.kernel.types import UnitContext
from vendorfake.core.util.json import compact
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION

__all__ = ["SEED_META", "hydrate_clover"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}
"""Journal meta on every seeded write. See the module docstring."""


def hydrate_clover(ctx: UnitContext, seed: object, config: CloverConfig) -> SeedDocument | None:
    """Load ``seed`` into ``ctx.store``; ``None`` (a profile with no seed)
    loads nothing and is legal. Returns the document that was loaded so a
    caller can ask what the unit was seeded with without re-reading the file."""
    if seed is None:
        return None
    doc = parse_seed_document(seed)
    _insert_merchant(ctx, doc)
    _insert_reference_data(ctx, doc)
    _insert_items(ctx, doc)
    _insert_customers(ctx, doc)
    _insert_orders(ctx, doc)
    _insert_tokens(ctx, doc, config)
    _insert_subscriptions(ctx, doc)
    return doc


# ---------------------------------------------------------------------------
# One function per collection, in dependency order: an order needs its items,
# order type, employee and customers to already be there.
# ---------------------------------------------------------------------------


def _insert_merchant(ctx: UnitContext, doc: SeedDocument) -> None:
    merchant = doc.merchant
    ctx.store.collection(COL.merchants).insert(
        MerchantEntity(
            id=merchant.id,
            name=merchant.name,
            currency=merchant.currency,
            owner=None if merchant.owner is None else merchant.owner.model_dump(exclude_none=True),
            address=None if merchant.address is None else merchant.address.model_dump(exclude_none=True),
        ).to_entity(),
        SEED_META,
    )


def _insert_reference_data(ctx: UnitContext, doc: SeedDocument) -> None:
    """The read-only lists, stored as the documents their reference pages
    list (``surface/merchant.py``, ``surface/inventory.py``)."""
    store = ctx.store
    for rate in doc.tax_rates:
        store.collection(COL.tax_rates).insert(rate.model_dump(), SEED_META)
    for group in doc.modifier_groups:
        store.collection(COL.modifier_groups).insert(group.model_dump(), SEED_META)
    for modifier in doc.modifiers:
        store.collection(COL.modifiers).insert(modifier.model_dump(), SEED_META)
    # Employees, order types and customers carry the merchant they belong to
    # (``merchant_id``, internal, stripped on projection): an order's
    # references resolve against the path merchant's rows only.
    scope = {"merchant_id": doc.merchant.id}
    for employee in doc.employees:
        store.collection(COL.employees).insert(compact({**employee.model_dump(), **scope}), SEED_META)
    for tender in doc.tenders:
        store.collection(COL.tenders).insert(tender.model_dump(), SEED_META)
    for order_type in doc.order_types:
        store.collection(COL.order_types).insert(compact({**order_type.model_dump(), **scope}), SEED_META)
    for charge in doc.service_charges:
        store.collection(COL.service_charges).insert(charge.model_dump(), SEED_META)


def _insert_items(ctx: UnitContext, doc: SeedDocument) -> None:
    items = ctx.store.collection(COL.items)
    for item in doc.items:
        items.insert(
            ItemEntity(
                id=item.id,
                name=item.name,
                price=item.price,
                hidden=item.hidden,
                available=item.available,
                priceType=item.priceType,
                defaultTaxRates=item.defaultTaxRates,
                isRevenue=item.isRevenue,
                sku=item.sku,
                code=item.code,
                modifiedTime=item.modifiedTime,
                taxRates=tuple({"id": ref.id} for ref in item.taxRates),
                modifierGroupIds=tuple(item.modifierGroupIds),
            ).to_entity(),
            SEED_META,
        )


def _insert_customers(ctx: UnitContext, doc: SeedDocument) -> None:
    customers = ctx.store.collection(COL.customers)
    for customer in doc.customers:
        customers.insert(
            compact(
                {
                    "id": customer.id,
                    "merchant_id": doc.merchant.id,
                    "firstName": customer.firstName,
                    "lastName": customer.lastName,
                    "customerSince": customer.customerSince,
                    "addresses": [address.model_dump(exclude_none=True) for address in customer.addresses] or None,
                }
            ),
            SEED_META,
        )


def _insert_orders(ctx: UnitContext, doc: SeedDocument) -> None:
    orders = ctx.store.collection(COL.orders)
    items = {item.id: item for item in doc.items}
    for order in doc.orders:
        orders.insert(_order_entity(order, doc, items).to_entity(), SEED_META)


def _order_entity(order: SeedOrder, doc: SeedDocument, items: dict[str, Any]) -> OrderEntity:
    lines: list[dict[str, Any]] = []
    for line in order.lineItems:
        item = None if line.item is None else items[line.item.id]
        lines.append(
            compact(
                {
                    "id": line.id,
                    "name": line.name if line.name is not None else (None if item is None else item.name),
                    "price": line.price if line.price is not None else (0 if item is None else item.price),
                    "note": line.note,
                    "unitQty": line.unitQty,
                    "exchanged": False,
                    "refunded": False,
                    "item": None if item is None else {"id": item.id},
                }
            )
        )
    return OrderEntity(
        id=order.id,
        merchant_id=doc.merchant.id,
        currency=order.currency or doc.merchant.currency,
        total=order.total,
        state=order.state,
        paymentState=order.paymentState,
        createdTime=order.createdTime,
        modifiedTime=order.createdTime if order.modifiedTime is None else order.modifiedTime,
        clientCreatedTime=order.createdTime if order.clientCreatedTime is None else order.clientCreatedTime,
        title=order.title,
        note=order.note,
        externalReferenceId=order.externalReferenceId,
        orderType=None if order.orderType is None else {"id": order.orderType.id},
        employee=None if order.employee is None else {"id": order.employee.id},
        customers=tuple({"id": ref.id} for ref in order.customers),
        lineItems=tuple(lines),
    )


def _insert_tokens(ctx: UnitContext, doc: SeedDocument, config: CloverConfig) -> None:
    """Expirations come from the configured TTLs at hydrate time, so a
    profile that shortens the access TTL shortens the seeded token too."""
    tokens = ctx.store.collection(COL.tokens)
    now = int(ctx.clock.now())
    for token in doc.tokens:
        tokens.insert(
            TokenEntity(
                id=token.id,
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                client_id=token.client_id or config.client_id,
                merchant_id=doc.merchant.id,
                access_token_expiration_ms=now + config.access_token_ttl_ms,
                refresh_token_expiration_ms=now + config.refresh_token_ttl_ms,
                permissions=tuple(config.permissions if token.permissions is None else token.permissions),
                createdTime=now,
            ).to_entity(),
            SEED_META,
        )


def _insert_subscriptions(ctx: UnitContext, doc: SeedDocument) -> None:
    """Subscribers declared by the scenario rather than by the profile.

    Built as a plain dict because the subscription entity belongs to the core
    -- the dispatcher reads it through ``Subscription.from_entity`` -- and a
    vendor-side mirror of its field names would be a second place to keep
    them. No ``verified`` key: that is what the webhook surface reads as
    pre-verified.
    """
    subscriptions = ctx.store.collection(SUBSCRIPTION_COLLECTION)
    for subscription in doc.webhook_subscriptions:
        subscriptions.insert(
            compact(
                {
                    "id": subscription.id,
                    "name": subscription.name or "Seeded subscription",
                    "notification_url": subscription.notification_url,
                    "event_types": list(subscription.event_types),
                    "signature_key": subscription.signature_key,
                    "enabled": subscription.enabled,
                }
            ),
            SEED_META,
        )
