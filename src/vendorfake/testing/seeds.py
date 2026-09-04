"""What the shipped scenarios contain, as one object per vendor. Every value is
re-exported from the vendor's own constants module, and the application
credentials come from the profile's ``vendor`` block. All seeds share one
structural type, :class:`Seed`, and one neutral view of the credentials."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Guarded: an unguarded import would pull the kernel into every conftest.
    from vendorfake.core.kernel.types import VendorDefinition

__all__ = [
    "SEED_COLLECTIONS_ATTR",
    "CloverSeed",
    "CloverSeedOverlay",
    "Credentials",
    "LightspeedSeed",
    "LightspeedSeedOverlay",
    "Seed",
    "SeedOverlay",
    "SquareSeed",
    "SquareSeedOverlay",
    "ToastSeed",
    "ToastSeedOverlay",
    "Token",
    "seed_collections_for",
    "seed_for",
]


@dataclass(frozen=True, slots=True)
class Credentials:
    """The application credential, under names that mean the same on every vendor.
    JUDGMENT: the names are invented, so a parametrized test does not branch."""

    app_id: str
    app_secret: str
    grant: Literal["refresh_token", "client_credentials"]
    """Which token lifecycle the vendor runs: ``refresh_token`` rotates a grant,
    ``client_credentials`` logs in again on expiry. JUDGMENT for the spelling; the
    lifecycle is DOCUMENTED at each seed's :attr:`~SquareSeed.credentials`."""


@dataclass(frozen=True, slots=True)
class Token:
    """The seeded credential a consumer stores per tenant. JUDGMENT: invented names.
    ``refresh_token`` is ``None`` exactly for a ``client_credentials`` grant."""

    access_token: str
    refresh_token: str | None
    tenant_id: str


