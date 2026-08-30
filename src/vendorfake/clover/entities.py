"""The shapes this vendor stores, and the collections it stores them in.

FOR: giving the surfaces one typed reading of every stored entity, so that the
name of a stored field is written down once instead of being spelled as a
dictionary key in each handler that touches it.

INVARIANT: **absence is absence.** A field never set is *missing* from the
entity dict, never present as ``None``: every :meth:`to_entity` drops unset
optionals through the core's ``compact()``, and a field is cleared with
``pop`` and never with ``= None``. The entity digest, the journal's
``changed`` list and the wire projections all depend on it, exactly as in the
Square package.

Time, in this package's entities
--------------------------------
Every stored instant is **epoch milliseconds**, matching both the core clock
(``Clock.now()`` is ms) and Clover's own entity timestamps (``createdTime``,
``modifiedTime``, ``ts`` are documented ms). The two OAuth expirations are
stored as ``access_token_expiration_ms`` / ``refresh_token_expiration_ms`` --
the ``_ms`` suffix is load-bearing, because the *wire* fields they project to
(``access_token_expiration``, documented Unix **seconds**) differ by a factor
of 1000 and an unsuffixed name would invite exactly that bug. The one
conversion lives in ``surface/common.py``'s :func:`~.common.wire_seconds`.

The stored model is this unit's own; internal bookkeeping fields
(``used_at_ms``, ``refresh_used_at_ms``) are snake_case like the Square
package's, while fields Clover itself names keep Clover's camelCase.

There is deliberately no ``revoked_at``: Clover's v2 OAuth documents no revoke
endpoint at all (the audit found authorize, token, refresh and the unmodelled
migrate), so a revocation state would be a state nothing can enter. A rotated
refresh token is recorded with ``refresh_used_at_ms`` instead -- "Refresh
token is for single use and becomes invalid immediately after a new
access_token and refresh_token pair is generated"
(https://docs.clover.com/dev/docs/refresh-access-tokens) -- and the *access*
token on that record stays valid until its own expiry (JUDGMENT: the docs are
silent on prior access tokens, and inventing revocation teaches consumers an
invalidation rule Clover does not publish).
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

    The reference-data collections (employees, tenders, order types, the
    default service charge, tax rates, modifier groups, modifiers) hold plain
    documents shaped as their Clover reference pages list them and are
    projected as stored; only the entities the surfaces *mutate* have typed
    readers below.
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


# ---------------------------------------------------------------------------
# Readers. Tolerant on type, strict on presence: a stored entity is produced by
# this package, so a wrong type is a defect here rather than bad input, and
# coercing it quietly beats raising from inside a projection.
# ---------------------------------------------------------------------------


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
    """The merchant this unit represents. One per unit, in practice.

    ``owner`` and ``address`` are stored as the nested documents the merchant
    reference lists (https://docs.clover.com/dev/docs/merchantgetmerchant --
    "owner{...}", "address{...}", contents undocumented; see
    ``model/merchant.py``). ``currency`` is what an order created without one
    is denominated in (JUDGMENT; see the orders surface).
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

    Everything documented is client-owned, ``total`` above all: "Order totals
    are calculated dynamically and updated by the app the merchant uses... If
    your app modifies an order, it must update the total as well"
    (https://docs.clover.com/dev/docs/creating-custom-orders). Nothing here
    recomputes it; the atomic endpoints compute a total *once*, at creation.

    ``merchant_id`` is this unit's own scoping field (snake_case, internal):
    every order route lives under ``/v3/merchants/{mId}/`` and a token for one
    merchant must not see another's. ``state`` is stored **verbatim** --
    ``Open`` and ``open`` both appear in Clover's docs -- and compared
    case-insensitively against the machine's lowercase canon (``machine.py``).
    Absent means null, "the default for hidden orders".

    ``lineItems``, ``discounts`` and ``serviceCharge`` are stored as the plain
    documents the wire carries; the line-item and discount shapes are typed
    on the request side (``model/order.py``) and projected back through the
    wire models.
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
    """An issued authorization code. ``id`` is the opaque code value itself.

    Single-use with a ten-minute expiry -- both JUDGMENT: Clover documents
    neither a code lifetime nor its reuse behaviour (the high-trust flow page
    shows only the redirect carrying ``code``); single-use is RFC 6749's own
    rule and the TTL is :attr:`~vendorfake.clover.config.CloverConfig.authorization_code_ttl_ms`.
    ``code_challenge`` is set when the authorize request carried one, which is
    what routes the exchange down the PKCE path.
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

    ``refresh_used_at_ms`` is the single-use rotation mark: set when this
    record's refresh token is exchanged for a new pair, at which point the
    refresh token is dead and the access token lives on to its own expiry.
    See the module docstring for the provenance of both halves.
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
