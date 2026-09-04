"""The shapes this vendor stores, one typed reading per entity so a field is
spelled once rather than respelled as a dict key per handler.

INVARIANT: absence is absence -- an unset field is missing from the entity
dict, never ``None`` (:meth:`to_entity` compacts it away; clearing pops
rather than sets ``None``), since the digest, the journal and the wire
projections all depend on it. Instants are epoch milliseconds; the OAuth
``_ms``-suffixed fields project to documented Unix-seconds wire fields
(``surface/common.py``'s :func:`~.common.wire_seconds`). There is no
``revoked_at`` -- Clover's v2 OAuth has no revoke endpoint -- so rotation is
marked with ``refresh_used_at_ms`` instead (refresh tokens are documented
single-use, https://docs.clover.com/dev/docs/refresh-access-tokens;
JUDGMENT: the access token stays valid to its own expiry).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = [
    "COL",
    "MAX_LINE_ITEMS_PER_ORDER",
    "AuthorizationCodeEntity",
    "CloverCollections",
    "ItemEntity",
    "MerchantEntity",
    "OrderEntity",
    "TokenEntity",
]

MAX_LINE_ITEMS_PER_ORDER = 3000
""""an order can have a maximum of 3,000 line items" -- exceeding it is a 400
(https://docs.clover.com/dev/docs/ordercreatelineitem)."""


@dataclass(frozen=True, slots=True)
class CloverCollections:
    """The store collections this vendor uses, named once.

    Only entities the surfaces *mutate* get typed readers below; the rest
    are plain documents shaped as Clover's reference pages list them.
    """

    merchants: str = "merchants"
    items: str = "items"
    orders: str = "orders"
    codes: str = "authorization_codes"
    tokens: str = "tokens"
    employees: str = "employees"
    tenders: str = "tenders"
    order_types: str = "order_types"
    service_charges: str = "service_charges"
    tax_rates: str = "tax_rates"
    modifier_groups: str = "modifier_groups"
    modifiers: str = "modifiers"
    customers: str = "customers"
    payments: str = "payments"
    print_events: str = "print_events"

    def names(self) -> tuple[str, ...]:
        """Every collection name, in declaration order."""
        return (
            self.merchants,
            self.items,
            self.orders,
            self.codes,
            self.tokens,
            self.employees,
            self.tenders,
            self.order_types,
            self.service_charges,
            self.tax_rates,
            self.modifier_groups,
            self.modifiers,
            self.customers,
            self.payments,
            self.print_events,
        )


COL = CloverCollections()
"""The one place a collection name is spelled."""


# Readers: tolerant on type, strict on presence -- a stored entity is
# produced by this package, so a wrong type is a defect here, not bad input.


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else value


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _opt_bool(value: Any) -> bool | None:
    return None if value is None else bool(value)


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


def _opt_mapping(value: Any) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


@dataclass(frozen=True, slots=True)
class MerchantEntity:
    """The merchant this unit represents; one per unit in practice.

    ``owner``/``address`` are Clover's own undocumented nested shapes
    (https://docs.clover.com/dev/docs/merchantgetmerchant). ``currency`` is
    the default for an order created without one (JUDGMENT).
    """

    id: str
    name: str
    currency: str = "USD"
    owner: dict[str, Any] | None = None
    address: dict[str, Any] | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> MerchantEntity:
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            currency=_str(entity.get("currency"), "USD"),
            owner=_opt_mapping(entity.get("owner")),
            address=_opt_mapping(entity.get("address")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "currency": self.currency,
                "owner": self.owner,
                "address": self.address,
            }
        )