class Seed(Protocol):
    """What every vendor's seed has, and the fallback type for a plain ``str``
    vendor. No bare ``refresh_token``, which Toast has not; :attr:`token` has it."""

    @property
    def credentials(self) -> Credentials: ...

    @property
    def token(self) -> Token: ...

    @property
    def auth(self) -> Mapping[str, str]: ...

    @property
    def read_only_auth(self) -> Mapping[str, str]: ...

    @property
    def event_types(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class SquareSeed:
    """The Square scenario: two tokens, a merchant, two locations, a catalog, two
    orders, a loyalty program. Ids are those of Square's documentation examples."""

    application_id: str
    application_secret: str
    redirect_uri: str
    #: Full scopes; the read-only one answers 403 to every write path.
    access_token: str
    refresh_token: str
    read_only_access_token: str
    merchant_id: str
    location_id: str
    kiosk_location_id: str
    tea_item_id: str
    tea_mug_variation_id: str
    tea_pot_variation_id: str
    cold_brew_item_id: str
    cold_brew_small_variation_id: str
    cold_brew_large_variation_id: str
    open_order_id: str
    completed_order_id: str
    loyalty_program_id: str
    loyalty_account_phone: str
    #: Every event type the unit can send, as the event-types route lists them.
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """DOCUMENTED, the ``grant``: Square issues a refresh token and a consumer
        rotates it
        (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope)."""
        return Credentials(app_id=self.application_id, app_secret=self.application_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-scope token; the tenant is the seller's ``merchant_id``."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.merchant_id)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}


@dataclass(frozen=True, slots=True)
class CloverSeed:
    """The Clover scenario: one merchant, three items, a modifier group, two
    employees, two tenders, two order types, the default service charge, a
    customer, one open order, two tokens and one disabled webhook subscriber."""

    client_id: str
    client_secret: str
    redirect_uri: str
    #: Every permission the app declares; the read-only one answers 401 to a write.
    access_token: str
    refresh_token: str
    read_only_access_token: str
    merchant_id: str
    item_beer_id: str
    item_espresso_id: str
    item_croissant_id: str
    modifier_group_milk_id: str
    modifier_oat_id: str
    modifier_soy_id: str
    order_type_dine_in_id: str
    order_type_take_out_id: str
    tender_cash_id: str
    tender_external_id: str
    employee_owner_id: str
    employee_barista_id: str
    service_charge_id: str
    tax_default_id: str
    customer_id: str
    open_order_id: str
    webhook_subscription_id: str
    webhook_auth_code: str
    #: ``<object key>:<change>``; a subscription may name a glob such as ``O:*``.
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """DOCUMENTED, the ``grant``: Clover's expiring-token apps rotate a
        single-use refresh token
        (https://docs.clover.com/dev/docs/refresh-access-tokens)."""
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-permission token; the tenant is the merchant."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.merchant_id)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}

    def path(self, suffix: str = "") -> str:
        return f"/v3/merchants/{self.merchant_id}{suffix}"


@dataclass(frozen=True, slots=True)
class ToastSeed:
    """The Toast scenario: one restaurant, a menu of three items, dining options,
    an open order, two tokens and a webhook subscription. Scoping is by header, so
    :attr:`auth` carries the bearer and the header together."""

    client_id: str
    client_secret: str
    partner_guid: str
    #: Full scopes, then reads only.
    access_token: str
    read_only_access_token: str
    restaurant_guid: str
    restaurant_name: str
    management_group_guid: str
    menu_guid: str
    item_soup_guid: str
    item_soup_price_cents: int
    item_burger_guid: str
    item_lemonade_guid: str
    modifier_group_sides_guid: str
    modifier_option_fries_guid: str
    dining_option_dine_in_guid: str
    dining_option_take_out_guid: str
    alt_payment_external_guid: str
    tax_rate_guid: str
    open_order_guid: str
    open_order_check_guid: str
    webhook_subscription_id: str
    #: The HMAC secret behind ``Toast-Signature``, and the scoping header's name.
    webhook_secret: str
    restaurant_header_name: str
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """DOCUMENTED, the ``grant``: Toast answers a bearer token with no refresh
        (https://doc.toasttab.com/doc/devguide/authentication.html)."""
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="client_credentials")

    @property
    def token(self) -> Token:
        """The seeded full-scope token; no refresh, and the tenant is the restaurant."""
        return Token(access_token=self.access_token, refresh_token=None, tenant_id=self.restaurant_guid)

    @property
    def restaurant_header(self) -> dict[str, str]:
        return {self.restaurant_header_name: self.restaurant_guid}

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}", **self.restaurant_header}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}", **self.restaurant_header}

    @property
    def bearer_only(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class LightspeedSeed:
    """The Lightspeed scenario: one retailer, two outlets and registers, three
    payment types, six products, one customer group and three customers, three
    sales, an OAuth pair, a read-only and a personal token, and one webhook.
    Scoping is by subdomain, so :attr:`api_path` prefixes the version segment."""

    client_id: str
    client_secret: str
    redirect_uri: str
    domain_prefix: str
    #: Full scopes; the refresh rotates the pair and revokes the access token it
    #: came with, the read-only one answers 403 to a write, and the personal token
    #: has full scopes and no expiry.
    access_token: str
    refresh_token: str
    read_only_access_token: str
    personal_access_token: str
    retailer_id: str
    retailer_name: str
    outlet_main_id: str
    outlet_second_id: str
    #: Seeded OPEN and CLOSED respectively, so neither action needs setup.
    register_main_id: str
    register_second_id: str
    payment_type_cash_id: str
    payment_type_card_id: str
    #: ``internal: true``, so absent from the payment-types list.
    payment_type_internal_id: str
    #: Two standalone products holding stock at both outlets, and a third seeded
    #: INACTIVE so ``include_inactive`` has something to include.
    product_trail_mix_id: str
    product_trail_mix_sku: str
    product_socks_id: str
    product_bottle_id: str
    product_bottle_sku: str
    #: A parent with ``has_variants`` and its two variants.
    product_tee_id: str
    product_tee_small_id: str
    product_tee_large_id: str
    #: The retailer's one customer group, then three customers: filled in
    #: completely, a company only, and one whose ``last_name`` is null.
    customer_group_id: str
    customer_ada_id: str
    customer_blake_id: str
    customer_noor_id: str
    #: The two reasons a ``CUSTOM`` stock adjustment may name, one of each sign.
    adjustment_reason_found_id: str
    adjustment_reason_spoiled_id: str
    #: The cashier every seeded sale names as ``source.author_id``.
    cashier_user_id: str
    #: Both outlets' ``default_tax_id`` and every line item's ``tax.id``.
    tax_id: str
    #: Parked and still editable; closed and terminal, so a ``PUT`` is a 409; and
    #: a layby, parked with the ``layby`` attribute and a part payment.
    sale_saved_id: str
    sale_closed_id: str
    sale_layby_id: str
    webhook_subscription_id: str
    #: The HMAC secret behind ``X-Signature``: the app's own ``client_secret``.
    webhook_secret: str
    #: ``/api/2026-07`` -- the version segment every resource route sits under.
    api_prefix: str
    #: The seven ``WebhookType`` values; the consignment pair is never fired.
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """DOCUMENTED, the ``grant``: a refresh revokes the access token it came
        with (https://x-series-api.lightspeedhq.com/docs/authorization)."""
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-scope pair; the tenant is the retailer."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.retailer_id)

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}

    @property
    def personal_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.personal_access_token}"}

    def api_path(self, suffix: str = "") -> str:
        return f"{self.api_prefix}{suffix}"


