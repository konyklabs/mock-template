"""The seed document's schema, as a model rather than as a cast.

FOR: stating what a scenario file may contain, so that a typo in one is a
startup failure naming the field instead of a unit that starts with an empty
world and answers 404 to every read.

INVARIANT: **a scenario is validated before a single entity is inserted.**
Every model here sets ``extra="forbid"``; hydration parses the whole document
first, and every reference (a menu item's tax rate, a modifier group's
options, a table's service area) must resolve inside the document.

Keys: the top level is snake_case like every JSON this project publishes; the
entity documents use Toast's own camelCase field names, so a documented
example pastes straight in. **Money is integer cents in the seed**, as in the
store; the wire converts (``model/money.py``).

The V3 menu models follow toast-menus-api-v3.yaml field for field where a
field is modelled; a few deeply nested documented blocks (``pricingRules``,
``availability.schedule``, ``portions``, ``images``) are carried as opaque
documents because nothing here computes from them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from vendorfake.core.kernel.types import UnitError, UnitErrorKind

__all__ = [
    "SeedAlternatePaymentType",
    "SeedCheck",
    "SeedCreditAuthorization",
    "SeedDiningOption",
    "SeedDiscount",
    "SeedDocument",
    "SeedMenu",
    "SeedMenuGroup",
    "SeedMenuItem",
    "SeedMenuV3",
    "SeedModifierGroup",
    "SeedModifierOption",
    "SeedOrder",
    "SeedPartner",
    "SeedPreModifier",
    "SeedPreModifierGroup",
    "SeedRestaurant",
    "SeedRestaurantGeneral",
    "SeedRestaurantService",
    "SeedRevenueCenter",
    "SeedSelection",
    "SeedServiceArea",
    "SeedServiceCharge",
    "SeedStock",
    "SeedTable",
    "SeedTaxRate",
    "SeedToken",
    "SeedVoidReason",
    "parse_seed_document",
]

_SEED = ConfigDict(extra="forbid")


class SeedRestaurantGeneral(BaseModel):
    model_config = _SEED

    name: str = Field(min_length=1)
    locationName: str | None = None
    locationCode: str | None = Field(default=None, min_length=3, max_length=4)
    description: str | None = None
    timeZone: str = "UTC"
    closeoutHour: int = Field(default=0, ge=0, le=12)
    managementGroupGuid: str | None = None
    currencyCode: str = "USD"


class SeedRestaurant(BaseModel):
    model_config = _SEED

    guid: str = Field(min_length=1)
    general: SeedRestaurantGeneral
    location: dict[str, Any] = Field(default_factory=dict)
    urls: dict[str, Any] = Field(default_factory=dict)
    schedules: dict[str, Any] = Field(default_factory=dict)
    delivery: dict[str, Any] = Field(default_factory=dict)
    onlineOrdering: dict[str, Any] = Field(default_factory=dict)
    prepTimes: dict[str, Any] = Field(default_factory=dict)


class SeedPartner(BaseModel):
    """How the restaurant appears in ``GET /partners/v1/connectedRestaurants``
    (toast-partners-api.yaml). ``createdDate`` is epoch ms, documented."""

    model_config = _SEED

    createdByEmailAddress: str
    externalGroupRef: str | None = None
    externalRestaurantRef: str | None = None
    createdDate: int
    modifiedDate: int


class SeedToken(BaseModel):
    model_config = _SEED

    id: str = Field(min_length=1)
    access_token: str = Field(min_length=1)
    scopes: list[str] | None = None
    client_id: str | None = None


# -- config/v2 ---------------------------------------------------------------


class _ConfigEntity(BaseModel):
    model_config = _SEED

    guid: str = Field(min_length=1)
    externalId: str | None = None


class SeedDiningOption(_ConfigEntity):
    name: str
    behavior: Literal["DINE_IN", "TAKE_OUT", "DELIVERY"]
    curbside: bool = False


class SeedAlternatePaymentType(_ConfigEntity):
    name: str


class SeedTaxRate(_ConfigEntity):
    name: str
    isDefault: bool = False
    #: A fraction: 0.0625 is 6.25% (apiOrderPrices.html shows ``"rate": 0.0625``).
    rate: float | None = None
    type: Literal["PERCENT", "FIXED", "TABLE", "NONE", "EXTERNAL"] = "PERCENT"
    roundingType: Literal["HALF_UP", "HALF_EVEN", "ALWAYS_UP", "ALWAYS_DOWN"] = "HALF_UP"
    taxTable: list[dict[str, Any]] = Field(default_factory=list)
    conditionalTaxRates: list[dict[str, Any]] = Field(default_factory=list)


class SeedRevenueCenter(_ConfigEntity):
    name: str
    description: str | None = None


class SeedRestaurantService(_ConfigEntity):
    name: str


class SeedServiceArea(_ConfigEntity):
    name: str
    revenueCenter: str
    """The revenue center's guid."""