@dataclass(frozen=True, slots=True)
class ItemEntity:
    """One inventory item, stored under the field names Clover's create-item
    example uses verbatim (https://docs.clover.com/dev/docs/inventorycreateitem).
    Defaults and their provenance are on ``model/inventory.py``."""

    id: str
    name: str
    price: int
    hidden: bool = False
    available: bool = True
    priceType: str = "FIXED"
    defaultTaxRates: bool = True
    isRevenue: bool = False
    sku: str | None = None
    code: str | None = None
    #: Epoch ms, per the documented example (``modifiedTime: 1755786102000``).
    modifiedTime: int | None = None
    #: Explicit tax-rate associations, ``[{"id"}]``, used when
    #: ``defaultTaxRates`` is false (taxratecreateordeletetaxrateitems).
    taxRates: tuple[dict[str, Any], ...] = ()
    #: Modifier-group associations, ids only; the ``modifierGroups``
    #: expansion resolves them (modifiercreateordeleteitemmodifiergroups).
    modifierGroupIds: tuple[str, ...] = ()

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> ItemEntity:
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            price=_int(entity.get("price")),
            hidden=bool(entity.get("hidden", False)),
            available=bool(entity.get("available", True)),
            priceType=_str(entity.get("priceType"), "FIXED"),
            defaultTaxRates=bool(entity.get("defaultTaxRates", True)),
            isRevenue=bool(entity.get("isRevenue", False)),
            sku=_opt_str(entity.get("sku")),
            code=_opt_str(entity.get("code")),
            modifiedTime=_opt_int(entity.get("modifiedTime")),
            taxRates=tuple(_dict_list(entity.get("taxRates"))),
            modifierGroupIds=_str_tuple(entity.get("modifierGroupIds")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "price": self.price,
                "hidden": self.hidden,
                "available": self.available,
                "priceType": self.priceType,
                "defaultTaxRates": self.defaultTaxRates,
                "isRevenue": self.isRevenue,
                "sku": self.sku,
                "code": self.code,
                "modifiedTime": self.modifiedTime,
                "taxRates": list(self.taxRates) if self.taxRates else None,
                "modifierGroupIds": list(self.modifierGroupIds) if self.modifierGroupIds else None,
            }
        )


@dataclass(frozen=True, slots=True)
class OrderEntity:
    """One order, stored under Clover's own field names.

    DOCUMENTED: totals are client-owned -- "If your app modifies an order,
    it must update the total as well"
    (https://docs.clover.com/dev/docs/creating-custom-orders); only the
    atomic endpoints recompute ``total``. ``state`` is stored verbatim --
    Clover's docs use both ``Open`` and ``open`` -- and compared
    case-insensitively (``machine.py``). ``lineItems``, ``discounts`` and
    ``serviceCharge`` are typed on the request side (``model/order.py``).
    """

    id: str
    merchant_id: str
    currency: str
    total: int
    state: str | None = None
    paymentState: str = "OPEN"
    payType: str | None = None
    createdTime: int | None = None
    modifiedTime: int | None = None
    clientCreatedTime: int | None = None
    deletedTime: int | None = None
    title: str | None = None
    note: str | None = None
    externalReferenceId: str | None = None
    testMode: bool | None = None
    taxRemoved: bool | None = None
    manualTransaction: bool | None = None
    groupLineItems: bool | None = None
    orderType: dict[str, Any] | None = None
    #: ``{"id"}`` references a consumer attaches before paying.
    employee: dict[str, Any] | None = None
    customers: tuple[dict[str, Any], ...] = ()
    lineItems: tuple[dict[str, Any], ...] = ()
    discounts: tuple[dict[str, Any], ...] = ()
    serviceCharge: dict[str, Any] | None = None
    #: ``[{"id"}]`` references to the payments collection.
    payments: tuple[dict[str, Any], ...] = ()

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> OrderEntity:
        return cls(
            id=_str(entity["id"]),
            merchant_id=_str(entity.get("merchant_id")),
            currency=_str(entity.get("currency"), "USD"),
            total=_int(entity.get("total")),
            state=_opt_str(entity.get("state")),
            paymentState=_str(entity.get("paymentState"), "OPEN"),
            payType=_opt_str(entity.get("payType")),
            createdTime=_opt_int(entity.get("createdTime")),
            modifiedTime=_opt_int(entity.get("modifiedTime")),
            clientCreatedTime=_opt_int(entity.get("clientCreatedTime")),
            deletedTime=_opt_int(entity.get("deletedTime")),
            title=_opt_str(entity.get("title")),
            note=_opt_str(entity.get("note")),
            externalReferenceId=_opt_str(entity.get("externalReferenceId")),
            testMode=_opt_bool(entity.get("testMode")),
            taxRemoved=_opt_bool(entity.get("taxRemoved")),
            manualTransaction=_opt_bool(entity.get("manualTransaction")),
            groupLineItems=_opt_bool(entity.get("groupLineItems")),
            orderType=_opt_mapping(entity.get("orderType")),
            employee=_opt_mapping(entity.get("employee")),
            customers=tuple(_dict_list(entity.get("customers"))),
            lineItems=tuple(_dict_list(entity.get("lineItems"))),
            discounts=tuple(_dict_list(entity.get("discounts"))),
            serviceCharge=_opt_mapping(entity.get("serviceCharge")),
            payments=tuple(_dict_list(entity.get("payments"))),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "merchant_id": self.merchant_id,
                "currency": self.currency,
                "total": self.total,
                "state": self.state,
                "paymentState": self.paymentState,
                "payType": self.payType,
                "createdTime": self.createdTime,
                "modifiedTime": self.modifiedTime,
                "clientCreatedTime": self.clientCreatedTime,
                "deletedTime": self.deletedTime,
                "title": self.title,
                "note": self.note,
                "externalReferenceId": self.externalReferenceId,
                "testMode": self.testMode,
                "taxRemoved": self.taxRemoved,
                "manualTransaction": self.manualTransaction,
                "groupLineItems": self.groupLineItems,
                "orderType": self.orderType,
                "employee": self.employee,
                "customers": list(self.customers) if self.customers else None,
                "lineItems": list(self.lineItems),
                "discounts": list(self.discounts) if self.discounts else None,
                "serviceCharge": self.serviceCharge,
                "payments": list(self.payments) if self.payments else None,
            }
        )

    @property
    def is_deleted(self) -> bool:
        return self.deletedTime is not None