# Seed overlays: one ``TypedDict(total=False)`` per vendor, keyed by its seed
# collections, so a checker rejects a key that is not one. Values are ``object``.


class SquareSeedOverlay(TypedDict, total=False):
    """The collections ``vendorfake/square/seed/default.seed.json`` carries."""

    merchant: object
    locations: object
    catalog: object
    orders: object
    loyalty_program: object
    loyalty_accounts: object
    inventory_counts: object
    tokens: object


class CloverSeedOverlay(TypedDict, total=False):
    """The collections ``vendorfake/clover/seed/default.seed.json`` carries."""

    merchant: object
    tax_rates: object
    modifier_groups: object
    modifiers: object
    items: object
    employees: object
    tenders: object
    order_types: object
    service_charges: object
    customers: object
    orders: object
    tokens: object
    webhook_subscriptions: object


class ToastSeedOverlay(TypedDict, total=False):
    """The collections ``vendorfake/toast/seed/default.seed.json`` carries."""

    restaurant: object
    partner: object
    tokens: object
    config_modified_ms: object
    dining_options: object
    alternate_payment_types: object
    tax_rates: object
    revenue_centers: object
    service_areas: object
    tables: object
    restaurant_services: object
    discounts: object
    service_charges: object
    void_reasons: object
    menu_v3: object
    orders: object
    credit_authorizations: object
    stock: object
    webhook_subscriptions: object


class LightspeedSeedOverlay(TypedDict, total=False):
    """The collections ``vendorfake/lightspeed/seed/default.seed.json`` carries."""

    retailer: object
    outlets: object
    registers: object
    payment_types: object
    products: object
    inventory: object
    adjustment_reasons: object
    stock_adjustments: object
    customer_groups: object
    customers: object
    tokens: object
    personal_tokens: object
    refresh_tokens: object
    webhooks: object
    sales: object


SeedOverlay = Mapping[str, Any]
"""What a plain ``str`` vendor accepts: any JSON object, the collections being a
property of a vendor this call site does not know."""


def _square(vendor_config: Mapping[str, object]) -> SquareSeed:
    from vendorfake.square.config import SquareConfig
    from vendorfake.square.events import SQUARE_EVENT_TYPES
    from vendorfake.square.seed import constants as c

    config = SquareConfig.model_validate(dict(vendor_config))
    return SquareSeed(
        application_id=config.application_id,
        application_secret=config.application_secret,
        redirect_uri=config.redirect_uri,
        access_token=c.SEED_ACCESS_TOKEN,
        refresh_token=c.SEED_REFRESH_TOKEN,
        read_only_access_token=c.SEED_READ_ONLY_ACCESS_TOKEN,
        merchant_id=c.SEED_MERCHANT_ID,
        location_id=c.SEED_LOCATION_ID,
        kiosk_location_id=c.SEED_KIOSK_LOCATION_ID,
        tea_item_id=c.TEA_ITEM_ID,
        tea_mug_variation_id=c.TEA_MUG_VARIATION_ID,
        tea_pot_variation_id=c.TEA_POT_VARIATION_ID,
        cold_brew_item_id=c.COLD_BREW_ITEM_ID,
        cold_brew_small_variation_id=c.COLD_BREW_SMALL_VARIATION_ID,
        cold_brew_large_variation_id=c.COLD_BREW_LARGE_VARIATION_ID,
        open_order_id=c.SEED_OPEN_ORDER_ID,
        completed_order_id=c.SEED_COMPLETED_ORDER_ID,
        loyalty_program_id=c.SEED_LOYALTY_PROGRAM_ID,
        loyalty_account_phone=c.SEED_LOYALTY_ACCOUNT_PHONE,
        event_types=tuple(SQUARE_EVENT_TYPES),
    )