class SeedTable(_ConfigEntity):
    name: str
    serviceArea: str
    revenueCenter: str


class SeedDiscount(_ConfigEntity):
    name: str
    active: bool = True
    type: Literal["PERCENT", "FIXED", "OPEN_PERCENT", "OPEN_FIXED", "BOGO", "FIXED_TOTAL"]
    percentage: float | None = None
    #: Cents.
    amount: int | None = None
    selectionType: Literal["CHECK", "ITEM", "BOGO"]
    nonExclusive: bool = False
    itemPickingPriority: str | None = None
    #: Cents.
    fixedTotal: int | None = None
    promoCodes: list[str] = Field(default_factory=list)


class SeedServiceCharge(_ConfigEntity):
    name: str
    amountType: Literal["FIXED", "PERCENT", "OPEN"]
    #: Cents.
    amount: int | None = None
    percent: float | None = None
    gratuity: bool = False
    taxable: bool = False
    serviceChargeCalculation: Literal["PRE_DISCOUNT", "POST_DISCOUNT"] = "PRE_DISCOUNT"
    destination: Literal["RESTAURANT", "SERVER", "TOAST", "THIRD_PARTY"] = "RESTAURANT"


class SeedVoidReason(_ConfigEntity):
    name: str


# -- menus/v3 ----------------------------------------------------------------


class SeedMenuItem(BaseModel):
    model_config = _SEED

    name: str
    kitchenName: str | None = None
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    description: str | None = None
    posName: str | None = None
    image: str | None = None
    #: Cents; null for SIZE_PRICE/OPEN_PRICE.
    price: int | None = None
    pricingStrategy: Literal["BASE_PRICE", "MENU_SPECIFIC_PRICE", "TIME_SPECIFIC_PRICE", "SIZE_PRICE", "OPEN_PRICE"] = (
        "BASE_PRICE"
    )
    pricingRules: dict[str, Any] | None = None
    isDeferred: bool = False
    isDiscountable: bool = True
    salesCategory: dict[str, Any] | None = None
    #: Tax rate guids.
    taxInfo: list[str] = Field(default_factory=list)
    taxInclusion: Literal["TAX_INCLUDED", "TAX_NOT_INCLUDED", "SMART_TAX"] = "TAX_NOT_INCLUDED"
    itemTags: list[dict[str, Any]] = Field(default_factory=list)
    plu: str | None = None
    sku: str | None = None
    calories: int | None = None
    contentAdvisories: dict[str, Any] | None = None
    unitOfMeasure: str = "NONE"
    portions: list[dict[str, Any]] = Field(default_factory=list)
    prepTime: int | None = None
    prepStations: list[str] = Field(default_factory=list)
    modifierGroupReferences: list[int] = Field(default_factory=list)
    eligiblePaymentAssistancePrograms: list[str] = Field(default_factory=list)
    isComboParent: bool = False