@dataclass(frozen=True, slots=True)
class AuthorizationCodeEntity:
    """An issued authorization code; ``id`` is the code value itself.

    Single-use, ten-minute TTL (JUDGMENT: Clover documents neither).
    ``code_challenge`` set means the exchange follows the PKCE path.
    """

    id: str
    client_id: str
    merchant_id: str
    #: Epoch ms; the instant itself is already too late (see `is_past_ms`).
    expires_at_ms: int
    code_challenge: str | None = None
    used_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> AuthorizationCodeEntity:
        return cls(
            id=_str(entity["id"]),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            expires_at_ms=_int(entity.get("expires_at_ms")),
            code_challenge=_opt_str(entity.get("code_challenge")),
            used_at_ms=_opt_int(entity.get("used_at_ms")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "expires_at_ms": self.expires_at_ms,
                "code_challenge": self.code_challenge,
                "used_at_ms": self.used_at_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class TokenEntity:
    """One issued access token and the refresh token that came with it.

    ``refresh_used_at_ms`` marks single-use rotation (provenance: module
    docstring); the access token then lives on to its own expiry.
    """

    id: str
    access_token: str
    refresh_token: str
    client_id: str
    merchant_id: str
    #: Epoch ms. Projected to the documented Unix-seconds wire fields by
    #: ``surface/common.py``; the ``_ms`` suffix is why nobody mixes them up.
    access_token_expiration_ms: int
    refresh_token_expiration_ms: int
    permissions: tuple[str, ...] = ()
    createdTime: int | None = None
    refresh_used_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TokenEntity:
        return cls(
            id=_str(entity["id"]),
            access_token=_str(entity.get("access_token")),
            refresh_token=_str(entity.get("refresh_token")),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            access_token_expiration_ms=_int(entity.get("access_token_expiration_ms")),
            refresh_token_expiration_ms=_int(entity.get("refresh_token_expiration_ms")),
            permissions=_str_tuple(entity.get("permissions")),
            createdTime=_opt_int(entity.get("createdTime")),
            refresh_used_at_ms=_opt_int(entity.get("refresh_used_at_ms")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "access_token_expiration_ms": self.access_token_expiration_ms,
                "refresh_token_expiration_ms": self.refresh_token_expiration_ms,
                "permissions": list(self.permissions),
                "createdTime": self.createdTime,
                "refresh_used_at_ms": self.refresh_used_at_ms,
            }
        )
