"""The shapes this vendor stores, and the collections it stores them in.

FOR: giving the surfaces one typed reading of every stored entity they read or
mutate, so the name of a stored field is written down once.

INVARIANT: **absence is absence.** A field never set is missing from the
entity dict, never present as ``None`` -- except where the vendor's own schema
declares a field ``nullable`` and its examples print the null, which is then
data rather than absence (an outlet's ``physical_state``, a register's
``register_close_time`` while it is open).

TWO VERSIONS, AND WHY THE STORED FIELD IS NOT CALLED ``version``
---------------------------------------------------------------
The core store keeps its own per-entity ``version``, starting at 1 and bumped
by one on every update; optimistic concurrency and the journal are written
against it and a vendor may not redefine it. Lightspeed's ``version`` is a
different thing entirely -- "simply a monotonically increasing integer", one
sequence **per retailer across every resource type**, bumped on every mutation
of anything (https://x-series-api.lightspeedhq.com/docs/pagination). So the
Lightspeed number is stored under :data:`OBJECT_VERSION` and projected to the
wire as ``version``; the store's own key keeps its meaning. ``versioning.py``
owns the counter.

Ids: the store needs ``id``, and so does Lightspeed. They are the same field.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact
from vendorfake.core.webhooks.models import SUBSCRIPTION_COLLECTION

__all__ = [
    "COL",
    "OBJECT_VERSION",
    "AuthorizationCodeEntity",
    "LightspeedCollections",
    "OutletEntity",
    "PaymentTypeEntity",
    "RefreshTokenEntity",
    "RegisterClosureEntity",
    "RegisterEntity",
    "RetailerEntity",
    "SaleEntity",
    "TokenEntity",
]

OBJECT_VERSION = "object_version"
"""Where a stored entity carries Lightspeed's retailer-global version number.
Projected to the wire as ``version``; see the module docstring for why it
cannot simply BE ``version``."""


@dataclass(frozen=True, slots=True)
class LightspeedCollections:
    """The store collections this vendor uses, named once.

    Several are declared here and populated by a later slice of
    konyklabs/roadmap#94 -- ``products``, ``inventory``, ``customers`` and
    ``sales`` -- so that the collection vocabulary is settled in one place and
    a surface module added later does not invent a second name for the same
    thing. Naming a collection costs nothing at run time: the store
    materialises one on first use.
    """

    #: One row. The unit serves ONE retailer (its ``domain_prefix``).
    retailer: str = "retailer"
    outlets: str = "outlets"
    registers: str = "registers"
    #: Synthesised at ``PUT /registers/{id}/actions/close``. No REST resource
    #: for a closure exists anywhere in the 135 documented paths; the only
    #: handles on it are that action and ``GET .../payments_summary``.
    register_closures: str = "register_closures"
    payment_types: str = "payment_types"
    #: Populated by a later slice.
    products: str = "products"
    inventory: str = "inventory"
    customers: str = "customers"
    sales: str = "sales"
    #: The webhook subscription list is the CORE's, so that the dispatcher's
    #: own matcher is what filters a delivery. Lightspeed's ``/webhooks`` CRUD
    #: reads and writes exactly this collection.
    webhooks: str = SUBSCRIPTION_COLLECTION
    #: The OAuth application(s) a code may be issued to.
    oauth_apps: str = "oauth_apps"
    #: Single-use authorization codes from the ``GET /connect`` stand-in.
    auth_codes: str = "auth_codes"
    #: Access tokens: seeded, minted by an exchange, or minted by a refresh.
    tokens: str = "tokens"
    #: Refresh tokens, retired on use ("rotation").
    refresh_tokens: str = "refresh_tokens"
    #: Personal tokens -- Plus-plan only, created in the web application, so
    #: they only ever arrive from the seed.
    personal_tokens: str = "personal_tokens"

    def names(self) -> tuple[str, ...]:
        return (
            self.retailer,
            self.outlets,
            self.registers,
            self.register_closures,
            self.payment_types,
            self.products,
            self.inventory,
            self.customers,
            self.sales,
            self.webhooks,
            self.oauth_apps,
            self.auth_codes,
            self.tokens,
            self.refresh_tokens,
            self.personal_tokens,
        )


COL = LightspeedCollections()


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else value


def _bool(value: Any, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [dict(row) for row in value if isinstance(row, Mapping)]
    return []


@dataclass(frozen=True, slots=True)
class RetailerEntity:
    """The one retailer, stored in the documented ``Retailer`` shape.

    Only the fields this slice reads back are typed; the rest of the
    documented document (``gift_cards``, ``loyalty``, ``sku_sequence``,
    ``on_account``) is carried in :attr:`document` exactly as the seed supplied
    it, because nothing here computes from those blocks.
    """

    id: str
    name: str
    domain_prefix: str
    currency_code: str
    currency_symbol: str
    timezone: str
    country: str
    document: dict[str, Any] = field(default_factory=dict)
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> RetailerEntity:
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            domain_prefix=_str(entity.get("domain_prefix")),
            currency_code=_str(entity.get("currency_code"), "USD"),
            currency_symbol=_str(entity.get("currency_symbol"), "$"),
            timezone=_str(entity.get("timezone"), "UTC"),
            country=_str(entity.get("country"), "US"),
            document=_mapping(entity.get("document")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return {
            "id": self.id,
            "name": self.name,
            "domain_prefix": self.domain_prefix,
            "currency_code": self.currency_code,
            "currency_symbol": self.currency_symbol,
            "timezone": self.timezone,
            "country": self.country,
            "document": dict(self.document),
            OBJECT_VERSION: self.object_version,
        }


@dataclass(frozen=True, slots=True)
class OutletEntity:
    """One outlet, in the documented ``Outlet`` shape.

    ``version`` is ``format: int64`` and REQUIRED on ``Outlet``, as are ``id``,
    ``name``, ``default_tax_id``, ``currency``, ``display_prices``,
    ``time_zone``, ``currency_symbol`` and ``attributes``.
    """

    id: str
    name: str
    currency: str
    currency_symbol: str
    display_prices: str
    time_zone: str
    default_tax_id: str
    attributes: list[dict[str, Any]] = field(default_factory=list)
    physical_address_1: str | None = None
    physical_address_2: str | None = None
    physical_city: str | None = None
    physical_state: str | None = None
    physical_suburb: str | None = None
    physical_postcode: str | None = None
    physical_country_id: str | None = None
    email: str | None = None
    deleted_at: str | None = None
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> OutletEntity:
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            currency=_str(entity.get("currency"), "USD"),
            currency_symbol=_str(entity.get("currency_symbol"), "$"),
            display_prices=_str(entity.get("display_prices"), "inclusive"),
            time_zone=_str(entity.get("time_zone"), "UTC"),
            default_tax_id=_str(entity.get("default_tax_id")),
            attributes=_rows(entity.get("attributes")),
            physical_address_1=_opt_str(entity.get("physical_address_1")),
            physical_address_2=_opt_str(entity.get("physical_address_2")),
            physical_city=_opt_str(entity.get("physical_city")),
            physical_state=_opt_str(entity.get("physical_state")),
            physical_suburb=_opt_str(entity.get("physical_suburb")),
            physical_postcode=_opt_str(entity.get("physical_postcode")),
            physical_country_id=_opt_str(entity.get("physical_country_id")),
            email=_opt_str(entity.get("email")),
            deleted_at=_opt_str(entity.get("deleted_at")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "currency": self.currency,
                "currency_symbol": self.currency_symbol,
                "display_prices": self.display_prices,
                "time_zone": self.time_zone,
                "default_tax_id": self.default_tax_id,
                "attributes": list(self.attributes),
                "physical_address_1": self.physical_address_1,
                "physical_address_2": self.physical_address_2,
                "physical_city": self.physical_city,
                "physical_state": self.physical_state,
                "physical_suburb": self.physical_suburb,
                "physical_postcode": self.physical_postcode,
                "physical_country_id": self.physical_country_id,
                "email": self.email,
                "deleted_at": self.deleted_at,
                OBJECT_VERSION: self.object_version,
            }
        )


@dataclass(frozen=True, slots=True)
class RegisterEntity:
    """One register (till), in the documented ``Register`` shape.

    ``ask_for_note_on_save`` is ``format: double`` with the documented meanings
    ``0`` never, ``1`` on save/layby/account/return, ``2`` always;
    ``invoice_sequence`` is likewise a number rather than an integer. Both are
    stored as ints and emitted as the JSON numbers the examples print.
    """

    id: str
    name: str
    outlet_id: str
    is_open: bool = False
    invoice_prefix: str = ""
    invoice_suffix: str = ""
    invoice_sequence: int = 1
    ask_for_note_on_save: int = 1
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
    register_open_time: str | None = None
    register_close_time: str | None = None
    deleted_at: str | None = None
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> RegisterEntity:
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            outlet_id=_str(entity.get("outlet_id")),
            is_open=_bool(entity.get("is_open")),
            invoice_prefix=_str(entity.get("invoice_prefix")),
            invoice_suffix=_str(entity.get("invoice_suffix")),
            invoice_sequence=_int(entity.get("invoice_sequence"), 1),
            ask_for_note_on_save=_int(entity.get("ask_for_note_on_save"), 1),
            ask_for_user_on_sale=_bool(entity.get("ask_for_user_on_sale")),
            email_receipt=_bool(entity.get("email_receipt")),
            print_receipt=_bool(entity.get("print_receipt"), True),
            print_note_on_receipt=_bool(entity.get("print_note_on_receipt")),
            is_quick_keys_enabled=_bool(entity.get("is_quick_keys_enabled"), True),
            show_discounts_on_receipts=_bool(entity.get("show_discounts_on_receipts"), True),
            receipt_template_id=_opt_str(entity.get("receipt_template_id")),
            button_layout_id=_opt_str(entity.get("button_layout_id")),
            cash_managed_payment_type_id=_opt_str(entity.get("cash_managed_payment_type_id")),
            register_open_sequence_id=_opt_str(entity.get("register_open_sequence_id")),
            register_open_time=_opt_str(entity.get("register_open_time")),
            register_close_time=_opt_str(entity.get("register_close_time")),
            deleted_at=_opt_str(entity.get("deleted_at")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "outlet_id": self.outlet_id,
                "is_open": self.is_open,
                "invoice_prefix": self.invoice_prefix,
                "invoice_suffix": self.invoice_suffix,
                "invoice_sequence": self.invoice_sequence,
                "ask_for_note_on_save": self.ask_for_note_on_save,
                "ask_for_user_on_sale": self.ask_for_user_on_sale,
                "email_receipt": self.email_receipt,
                "print_receipt": self.print_receipt,
                "print_note_on_receipt": self.print_note_on_receipt,
                "is_quick_keys_enabled": self.is_quick_keys_enabled,
                "show_discounts_on_receipts": self.show_discounts_on_receipts,
                "receipt_template_id": self.receipt_template_id,
                "button_layout_id": self.button_layout_id,
                "cash_managed_payment_type_id": self.cash_managed_payment_type_id,
                "register_open_sequence_id": self.register_open_sequence_id,
                "register_open_time": self.register_open_time,
                "register_close_time": self.register_close_time,
                "deleted_at": self.deleted_at,
                OBJECT_VERSION: self.object_version,
            }
        )


@dataclass(frozen=True, slots=True)
class RegisterClosureEntity:
    """One register closure -- synthesised at close, because no REST resource
    for one exists.

    The fields are the ones the documented ``payments_summary`` example prints:
    ``register_closure_id``, ``register_closure_sequence_number``,
    ``register_open_time`` and the per-payment-type ``payments`` totals. The
    ``register_id`` and the closing instant are this unit's, so the closure can
    be found again and carried into the ``register_closure.create`` webhook.
    """

    id: str
    register_id: str
    outlet_id: str
    sequence_number: int
    register_open_time: str | None
    register_close_time: str
    payments: list[dict[str, Any]] = field(default_factory=list)
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> RegisterClosureEntity:
        return cls(
            id=_str(entity["id"]),
            register_id=_str(entity.get("register_id")),
            outlet_id=_str(entity.get("outlet_id")),
            sequence_number=_int(entity.get("sequence_number"), 1),
            register_open_time=_opt_str(entity.get("register_open_time")),
            register_close_time=_str(entity.get("register_close_time")),
            payments=_rows(entity.get("payments")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "register_id": self.register_id,
                "outlet_id": self.outlet_id,
                "sequence_number": self.sequence_number,
                "register_open_time": self.register_open_time,
                "register_close_time": self.register_close_time,
                "payments": list(self.payments),
                OBJECT_VERSION: self.object_version,
            }
        )


@dataclass(frozen=True, slots=True)
class PaymentTypeEntity:
    """One payment type, in the documented ``PaymentType`` shape.

    ``id``, ``name``, ``type_id``, ``version``, ``disabled`` and ``internal``
    are the required fields; ``config`` is ``additionalProperties: true`` and
    is stored as the seed supplied it.
    """

    id: str
    name: str
    type_id: int
    disabled: bool = False
    internal: bool = False
    gateway: bool = False
    name_changed_by_user: bool = False
    config: dict[str, Any] | None = None
    outlet_ids: tuple[str, ...] = ()
    deleted_at: str | None = None
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> PaymentTypeEntity:
        raw_config = entity.get("config")
        return cls(
            id=_str(entity["id"]),
            name=_str(entity.get("name")),
            type_id=_int(entity.get("type_id")),
            disabled=_bool(entity.get("disabled")),
            internal=_bool(entity.get("internal")),
            gateway=_bool(entity.get("gateway")),
            name_changed_by_user=_bool(entity.get("name_changed_by_user")),
            config=_mapping(raw_config) if isinstance(raw_config, Mapping) else None,
            outlet_ids=_str_tuple(entity.get("outlet_ids")),
            deleted_at=_opt_str(entity.get("deleted_at")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "name": self.name,
                "type_id": self.type_id,
                "disabled": self.disabled,
                "internal": self.internal,
                "gateway": self.gateway,
                "name_changed_by_user": self.name_changed_by_user,
                "config": None if self.config is None else dict(self.config),
                "outlet_ids": list(self.outlet_ids) if self.outlet_ids else None,
                "deleted_at": self.deleted_at,
                OBJECT_VERSION: self.object_version,
            }
        )


@dataclass(frozen=True, slots=True)
class TokenEntity:
    """One access token: seeded, exchanged, refreshed, or personal.

    ``kind`` separates the three the vendor names -- ``oauth`` for a token from
    the code or refresh grant, ``personal`` for one an admin created in the web
    application (Plus plan only), and it is the only difference a route sees.
    ``revoked_at_ms`` is set when a refresh call retires the token that was
    issued with the consumed refresh token: "Using a refresh token will revoke
    the access token that was returned with it."
    """

    id: str
    access_token: str
    client_id: str
    scopes: tuple[str, ...] = ()
    kind: str = "oauth"
    expires_at_ms: int | None = None
    revoked_at_ms: int | None = None
    refresh_token_id: str | None = None
    created_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TokenEntity:
        expires = entity.get("expires_at_ms")
        revoked = entity.get("revoked_at_ms")
        created = entity.get("created_at_ms")
        return cls(
            id=_str(entity["id"]),
            access_token=_str(entity.get("access_token")),
            client_id=_str(entity.get("client_id")),
            scopes=_str_tuple(entity.get("scopes")),
            kind=_str(entity.get("kind"), "oauth"),
            expires_at_ms=None if expires is None else _int(expires),
            revoked_at_ms=None if revoked is None else _int(revoked),
            refresh_token_id=_opt_str(entity.get("refresh_token_id")),
            created_at_ms=None if created is None else _int(created),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "access_token": self.access_token,
                "client_id": self.client_id,
                "scopes": list(self.scopes),
                "kind": self.kind,
                "expires_at_ms": self.expires_at_ms,
                "revoked_at_ms": self.revoked_at_ms,
                "refresh_token_id": self.refresh_token_id,
                "created_at_ms": self.created_at_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class AuthorizationCodeEntity:
    """One single-use authorization code from the ``GET /connect`` stand-in.

    Bound to the ``client_id``, the ``scope`` list and the ``redirect_uri`` the
    authorization request carried, because those three are what the token
    exchange has to check the code against. ``redirect_uri`` records what was
    SUPPLIED, not the resolved default: the exchange only has to match one when
    the authorization request actually named one.

    ``used_at_ms`` marks the code spent. Single use and a ten-minute expiry are
    both JUDGMENT figures carried from the roadmap#75 spike -- see
    ``config.py``.
    """

    id: str
    client_id: str
    scopes: tuple[str, ...]
    expires_at_ms: int
    redirect_uri: str | None = None
    state: str | None = None
    used_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> AuthorizationCodeEntity:
        used = entity.get("used_at_ms")
        return cls(
            id=_str(entity["id"]),
            client_id=_str(entity.get("client_id")),
            scopes=_str_tuple(entity.get("scopes")),
            expires_at_ms=_int(entity.get("expires_at_ms")),
            redirect_uri=_opt_str(entity.get("redirect_uri")),
            state=_opt_str(entity.get("state")),
            used_at_ms=None if used is None else _int(used),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "client_id": self.client_id,
                "scopes": list(self.scopes),
                "expires_at_ms": self.expires_at_ms,
                "redirect_uri": self.redirect_uri,
                "state": self.state,
                "used_at_ms": self.used_at_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class RefreshTokenEntity:
    """One refresh token.

    ``access_token_id`` is the access token that was issued WITH this refresh
    token, because refreshing revokes it: "Using a refresh token will revoke
    the access token that was returned with it"
    (https://x-series-api.lightspeedhq.com/docs/authorization).

    ``retired_at_ms`` marks the token consumed. A consumed refresh token
    presented again is refused -- "You must save this new refresh token and use
    it the next time" documents the rotation; the STATUS a reuse then gets is
    JUDGMENT (``errors.py``).

    There is no expiry field: the authorization page states no refresh-token
    lifetime and this pass found none, so a refresh token here is retired only
    by use. Recorded as a documentation gap in ``capabilities.py``.
    """

    id: str
    refresh_token: str
    client_id: str
    scopes: tuple[str, ...] = ()
    access_token_id: str | None = None
    retired_at_ms: int | None = None
    created_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> RefreshTokenEntity:
        retired = entity.get("retired_at_ms")
        created = entity.get("created_at_ms")
        return cls(
            id=_str(entity["id"]),
            refresh_token=_str(entity.get("refresh_token")),
            client_id=_str(entity.get("client_id")),
            scopes=_str_tuple(entity.get("scopes")),
            access_token_id=_opt_str(entity.get("access_token_id")),
            retired_at_ms=None if retired is None else _int(retired),
            created_at_ms=None if created is None else _int(created),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "scopes": list(self.scopes),
                "access_token_id": self.access_token_id,
                "retired_at_ms": self.retired_at_ms,
                "created_at_ms": self.created_at_ms,
            }
        )


# ---------------------------------------------------------------------------
# SALES (slice L2b of konyklabs/roadmap#94). Added as one block so that the
# sibling slice's products/inventory/customers entities merge beside it rather
# than through it.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SaleEntity:
    """One sale, in the documented ``Sale`` shape, with money in minor units.

    WHY THE NESTED ROWS ARE PLAIN DICTS. ``line_items``, ``payments``,
    ``source`` and ``return`` are stored as the dicts ``model/sale.py`` builds,
    the same way :class:`RegisterClosureEntity` stores its ``payments``: they
    are computed wholesale by the surface on every write (a sale's totals are a
    function of its line items, so there is no partial update of one), and a
    second dataclass layer per row would be a shape to keep in step with the
    projection for no reader's benefit.

    MONEY IS MINOR UNITS IN THE STORE and JSON **numbers** on the wire, which
    is the opposite of the register close totals. Both are the vendor's:
    ``RegisterClosePaymentType.total`` is typed ``string``, while every money
    member of a sale (``SaleTotals.price``, ``LineItemPricing.price``,
    ``SalePayment.amount``, ``LineItemTax.amount``) is typed ``number`` with
    ``format: double``. ``model/money.py`` owns both conversions.

    ``state`` is the four-value enum ``machine.py`` enforces. ``deleted_at`` is
    carried because ``Sale`` declares it and because the shared list filter in
    ``versioning.py`` reads it, not because any route in this slice sets one:
    the Sales tag has no delete operation.
    """

    id: str
    state: str
    source: dict[str, Any] = field(default_factory=dict)
    line_items: list[dict[str, Any]] = field(default_factory=list)
    payments: list[dict[str, Any]] = field(default_factory=list)
    attributes: tuple[str, ...] = ()
    customer_id: str | None = None
    note: str | None = None
    short_code: str | None = None
    invoice_number: str | None = None
    receipt_number: str | None = None
    accounts_transaction_id: str | None = None
    #: RFC 3339. ``SaleRequestBase.date`` -- "If not provided will be added as
    #: the time the sale reached the server". This one is the VENDOR's: it is
    #: an editable member of the request body.
    date: str = ""
    #: ``created_at`` and ``updated_at`` are the CORE STORE's, not this
    #: package's, and that is why they are empty by default here.
    #: ``Collection.insert`` stamps both unless the caller supplies them, and
    #: ``Collection.update`` restores ``created_at`` and rewrites
    #: ``updated_at`` after every mutator -- so a vendor that also wrote them
    #: would have its value silently replaced on the first update, leaving a
    #: sale whose two timestamps were spelled to different precisions. The seed
    #: DOES supply both (an insert honours them, which is how a scenario states
    #: that a sale happened last Tuesday); a live create supplies neither.
    created_at: str = ""
    updated_at: str = ""
    deleted_at: str | None = None
    #: ``SaleReturn``: whether this sale IS a return, the sale it returns, and
    #: the returns made from it.
    is_return: bool = False
    original_sale_id: str | None = None
    return_sale_ids: tuple[str, ...] = ()
    object_version: int = 0

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> SaleEntity:
        returns = _mapping(entity.get("return"))
        return cls(
            id=_str(entity["id"]),
            state=_str(entity.get("state")),
            source=_mapping(entity.get("source")),
            line_items=_rows(entity.get("line_items")),
            payments=_rows(entity.get("payments")),
            attributes=_str_tuple(entity.get("attributes")),
            customer_id=_opt_str(entity.get("customer_id")),
            note=_opt_str(entity.get("note")),
            short_code=_opt_str(entity.get("short_code")),
            invoice_number=_opt_str(entity.get("invoice_number")),
            receipt_number=_opt_str(entity.get("receipt_number")),
            accounts_transaction_id=_opt_str(entity.get("accounts_transaction_id")),
            date=_str(entity.get("date")),
            created_at=_str(entity.get("created_at")),
            updated_at=_str(entity.get("updated_at")),
            deleted_at=_opt_str(entity.get("deleted_at")),
            is_return=_bool(returns.get("is_return")),
            original_sale_id=_opt_str(returns.get("original_sale_id")),
            return_sale_ids=_str_tuple(returns.get("return_sale_ids")),
            object_version=_int(entity.get(OBJECT_VERSION)),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "state": self.state,
                "source": dict(self.source),
                "line_items": [dict(row) for row in self.line_items],
                "payments": [dict(row) for row in self.payments],
                "attributes": list(self.attributes),
                "customer_id": self.customer_id,
                "note": self.note,
                "short_code": self.short_code,
                "invoice_number": self.invoice_number,
                "receipt_number": self.receipt_number,
                "accounts_transaction_id": self.accounts_transaction_id,
                "date": self.date,
                # Empty means "the store owns this one"; see the field comment.
                "created_at": self.created_at or None,
                "updated_at": self.updated_at or None,
                "deleted_at": self.deleted_at,
                # Compacted in its own right: `compact` is shallow by design,
                # so a nested projection compacts itself or the "absence is
                # absence" invariant stops at the first level.
                "return": compact(
                    {
                        "is_return": self.is_return,
                        "original_sale_id": self.original_sale_id,
                        "return_sale_ids": list(self.return_sale_ids),
                    }
                ),
                OBJECT_VERSION: self.object_version,
            }
        )