def _clover(vendor_config: Mapping[str, object]) -> CloverSeed:
    from vendorfake.clover import events
    from vendorfake.clover.config import CloverConfig
    from vendorfake.clover.seed import constants as c

    config = CloverConfig.model_validate(dict(vendor_config))
    keys = (events.KEY_ORDERS, events.KEY_INVENTORY, events.KEY_CUSTOMERS, events.KEY_PAYMENTS)
    event_types = tuple(events.event_type(key, change) for key in keys for change in events.CHANGE_TYPES)
    return CloverSeed(
        client_id=config.client_id,
        client_secret=config.client_secret,
        redirect_uri=config.redirect_uri,
        access_token=c.SEED_ACCESS_TOKEN,
        refresh_token=c.SEED_REFRESH_TOKEN,
        read_only_access_token=c.SEED_READ_ONLY_ACCESS_TOKEN,
        merchant_id=c.SEED_MERCHANT_ID,
        item_beer_id=c.ITEM_BEER_ID,
        item_espresso_id=c.ITEM_ESPRESSO_ID,
        item_croissant_id=c.ITEM_CROISSANT_ID,
        modifier_group_milk_id=c.MODIFIER_GROUP_MILK_ID,
        modifier_oat_id=c.MODIFIER_OAT_ID,
        modifier_soy_id=c.MODIFIER_SOY_ID,
        order_type_dine_in_id=c.ORDER_TYPE_DINE_IN_ID,
        order_type_take_out_id=c.ORDER_TYPE_TAKE_OUT_ID,
        tender_cash_id=c.TENDER_CASH_ID,
        tender_external_id=c.TENDER_EXTERNAL_ID,
        employee_owner_id=c.EMPLOYEE_OWNER_ID,
        employee_barista_id=c.EMPLOYEE_BARISTA_ID,
        service_charge_id=c.SERVICE_CHARGE_DEFAULT_ID,
        tax_default_id=c.TAX_DEFAULT_ID,
        customer_id=c.CUSTOMER_ADA_ID,
        open_order_id=c.SEED_OPEN_ORDER_ID,
        webhook_subscription_id=c.SEED_WEBHOOK_SUBSCRIPTION_ID,
        webhook_auth_code=c.SEED_WEBHOOK_AUTH_CODE,
        event_types=event_types,
    )


def _toast(vendor_config: Mapping[str, object]) -> ToastSeed:
    from vendorfake.toast.config import ToastConfig
    from vendorfake.toast.events import TOAST_EVENT_TYPES
    from vendorfake.toast.seed import constants as c
    from vendorfake.toast.surface.common import RESTAURANT_HEADER

    config = ToastConfig.model_validate(dict(vendor_config))
    return ToastSeed(
        client_id=config.client_id,
        client_secret=config.client_secret,
        partner_guid=config.partner_guid,
        access_token=c.SEED_ACCESS_TOKEN,
        read_only_access_token=c.SEED_READ_ONLY_ACCESS_TOKEN,
        restaurant_guid=c.SEED_RESTAURANT_GUID,
        restaurant_name=c.SEED_RESTAURANT_NAME,
        management_group_guid=c.SEED_MANAGEMENT_GROUP_GUID,
        menu_guid=c.MENU_GUID,
        item_soup_guid=c.ITEM_SOUP_GUID,
        item_soup_price_cents=c.ITEM_SOUP_PRICE_CENTS,
        item_burger_guid=c.ITEM_BURGER_GUID,
        item_lemonade_guid=c.ITEM_LEMONADE_GUID,
        modifier_group_sides_guid=c.MODIFIER_GROUP_SIDES_GUID,
        modifier_option_fries_guid=c.MODIFIER_OPTION_FRIES_GUID,
        dining_option_dine_in_guid=c.DINING_OPTION_DINE_IN_GUID,
        dining_option_take_out_guid=c.DINING_OPTION_TAKE_OUT_GUID,
        alt_payment_external_guid=c.ALT_PAYMENT_EXTERNAL_GUID,
        tax_rate_guid=c.TAX_RATE_DEFAULT_GUID,
        open_order_guid=c.SEED_ORDER_GUID,
        open_order_check_guid=c.SEED_ORDER_CHECK_GUID,
        webhook_subscription_id=c.SEED_WEBHOOK_SUBSCRIPTION_ID,
        webhook_secret=c.SEED_WEBHOOK_SECRET,
        restaurant_header_name=RESTAURANT_HEADER,
        event_types=tuple(TOAST_EVENT_TYPES),
    )


