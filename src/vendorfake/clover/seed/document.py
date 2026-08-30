"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
inventory and answers 404 to every read as though the scenario were simply
small.

INVARIANT: **a scenario is validated before a single entity is inserted.**
Every model here sets ``extra="forbid"`` -- a seed is this project's own
document, so an unknown key is a typo and not a documented field this build
happens not to model -- and hydration parses the whole document first.

SECOND INVARIANT: **a reference that does not resolve is a startup failure.**
An item naming a tax rate that is not in the document, a modifier naming an
absent group, an order line naming an absent item: each raises before
anything is inserted (:func:`parse_seed_document`), rather than producing a
half-formed entity whose symptom is a total that is quietly wrong.

Keys: the top level is snake_case like every JSON this project publishes;
the *entity documents* use Clover's own camelCase field names, because that
is what the store holds and what the wire carries, and it lets a scenario
author paste a documented Clover example straight in.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedAddress",
    "SeedCustomer",
    "SeedDocument",
    "SeedEmployee",
    "SeedItem",
    "SeedLineItem",
    "SeedMerchant",
    "SeedModifier",
    "SeedModifierGroup",
    "SeedOrder",
    "SeedOrderType",
    "SeedOwner",
    "SeedRef",
    "SeedServiceCharge",
    "SeedTaxRate",
    "SeedTender",
    "SeedToken",
    "SeedWebhookSubscription",
    "parse_seed_document",
]

_SEED = ConfigDict(extra="forbid")


class SeedRef(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)


