"""Turning a validated scenario into store state; the one function
``ToastVendor.hydrate`` calls, at start and again on
``POST /__unit/state/reset``.

INVARIANT: a seeded mutation is marked as one -- every insert carries
``{"seed": True}`` in its journal meta, which stops the webhook dispatcher
pushing an event for a record that existed before the process started.

INVARIANT: seeded ids come from the document, never the id stream; the only
hydrate-time values are token expirations and creation instants, both
volatile fields the digest ignores.

JUDGMENT: the config API's ``menuItems``/``menuGroups``/``menus`` are derived
from the V3 menu at hydrate, so the scenario has one menu and both APIs agree
by construction; the partners row is likewise derived from the restaurant
plus the seed's ``partner`` block.
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.types import UnitContext
from vendorfake.core.util.json import compact
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION
from vendorfake.toast.config import ToastConfig
from vendorfake.toast.entities import COL, RestaurantEntity, TokenEntity
from vendorfake.toast.model.build import MenuIndex, build_check
from vendorfake.toast.model.config import MODIFIED_KEY
from vendorfake.toast.model.dates import business_date
from vendorfake.toast.model.order import CheckRequest, SelectionRequest
from vendorfake.toast.model.references import RefRequest
from vendorfake.toast.seed.document import SeedDocument, SeedMenuGroup, SeedSelection, parse_seed_document

__all__ = ["SEED_META", "hydrate_toast"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}


def hydrate_toast(ctx: UnitContext, seed: object, config: ToastConfig) -> SeedDocument | None:
    """Load ``seed`` into ``ctx.store``; ``None`` loads nothing and is legal."""
    if seed is None:
        return None
    doc = parse_seed_document(seed)
    _insert_restaurant(ctx, doc)
    _insert_partner(ctx, doc)
    _insert_config(ctx, doc)
    _insert_menu(ctx, doc)
    _insert_credit_authorizations(ctx, doc)
    _insert_stock(ctx, doc)
    _insert_orders(ctx, doc, config)
    _insert_tokens(ctx, doc, config)
    _insert_subscriptions(ctx, doc)
    return doc


def _insert_subscriptions(ctx: UnitContext, doc: SeedDocument) -> None:
    """Subscribers declared by the scenario, as plain dicts: the subscription
    entity belongs to the core."""
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


def _insert_stock(ctx: UnitContext, doc: SeedDocument) -> None:
    """One row per seeded item or option, under the item's guid, with the
    item's ``multiLocationId`` read off the menu."""
    if doc.menu_v3 is None:
        return
    index = MenuIndex.from_store(ctx.store, doc.restaurant.guid)
    for row in doc.stock:
        source = index.items.get(row.guid) or index.options.get(row.guid) or {}
        ctx.store.collection(COL.stock).insert(
            compact(
                {
                    "id": row.guid,
                    "restaurant_guid": doc.restaurant.guid,
                    "multiLocationId": source.get("multiLocationId"),
                    "status": row.status,
                    "quantity": row.quantity,
                    "versionId": row.versionId,
                    "modifiedDate": doc.config_modified_ms,
                }
            ),
            SEED_META,
        )


def _insert_credit_authorizations(ctx: UnitContext, doc: SeedDocument) -> None:
    for authorization in doc.credit_authorizations:
        ctx.store.collection(COL.credit_authorizations).insert(
            {"id": authorization.guid, **authorization.model_dump(exclude={"guid"})}, SEED_META
        )


def _seed_selection(selection: SeedSelection) -> SelectionRequest:
    return SelectionRequest(
        item=RefRequest(guid=selection.item),
        quantity=selection.quantity,
        externalId=selection.externalId,
        preModifier=None if selection.preModifier is None else RefRequest(guid=selection.preModifier),
        modifiers=[_seed_selection(modifier) for modifier in selection.modifiers],
    )


def _assign_guids(built: dict[str, Any], seeded: SeedSelection) -> None:
    built["guid"] = seeded.guid
    for built_modifier, seeded_modifier in zip(built.get("modifiers", []), seeded.modifiers, strict=True):
        _assign_guids(built_modifier, seeded_modifier)