def _lightspeed(vendor_config: Mapping[str, object]) -> LightspeedSeed:
    from vendorfake.lightspeed.config import LightspeedConfig
    from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES
    from vendorfake.lightspeed.seed import constants as c
    from vendorfake.lightspeed.surface.common import API_PREFIX

    config = LightspeedConfig.model_validate(dict(vendor_config))
    return LightspeedSeed(
        client_id=config.client_id,
        client_secret=config.client_secret,
        redirect_uri=config.redirect_uri,
        domain_prefix=config.domain_prefix,
        access_token=c.SEED_ACCESS_TOKEN,
        refresh_token=c.SEED_REFRESH_TOKEN,
        read_only_access_token=c.SEED_READ_ONLY_ACCESS_TOKEN,
        personal_access_token=c.SEED_PERSONAL_ACCESS_TOKEN,
        retailer_id=c.SEED_RETAILER_ID,
        retailer_name=c.SEED_RETAILER_NAME,
        outlet_main_id=c.SEED_OUTLET_MAIN_ID,
        outlet_second_id=c.SEED_OUTLET_SECOND_ID,
        register_main_id=c.SEED_REGISTER_MAIN_ID,
        register_second_id=c.SEED_REGISTER_SECOND_ID,
        payment_type_cash_id=c.SEED_PAYMENT_TYPE_CASH_ID,
        payment_type_card_id=c.SEED_PAYMENT_TYPE_CARD_ID,
        payment_type_internal_id=c.SEED_PAYMENT_TYPE_INTERNAL_ID,
        product_trail_mix_id=c.SEED_PRODUCT_TRAIL_MIX_ID,
        product_trail_mix_sku=c.SEED_PRODUCT_TRAIL_MIX_SKU,
        product_socks_id=c.SEED_PRODUCT_SOCKS_ID,
        product_bottle_id=c.SEED_PRODUCT_BOTTLE_ID,
        product_bottle_sku=c.SEED_PRODUCT_BOTTLE_SKU,
        product_tee_id=c.SEED_PRODUCT_TEE_ID,
        product_tee_small_id=c.SEED_PRODUCT_TEE_SMALL_ID,
        product_tee_large_id=c.SEED_PRODUCT_TEE_LARGE_ID,
        customer_group_id=c.SEED_CUSTOMER_GROUP_ID,
        customer_ada_id=c.SEED_CUSTOMER_ADA_ID,
        customer_blake_id=c.SEED_CUSTOMER_BLAKE_ID,
        customer_noor_id=c.SEED_CUSTOMER_NOOR_ID,
        adjustment_reason_found_id=c.SEED_ADJUSTMENT_REASON_FOUND_ID,
        adjustment_reason_spoiled_id=c.SEED_ADJUSTMENT_REASON_SPOILED_ID,
        cashier_user_id=c.SEED_USER_ID,
        tax_id=c.SEED_TAX_ID,
        sale_saved_id=c.SEED_SALE_SAVED_ID,
        sale_closed_id=c.SEED_SALE_CLOSED_ID,
        sale_layby_id=c.SEED_SALE_LAYBY_ID,
        webhook_subscription_id=c.SEED_WEBHOOK_ID,
        # Lightspeed signs with the application's own secret.
        webhook_secret=config.client_secret,
        api_prefix=API_PREFIX,
        event_types=tuple(LIGHTSPEED_EVENT_TYPES),
    )


@dataclass(frozen=True, slots=True)
class _BuiltInSeed:
    """One shipped vendor's seed: :attr:`build` reads module constants, and
    :attr:`collections` names what they are the values of. Kept together."""

    build: Callable[[Mapping[str, object]], Seed]
    collections: frozenset[str]
    """The seed collections this seed object speaks for: its credentials and its
    identity, the two whose divergence from an overlay would be silent."""


