"""Turning a validated scenario into store state.

FOR: the one function ``SquareVendor.hydrate`` calls -- and therefore the one
place that decides what a unit's world looks like at start and again after
``POST /__unit/state/reset``.

INVARIANT: **a seeded mutation is marked as one.** Every insert below carries
``{"seed": True}`` in its journal meta, which is what stops the dispatcher
pushing an ``order.created`` for an order that has existed since before the
process started. Without it, subscribing to a fresh unit would deliver a
backlog of events for history, and a consumer counting webhooks would be
counting the scenario.

SECOND INVARIANT: **a reference that does not resolve is a startup failure.** An
order naming a location that is not in the document, or a line item naming a
catalog variation that is not, raises rather than inserting a half-formed
entity. The reference does the same, and the reason is worth stating: the
alternative is a scenario that loads and then produces an order whose totals
are silently zero.

Entities are built through the frozen dataclasses in
:mod:`vendorfake.square.entities` and inserted as ``to_entity()`` dicts, so
"absence is absence" is mechanical here rather than remembered -- ``compact()``
runs inside every one of them.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import UnitContext, UnitError, UnitErrorKind
from vendorfake.core.state.store import Collection
from vendorfake.core.util.json import compact
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION
from vendorfake.square.config import SquareConfig
from vendorfake.square.entities import (
    COL,
    CatalogObjectEntity,
    InventoryCountEntity,
    LocationEntity,
    LoyaltyAccountEntity,
    LoyaltyProgramEntity,
    MerchantEntity,
    Money,
    OrderEntity,
    OrderLineItem,
    Tender,
    TokenEntity,
)
from vendorfake.square.seed.document import (
    SeedDocument,
    SeedLineItem,
    SeedOrder,
    SeedTender,
    parse_seed_document,
)

__all__ = ["SEED_META", "hydrate_square"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}
"""Journal meta on every seeded write. See the module docstring."""


def hydrate_square(ctx: UnitContext, seed: object, config: SquareConfig) -> SeedDocument:
    """Load ``seed`` into ``ctx.store``. Returns the document that was loaded.

    The document is returned rather than discarded so that a caller -- a test,
    or a future control route reporting what the unit was seeded with -- can
    ask what was loaded without re-reading the file.
    """
    doc = parse_seed_document(seed)
    _insert_merchant(ctx, doc)
    _insert_locations(ctx, doc)
    _insert_catalog(ctx, doc)
    _insert_orders(ctx, doc)
    _insert_loyalty(ctx, doc)
    _insert_inventory(ctx, doc)
    _insert_tokens(ctx, doc, config)
    _insert_subscriptions(ctx, doc, config)
    return doc


# ---------------------------------------------------------------------------
# One function per collection, in dependency order: an order needs its location
# and its catalog variations to already be there.
# ---------------------------------------------------------------------------


def _insert_merchant(ctx: UnitContext, doc: SeedDocument) -> None:
    merchant = doc.merchant
    entity = MerchantEntity(
        id=merchant.id,
        business_name=merchant.business_name,
        country=merchant.country,
        language_code=merchant.language_code,
        currency=merchant.currency,
    ).to_entity()
    if merchant.created_at is not None:
        entity["created_at"] = merchant.created_at
    ctx.store.collection(COL.merchants).insert(entity, SEED_META)


def _insert_locations(ctx: UnitContext, doc: SeedDocument) -> None:
    merchant = doc.merchant
    locations = ctx.store.collection(COL.locations)
    for location in doc.locations:
        entity = LocationEntity(
            id=location.id,
            merchant_id=merchant.id,
            name=location.name,
            business_name=merchant.business_name,
            timezone=location.timezone,
            capabilities=location.capabilities,
            status=location.status,
            country=location.country or merchant.country,
            language_code=location.language_code or merchant.language_code,
            currency=location.currency or merchant.currency,
            type=location.type,
            address=location.address,
            phone_number=location.phone_number,
        ).to_entity()
        if location.created_at is not None:
            # Stated rather than defaulted: a scenario is entitled to say this
            # location opened in 2016, and `Collection.insert` fills the key
            # from the clock only when it is absent.
            entity["created_at"] = location.created_at
        locations.insert(entity, SEED_META)


def _insert_catalog(ctx: UnitContext, doc: SeedDocument) -> None:
    if doc.catalog is None:
        return
    catalog = ctx.store.collection(COL.catalog)
    for item in doc.catalog.items:
        entity = CatalogObjectEntity(
            id=item.id,
            object_type="ITEM",
            catalog_version=item.catalog_version,
            item_name=item.name,
            item_description=item.description,
        ).to_entity()
        if item.updated_at is not None:
            entity["updated_at"] = item.updated_at
        catalog.insert(entity, SEED_META)
        for variation in item.variations:
            child = CatalogObjectEntity(
                id=variation.id,
                object_type="ITEM_VARIATION",
                catalog_version=item.catalog_version,
                item_id=item.id,
                variation_name=variation.name,
                pricing_type=variation.pricing_type,
                price_money=Money(amount=variation.price_money.amount, currency=variation.price_money.currency),
            ).to_entity()
            if item.updated_at is not None:
                child["updated_at"] = item.updated_at
            catalog.insert(child, SEED_META)


def _insert_orders(ctx: UnitContext, doc: SeedDocument) -> None:
    locations = ctx.store.collection(COL.locations)
    catalog = ctx.store.collection(COL.catalog)
    orders = ctx.store.collection(COL.orders)
    for order in doc.orders:
        stored_location = locations.get(order.location_id)
        if stored_location is None:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=f"Seed order {order.id} references unknown location {order.location_id}.",
                info={"order": order.id, "location": order.location_id},
            )
        location = LocationEntity.from_entity(stored_location)
        entity = OrderEntity(
            id=order.id,
            location_id=order.location_id,
            merchant_id=doc.merchant.id,
            currency=location.currency,
            state=order.state,
            line_items=tuple(_resolve_line_item(catalog, order, line) for line in order.line_items),
            reference_id=order.reference_id,
            customer_id=order.customer_id,
            source_name=order.source_name,
            ticket_name=order.ticket_name,
            tenders=tuple(_resolve_tender(order, tender) for tender in order.tenders),
            closed_at=order.closed_at,
            version=order.version,
            created_at=order.created_at or "",
            updated_at=order.updated_at or "",
        ).to_entity()
        orders.insert(entity, SEED_META)


def _resolve_tender(order: SeedOrder, tender: SeedTender) -> Tender:
    """A seeded payment, with the fields PayOrder would have filled in.

    ``location_id`` and ``transaction_id`` default to the order's, and
    ``created_at`` to the moment the order was last touched, so a scenario
    states the amount and the id and nothing that can contradict the order it
    belongs to.
    """
    return Tender(
        id=tender.id,
        location_id=tender.location_id or order.location_id,
        transaction_id=tender.transaction_id or order.id,
        created_at=tender.created_at or order.updated_at or order.created_at or "",
        amount_money=Money(amount=tender.amount_money.amount, currency=tender.amount_money.currency),
        type=tender.type,
        payment_id=tender.payment_id or tender.id,
    )


def _resolve_line_item(catalog: Collection, order: SeedOrder, line: SeedLineItem) -> OrderLineItem:
    """Fill a seeded line's price and names from the catalog variation it names.

    A line that names no ``catalog_object_id`` must carry its own
    ``base_price_money``; a line that names one inherits price, variation name
    and item name from it. Either way a line with no price is a scenario
    defect, not a zero-priced order.
    """
    price = (
        None
        if line.base_price_money is None
        else Money(amount=line.base_price_money.amount, currency=line.base_price_money.currency)
    )
    name = line.name
    variation_name = line.variation_name

    if line.catalog_object_id is not None:
        stored = catalog.get(line.catalog_object_id)
        variation = None if stored is None else CatalogObjectEntity.from_entity(stored)
        if variation is None or not variation.is_variation:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=(f"Seed order {order.id} references unknown catalog variation {line.catalog_object_id}."),
                info={"order": order.id, "catalog_object_id": line.catalog_object_id},
            )
        price = price or variation.price_money
        variation_name = variation_name or variation.variation_name
        if name is None and variation.item_id is not None:
            parent = catalog.get(variation.item_id)
            name = None if parent is None else CatalogObjectEntity.from_entity(parent).item_name

    if price is None:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=f"Seed order {order.id} line item {line.uid} has no price.",
            info={"order": order.id, "uid": line.uid},
        )
    return OrderLineItem(
        uid=line.uid,
        quantity=line.quantity,
        base_price_money=price,
        name=name,
        note=line.note,
        catalog_object_id=line.catalog_object_id,
        variation_name=variation_name,
    )


def _insert_loyalty(ctx: UnitContext, doc: SeedDocument) -> None:
    """The program, then the accounts that belong to it.

    An account with no program is a scenario defect: Square accounts exist
    only inside a program, and a unit that loaded them anyway would answer
    the search and then fail every accumulation.
    """
    program = doc.loyalty_program
    if program is None:
        if doc.loyalty_accounts:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail="Seed loyalty_accounts need a loyalty_program to belong to.",
                info={"accounts": len(doc.loyalty_accounts)},
            )
        return
    location_ids = (
        tuple(location.id for location in doc.locations) if program.location_ids is None else program.location_ids
    )
    entity = LoyaltyProgramEntity(
        id=program.id,
        merchant_id=doc.merchant.id,
        status=program.status,
        terminology_one=program.terminology_one,
        terminology_other=program.terminology_other,
        location_ids=location_ids,
        accrual_points=program.accrual_points,
        spend_amount=Money(amount=program.spend_amount.amount, currency=program.spend_amount.currency),
        tax_mode=program.tax_mode,
        reward_tiers=tuple(
            compact(
                {
                    "id": tier.id,
                    "points": tier.points,
                    "name": tier.name,
                    "pricing_rule_reference": compact(
                        {
                            "object_id": tier.pricing_rule_reference.object_id,
                            "catalog_version": tier.pricing_rule_reference.catalog_version,
                        }
                    ),
                    "created_at": tier.created_at,
                }
            )
            for tier in program.reward_tiers
        ),
    ).to_entity()
    if program.created_at is not None:
        entity["created_at"] = program.created_at
        entity["updated_at"] = program.created_at
    ctx.store.collection(COL.loyalty_programs).insert(entity, SEED_META)

    accounts = ctx.store.collection(COL.loyalty_accounts)
    for index, account in enumerate(doc.loyalty_accounts, start=1):
        entity = LoyaltyAccountEntity(
            id=account.id,
            program_id=program.id,
            customer_id=account.customer_id,
            phone_number=account.phone_number,
            mapping_id=account.mapping_id or f"{account.id}-mapping-{index:02d}",
            balance=account.balance,
            lifetime_points=account.lifetime_points,
            enrolled_at=account.enrolled_at or "",
            mapping_created_at=account.enrolled_at or "",
        ).to_entity()
        if account.enrolled_at is not None:
            entity["created_at"] = account.enrolled_at
            entity["updated_at"] = account.enrolled_at
        accounts.insert(entity, SEED_META)


def _insert_inventory(ctx: UnitContext, doc: SeedDocument) -> None:
    """Stock counts, each naming a variation and a location that exist."""
    catalog = ctx.store.collection(COL.catalog)
    locations = ctx.store.collection(COL.locations)
    counts = ctx.store.collection(COL.inventory_counts)
    for count in doc.inventory_counts:
        stored = catalog.get(count.catalog_object_id)
        if stored is None or not CatalogObjectEntity.from_entity(stored).is_variation:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=f"Seed inventory count references unknown catalog variation {count.catalog_object_id}.",
                info={"catalog_object_id": count.catalog_object_id},
            )
        if locations.get(count.location_id) is None:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=f"Seed inventory count references unknown location {count.location_id}.",
                info={"location_id": count.location_id},
            )
        entity = InventoryCountEntity(
            catalog_object_id=count.catalog_object_id,
            location_id=count.location_id,
            quantity=count.quantity,
            calculated_at=count.calculated_at or "",
        ).to_entity()
        if count.calculated_at is not None:
            entity["created_at"] = count.calculated_at
            entity["updated_at"] = count.calculated_at
        counts.insert(entity, SEED_META)


def _insert_tokens(ctx: UnitContext, doc: SeedDocument, config: SquareConfig) -> None:
    tokens = ctx.store.collection(COL.tokens)
    for index, token in enumerate(doc.tokens, start=1):
        ttl_ms = config.access_token_ttl_ms if token.expires_in_ms is None else token.expires_in_ms
        tokens.insert(
            TokenEntity(
                id=token.id or f"tok_seed_{index:02d}",
                access_token=token.access_token,
                refresh_token=token.refresh_token,
                client_id=token.client_id or config.application_id,
                merchant_id=doc.merchant.id,
                expires_at=ctx.clock.iso_seconds(ttl_ms),
                scopes=token.scopes,
                short_lived=token.short_lived,
                flow=token.flow,
            ).to_entity(),
            SEED_META,
        )


def _insert_subscriptions(ctx: UnitContext, doc: SeedDocument, config: SquareConfig) -> None:
    """Subscribers declared by the scenario rather than by the profile.

    Built as a plain dict rather than through a dataclass because the
    subscription entity belongs to the core -- the dispatcher reads it through
    ``Subscription.from_entity`` -- and a vendor-side mirror of its field names
    would be a second place to keep them.
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
                    "api_version": config.api_version,
                }
            ),
            SEED_META,
        )