def _insert_orders(ctx: UnitContext, doc: SeedDocument, config: ToastConfig) -> None:
    """Seeded orders are priced by the same builder the surfaces use, with
    the seed's guids written over the builder's ``None``s -- so a seeded
    amount can never disagree with what ``/prices`` would say."""
    if not doc.orders:
        return
    restaurant = doc.restaurant
    index = MenuIndex.from_store(ctx.store, restaurant.guid)
    dining = {option.guid: option for option in doc.dining_options}
    tables = {table.guid: table for table in doc.tables}
    for position, order in enumerate(doc.orders):
        checks = []
        for check in order.checks:
            request = CheckRequest(
                externalId=check.externalId,
                tabName=check.tabName,
                selections=[_seed_selection(selection) for selection in check.selections],
            )
            built = build_check(
                index, request, now=order.openedDate, mint=None, field="", display_number=str(position + 1)
            )
            built["guid"] = check.guid
            for built_selection, seeded in zip(built["selections"], check.selections, strict=True):
                _assign_guids(built_selection, seeded)
            checks.append(built)
        table = None if order.table is None else tables[order.table]
        ctx.store.collection(COL.orders).insert(
            compact(
                {
                    "id": order.guid,
                    "restaurant_guid": restaurant.guid,
                    "client_id": config.client_id,
                    "externalId": order.externalId,
                    "openedDate": order.openedDate,
                    "modifiedDate": order.openedDate,
                    "createdDate": order.openedDate,
                    "businessDate": business_date(
                        order.openedDate,
                        time_zone=restaurant.general.timeZone,
                        closeout_hour=restaurant.general.closeoutHour,
                    ),
                    "diningOption": {
                        "guid": order.diningOption,
                        "entityType": "DiningOption",
                        "externalId": dining[order.diningOption].externalId,
                    },
                    "checks": checks,
                    "table": None if table is None else {"guid": table.guid, "entityType": "Table"},
                    "serviceArea": None if table is None else _ref(table.serviceArea, "ServiceArea"),
                    "revenueCenter": None if table is None else _ref(table.revenueCenter, "RevenueCenter"),
                    "source": "API",
                    "approvalStatus": "APPROVED",
                    "guestOrderStatus": "RECEIVED",
                    "voided": False,
                    "numberOfGuests": order.numberOfGuests,
                    "pricingFeatures": [],
                    "createdInTestMode": False,
                    "displayNumber": str(position + 1),
                }
            ),
            SEED_META,
        )


def _insert_restaurant(ctx: UnitContext, doc: SeedDocument) -> None:
    restaurant = doc.restaurant
    ctx.store.collection(COL.restaurants).insert(
        RestaurantEntity(
            id=restaurant.guid,
            general=restaurant.general.model_dump(exclude_none=True),
            location=dict(restaurant.location),
            urls=dict(restaurant.urls),
            schedules=dict(restaurant.schedules),
            delivery=dict(restaurant.delivery),
            onlineOrdering=dict(restaurant.onlineOrdering),
            prepTimes=dict(restaurant.prepTimes),
        ).to_entity(),
        SEED_META,
    )


def _insert_partner(ctx: UnitContext, doc: SeedDocument) -> None:
    if doc.partner is None:
        return
    general = doc.restaurant.general
    ctx.store.collection(COL.partners).insert(
        {
            "id": doc.restaurant.guid,
            "managementGroupGuid": general.managementGroupGuid,
            "restaurantName": general.name,
            "locationName": general.locationName,
            "createdByEmailAddress": doc.partner.createdByEmailAddress,
            "externalGroupRef": doc.partner.externalGroupRef,
            "externalRestaurantRef": doc.partner.externalRestaurantRef,
            "modifiedDate": doc.partner.modifiedDate,
            "createdDate": doc.partner.createdDate,
            "deleted": False,
        },
        SEED_META,
    )


def _ref(guid: str, entity_type: str) -> dict[str, str]:
    return {"guid": guid, "entityType": entity_type}


def _insert_config(ctx: UnitContext, doc: SeedDocument) -> None:
    """The reference lists, stored as the config specification shapes them
    (``model/config.py``), money in cents, plus the internal modified instant."""
    store = ctx.store
    stamp = {MODIFIED_KEY: doc.config_modified_ms}

    def put(collection: str, guid: str, fields: dict[str, Any]) -> None:
        store.collection(collection).insert({"id": guid, **fields, **stamp}, SEED_META)

    for option in doc.dining_options:
        put(COL.dining_options, option.guid, option.model_dump(exclude={"guid"}))
    for kind in doc.alternate_payment_types:
        put(COL.alternate_payment_types, kind.guid, kind.model_dump(exclude={"guid"}))
    for rate in doc.tax_rates:
        put(COL.tax_rates, rate.guid, rate.model_dump(exclude={"guid"}))
    for center in doc.revenue_centers:
        put(COL.revenue_centers, center.guid, center.model_dump(exclude={"guid"}))
    for area in doc.service_areas:
        put(
            COL.service_areas,
            area.guid,
            {
                **area.model_dump(exclude={"guid", "revenueCenter"}),
                "revenueCenter": _ref(area.revenueCenter, "RevenueCenter"),
            },
        )
    for table in doc.tables:
        put(
            COL.tables,
            table.guid,
            {
                **table.model_dump(exclude={"guid", "serviceArea", "revenueCenter"}),
                "serviceArea": _ref(table.serviceArea, "ServiceArea"),
                "revenueCenter": _ref(table.revenueCenter, "RevenueCenter"),
            },
        )
    for service in doc.restaurant_services:
        put(COL.restaurant_services, service.guid, service.model_dump(exclude={"guid"}))
    for discount in doc.discounts:
        put(COL.discounts, discount.guid, discount.model_dump(exclude={"guid"}))
    for charge in doc.service_charges:
        put(COL.service_charges, charge.guid, charge.model_dump(exclude={"guid"}))
    for reason in doc.void_reasons:
        put(COL.void_reasons, reason.guid, reason.model_dump(exclude={"guid"}))


