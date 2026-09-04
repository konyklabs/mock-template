"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
world and answers 404 to every read.

INVARIANT: **a scenario is validated before a single entity is inserted.**
Every model here sets ``extra="forbid"``; hydration parses the whole document
first, and every reference -- a register's outlet, a token's scopes, a payment
type a close request could name -- must resolve inside the document.

Keys: the top level is snake_case like every JSON this project publishes, and
the entity documents use Lightspeed's own field names, which are snake_case
too, so a documented example pastes straight in.

MONEY AND QUANTITIES IN THE SEED ARE DECIMAL STRINGS -- ``"12.50"``, not
``12.5`` -- even though the products and inventory surfaces put them on the
wire as JSON numbers. A scenario file is read by people and diffed by machines,
and a float in JSON is neither exact nor stable in either. ``hydrate`` runs
every one through ``model/scalars.decimal_text``, so a seed that writes
``"12.50"`` and one that writes ``"12.5"`` produce the same unit.

The Lightspeed ``version`` is NOT in the seed. It is drawn from the retailer's
one monotonically increasing counter at hydrate, in document order, so that two
units stamp the same numbers -- see ``versioning.py``. A seed that pinned a
version would be pinning a number the counter does not know about.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedAdjustmentReason",
    "SeedCustomer",
    "SeedCustomerGroup",
    "SeedDocument",
    "SeedInventory",
    "SeedOutlet",
    "SeedPaymentType",
    "SeedPersonalToken",
    "SeedProduct",
    "SeedRefreshToken",
    "SeedRegister",
    "SeedRetailer",
    "SeedStockAdjustment",
    "SeedToken",
    "SeedWebhook",
    "parse_seed_document",
]

_SEED = ConfigDict(extra="forbid")


class SeedRetailer(BaseModel):
    """The one retailer. ``document`` carries the documented blocks this unit
    does not compute from (``gift_cards``, ``loyalty``, ``sku_sequence``,
    ``on_account``, ``store_url`` ...) exactly as the wire should answer
    them."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    domain_prefix: str = Field(min_length=1)
    currency_code: str = Field(min_length=3, max_length=3)
    currency_symbol: str = Field(min_length=1)
    timezone: str = "UTC"
    country: str = Field(default="US", min_length=2, max_length=2)
    document: dict[str, Any] = Field(default_factory=dict)


class SeedOutlet(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    default_tax_id: str = Field(min_length=1)
    currency: str = Field(min_length=3, max_length=3)
    currency_symbol: str = Field(min_length=1)
    display_prices: Literal["inclusive", "exclusive"] = "inclusive"
    time_zone: str = "UTC"
    attributes: list[dict[str, str]] = Field(default_factory=list)
    physical_address_1: str | None = None
    physical_suburb: str | None = None
    physical_city: str | None = None
    physical_state: str | None = None
    physical_postcode: str | None = None
    physical_country_id: str | None = None
    email: str | None = None


class SeedRegister(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    outlet_id: str = Field(min_length=1)
    is_open: bool = True
    invoice_prefix: str = ""
    invoice_suffix: str = ""
    invoice_sequence: int = Field(default=1, ge=0)
    #: 0 never, 1 on save/layby/account/return, 2 always -- the documented
    #: meanings of ``Register.ask_for_note_on_save``.
    ask_for_note_on_save: Literal[0, 1, 2] = 1
    ask_for_user_on_sale: bool = False
    email_receipt: bool = False
    print_receipt: bool = True
    print_note_on_receipt: bool = False
    is_quick_keys_enabled: bool = True
    show_discounts_on_receipts: bool = True
    receipt_template_id: str | None = None
    button_layout_id: str | None = None
    cash_managed_payment_type_id: str | None = None
    register_open_sequence_id: str | None = None
    #: RFC 3339. Present exactly when the register starts open.
    register_open_time: str | None = None


class SeedPaymentType(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type_id: int
    disabled: bool = False
    #: An internal type is excluded from the list the ``payment_types:read``
    #: scope grants; seeding one is what makes that testable.
    internal: bool = False
    gateway: bool = False
    name_changed_by_user: bool = False
    config: dict[str, Any] | None = None
    outlet_ids: list[str] = Field(default_factory=list)


class SeedProduct(BaseModel):
    """One product. ``family_id`` groups a parent with its variants; a product
    with none is a family of one, because ``Product.family_id`` is not
    nullable."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    sku: str = Field(min_length=1)
    family_id: str = Field(min_length=1)
    handle: str | None = None
    description: str | None = None
    active: bool = True
    price_excluding_tax: str = "0"
    price_including_tax: str = "0"
    supply_price: str = "0"
    has_inventory: bool = True
    has_variants: bool = False
    variant_parent_id: str | None = None
    variant_name: str | None = None
    variant_count: int | None = Field(default=None, ge=0)
    variant_options: list[dict[str, str]] = Field(default_factory=list)
    tag_ids: list[str] = Field(default_factory=list)
    attributes: list[dict[str, str]] = Field(default_factory=list)
    product_codes: list[dict[str, str]] = Field(default_factory=list)
    #: ``[{"outlet_id": ..., "tax_id": ...}]`` -- the documented
    #: ``ProductAddOutletTax`` shape, echoed back on the product.
    outlet_taxes: list[dict[str, str]] = Field(default_factory=list)