class SeedOwner(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str | None = None


class SeedAddress(BaseModel):
    model_config = _SEED

    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    zip: str | None = None
    country: str | None = None


class SeedMerchant(BaseModel):
    """One merchant. ``currency`` is what an order created without one is
    denominated in (JUDGMENT on the orders surface)."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    currency: str = "USD"
    owner: SeedOwner | None = None
    address: SeedAddress | None = None


class SeedTaxRate(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    #: Percent x 100000 (JUDGMENT scale; ``model/order.py``).
    rate: int
    isDefault: bool = False


class SeedItem(BaseModel):
    """Field names and defaults per ``model/inventory.py``."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price: int
    hidden: bool = False
    available: bool = True
    priceType: str = "FIXED"
    defaultTaxRates: bool = True
    isRevenue: bool = False
    sku: str | None = None
    code: str | None = None
    modifiedTime: int | None = None
    taxRates: list[SeedRef] = Field(default_factory=list)
    modifierGroupIds: list[str] = Field(default_factory=list)


class SeedModifierGroup(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    showByDefault: bool = True
    modifierIds: list[str] = Field(default_factory=list)


class SeedModifier(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    price: int = 0
    available: bool = True
    modifierGroup: SeedRef


class SeedEmployee(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    nickname: str | None = None
    role: str = "EMPLOYEE"
    isOwner: bool = False


class SeedTender(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    label: str
    labelKey: str
    enabled: bool = True
    visible: bool = True
    opensCashDrawer: bool = False
    editable: bool = True


class SeedOrderType(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    label: str
    labelKey: str | None = None
    taxable: bool = True
    isDefault: bool = False
    filterCategories: bool = False
    isHidden: bool = False
    fee: int = 0


class SeedServiceCharge(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    name: str
    #: Percent x 10000 (documented).
    percentageDecimal: int
    enabled: bool = True
    isDefault: bool = False


class SeedCustomer(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    firstName: str | None = None
    lastName: str | None = None
    customerSince: int | None = None
    addresses: list[SeedAddress] = Field(default_factory=list)


class SeedLineItem(BaseModel):
    """A line: ``item`` supplies price and name when the line omits them;
    a line with neither ``item`` nor ``price`` is refused."""

    model_config = _SEED

    id: str = Field(min_length=1)
    item: SeedRef | None = None
    price: int | None = None
    name: str | None = None
    note: str | None = None
    unitQty: int | None = None


class SeedOrder(BaseModel):
    """A seeded order carries its own client-set ``total`` and fixed
    timestamps (ms), so list filters are reproducible."""

    model_config = _SEED

    id: str = Field(min_length=1)
    total: int
    currency: str | None = None
    state: str | None = "open"
    paymentState: str = "OPEN"
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    createdTime: int
    modifiedTime: int | None = None
    clientCreatedTime: int | None = None
    orderType: SeedRef | None = None
    employee: SeedRef | None = None
    customers: list[SeedRef] = Field(default_factory=list)
    lineItems: list[SeedLineItem] = Field(default_factory=list)


class SeedToken(BaseModel):
    """A pre-minted bearer. ``permissions`` defaults to the app's full set;
    the expirations are stamped at hydrate from the configured TTLs, which
    is why they are not in the document."""

    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    refresh_token: str = Field(min_length=1)
    permissions: list[str] | None = None
    client_id: str | None = None


class SeedWebhookSubscription(BaseModel):
    """A pre-verified callback, in the core's own subscription vocabulary
    (``notification_url``, ``event_types`` patterns such as ``O:*``,
    ``signature_key`` = the ``X-Clover-Auth`` code). No ``verified`` key is
    what makes it pre-verified to the webhook surface."""

    model_config = _SEED

    id: str = Field(min_length=1)
    name: str | None = None
    notification_url: str = Field(min_length=1)
    event_types: list[str] = Field(default_factory=lambda: ["*"])
    signature_key: str = Field(min_length=1)
    enabled: bool = True


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    #: A scenario author's notes; carried so the shipped file can explain
    #: itself, and ignored by hydration.
    comment: list[str] | None = Field(default=None, alias="_comment")
    merchant: SeedMerchant
    tax_rates: list[SeedTaxRate] = Field(default_factory=list)
    items: list[SeedItem] = Field(default_factory=list)
    modifier_groups: list[SeedModifierGroup] = Field(default_factory=list)
    modifiers: list[SeedModifier] = Field(default_factory=list)
    employees: list[SeedEmployee] = Field(default_factory=list)
    tenders: list[SeedTender] = Field(default_factory=list)
    order_types: list[SeedOrderType] = Field(default_factory=list)
    service_charges: list[SeedServiceCharge] = Field(default_factory=list)
    customers: list[SeedCustomer] = Field(default_factory=list)
    orders: list[SeedOrder] = Field(default_factory=list)
    tokens: list[SeedToken] = Field(default_factory=list)
    webhook_subscriptions: list[SeedWebhookSubscription] = Field(default_factory=list)


def _refuse(path: str, message: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"The seed document is not valid at {path}: {message}.",
        field="seed",
        info={"path": path},
    )


def _check_references(doc: SeedDocument) -> None:
    """Every reference resolves inside the document (second invariant)."""
    tax_ids = {rate.id for rate in doc.tax_rates}
    group_ids = {group.id for group in doc.modifier_groups}
    modifier_ids = {modifier.id for modifier in doc.modifiers}
    item_ids = {item.id for item in doc.items}
    employee_ids = {employee.id for employee in doc.employees}
    customer_ids = {customer.id for customer in doc.customers}
    order_type_ids = {order_type.id for order_type in doc.order_types}
    for i, item in enumerate(doc.items):
        for j, ref in enumerate(item.taxRates):
            if ref.id not in tax_ids:
                raise _refuse(f"items[{i}].taxRates[{j}].id", f"tax rate {ref.id!r} is not in tax_rates")
        for j, gid in enumerate(item.modifierGroupIds):
            if gid not in group_ids:
                raise _refuse(f"items[{i}].modifierGroupIds[{j}]", f"modifier group {gid!r} is not in modifier_groups")
    for i, group in enumerate(doc.modifier_groups):
        for j, mid in enumerate(group.modifierIds):
            if mid not in modifier_ids:
                raise _refuse(f"modifier_groups[{i}].modifierIds[{j}]", f"modifier {mid!r} is not in modifiers")
    for i, modifier in enumerate(doc.modifiers):
        if modifier.modifierGroup.id not in group_ids:
            raise _refuse(f"modifiers[{i}].modifierGroup.id", f"modifier group {modifier.modifierGroup.id!r} is absent")
    for i, order in enumerate(doc.orders):
        if order.orderType is not None and order.orderType.id not in order_type_ids:
            raise _refuse(f"orders[{i}].orderType.id", f"order type {order.orderType.id!r} is not in order_types")
        if order.employee is not None and order.employee.id not in employee_ids:
            raise _refuse(f"orders[{i}].employee.id", f"employee {order.employee.id!r} is not in employees")
        for j, ref in enumerate(order.customers):
            if ref.id not in customer_ids:
                raise _refuse(f"orders[{i}].customers[{j}].id", f"customer {ref.id!r} is not in customers")
        for j, line in enumerate(order.lineItems):
            if line.item is not None and line.item.id not in item_ids:
                raise _refuse(f"orders[{i}].lineItems[{j}].item.id", f"item {line.item.id!r} is not in items")
            if line.item is None and line.price is None:
                raise _refuse(f"orders[{i}].lineItems[{j}].price", "a line needs an item or a price")
    defaults = [charge for charge in doc.service_charges if charge.isDefault]
    if len(defaults) > 1:
        raise _refuse("service_charges", "at most one service charge may be the default")


def parse_seed_document(raw: object) -> SeedDocument:
    """Validate a seed document, raising the vendor's ``invalid_value`` on
    the ``seed`` field with the offending path in the detail."""
    try:
        doc = SeedDocument.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        path = ".".join(str(part) for part in first.get("loc", ())) or "seed"
        raise _refuse(path, str(first.get("msg", "invalid"))) from exc
    _check_references(doc)
    return doc
