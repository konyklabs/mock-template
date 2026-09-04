"""Building priced checks and selections from requests -- shared by
``/prices``, ``POST /orders``, the selection append, the discount routes and
the seed, so there is exactly one place an amount is computed.

FOR: the documented rule "Before you POST the order, you must retrieve the
check prices from the /prices endpoint" only means something if ``/prices``
and ``/orders`` compute identically. They call the same functions here.

INVARIANT: **no id is drawn before every refusal has had its chance.** A
builder takes a ``mint`` callable and calls it only after a selection has
resolved; ``/prices`` passes ``None`` and gets ``"guid": null`` everywhere
(documented for the order and check; JUDGMENT that selections and modifiers
read the same way).

Refusals follow the documented ``POST /orders`` codes: **404** for a
referenced entity that is missing -- a menu item, a modifier option, a
pre-modifier, a dining option, a table, a revenue centre, an alternate payment
type -- and **400** for unsupported data: a missing price, a negatively priced
item, a modifier whose option the item's groups do not offer (JUDGMENT: the
V3 document says which options an item offers, and accepting any option on any
item would price a document Toast would refuse).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from vendorfake.core.kernel.types import UnitError, UnitErrorKind
from vendorfake.core.state.store import Store
from vendorfake.core.util.json import compact
from vendorfake.toast.entities import COL
from vendorfake.toast.model.menus import menu_items_by_guid, modifier_options_by_guid, pre_modifiers_by_guid
from vendorfake.toast.model.money import opt_cents
from vendorfake.toast.model.order import CheckRequest, SelectionRequest
from vendorfake.toast.model.pricing import TaxRate, discount_amount, quantity_price, taxes_on

__all__ = ["MenuIndex", "Minter", "build_check", "build_selection", "retotal_check", "selection_by_guid"]

Minter = Callable[[], str] | None
"""How a builder gets a guid: the vendor's id stream, or ``None`` for ``/prices``."""


@dataclass(frozen=True, slots=True)
class MenuIndex:
    """Everything the builder resolves references against, read once per request."""

    items: Mapping[str, Mapping[str, Any]]
    options: Mapping[str, Mapping[str, Any]]
    pre_modifiers: Mapping[str, Mapping[str, Any]]
    modifier_groups: Mapping[int, Mapping[str, Any]]
    tax_rates: Mapping[str, TaxRate]
    discounts: Mapping[str, Mapping[str, Any]]
    service_charges: Mapping[str, Mapping[str, Any]]
    #: Item or option guid -> stock status, for the OUT_OF_STOCK refusal.
    stock: Mapping[str, str]

    @classmethod
    def from_store(cls, store: Store, restaurant_guid: str) -> MenuIndex:
        menu = store.collection(COL.menus).get(restaurant_guid) or {}
        groups = menu.get("modifierGroups", [])
        return cls(
            stock={
                str(row["id"]): str(row.get("status", "IN_STOCK"))
                for row in store.collection(COL.stock).all()
                if row.get("restaurant_guid") == restaurant_guid
            },
            items=menu_items_by_guid(menu),
            options=modifier_options_by_guid(menu),
            pre_modifiers=pre_modifiers_by_guid(menu),
            modifier_groups={int(g["referenceId"]): g for g in groups if isinstance(g, Mapping)},
            tax_rates={str(row["id"]): TaxRate.from_entity(row) for row in store.collection(COL.tax_rates).all()},
            discounts={str(row["id"]): row for row in store.collection(COL.discounts).all()},
            service_charges={str(row["id"]): row for row in store.collection(COL.service_charges).all()},
        )

    def default_rates(self) -> list[TaxRate]:
        return [rate for rate in self.tax_rates.values() if rate.type == "PERCENT"]

    def options_offered_by(self, item: Mapping[str, Any]) -> set[str]:
        offered: set[str] = set()
        for ref in item.get("modifierGroupReferences", []):
            group = self.modifier_groups.get(int(ref))
            if group is None:
                continue
            for option_ref in group.get("modifierOptionReferences", []):
                for guid, option in self.options.items():
                    if option.get("referenceId") == option_ref:
                        offered.add(guid)
        return offered


def _not_found(what: str, guid: str, field: str) -> UnitError:
    return UnitError(UnitErrorKind.NOT_FOUND, detail=f"{what} {guid} was not found.", field=field)