class SeedInventory(BaseModel):
    """One ``Inventory`` record: one product's stock at one outlet."""

    model_config = _SEED

    id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    outlet_id: str = Field(min_length=1)
    current_inventory_level: str = "0"
    average_cost: str | None = None
    reorder_point: str | None = None
    reorder_amount: str | None = None
    reorder_target: str | None = None
    #: ``FIXED`` or ``MIN_MAX``; the documented enum's third value is null.
    reorder_method: Literal["FIXED", "MIN_MAX"] | None = None


class SeedAdjustmentReason(BaseModel):
    """One ``CustomInventoryAdjustmentReason``. There is no route to create
    another: the tag's three operations are deferred, so a CUSTOM stock
    adjustment can only ever name one seeded here."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    type: Literal["POSITIVE", "NEGATIVE"]
    enabled: bool = True


class SeedStockAdjustment(BaseModel):
    """One row of the adjustment log the scenario starts with.

    Seeded adjustments do NOT move the seeded inventory levels: the levels are
    stated outright and these are the history that produced them, so a scenario
    stating both is stating one fact twice and gets to keep them consistent
    itself.
    """

    model_config = _SEED

    id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    outlet_id: str = Field(min_length=1)
    quantity: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    custom_inventory_adjustment_reason_id: str | None = None


class SeedCustomerGroup(BaseModel):
    """One customer group. The scenario needs at least one: every customer
    belongs to a group and no route can create one."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)


class SeedCustomer(BaseModel):
    """One customer.

    ``document`` carries the flat ``CustomerBase`` members this unit stores
    verbatim -- addresses, contact details, the four custom fields -- and its
    keys are checked against ``model/customer.CUSTOMER_DOCUMENT_FIELDS`` so a
    misspelling in a scenario is a startup failure naming the key.
    """

    model_config = _SEED

    id: str = Field(min_length=1)
    first_name: str | None
    last_name: str | None
    customer_code: str = Field(min_length=1)
    customer_group_id: str | None = None
    email: str | None = None
    balance: str = "0"
    loyalty_balance: str = "0"
    year_to_date: str = "0"
    document: dict[str, Any] = Field(default_factory=dict)


class SeedToken(BaseModel):
    """A pre-issued OAuth access token."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    #: Absent means "expires ``access_token_ttl_s`` from the unit's start",
    #: which is what a token issued a moment ago would do.
    expires_in_s: int | None = None


class SeedPersonalToken(BaseModel):
    """A personal token: no expiry, and no way to create one over the API."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)


class SeedRefreshToken(BaseModel):
    """A refresh token, and the seeded access token it was issued with."""

    model_config = _SEED

    id: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    scopes: list[str] = Field(min_length=1)
    access_token_id: str = Field(min_length=1)