_BUILT_IN_SEEDS: Mapping[str, _BuiltInSeed] = {
    "square": _BuiltInSeed(build=_square, collections=frozenset({"tokens", "merchant"})),
    "clover": _BuiltInSeed(build=_clover, collections=frozenset({"tokens", "merchant"})),
    "toast": _BuiltInSeed(build=_toast, collections=frozenset({"tokens", "restaurant"})),
    # Three credential collections plus ``retailer``: all four are what
    # `_lightspeed` reads its constants for.
    "lightspeed": _BuiltInSeed(
        build=_lightspeed,
        collections=frozenset({"tokens", "personal_tokens", "refresh_tokens", "retailer"}),
    ),
}
"""The four vendors this distribution ships, as data. An entry-point vendor is
not here: it publishes its own via :data:`SEED_COLLECTIONS_ATTR`."""

SEED_COLLECTIONS_ATTR = "seed_collections"
"""The optional attribute a ``SeedingVendor`` declares its seed's collections in;
silence means "this vendor has not said". Read with :func:`getattr`, a new
protocol member dropping every existing seed."""


_SEED_MEMBERS = ("credentials", "token", "auth", "read_only_auth", "event_types")
"""The five names :class:`Seed` requires, for the hook's shape check. Written out
because ``Seed.__protocol_attrs__`` has no compatibility promise."""


def _from_hook(definition: VendorDefinition, vendor: str, vendor_config: Mapping[str, object]) -> Seed | None:
    """The vendor's own seed, if it publishes one, checked before it escapes: the
    hook returns ``object``, so a wrong shape is named here as a hook defect."""
    from vendorfake.core.kernel.types import SeedingVendor

    if not isinstance(definition, SeedingVendor):
        return None
    hook = definition.seed
    if not callable(hook):
        # `runtime_checkable` checks attribute presence, not callability, and
        # `seed` is also this package's name for seed data -- a real collision.
        raise TypeError(
            f"vendor {vendor!r} has a SeedingVendor.seed attribute that is not callable "
            f"(a {type(hook).__name__!r}). SeedingVendor.seed must be a method: "
            f"seed(self, vendor_config) -> object."
        )
    published = hook(vendor_config)
    missing = [name for name in _SEED_MEMBERS if not hasattr(published, name)]
    if missing:
        raise TypeError(
            f"vendor {vendor!r} published a seed of type {type(published).__name__!r} that is not a "
            f"vendorfake.testing.Seed: no {', '.join(missing)}. A seed must carry "
            f"{', '.join(_SEED_MEMBERS)}."
        )
    return cast("Seed", published)


def seed_for(
    vendor: str,
    vendor_config: Mapping[str, object],
    *,
    definition: VendorDefinition | None = None,
) -> Seed | None:
    """The seed object for ``vendor``, or ``None`` when it publishes none. The
    ``SeedingVendor`` hook is asked first, so it is never shadowed by a built-in."""
    if definition is None:
        definition = _resolve_quietly(vendor)
    if definition is not None:
        published = _from_hook(definition, vendor, vendor_config)
        if published is not None:
            return published
    built_in = _BUILT_IN_SEEDS.get(vendor)
    return None if built_in is None else built_in.build(vendor_config)


def seed_collections_for(
    vendor: str,
    *,
    definition: VendorDefinition | None = None,
) -> frozenset[str]:
    """Which seed collections ``vendor``'s seed object is built from, resolved as
    :func:`seed_for` is. Empty when no seed is published or none is declared."""
    if definition is None:
        definition = _resolve_quietly(vendor)
    if definition is not None:
        from vendorfake.core.kernel.types import SeedingVendor

        if isinstance(definition, SeedingVendor):
            declared = getattr(definition, SEED_COLLECTIONS_ATTR, None)
            if declared is None or isinstance(declared, str) or not isinstance(declared, Iterable):
                # A `str` is iterable, so it is rejected with the undeclared case.
                return frozenset()
            return frozenset(str(name) for name in declared)
    built_in = _BUILT_IN_SEEDS.get(vendor)
    return frozenset() if built_in is None else built_in.collections


def _resolve_quietly(vendor: str) -> VendorDefinition | None:
    """``vendor``'s definition, or ``None`` if there is no such vendor. The import
    is deferred because the registry pulls the kernel in behind it."""
    from vendorfake.registry import resolve_vendor

    try:
        return resolve_vendor(vendor)
    except ValueError:
        return None