def build_selection(
    index: MenuIndex,
    request: SelectionRequest,
    *,
    now: int,
    mint: Minter,
    field: str,
    parent_item: Mapping[str, Any] | None = None,
    parent_quantity: float = 1.0,
    parent_rates: list[TaxRate] | None = None,
) -> dict[str, Any]:
    """One stored selection (cents, ms) from a request, modifiers included."""
    guid = request.item.guid
    is_modifier = parent_item is not None
    if is_modifier:
        source = index.options.get(guid)
        if source is None:
            raise _not_found("Modifier option", guid, f"{field}item.guid")
        assert parent_item is not None
        if guid not in index.options_offered_by(parent_item):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Modifier option {guid} is not offered by menu item {parent_item.get('guid')}.",
                field=f"{field}item.guid",
            )
        rates = list(parent_rates or [])
        entity_type = "MenuItem"
    else:
        source = index.items.get(guid)
        if source is None:
            raise _not_found("Menu item", guid, f"{field}item.guid")
        rates = [index.tax_rates[g] for g in source.get("taxInfo", []) if g in index.tax_rates]
        entity_type = "MenuItem"

    if index.stock.get(guid) == "OUT_OF_STOCK":
        # JUDGMENT (audit gap 4): Toast documents no answer to ordering an
        # out-of-stock item; refusing beats silently selling it.
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{source.get('name', 'Menu item')} ({guid}) is OUT_OF_STOCK and cannot be ordered.",
            field=f"{field}item.guid",
            info={"stock_status": "OUT_OF_STOCK"},
        )
    unit = source.get("price")
    open_price = opt_cents(request.openPriceAmount, field=f"{field}openPriceAmount", allow_negative=True)
    if source.get("pricingStrategy") == "OPEN_PRICE" or (unit is None and open_price is not None):
        if open_price is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=f"Menu item {guid} is open-priced; openPriceAmount is required.",
                field=f"{field}openPriceAmount",
            )
        unit = open_price
    if not isinstance(unit, int) or isinstance(unit, bool):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Menu item {guid} carries no price and cannot be ordered.",
            field=f"{field}item.guid",
        )
    if unit < 0:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="A negatively priced item is not supported.",
            field=f"{field}openPriceAmount",
        )

    factor = 1.0
    pre_modifier: dict[str, Any] | None = None
    if request.preModifier is not None:
        pre = index.pre_modifiers.get(request.preModifier.guid)
        if pre is None:
            raise _not_found("Pre-modifier", request.preModifier.guid, f"{field}preModifier.guid")
        fixed = pre.get("fixedPrice")
        if isinstance(fixed, int) and not isinstance(fixed, bool):
            unit = fixed
        else:
            factor = float(pre.get("multiplicationFactor", 1.0))
        pre_modifier = {"guid": str(pre["guid"]), "entityType": "PreModifier"}

    quantity = float(request.quantity)
    price = quantity_price(unit, quantity * parent_quantity, factor, field=f"{field}quantity")
    selection_guid = None if mint is None else mint()
    applied = taxes_on(price, rates, owner=selection_guid or "")
    modifiers = [
        build_selection(
            index,
            modifier,
            now=now,
            mint=mint,
            field=f"{field}modifiers[{i}].",
            parent_item=source,
            parent_quantity=quantity * parent_quantity,
            parent_rates=rates,
        )
        for i, modifier in enumerate(request.modifiers)
    ]
    return compact(
        {
            "guid": selection_guid,
            "externalId": request.externalId,
            "item": compact(
                {"guid": guid, "entityType": entity_type, "multiLocationId": source.get("multiLocationId")}
            ),
            "itemGroup": None
            if request.itemGroup is None
            else {"guid": request.itemGroup.guid, "entityType": "MenuGroup"},
            "optionGroup": (
                None if request.optionGroup is None else {"guid": request.optionGroup.guid, "entityType": "MenuGroup"}
            ),
            "preModifier": pre_modifier,
            "quantity": quantity,
            "seatNumber": request.seatNumber,
            "unitOfMeasure": request.unitOfMeasure or str(source.get("unitOfMeasure", "NONE")),
            "selectionType": request.selectionType or "NONE",
            "salesCategory": _sales_category(source),
            "appliedDiscounts": [],
            "deferred": bool(request.deferred)
            if request.deferred is not None
            else bool(source.get("isDeferred", False)),
            "preDiscountPrice": price,
            "price": price,
            "tax": sum(int(t["taxAmount"]) for t in applied),
            "voided": False,
            "displayName": request.displayName or str(source.get("name", "")),
            "plu": source.get("plu"),
            "createdDate": now,
            "modifiedDate": now,
            "modifiers": modifiers,
            "fulfillmentStatus": "NEW",
            "taxInclusion": request.taxInclusion or "NOT_INCLUDED",
            "appliedTaxes": applied,
            "openPriceAmount": open_price,
            "receiptLinePrice": price,
            "_rates": [rate.guid for rate in rates],
        }
    )


def _sales_category(source: Mapping[str, Any]) -> dict[str, Any] | None:
    category = source.get("salesCategory")
    if not isinstance(category, Mapping) or "guid" not in category:
        return None
    return {"guid": str(category["guid"]), "entityType": "SalesCategory"}