def _insert_menu(ctx: UnitContext, doc: SeedDocument) -> None:
    """The V3 document as one entity, then the config-API views derived from it."""
    menu = doc.menu_v3
    if menu is None:
        return
    store = ctx.store
    store.collection(COL.menus).insert(
        {
            "id": doc.restaurant.guid,
            "lastUpdated": menu.lastUpdated,
            "menus": [m.model_dump() for m in menu.menus],
            "modifierGroups": [g.model_dump() for g in menu.modifierGroups],
            "modifierOptions": [o.model_dump() for o in menu.modifierOptions],
            "preModifierGroups": [p.model_dump() for p in menu.preModifierGroups],
        },
        SEED_META,
    )
    stamp = {MODIFIED_KEY: doc.config_modified_ms}
    groups_by_ref = {group.referenceId: group for group in menu.modifierGroups}
    for menu_doc in menu.menus:
        store.collection(COL.config_menus).insert(
            {
                "id": menu_doc.guid,
                "externalId": None,
                "name": menu_doc.name,
                "groups": [_ref(group.guid, "MenuGroup") for group in menu_doc.menuGroups],
                **stamp,
            },
            SEED_META,
        )
        _insert_groups(ctx, doc, menu_doc.guid, menu_doc.menuGroups, groups_by_ref, stamp)


def _insert_groups(
    ctx: UnitContext,
    doc: SeedDocument,
    menu_guid: str,
    groups: list[SeedMenuGroup],
    groups_by_ref: dict[int, Any],
    stamp: dict[str, Any],
) -> None:
    store = ctx.store
    for group in groups:
        store.collection(COL.menu_groups).insert(
            {
                "id": group.guid,
                "externalId": None,
                "name": group.name,
                "menu": _ref(menu_guid, "Menu"),
                "items": [
                    compact({"guid": item.guid, "entityType": "MenuItem", "multiLocationId": item.multiLocationId})
                    for item in group.menuItems
                ],
                "subgroups": [_ref(sub.guid, "MenuGroup") for sub in group.menuGroups],
                "optionGroups": [],
                **stamp,
            },
            SEED_META,
        )
        for item in group.menuItems:
            option_groups = [
                _ref(groups_by_ref[ref].guid, "MenuOptionGroup")
                for ref in item.modifierGroupReferences
                if ref in groups_by_ref
            ]
            store.collection(COL.menu_items).insert(
                {
                    "id": item.guid,
                    "externalId": None,
                    "name": item.name,
                    "calories": item.calories,
                    "sku": item.sku,
                    "plu": item.plu,
                    "type": "NONE",
                    "optionGroups": option_groups,
                    "inheritOptionGroups": False,
                    "unitOfMeasure": item.unitOfMeasure,
                    "inheritUnitOfMeasure": False,
                    **stamp,
                },
                SEED_META,
            )
        _insert_groups(ctx, doc, menu_guid, group.menuGroups, groups_by_ref, stamp)


def _insert_tokens(ctx: UnitContext, doc: SeedDocument, config: ToastConfig) -> None:
    """Expirations come from the configured TTL at hydrate time."""
    tokens = ctx.store.collection(COL.tokens)
    now = int(ctx.clock.now())
    for token in doc.tokens:
        tokens.insert(
            TokenEntity(
                id=token.id,
                access_token=token.access_token,
                client_id=token.client_id or config.client_id,
                partner_guid=config.partner_guid,
                expires_at_ms=now + config.access_token_ttl_ms,
                scopes=tuple(config.scopes if token.scopes is None else token.scopes),
                createdDate=now,
            ).to_entity(),
            SEED_META,
        )