class SeedMenuGroup(BaseModel):
    model_config = _SEED

    name: str
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    description: str | None = None
    posName: str | None = None
    image: str | None = None
    itemTags: list[dict[str, Any]] = Field(default_factory=list)
    menuGroups: list[SeedMenuGroup] = Field(default_factory=list)
    menuItems: list[SeedMenuItem] = Field(default_factory=list)


class SeedMenu(BaseModel):
    model_config = _SEED

    name: str
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    description: str | None = None
    posButtonColorLight: str | None = None
    posButtonColorDark: str | None = None
    highResImage: str | None = None
    image: str | None = None
    availability: dict[str, Any] = Field(default_factory=lambda: {"alwaysAvailable": True})
    menuGroups: list[SeedMenuGroup] = Field(default_factory=list)


class SeedModifierOption(BaseModel):
    model_config = _SEED

    referenceId: int
    name: str
    kitchenName: str | None = None
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    #: Cents; null when the group prices it.
    price: int | None = None
    pricingStrategy: str = "BASE_PRICE"
    pricingRules: dict[str, Any] | None = None
    salesCategory: dict[str, Any] | None = None
    modifierOptionTaxInfo: dict[str, Any] | None = None
    plu: str | None = None
    sku: str | None = None
    calories: int | None = None
    isDefault: bool = False
    allowsDuplicates: bool = False
    portions: list[dict[str, Any]] = Field(default_factory=list)
    prepTime: int | None = None
    modifierGroupReferences: list[int] = Field(default_factory=list)
    sortOrder: int | None = None


class SeedModifierGroup(BaseModel):
    model_config = _SEED

    referenceId: int
    name: str
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    posName: str | None = None
    pricingStrategy: Literal["NONE", "SEQUENCE_PRICE", "SIZE_PRICE", "SIZE_SEQUENCE_PRICE"] = "NONE"
    pricingRules: dict[str, Any] | None = None
    defaultOptionsChargePrice: Literal["NO", "YES"] = "NO"
    defaultOptionsSubstitutionPricing: Literal["NO", "YES"] = "NO"
    minSelections: int = 0
    maxSelections: int | None = None
    requiredMode: Literal["REQUIRED", "OPTIONAL_FORCE_SHOW", "OPTIONAL"] = "OPTIONAL"
    isMultiSelect: bool = False
    preModifierGroupReference: int | None = None
    modifierOptionReferences: list[int] = Field(default_factory=list)


class SeedPreModifier(BaseModel):
    model_config = _SEED

    name: str
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    #: Cents, or null.
    fixedPrice: int | None = None
    multiplicationFactor: float = 1.0
    displayMode: Literal["PREFIX", "SUFFIX"] = "PREFIX"
    chargeAsExtra: bool = False
    plu: str | None = None


class SeedPreModifierGroup(BaseModel):
    model_config = _SEED

    referenceId: int
    name: str
    guid: str = Field(min_length=1)
    multiLocationId: str = Field(min_length=1)
    preModifiers: list[SeedPreModifier] = Field(default_factory=list)


class SeedMenuV3(BaseModel):
    """The published V3 document: ``lastUpdated`` is epoch ms here and a
    string on the wire; the reference maps are lists here and keyed by
    ``referenceId`` on the wire."""

    model_config = _SEED

    lastUpdated: int
    menus: list[SeedMenu]
    modifierGroups: list[SeedModifierGroup] = Field(default_factory=list)
    modifierOptions: list[SeedModifierOption] = Field(default_factory=list)
    preModifierGroups: list[SeedPreModifierGroup] = Field(default_factory=list)


# -- orders ------------------------------------------------------------------


class SeedSelection(BaseModel):
    """A seeded selection is priced at hydrate through the same builder the
    surfaces use; the seed states what was ordered, never an amount."""

    model_config = _SEED

    guid: str = Field(min_length=1)
    item: str = Field(min_length=1)
    quantity: float = Field(default=1.0, gt=0)
    externalId: str | None = None
    preModifier: str | None = None
    modifiers: list[SeedSelection] = Field(default_factory=list)