def build_check(
    index: MenuIndex,
    request: CheckRequest,
    *,
    now: int,
    mint: Minter,
    field: str,
    display_number: str | None,
) -> dict[str, Any]:
    """One stored check, selections priced and the three amounts computed."""
    selections = [
        build_selection(index, selection, now=now, mint=mint, field=f"{field}selections[{i}].")
        for i, selection in enumerate(request.selections)
    ]
    check: dict[str, Any] = compact(
        {
            "guid": None if mint is None else mint(),
            "externalId": request.externalId,
            "createdDate": now,
            "openedDate": now,
            "modifiedDate": now,
            "deleted": False,
            "selections": selections,
            "customer": None if request.customer is None else request.customer.model_dump(exclude_none=True) or None,
            "taxExempt": request.taxExempt,
            "displayNumber": display_number,
            "appliedServiceCharges": [
                _applied_service_charge(index, charge, f"{field}appliedServiceCharges[{i}].")
                for i, charge in enumerate(request.appliedServiceCharges or [])
            ],
            "appliedDiscounts": [],
            "payments": [],
            "tabName": request.tabName,
            "paymentStatus": "OPEN",
            "voided": False,
        }
    )
    retotal_check(check, index)
    return check


def _applied_service_charge(index: MenuIndex, request: Any, field: str) -> dict[str, Any]:
    """One stored applied service charge: the reference resolved like every
    other, the caller's amount in cents, the config record's own vocabulary
    filling what the caller left out. Never computed (``TOAST_NOT_MODELED``);
    never echoed verbatim (finding 7)."""
    source = index.service_charges.get(request.serviceCharge.guid)
    if source is None:
        raise _not_found("Service charge", request.serviceCharge.guid, f"{field}serviceCharge.guid")
    return compact(
        {
            "guid": request.guid,
            "entityType": "AppliedServiceCharge",
            "externalId": request.externalId,
            "serviceCharge": {"guid": str(source["id"]), "entityType": "ServiceCharge"},
            "chargeAmount": opt_cents(request.chargeAmount, field=f"{field}chargeAmount"),
            "chargeType": request.chargeType or source.get("amountType"),
            "name": request.name or source.get("name"),
            "gratuity": bool(source.get("gratuity", False)) if request.gratuity is None else request.gratuity,
            "taxable": bool(source.get("taxable", False)) if request.taxable is None else request.taxable,
        }
    )


def _line_total(selection: Mapping[str, Any]) -> tuple[int, int]:
    """``(price, tax)`` of a selection and its modifiers, voided ones excluded."""
    if selection.get("voided"):
        return 0, 0
    price = int(selection.get("price", 0))
    tax = int(selection.get("tax", 0))
    for modifier in selection.get("modifiers", []):
        more_price, more_tax = _line_total(modifier)
        price += more_price
        tax += more_tax
    return price, tax


def retotal_check(check: dict[str, Any], index: MenuIndex) -> None:
    """Recompute ``amount``, ``taxAmount`` and ``totalAmount`` in place, and
    re-derive every check-level discount's ``discountAmount`` from the
    current selection total. See ``model/pricing.py`` for the rules."""
    selections_total = 0
    taxes = 0
    for selection in check.get("selections", []):
        price, tax = _line_total(selection)
        selections_total += price
        taxes += tax
    if check.get("taxExempt"):
        # JUDGMENT: a tax-exempt check levies nothing -- the specification
        # documents the flag alone -- and the exemption is written all the way
        # down, so a selection never contradicts its check by carrying a
        # ``tax`` the check does not levy.
        _exempt_selections(check.get("selections", []))
        taxes = 0
    discounted = 0
    for applied in check.get("appliedDiscounts", []):
        source = index.discounts.get(str(applied.get("discount", {}).get("guid", "")))
        if source is not None:
            applied["discountAmount"] = discount_amount(max(0, selections_total - discounted), source)
        discounted += int(applied.get("discountAmount", 0))
    check["amount"] = max(0, selections_total - discounted)
    check["taxAmount"] = taxes
    check["totalAmount"] = check["amount"] + taxes


def _exempt_selections(selections: Any) -> None:
    if not isinstance(selections, list):
        return
    for selection in selections:
        if isinstance(selection, dict):
            selection["tax"] = 0
            selection["appliedTaxes"] = []
            _exempt_selections(selection.get("modifiers"))


def selection_by_guid(check: Mapping[str, Any], guid: str) -> dict[str, Any] | None:
    """A selection or nested modifier of ``check`` by guid; ``None`` if absent."""

    def walk(rows: Any) -> dict[str, Any] | None:
        if not isinstance(rows, list):
            return None
        for row in rows:
            if isinstance(row, dict):
                if row.get("guid") == guid:
                    return row
                found = walk(row.get("modifiers"))
                if found is not None:
                    return found
        return None

    return walk(check.get("selections"))