class SeedWebhook(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    url: str = Field(min_length=3)
    active: bool = True


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    comment: list[str] | None = Field(default=None, alias="_comment")
    retailer: SeedRetailer
    outlets: list[SeedOutlet] = Field(default_factory=list)
    registers: list[SeedRegister] = Field(default_factory=list)
    payment_types: list[SeedPaymentType] = Field(default_factory=list)
    products: list[SeedProduct] = Field(default_factory=list)
    inventory: list[SeedInventory] = Field(default_factory=list)
    adjustment_reasons: list[SeedAdjustmentReason] = Field(default_factory=list)
    stock_adjustments: list[SeedStockAdjustment] = Field(default_factory=list)
    customer_groups: list[SeedCustomerGroup] = Field(default_factory=list)
    customers: list[SeedCustomer] = Field(default_factory=list)
    tokens: list[SeedToken] = Field(default_factory=list)
    personal_tokens: list[SeedPersonalToken] = Field(default_factory=list)
    refresh_tokens: list[SeedRefreshToken] = Field(default_factory=list)
    webhooks: list[SeedWebhook] = Field(default_factory=list)


def _refuse(path: str, message: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"The seed document is not valid at {path}: {message}.",
        field="seed",
        info={"path": path},
    )


def _check_references(doc: SeedDocument) -> None:
    """Every reference resolves inside the document."""
    from vendorfake.lightspeed.config import DEFAULT_SCOPES
    from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES
    from vendorfake.lightspeed.model.customer import CUSTOMER_DOCUMENT_FIELDS
    from vendorfake.lightspeed.model.inventory import STOCK_ADJUSTMENT_REASONS

    outlet_ids = {outlet.id for outlet in doc.outlets}
    payment_type_ids = {payment_type.id for payment_type in doc.payment_types}
    token_ids = {token.id for token in doc.tokens}
    for index, register in enumerate(doc.registers):
        if register.outlet_id not in outlet_ids:
            raise _refuse(f"registers[{index}].outlet_id", f"outlet {register.outlet_id!r} is absent")
        if register.cash_managed_payment_type_id is not None and (
            register.cash_managed_payment_type_id not in payment_type_ids
        ):
            raise _refuse(
                f"registers[{index}].cash_managed_payment_type_id",
                f"payment type {register.cash_managed_payment_type_id!r} is absent",
            )
        if register.is_open != (register.register_open_time is not None):
            raise _refuse(
                f"registers[{index}].register_open_time",
                "register_open_time is present exactly when the register starts open",
            )
    for index, payment_type in enumerate(doc.payment_types):
        for position, outlet_id in enumerate(payment_type.outlet_ids):
            if outlet_id not in outlet_ids:
                raise _refuse(f"payment_types[{index}].outlet_ids[{position}]", f"outlet {outlet_id!r} is absent")
    for index, refresh in enumerate(doc.refresh_tokens):
        if refresh.access_token_id not in token_ids:
            raise _refuse(
                f"refresh_tokens[{index}].access_token_id", f"access token {refresh.access_token_id!r} is absent"
            )
    for index, webhook in enumerate(doc.webhooks):
        if webhook.type not in LIGHTSPEED_EVENT_TYPES:
            raise _refuse(
                f"webhooks[{index}].type",
                f"{webhook.type!r} is not one of the seven documented WebhookType values",
            )
    # -- konyklabs/roadmap#94 slice L2a --------------------------------------
    product_ids = {product.id for product in doc.products}
    for index, product in enumerate(doc.products):
        if product.variant_parent_id is not None and product.variant_parent_id not in product_ids:
            raise _refuse(f"products[{index}].variant_parent_id", f"product {product.variant_parent_id!r} is absent")
        if product.has_variants and product.variant_parent_id is not None:
            raise _refuse(
                f"products[{index}].has_variants",
                "a variant cannot itself have variants: this API's families are one level deep",
            )
    for index, record in enumerate(doc.inventory):
        if record.product_id not in product_ids:
            raise _refuse(f"inventory[{index}].product_id", f"product {record.product_id!r} is absent")
        if record.outlet_id not in outlet_ids:
            raise _refuse(f"inventory[{index}].outlet_id", f"outlet {record.outlet_id!r} is absent")
    reason_ids = {reason.id for reason in doc.adjustment_reasons}
    for index, adjustment in enumerate(doc.stock_adjustments):
        if adjustment.product_id not in product_ids:
            raise _refuse(f"stock_adjustments[{index}].product_id", f"product {adjustment.product_id!r} is absent")
        if adjustment.outlet_id not in outlet_ids:
            raise _refuse(f"stock_adjustments[{index}].outlet_id", f"outlet {adjustment.outlet_id!r} is absent")
        if adjustment.reason not in STOCK_ADJUSTMENT_REASONS:
            raise _refuse(
                f"stock_adjustments[{index}].reason",
                f"{adjustment.reason!r} is not one of the documented StockAdjustmentReason values",
            )
        custom_id = adjustment.custom_inventory_adjustment_reason_id
        if custom_id is not None and custom_id not in reason_ids:
            raise _refuse(
                f"stock_adjustments[{index}].custom_inventory_adjustment_reason_id",
                f"reason {custom_id!r} is absent",
            )
    group_ids = {group.id for group in doc.customer_groups}
    for index, customer in enumerate(doc.customers):
        if customer.customer_group_id is not None and customer.customer_group_id not in group_ids:
            raise _refuse(
                f"customers[{index}].customer_group_id", f"customer group {customer.customer_group_id!r} is absent"
            )
        unknown_keys = [key for key in customer.document if key not in CUSTOMER_DOCUMENT_FIELDS]
        if unknown_keys:
            raise _refuse(
                f"customers[{index}].document",
                f"{unknown_keys} are not members CustomerBase declares",
            )
    if doc.customers and not doc.customer_groups:
        raise _refuse(
            "customer_groups",
            "a scenario with customers needs at least one customer group: every customer belongs to one and "
            "no route in this surface can create one",
        )

    granted = set(DEFAULT_SCOPES)
    holders: list[tuple[str, list[list[str]]]] = [
        ("tokens", [token.scopes for token in doc.tokens]),
        ("personal_tokens", [token.scopes for token in doc.personal_tokens]),
        ("refresh_tokens", [token.scopes for token in doc.refresh_tokens]),
    ]
    for holder, scope_lists in holders:
        for index, scopes in enumerate(scope_lists):
            unknown = [scope for scope in scopes if scope not in granted]
            if unknown:
                raise _refuse(f"{holder}[{index}].scopes", f"{unknown} are not scopes this application carries")


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a seed document, raising the vendor's ``invalid_value`` on the
    ``seed`` field with the offending path in the detail."""
    try:
        doc = SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ())) or "seed"
        raise _refuse(path, str(first.get("msg", "invalid"))) from exc
    _check_references(doc)
    return doc