class SeedCheck(BaseModel):
    model_config = _SEED

    guid: str = Field(min_length=1)
    externalId: str | None = None
    tabName: str | None = None
    selections: list[SeedSelection] = Field(min_length=1)


class SeedOrder(BaseModel):
    """An existing, unpaid order with fixed instants (ms) so list filters are
    reproducible; ``client_id`` defaults to the configured client at hydrate."""

    model_config = _SEED

    guid: str = Field(min_length=1)
    externalId: str | None = None
    diningOption: str = Field(min_length=1)
    table: str | None = None
    openedDate: int
    numberOfGuests: int | None = None
    checks: list[SeedCheck] = Field(min_length=1)


class SeedStock(BaseModel):
    """A stock row for a menu item or modifier option, by guid; the
    ``multiLocationId`` is the item's. ``quantity`` only with ``QUANTITY``."""

    model_config = _SEED

    guid: str = Field(min_length=1)
    status: Literal["IN_STOCK", "QUANTITY", "OUT_OF_STOCK"] = "IN_STOCK"
    quantity: float | None = Field(default=None, gt=0)
    versionId: str = Field(min_length=1)


class SeedCreditAuthorization(BaseModel):
    """A pre-authorised card payment, as ``PUT /merchants/{m}/payments/{p}``
    would have created one (authorizingCcPayments.html); ``amount`` in cents."""

    model_config = _SEED

    guid: str = Field(min_length=1)
    amount: int = Field(gt=0)
    cardType: str = "VISA"
    last4Digits: str = Field(min_length=4, max_length=4)
    cardEntryMode: str = "PRE_AUTHED"


class SeedDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    comment: list[str] | None = Field(default=None, alias="_comment")
    restaurant: SeedRestaurant
    partner: SeedPartner | None = None
    tokens: list[SeedToken] = Field(default_factory=list)
    orders: list[SeedOrder] = Field(default_factory=list)
    credit_authorizations: list[SeedCreditAuthorization] = Field(default_factory=list)
    stock: list[SeedStock] = Field(default_factory=list)
    #: The instant every config entity reports as last modified (epoch ms).
    config_modified_ms: int = 0
    dining_options: list[SeedDiningOption] = Field(default_factory=list)
    alternate_payment_types: list[SeedAlternatePaymentType] = Field(default_factory=list)
    tax_rates: list[SeedTaxRate] = Field(default_factory=list)
    revenue_centers: list[SeedRevenueCenter] = Field(default_factory=list)
    service_areas: list[SeedServiceArea] = Field(default_factory=list)
    tables: list[SeedTable] = Field(default_factory=list)
    restaurant_services: list[SeedRestaurantService] = Field(default_factory=list)
    discounts: list[SeedDiscount] = Field(default_factory=list)
    service_charges: list[SeedServiceCharge] = Field(default_factory=list)
    void_reasons: list[SeedVoidReason] = Field(default_factory=list)
    menu_v3: SeedMenuV3 | None = None


def _refuse(path: str, message: str) -> UnitError:
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"The seed document is not valid at {path}: {message}.",
        field="seed",
        info={"path": path},
    )


def _walk_items(groups: list[SeedMenuGroup], path: str) -> list[tuple[str, SeedMenuItem]]:
    found: list[tuple[str, SeedMenuItem]] = []
    for i, group in enumerate(groups):
        for j, item in enumerate(group.menuItems):
            found.append((f"{path}[{i}].menuItems[{j}]", item))
        found.extend(_walk_items(group.menuGroups, f"{path}[{i}].menuGroups"))
    return found


