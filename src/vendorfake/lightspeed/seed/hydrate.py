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
registers, payment types, products, customers, sales -- so the version numbers
ascend in that order and a consumer paging ``/outlets`` sees them in the order
the document lists them. Changing the order here renumbers every entity, which
is why it is stated rather than incidental. Sales come last because a sale
refers to everything before it.
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.types import UnitContext
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.auth import KIND_OAUTH, KIND_PERSONAL
from vendorfake.lightspeed.config import LightspeedConfig
from vendorfake.lightspeed.entities import (
    COL,
    OBJECT_VERSION,
    OutletEntity,
    PaymentTypeEntity,
    RefreshTokenEntity,
    RegisterEntity,
    RetailerEntity,
    SaleEntity,
    TokenEntity,
)
from vendorfake.lightspeed.model.money import to_minor
from vendorfake.lightspeed.seed.document import SeedDocument, SeedSale, parse_seed_document
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
    _insert_customers(ctx, doc, versions)
    _insert_sales(ctx, doc, versions)
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


# -- sales, and the minimum they reference (slice L2b of konyklabs/roadmap#94).
# The two loaders below insert the documented wire shape as a plain dict rather
# than through an entity dataclass, because `products` and `customers` are the
# sibling slice's collections: this slice seeds only what a sale must resolve
# against, and the surfaces that own those collections bring their own typed
# entity when they land.


def _insert_products(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    products = ctx.store.collection(COL.products)
    for product in doc.products:
        products.insert(
            compact({**product.model_dump(), OBJECT_VERSION: versions.bump()}),
            SEED_META,
        )


def _insert_customers(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    customers = ctx.store.collection(COL.customers)
    for customer in doc.customers:
        customers.insert(
            compact({**customer.model_dump(), OBJECT_VERSION: versions.bump()}),
            SEED_META,
        )


def _insert_sales(ctx: UnitContext, doc: SeedDocument, versions: LightspeedVersions) -> None:
    """Seeded sales, through the same stored shape a request produces.

    The money conversion is ``model/money.py``'s, the same call the surface
    makes, so a seeded sale and a posted one are indistinguishable in the store
    -- which is what lets a test drive the seeded closed sale through the
    return action.

    ``outlet_id`` and ``invoice_number`` are DERIVED here exactly as
    ``surface/sales.py`` derives them: the register's outlet, and the
    register's ``invoice_prefix``/``invoice_sequence``/``invoice_suffix`` with
    the sequence advanced by the number of sales already seeded on that
    register. A scenario may state its own ``invoice_number`` and then that
    wins.
    """
    registers = {register.id: register for register in doc.registers}
    taken: dict[str, int] = {}
    sales = ctx.store.collection(COL.sales)
    for index, sale in enumerate(doc.sales):
        register = registers.get(sale.source.register_id or "")
        outlet_id = register.outlet_id if register is not None else None
        invoice = sale.invoice_number
        if invoice is None and register is not None:
            offset = taken.get(register.id, 0)
            invoice = f"{register.invoice_prefix}{register.invoice_sequence + offset}{register.invoice_suffix}"
            taken[register.id] = offset + 1
        sales.insert(
            SaleEntity(
                id=sale.id,
                state=sale.state,
                source=compact(
                    {
                        "author_id": sale.source.author_id,
                        "register_id": sale.source.register_id,
                        "outlet_id": outlet_id,
                        "id": sale.source.id,
                        "type": sale.source.type,
                    }
                ),
                line_items=_seed_line_items(sale, index),
                payments=_seed_payments(sale, index),
                attributes=tuple(sale.attributes),
                customer_id=sale.customer_id,
                note=sale.note,
                short_code=sale.short_code,
                invoice_number=invoice,
                receipt_number=invoice,
                date=sale.date,
                created_at=sale.date,
                updated_at=sale.date,
                object_version=versions.bump(),
            ).to_entity(),
            SEED_META,
        )


def _seed_line_items(sale: SeedSale, index: int) -> list[dict[str, Any]]:
    return [
        compact(
            {
                "id": line.id,
                "product_id": line.product_id,
                "quantity": line.quantity,
                "price_minor": to_minor(line.price, field=f"sales[{index}].line_items[{position}].price"),
                "cost_minor": None
                if line.cost is None
                else to_minor(line.cost, field=f"sales[{index}].line_items[{position}].cost"),
                "discount_minor": None
                if line.discount is None
                else to_minor(line.discount, field=f"sales[{index}].line_items[{position}].discount"),
                "loyalty_minor": None
                if line.loyalty_amount is None
                else to_minor(line.loyalty_amount, field=f"sales[{index}].line_items[{position}].loyalty_amount"),
                "tax_id": line.tax_id,
                "tax_minor": to_minor(line.tax, field=f"sales[{index}].line_items[{position}].tax"),
                "fulfilment_outlet_id": line.fulfilment_outlet_id,
                "sequence": position,
            }
        )
        for position, line in enumerate(sale.line_items)
    ]


def _seed_payments(sale: SeedSale, index: int) -> list[dict[str, Any]]:
    return [
        compact(
            {
                "id": payment.id,
                "payment_type_id": payment.payment_type_id,
                "amount_minor": to_minor(
                    payment.amount, field=f"sales[{index}].payments[{position}].amount", allow_negative=True
                ),
                "register_id": payment.register_id or sale.source.register_id,
                "date": payment.date or sale.date,
            }
        )
        for position, payment in enumerate(sale.payments)
    ]


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
