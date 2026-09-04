"""The order wire vocabulary: what the orders surface parses, and how a stored
order, check, selection, payment and applied discount are projected.

Field sets are the orders specification's (toast-orders-api.yaml v2.9.5).
Units on the wire: money is decimal dollars (``model/money.py``), instants
are ``...+0000`` strings (``model/dates.py``), ``businessDate`` is an integer
``yyyyMMdd``, ``quantity`` is a double; the store keeps cents, epoch ms, and
the same integer and double.

Projection emits the read-only fields with the documented nulls
(``"externalId": null``) and drops what the scenario never set, so a
consumer's ``if "table" in order`` sees what the sparse real document would
give.

Requests are ``extra="ignore"``, since a documented Order carries far more
than an integration sends; money in a request is ``int | float | str``,
converted by the surface so a 400 names the field path.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.toast.model.dates import rest_date
from vendorfake.toast.model.money import to_dollars
from vendorfake.toast.model.pricing import APPLIED_TAX_NAMESPACE
from vendorfake.toast.model.references import RefRequest

__all__ = [
    "AppliedDiscountRequest",
    "AppliedServiceChargeRequest",
    "CheckRequest",
    "CustomerRequest",
    "DeliveryInfoRequest",
    "Money",
    "OrderRequest",
    "PaymentRequest",
    "SelectionRequest",
    "TipRequest",
    "VoidAllRequest",
    "VoidRequest",
    "project_applied_discount",
    "project_check",
    "project_order",
    "project_payment",
    "project_selection",
]

_REQUEST = ConfigDict(extra="ignore", frozen=True)

Money = int | float | str | None
"""A wire amount before conversion: number or numeric string (audit gap 8)."""


# ---------------------------------------------------------------------------
# Requests.
# ---------------------------------------------------------------------------


class CustomerRequest(BaseModel):
    model_config = _REQUEST

    firstName: str | None = None
    lastName: str | None = None
    phone: str | None = None
    email: str | None = None


class DeliveryInfoRequest(BaseModel):
    model_config = _REQUEST

    address1: str | None = None
    address2: str | None = None
    city: str | None = None
    state: str | None = None
    zipCode: str | None = None
    country: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    notes: str | None = None
    deliveryState: str | None = None


class AppliedServiceChargeRequest(BaseModel):
    """A service charge a caller puts on a check.

    ``extra="forbid"``, alone among the request models, since the stored check
    echoes these back through every GET and webhook (konyklabs/roadmap#39).
    The field set is JUDGMENT, assembled from the config API's ServiceCharge
    vocabulary; the orders specification lists the field and no shape.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    guid: str | None = None
    entityType: str | None = None
    externalId: str | None = None
    serviceCharge: RefRequest
    chargeAmount: Money = None
    chargeType: str | None = None
    name: str | None = None
    gratuity: bool | None = None
    taxable: bool | None = None


class SelectionRequest(BaseModel):
    """``{item{guid}, quantity}`` is the documented minimum."""

    model_config = _REQUEST

    item: RefRequest
    quantity: float = Field(gt=0)
    externalId: str | None = None
    itemGroup: RefRequest | None = None
    optionGroup: RefRequest | None = None
    preModifier: RefRequest | None = None
    modifiers: list[SelectionRequest] = Field(default_factory=list)
    seatNumber: int | None = None
    displayName: str | None = None
    unitOfMeasure: str | None = None
    selectionType: str | None = None
    taxInclusion: str | None = None
    #: POST-only documented fields, money.
    openPriceAmount: Money = None
    externalPriceAmount: Money = None
    deferred: bool | None = None


class PaymentRequest(BaseModel):
    """A payment on a check, on create or through ``POST .../payments``.

    ``amount`` is untyped here so an absent one reaches the surface, which
    answers the one documented code: 10025 "Payment amount cannot be empty".
    """

    model_config = _REQUEST

    type: str = Field(min_length=1)
    amount: Money = None
    tipAmount: Money = None
    amountTendered: Money = None
    guid: str | None = None
    externalId: str | None = None
    otherPayment: RefRequest | None = None
    paidDate: str | None = None


class CheckRequest(BaseModel):
    model_config = _REQUEST

    externalId: str | None = None
    selections: list[SelectionRequest] = Field(min_length=1)
    customer: CustomerRequest | None = None
    tabName: str | None = Field(default=None, max_length=255)
    taxExempt: bool = False
    appliedServiceCharges: list[AppliedServiceChargeRequest] | None = None
    payments: list[PaymentRequest] | None = None


class OrderRequest(BaseModel):
    """``POST /orders`` and ``POST /prices``: ``diningOption`` and at least one
    check are required (toast-orders-api.yaml)."""

    model_config = _REQUEST

    externalId: str | None = None
    diningOption: RefRequest
    checks: list[CheckRequest] = Field(min_length=1)
    table: RefRequest | None = None
    revenueCenter: RefRequest | None = None
    server: RefRequest | None = None
    numberOfGuests: int | None = Field(default=None, ge=0)
    deliveryInfo: DeliveryInfoRequest | None = None
    curbsidePickupInfo: dict[str, Any] | None = None
    promisedDate: str | None = None
    openedDate: str | None = None
    requiredPrepTime: str | None = None
    channelGuid: str | None = None
    pricingFeatures: list[str] | None = None
    createdInTestMode: bool | None = None
    appliedPackagingInfo: dict[str, Any] | None = None
    marketplaceFacilitatorTaxInfo: dict[str, Any] | None = None
    thirdPartyProviderInfo: dict[str, Any] | None = None


class VoidAllRequest(BaseModel):
    model_config = _REQUEST

    voidAll: bool


class VoidRequest(BaseModel):
    """``{"selections": {"voidAll": true}, "payments": {"voidAll": true}}``."""

    model_config = _REQUEST

    selections: VoidAllRequest
    payments: VoidAllRequest


class AppliedDiscountRequest(BaseModel):
    """``[{"discount": {"guid": ...}}, {"discount": {...}, "appliedPromoCode": "..."}]``."""

    model_config = _REQUEST

    discount: RefRequest
    appliedPromoCode: str | None = None


class TipRequest(BaseModel):
    """``PATCH .../payments/{guid}``: ``{"tipAmount"}`` only."""

    model_config = _REQUEST

    tipAmount: Money = None


# ---------------------------------------------------------------------------
# Projections. Every stored money field is cents; every stored instant is ms.
# ---------------------------------------------------------------------------


def _money(value: Any) -> Any:
    return to_dollars(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _date(value: Any) -> Any:
    return rest_date(value) if isinstance(value, int | float) and not isinstance(value, bool) else value


def _ref(value: Any) -> Any:
    return dict(value) if isinstance(value, Mapping) else None


def _external_ref(guid: Any, entity_type: str, external_id: Any) -> dict[str, Any]:
    return {"guid": guid, "entityType": entity_type, "externalId": external_id}


def project_applied_discount(stored: Mapping[str, Any]) -> dict[str, Any]:
    """The documented AppliedDiscount, key order from apiDiscountingOrders.html."""
    # JUDGMENT: ``approver``/``processingState``/``loyaltyDetails`` are omitted, not
    # null -- non-nullable in the schema and absent from the example (konyklabs/roadmap#56).
    document = {
        "guid": stored.get("guid"),
        "entityType": "AppliedCustomDiscount",
        "externalId": stored.get("externalId"),
    }
    document.update(
        compact(
            {
                "name": stored.get("name"),
                "comboItems": [],
                "discountAmount": _money(stored.get("discountAmount")),
                "discount": _ref(stored.get("discount")),
                "triggers": [
                    {"selection": _ref(trigger.get("selection")), "quantity": trigger.get("quantity")}
                    for trigger in stored.get("triggers", [])
                    if isinstance(trigger, Mapping)
                ],
                "appliedPromoCode": stored.get("appliedPromoCode"),
            }
        )
    )
    return document


def _external(stored: Mapping[str, Any], entity_type: str, rest: dict[str, Any]) -> dict[str, Any]:
    """``guid`` and ``externalId`` are always spelled, null included: the
    documented Order example shows ``"externalId": null`` and ``/prices``
    answers ``"guid": null`` (apiOrderPrices.html)."""
    return {
        "guid": stored.get("guid", stored.get("id")),
        "entityType": entity_type,
        "externalId": stored.get("externalId"),
        **compact(rest),
    }


def applied_tax_guid(selection_guid: object, tax: Mapping[str, Any]) -> str | None:
    """The derived AppliedTaxRate guid for a saved selection, or None for an
    unsaved one (the documented null). One rule, shared with the builder."""
    rate = tax.get("taxRate")
    rate_guid = rate.get("guid") if isinstance(rate, Mapping) else None
    if not selection_guid or not rate_guid:
        return None
    return str(uuid.uuid5(APPLIED_TAX_NAMESPACE, f"{selection_guid}:{rate_guid}"))


def project_selection(stored: Mapping[str, Any]) -> dict[str, Any]:
    return _external(
        stored,
        "MenuItemSelection",
        {
            "item": _ref(stored.get("item")),
            "itemGroup": _ref(stored.get("itemGroup")),
            "optionGroup": _ref(stored.get("optionGroup")),
            "preModifier": _ref(stored.get("preModifier")),
            "quantity": stored.get("quantity"),
            "seatNumber": stored.get("seatNumber"),
            "unitOfMeasure": stored.get("unitOfMeasure", "NONE"),
            "selectionType": stored.get("selectionType", "NONE"),
            "salesCategory": _ref(stored.get("salesCategory")),
            "appliedDiscounts": [
                project_applied_discount(d) for d in stored.get("appliedDiscounts", []) if isinstance(d, Mapping)
            ],
            "deferred": bool(stored.get("deferred", False)),
            "preDiscountPrice": _money(stored.get("preDiscountPrice")),
            "price": _money(stored.get("price")),
            "tax": _money(stored.get("tax")),
            "voided": bool(stored.get("voided", False)),
            "voidDate": _date(stored.get("voidDate")),
            "voidBusinessDate": stored.get("voidBusinessDate"),
            "voidReason": _ref(stored.get("voidReason")),
            "displayName": stored.get("displayName"),
            "plu": stored.get("plu"),
            "createdDate": _date(stored.get("createdDate")),
            "modifiedDate": _date(stored.get("modifiedDate")),
            "modifiers": [project_selection(m) for m in stored.get("modifiers", []) if isinstance(m, Mapping)],
            "fulfillmentStatus": stored.get("fulfillmentStatus", "NEW"),
            "taxInclusion": stored.get("taxInclusion", "NOT_INCLUDED"),
            "appliedTaxes": [
                {
                    # ``guid`` stays even when null: the schema requires the key,
                    # and on an unsaved order the value is the documented null.
                    # A saved selection whose taxes were built before it had a
                    # guid (the seed, add_selections) gets the same derived id
                    # the builder would have given it.
                    "guid": tax.get("guid") or applied_tax_guid(stored.get("guid"), tax),
                }
                | compact(
                    {
                        "entityType": tax.get("entityType", "AppliedTaxRate"),
                        "taxRate": _ref(tax.get("taxRate")),
                        "name": tax.get("name"),
                        "rate": tax.get("rate"),
                        "taxAmount": _money(tax.get("taxAmount")),
                        "type": tax.get("type"),
                    }
                )
                for tax in stored.get("appliedTaxes", [])
                if isinstance(tax, Mapping)
            ],
            "diningOption": _ref(stored.get("diningOption")),
            "openPriceAmount": _money(stored.get("openPriceAmount")),
            "receiptLinePrice": _money(stored.get("receiptLinePrice")),
        },
    )


def project_payment(stored: Mapping[str, Any]) -> dict[str, Any]:
    void_info = stored.get("voidInfo")
    return compact(
        {
            "guid": stored.get("id", stored.get("guid")),
            "entityType": "OrderPayment",
            "externalId": stored.get("externalId"),
            "paidDate": _date(stored.get("paidDate")),
            "paidBusinessDate": stored.get("paidBusinessDate"),
            "type": stored.get("type"),
            "amount": _money(stored.get("amount")),
            "tipAmount": _money(stored.get("tipAmount")),
            "amountTendered": _money(stored.get("amountTendered")),
            "cardEntryMode": stored.get("cardEntryMode"),
            "cardType": stored.get("cardType"),
            "last4Digits": stored.get("last4Digits"),
            "paymentStatus": stored.get("paymentStatus"),
            "refundStatus": stored.get("refundStatus", "NONE"),
            "voidInfo": (
                compact(
                    {
                        "voidDate": _date(void_info.get("voidDate")),
                        "voidBusinessDate": void_info.get("voidBusinessDate"),
                        "voidReason": _ref(void_info.get("voidReason")),
                    }
                )
                if isinstance(void_info, Mapping)
                else None
            ),
            "otherPayment": _ref(stored.get("otherPayment")),
            "checkGuid": stored.get("checkGuid"),
            "orderGuid": stored.get("orderGuid"),
        }
    )


def project_check(
    stored: Mapping[str, Any], payments: Sequence[Mapping[str, Any]], *, guest_pi: bool
) -> dict[str, Any]:
    """``payments`` are the check's payment documents, resolved by the caller.
    ``customer`` is emitted only with ``guest.pi:read`` (documented)."""
    return _external(
        stored,
        "Check",
        {
            "createdDate": _date(stored.get("createdDate")),
            "openedDate": _date(stored.get("openedDate")),
            "closedDate": _date(stored.get("closedDate")),
            "modifiedDate": _date(stored.get("modifiedDate")),
            "deletedDate": _date(stored.get("deletedDate")),
            "deleted": bool(stored.get("deleted", False)),
            "selections": [project_selection(s) for s in stored.get("selections", []) if isinstance(s, Mapping)],
            "customer": _ref(stored.get("customer")) if guest_pi else None,
            "taxExempt": bool(stored.get("taxExempt", False)),
            "displayNumber": stored.get("displayNumber"),
            "appliedServiceCharges": [
                compact({**dict(charge), "chargeAmount": _money(charge.get("chargeAmount"))})
                for charge in stored.get("appliedServiceCharges", [])
                if isinstance(charge, Mapping)
            ],
            "appliedDiscounts": [
                project_applied_discount(d) for d in stored.get("appliedDiscounts", []) if isinstance(d, Mapping)
            ],
            "amount": _money(stored.get("amount")),
            "taxAmount": _money(stored.get("taxAmount")),
            "totalAmount": _money(stored.get("totalAmount")),
            "payments": [project_payment(p) for p in payments],
            "tabName": stored.get("tabName"),
            "paymentStatus": stored.get("paymentStatus"),
            "voided": bool(stored.get("voided", False)),
            "voidDate": _date(stored.get("voidDate")),
            "voidBusinessDate": stored.get("voidBusinessDate"),
            "paidDate": _date(stored.get("paidDate")),
        },
    )


def project_order(
    stored: Mapping[str, Any],
    payments_by_guid: Mapping[str, Mapping[str, Any]],
    *,
    guest_pi: bool = True,
    delivery_address: bool = True,
) -> dict[str, Any]:
    """A stored order as the documented Order document.

    ``payments_by_guid`` resolves each check's payment references.
    ``customer`` needs ``guest.pi:read`` and ``deliveryInfo`` needs
    ``delivery_info.address:read`` (documented on GET /orders/{guid}).
    """
    checks = []
    for check in stored.get("checks", []):
        if not isinstance(check, Mapping):
            continue
        refs = [str(guid) for guid in check.get("payments", [])]
        checks.append(
            project_check(check, [payments_by_guid[g] for g in refs if g in payments_by_guid], guest_pi=guest_pi)
        )
    return _external(
        stored,
        "Order",
        {
            "openedDate": _date(stored.get("openedDate")),
            "modifiedDate": _date(stored.get("modifiedDate")),
            "promisedDate": _date(stored.get("promisedDate")),
            "createdDate": _date(stored.get("createdDate")),
            "paidDate": _date(stored.get("paidDate")),
            "closedDate": _date(stored.get("closedDate")),
            "deletedDate": _date(stored.get("deletedDate")),
            "deleted": bool(stored.get("deleted", False)),
            "businessDate": stored.get("businessDate"),
            "channelGuid": stored.get("channelGuid"),
            "diningOption": _ref(stored.get("diningOption")),
            "checks": checks,
            "table": _ref(stored.get("table")),
            "serviceArea": _ref(stored.get("serviceArea")),
            "restaurantService": _ref(stored.get("restaurantService")),
            "revenueCenter": _ref(stored.get("revenueCenter")),
            "server": _ref(stored.get("server")),
            "source": stored.get("source", "API"),
            "approvalStatus": stored.get("approvalStatus", "APPROVED"),
            "guestOrderStatus": stored.get("guestOrderStatus"),
            "voided": bool(stored.get("voided", False)),
            "voidDate": _date(stored.get("voidDate")),
            "voidBusinessDate": stored.get("voidBusinessDate"),
            "numberOfGuests": stored.get("numberOfGuests"),
            "deliveryInfo": _ref(stored.get("deliveryInfo")) if delivery_address else None,
            "curbsidePickupInfo": _ref(stored.get("curbsidePickupInfo")),
            "requiredPrepTime": stored.get("requiredPrepTime"),
            "estimatedFulfillmentDate": _date(stored.get("estimatedFulfillmentDate")),
            "pricingFeatures": list(stored.get("pricingFeatures", [])),
            "createdInTestMode": bool(stored.get("createdInTestMode", False)),
            "displayNumber": stored.get("displayNumber"),
            "appliedPackagingInfo": _ref(stored.get("appliedPackagingInfo")),
            "marketplaceFacilitatorTaxInfo": _ref(stored.get("marketplaceFacilitatorTaxInfo")),
            "thirdPartyProviderInfo": _ref(stored.get("thirdPartyProviderInfo")),
        },
    )
