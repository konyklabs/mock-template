"""Turning a validated scenario into store state.

FOR: the one function ``LightspeedVendor.hydrate`` calls -- at start and again
on ``POST /__unit/state/reset``.

INVARIANT: **a seeded mutation is marked as one.** Every insert carries
``{"seed": True}`` in its journal meta, which is what stops the webhook
dispatcher pushing an event for a record that existed before the process
started.

SECOND INVARIANT: **seeded ids come from the document, never from the id
stream.** The only values this function computes are the Lightspeed
``version`` numbers -- drawn from the retailer's counter in document order, so
two units stamp the same sequence -- and the token expiries, which come from
the unit's clock.

THE ORDER IS THE VERSION ORDER. Entities are inserted retailer, outlets,
registers, payment types, products, inventory, customer groups, customers,
adjustment reasons, stock adjustments -- so the version numbers ascend in that
order and a consumer paging ``/outlets`` sees them in the order the document
lists them. Changing the order here renumbers every entity, which is why it is
stated rather than incidental, and why the products of konyklabs/roadmap#94's
second slice were appended AFTER the payment types rather than slotted in
beside them.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import UnitContext
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.auth import KIND_OAUTH, KIND_PERSONAL
from vendorfake.lightspeed.config import LightspeedConfig
from vendorfake.lightspeed.entities import (
    COL,
    OBJECT_VERSION,
    AdjustmentReasonEntity,
    CustomerEntity,
    CustomerGroupEntity,
    InventoryEntity,
    OutletEntity,
    PaymentTypeEntity,
    ProductEntity,
    RefreshTokenEntity,
    RegisterEntity,
    RetailerEntity,
    StockAdjustmentEntity,
    TokenEntity,
)
from vendorfake.lightspeed.model.customer import customer_document
from vendorfake.lightspeed.model.product import product_document
from vendorfake.lightspeed.model.scalars import decimal_text
from vendorfake.lightspeed.seed.document import SeedDocument, parse_seed_document
from vendorfake.lightspeed.versioning import LightspeedVersions

__all__ = ["SEED_META", "hydrate_lightspeed"]

SEED_META = {"seed": True, "operation_id": "SeedScenario"}


def hydrate_lightspeed(
    ctx: UnitContext,
    seed: object,
    config: LightspeedConfig,
    versions: LightspeedVersions,
) -> SeedDocument | None:
    """Load ``seed`` into ``ctx.store``; ``None`` loads nothing and is legal."""
    if seed is None:
        return None
    doc = parse_seed_document(seed)
    _insert_retailer(ctx, doc, versions)
    _insert_outlets(ctx, doc, versions)
    _insert_registers(ctx, doc, versions)
    _insert_payment_types(ctx, doc, versions)
    _insert_products(ctx, doc, versions)
    _insert_inventory(ctx, doc, versions)
    _insert_customers(ctx, doc, versions)
    _insert_adjustments(ctx, doc, versions)
    _insert_tokens(ctx, doc, config)
    _insert_webhooks(ctx, doc, config)
    return doc


def _insert_retailer(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    retailer = doc.retailer
    ctx.store.collection(COL.retailer).insert(
        RetailerEntity(
            id=retailer.id,
            name=retailer.name,
            domain_prefix=retailer.domain_prefix,
            currency_code=retailer.currency_code,
            currency_symbol=retailer.currency_symbol,
            timezone=retailer.timezone,
            country=retailer.country,
            document=dict(retailer.document),
            object_version=versions.bump(),
        ).to_entity(),
        SEED_META,
    )


def _insert_outlets(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    outlets = ctx.store.collection(COL.outlets)
    for outlet in doc.outlets:
        outlets.insert(
            OutletEntity(
                id=outlet.id,
                name=outlet.name,
                currency=outlet.currency,
                currency_symbol=outlet.currency_symbol,
                display_prices=outlet.display_prices,
                time_zone=outlet.time_zone,
                default_tax_id=outlet.default_tax_id,
                attributes=[dict(row) for row in outlet.attributes],
                physical_address_1=outlet.physical_address_1,
                physical_suburb=outlet.physical_suburb,
                physical_city=outlet.physical_city,
                physical_state=outlet.physical_state,
                physical_postcode=outlet.physical_postcode,
                physical_country_id=outlet.physical_country_id,
                email=outlet.email,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_registers(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    registers = ctx.store.collection(COL.registers)
    for register in doc.registers:
        registers.insert(
            RegisterEntity(
                id=register.id,
                name=register.name,
                outlet_id=register.outlet_id,
                is_open=register.is_open,
                invoice_prefix=register.invoice_prefix,
                invoice_suffix=register.invoice_suffix,
                invoice_sequence=register.invoice_sequence,
                ask_for_note_on_save=register.ask_for_note_on_save,
                ask_for_user_on_sale=register.ask_for_user_on_sale,
                email_receipt=register.email_receipt,
                print_receipt=register.print_receipt,
                print_note_on_receipt=register.print_note_on_receipt,
                is_quick_keys_enabled=register.is_quick_keys_enabled,
                show_discounts_on_receipts=register.show_discounts_on_receipts,
                receipt_template_id=register.receipt_template_id,
                button_layout_id=register.button_layout_id,
                cash_managed_payment_type_id=register.cash_managed_payment_type_id,
                register_open_sequence_id=register.register_open_sequence_id,
                register_open_time=register.register_open_time,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_payment_types(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    payment_types = ctx.store.collection(COL.payment_types)
    for payment_type in doc.payment_types:
        payment_types.insert(
            PaymentTypeEntity(
                id=payment_type.id,
                name=payment_type.name,
                type_id=payment_type.type_id,
                disabled=payment_type.disabled,
                internal=payment_type.internal,
                gateway=payment_type.gateway,
                name_changed_by_user=payment_type.name_changed_by_user,
                config=None if payment_type.config is None else dict(payment_type.config),
                outlet_ids=tuple(payment_type.outlet_ids),
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_products(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    products = ctx.store.collection(COL.products)
    for index, product in enumerate(doc.products):
        where = f"products[{index}]."
        products.insert(
            ProductEntity(
                id=product.id,
                name=product.name,
                handle=product.handle or product.sku,
                sku=product.sku,
                family_id=product.family_id,
                price_excluding_tax=decimal_text(product.price_excluding_tax, field=f"{where}price_excluding_tax"),
                price_including_tax=decimal_text(product.price_including_tax, field=f"{where}price_including_tax"),
                supply_price=decimal_text(product.supply_price, field=f"{where}supply_price"),
                has_inventory=product.has_inventory,
                has_variants=product.has_variants,
                variant_parent_id=product.variant_parent_id,
                variant_name=product.variant_name or product.name,
                variant_count=product.variant_count,
                variant_options=[dict(row) for row in product.variant_options],
                document=product_document(
                    active=product.active,
                    description=product.description,
                    tag_ids=list(product.tag_ids),
                    attributes=[dict(row) for row in product.attributes],
                    product_codes=[dict(row) for row in product.product_codes],
                    outlet_taxes=[dict(row) for row in product.outlet_taxes],
                ),
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_inventory(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    inventory = ctx.store.collection(COL.inventory)
    for index, record in enumerate(doc.inventory):
        where = f"inventory[{index}]."
        inventory.insert(
            InventoryEntity(
                id=record.id,
                product_id=record.product_id,
                outlet_id=record.outlet_id,
                current_inventory_level=decimal_text(
                    record.current_inventory_level,
                    field=f"{where}current_inventory_level",
                    allow_negative=True,
                ),
                average_cost=_optional(record.average_cost, f"{where}average_cost"),
                reorder_point=_optional(record.reorder_point, f"{where}reorder_point"),
                reorder_amount=_optional(record.reorder_amount, f"{where}reorder_amount"),
                reorder_target=_optional(record.reorder_target, f"{where}reorder_target"),
                reorder_method=record.reorder_method,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_customers(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    """The groups first, then the customers: a customer references a group and
    the version order should read the same way the dependency does."""
    retailer_id = doc.retailer.id
    groups = ctx.store.collection(COL.customer_groups)
    for group in doc.customer_groups:
        groups.insert(
            CustomerGroupEntity(
                id=group.id,
                name=group.name,
                retailer_id=retailer_id,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )
    default_group = doc.customer_groups[0].id if doc.customer_groups else ""
    customers = ctx.store.collection(COL.customers)
    for index, customer in enumerate(doc.customers):
        where = f"customers[{index}]."
        customers.insert(
            CustomerEntity(
                id=customer.id,
                first_name=customer.first_name,
                last_name=customer.last_name,
                customer_code=customer.customer_code,
                customer_group_id=customer.customer_group_id or default_group,
                email=customer.email,
                balance=decimal_text(customer.balance, field=f"{where}balance", allow_negative=True),
                loyalty_balance=decimal_text(
                    customer.loyalty_balance, field=f"{where}loyalty_balance", allow_negative=True
                ),
                year_to_date=decimal_text(customer.year_to_date, field=f"{where}year_to_date", allow_negative=True),
                document=customer_document(customer.document),
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _insert_adjustments(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    """The reasons first, then the log that may reference them."""
    reasons = ctx.store.collection(COL.adjustment_reasons)
    for reason in doc.adjustment_reasons:
        reasons.insert(
            AdjustmentReasonEntity(
                id=reason.id,
                name=reason.name,
                type=reason.type,
                enabled=reason.enabled,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )
    adjustments = ctx.store.collection(COL.stock_adjustments)
    for index, adjustment in enumerate(doc.stock_adjustments):
        adjustments.insert(
            StockAdjustmentEntity(
                id=adjustment.id,
                product_id=adjustment.product_id,
                outlet_id=adjustment.outlet_id,
                quantity=decimal_text(
                    adjustment.quantity, field=f"stock_adjustments[{index}].quantity", allow_negative=True
                ),
                reason=adjustment.reason,
                user_id=doc.retailer.id,
                custom_inventory_adjustment_reason_id=adjustment.custom_inventory_adjustment_reason_id,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _optional(value: str | None, field: str) -> str | None:
    return None if value is None else decimal_text(value, field=field, allow_negative=True)


def _insert_tokens(ctx: UnitContext, doc: SeedDocument, config: LightspeedConfig) -> None:
    """The three kinds of credential a scenario can pre-issue.

    A token draws no version: credentials are not a versioned resource in this
    API -- ``VersionsData``'s key list names ``users`` and thirty-two other
    resource types and no token of any sort -- so nothing here bumps the
    counter.
    """
    now = int(ctx.clock.now())
    tokens = ctx.store.collection(COL.tokens)
    for token in doc.tokens:
        ttl_s = config.access_token_ttl_s if token.expires_in_s is None else token.expires_in_s
        tokens.insert(
            TokenEntity(
                id=token.id,
                access_token=token.access_token,
                client_id=config.client_id,
                scopes=tuple(token.scopes),
                kind=KIND_OAUTH,
                expires_at_ms=now + ttl_s * 1000,
                created_at_ms=now,
            ).to_entity(),
            SEED_META,
        )
    for personal in doc.personal_tokens:
        tokens.insert(
            TokenEntity(
                id=personal.id,
                access_token=personal.access_token,
                client_id=config.client_id,
                scopes=tuple(personal.scopes),
                kind=KIND_PERSONAL,
                # No expiry: an admin creates a personal token in the web
                # application and the docs state no lifetime for one.
                expires_at_ms=None,
                created_at_ms=now,
            ).to_entity(),
            SEED_META,
        )
    refresh_tokens = ctx.store.collection(COL.refresh_tokens)
    for refresh in doc.refresh_tokens:
        refresh_tokens.insert(
            RefreshTokenEntity(
                id=refresh.id,
                refresh_token=refresh.refresh_token,
                client_id=config.client_id,
                scopes=tuple(refresh.scopes),
                access_token_id=refresh.access_token_id,
                created_at_ms=now,
            ).to_entity(),
            SEED_META,
        )
    ctx.store.collection(COL.oauth_apps).insert(
        compact(
            {
                "id": config.client_id,
                "client_secret": config.client_secret,
                "redirect_uri": config.redirect_uri,
                "scopes": list(config.scopes),
                OBJECT_VERSION: None,
            }
        ),
        SEED_META,
    )


def _insert_webhooks(ctx: UnitContext, doc: SeedDocument, config: LightspeedConfig) -> None:
    """Subscriptions declared by the scenario, as plain dicts: the subscription
    entity belongs to the core.

    ``signature_key`` is the application's ``client_secret`` on every one,
    because that is what Lightspeed signs with -- ``WebhookRequest`` carries no
    per-hook secret. See ``signer.py``.
    """
    subscriptions = ctx.store.collection(COL.webhooks)
    for webhook in doc.webhooks:
        subscriptions.insert(
            {
                "id": webhook.id,
                "name": f"{webhook.type} -> {webhook.url}",
                "notification_url": webhook.url,
                "event_types": [webhook.type],
                "signature_key": config.client_secret,
                "enabled": webhook.active,
            },
            SEED_META,
        )