def _check_references(doc: SeedDocument) -> None:
    """Every reference resolves inside the document."""
    tax_ids = {rate.guid for rate in doc.tax_rates}
    revenue_ids = {center.guid for center in doc.revenue_centers}
    area_ids = {area.guid for area in doc.service_areas}
    for i, area in enumerate(doc.service_areas):
        if area.revenueCenter not in revenue_ids:
            raise _refuse(f"service_areas[{i}].revenueCenter", f"revenue center {area.revenueCenter!r} is absent")
    for i, table in enumerate(doc.tables):
        if table.serviceArea not in area_ids:
            raise _refuse(f"tables[{i}].serviceArea", f"service area {table.serviceArea!r} is absent")
        if table.revenueCenter not in revenue_ids:
            raise _refuse(f"tables[{i}].revenueCenter", f"revenue center {table.revenueCenter!r} is absent")
    if len([rate for rate in doc.tax_rates if rate.isDefault]) > 1:
        raise _refuse("tax_rates", "at most one tax rate may be the default")
    dining_ids = {option.guid for option in doc.dining_options}
    table_ids = {table.guid for table in doc.tables}
    menu = doc.menu_v3
    item_ids = set() if menu is None else {item.guid for m in menu.menus for _, item in _walk_items(m.menuGroups, "")}
    option_ids = set() if menu is None else {option.guid for option in menu.modifierOptions}
    for i, order in enumerate(doc.orders):
        if order.diningOption not in dining_ids:
            raise _refuse(f"orders[{i}].diningOption", f"dining option {order.diningOption!r} is absent")
        if order.table is not None and order.table not in table_ids:
            raise _refuse(f"orders[{i}].table", f"table {order.table!r} is absent")
        for j, check in enumerate(order.checks):
            for k, selection in enumerate(check.selections):
                if selection.item not in item_ids:
                    raise _refuse(
                        f"orders[{i}].checks[{j}].selections[{k}].item", f"menu item {selection.item!r} is absent"
                    )
                for m, modifier in enumerate(selection.modifiers):
                    if modifier.item not in option_ids:
                        raise _refuse(
                            f"orders[{i}].checks[{j}].selections[{k}].modifiers[{m}].item",
                            f"modifier option {modifier.item!r} is absent",
                        )
    for i, row in enumerate(doc.stock):
        if row.guid not in item_ids and row.guid not in option_ids:
            raise _refuse(f"stock[{i}].guid", f"{row.guid!r} is neither a menu item nor a modifier option")
        if (row.status == "QUANTITY") != (row.quantity is not None):
            raise _refuse(f"stock[{i}].quantity", "quantity is present exactly when status is QUANTITY")
    if menu is None:
        return
    group_refs = {group.referenceId for group in menu.modifierGroups}
    option_refs = {option.referenceId for option in menu.modifierOptions}
    pre_refs = {group.referenceId for group in menu.preModifierGroups}
    if len(group_refs) != len(menu.modifierGroups) or len(option_refs) != len(menu.modifierOptions):
        raise _refuse("menu_v3", "referenceIds must be unique within each map")
    for i, group in enumerate(menu.modifierGroups):
        for j, ref in enumerate(group.modifierOptionReferences):
            if ref not in option_refs:
                raise _refuse(f"menu_v3.modifierGroups[{i}].modifierOptionReferences[{j}]", f"option {ref} is absent")
        if group.preModifierGroupReference is not None and group.preModifierGroupReference not in pre_refs:
            raise _refuse(f"menu_v3.modifierGroups[{i}].preModifierGroupReference", "pre-modifier group is absent")
    for i, menu_doc in enumerate(menu.menus):
        for path, item in _walk_items(menu_doc.menuGroups, f"menu_v3.menus[{i}].menuGroups"):
            for j, tax in enumerate(item.taxInfo):
                if tax not in tax_ids:
                    raise _refuse(f"{path}.taxInfo[{j}]", f"tax rate {tax!r} is not in tax_rates")
            for j, ref in enumerate(item.modifierGroupReferences):
                if ref not in group_refs:
                    raise _refuse(f"{path}.modifierGroupReferences[{j}]", f"modifier group {ref} is absent")


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
