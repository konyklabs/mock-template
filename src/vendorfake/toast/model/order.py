"""The order wire vocabulary: what the orders surface parses, and how a stored
order, check, selection, payment and applied discount are projected.

FOR: stating once what a Toast order document carries -- field names, units,
enums -- so the surfaces parse and project through one vocabulary and tests
can pin it without a unit.

Field sets are the orders specification's (toast-orders-api.yaml v2.9.5); see
the audit for the full listing. Units on the wire: money is decimal dollars
(``model/money.py``), instants are ``...+0000`` strings (``model/dates.py``),
``businessDate`` is an integer ``yyyyMMdd``, ``quantity`` is a double. In the
store: cents, epoch ms, the same integer, the same double.

Projection emits the read-only fields the specification lists with the
documented nulls where the documented Order example shows them
(``"externalId": null``) and drops what the scenario never set; a consumer
writing ``if "table" in order`` takes the branch the sparse real document
would give.

Requests are ``extra="ignore"``: a documented Order carries far more than an
ordering integration sends, and a body copied back from a GET must not 400 on
a read-only field. Money in a request is ``int | float | str`` and converted
by the surface so the 400 names the field path.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.util.json import compact
from vendorfake.toast.model.dates import rest_date
from vendorfake.toast.model.money import to_dollars
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

    ``extra="forbid"``, alone among the request models, and deliberately: the
    stored check echoes these back through every GET and every webhook, so a
    lax model here would be the one place a client injects free JSON into a
    projected document (konyklabs/roadmap#39 review, finding 7). The field
    set is JUDGMENT, assembled from the config API's ServiceCharge vocabulary
    -- the orders specification lists ``appliedServiceCharges`` and no shape.
    ``serviceCharge.guid`` must resolve like every other reference;
    ``chargeAmount`` is caller-stated and never computed
    (``TOAST_NOT_MODELED``).
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
    # ``approver``, ``processingState`` and ``loyaltyDetails`` are omitted
    # rather than answered null: the schema types them as objects/enums with
    # no nullable, and the discounting walkthrough's example does not carry
    # them (JUDGMENT: omission; found by the fidelity validator, roadmap#56).
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
                compact(
                    {
                        "guid": tax.get("guid"),
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
