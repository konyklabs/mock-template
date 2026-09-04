"""What the shipped scenarios contain, as one object per vendor.

FOR: a consumer's fixture. ``started.seed.access_token`` reads better than an
import from ``vendorfake.square.seed.constants``, and a consumer who does not
know the package layout should not have to. Every value here is re-exported
from the vendor's own constants module, which is pinned to the seed document
by that vendor's tests -- nothing is typed twice.

The application credentials (``application_id`` / ``client_id`` and their
secrets) come from the profile's ``vendor`` block rather than from a constant:
a profile may override them, and a fixture that reported the default while the
unit ran on an override would send a consumer chasing a 401 that the fixture
caused.

The three seeds also share one structural type, :class:`Seed`, and one neutral
view of their application credentials, :class:`Credentials`. That exists
because the vendor-faithful spellings are the whole difficulty for a consumer
who parametrizes over vendors: ``application_id`` on Square and ``client_id``
on Clover and Toast name the same thing, and a single test body cannot spell
both. Nothing here renames or removes a vendor-faithful field; the neutral
names are a second view of the same strings.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict, cast

if TYPE_CHECKING:  # pragma: no cover - typing only
    # Guarded because the runtime import belongs inside the two functions that
    # need it: this module is reached through `vendorfake.testing`, which a
    # consumer's conftest imports, and an unguarded import here would pull the
    # registry and the whole kernel in behind it for every such session.
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
    """The application credential a consumer authenticates with, under names
    that mean the same thing on every vendor.

    JUDGMENT: the names are invented. No vendor calls it ``app_id``; Square
    calls it ``application_id`` and Clover and Toast call it ``client_id``.
    The point is a call site that does not have to know which -- a test
    parametrized over three vendors reads ``seed.credentials.app_id`` once
    instead of branching on the vendor to pick a spelling.
    """

    app_id: str
    app_secret: str
    grant: Literal["refresh_token", "client_credentials"]
    """Which token lifecycle the vendor runs.

    This is the difference a consumer's session handling genuinely has to
    care about, so it is on the neutral view rather than hidden behind a
    field that only two of the three seeds carry: ``refresh_token`` means a
    long-lived grant is rotated (there is a ``refresh_token`` on the seed),
    ``client_credentials`` means there is no refresh and the client logs in
    again when the token expires.

    JUDGMENT for the *spelling*: ``client_credentials`` is OAuth's word for
    the shape, and Toast's login is not literally an OAuth grant. The
    lifecycle it names is DOCUMENTED per vendor at each seed's
    :attr:`~SquareSeed.credentials`.
    """


@dataclass(frozen=True, slots=True)
class Token:
    """The seeded credential a consumer *stores* per tenant, under names that
    mean the same thing on every vendor -- the other half of
    :class:`Credentials`, which is what the application authenticates *as*.

    JUDGMENT: the names are invented, for the same reason ``app_id`` is. A
    consumer's stored-credential row is an access token, maybe a refresh
    token, and the vendor's own id for the tenant the token is scoped to;
    each vendor spells the third one differently, and a test parametrized
    over vendors wants to read all three without branching.

    ``tenant_id`` is the id the *token* is scoped to, which is not always the
    narrowest id a vendor has: Clover's ``merchant_id``, Toast's
    ``restaurant_guid``, and Square's ``merchant_id`` rather than its
    ``location_id`` -- a Square OAuth token belongs to a seller (the
    ``merchant_id`` the token response itself carries) and a seller has
    several locations, so the location is a parameter of a call, not of the
    credential. ``refresh_token`` is ``None`` exactly when
    :attr:`Credentials.grant` is ``client_credentials``: the two agree by
    construction, and a consumer may branch on either.
    """

    access_token: str
    refresh_token: str | None
    tenant_id: str


class Seed(Protocol):
    """What every vendor's seed has, whichever vendor it is.

    FOR: a consumer parametrized over vendors, and for
    :class:`~vendorfake.testing.StartedUnit`'s fallback type when the vendor
    is a plain ``str`` rather than a literal. Reading a field through this
    protocol needs no ``isinstance`` and no per-vendor helper.

    Deliberately small, and deliberately without a bare ``refresh_token``
    field: Square and Clover have one and Toast does not, so putting it here
    would either lie about Toast or force a fake value onto it. The seeded
    token is reached through :attr:`token` instead, whose ``refresh_token``
    is honestly ``str | None``; a consumer that needs the refresh branch
    reads :attr:`Credentials.grant`, which is the real vendor difference
    rather than an artefact of this package.
    """

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
    """The Square scenario: two tokens, a merchant, two locations, a catalog,
    two orders, a loyalty program. Ids are the ones Square's own documentation
    examples use, so they look familiar in a consumer's assertions."""

    application_id: str
    application_secret: str
    redirect_uri: str
    #: Full scopes, including webhook subscriptions.
    access_token: str
    refresh_token: str
    #: Reads only; every write path answers 403 to it.
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
    #: Every event type the unit can send, as ``GET /v2/webhooks/event-types``
    #: lists them: ``order.created``, ``order.updated``, ``payment.*`` ...
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """The application credential, under the neutral names.

        DOCUMENTED, the ``grant``: Square issues a refresh token alongside
        the access token and a consumer rotates it -- "call ObtainToken with
        the refresh token" -- rather than re-authorizing
        (https://developer.squareup.com/docs/oauth-api/refresh-revoke-limit-scope).
        """
        return Credentials(app_id=self.application_id, app_secret=self.application_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-scope token, under the neutral names. The tenant is
        the seller -- ``merchant_id``, as the token response spells it -- not
        a location; see :class:`Token`."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.merchant_id)

    @property
    def auth(self) -> dict[str, str]:
        """``Authorization: Bearer`` for the full-scope token."""
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}


@dataclass(frozen=True, slots=True)
class CloverSeed:
    """The Clover scenario: one merchant, three items, a modifier group, two
    employees, two tenders, two order types, the default service charge, a
    customer, one open order, two tokens and one (disabled) webhook subscriber."""

    client_id: str
    client_secret: str
    redirect_uri: str
    #: Every permission the app declares.
    access_token: str
    refresh_token: str
    #: Reads only; a write answers Clover's undifferentiated 401.
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
    #: Clover's vocabulary is ``<object key>:<change>`` -- ``O:CREATE``,
    #: ``P:UPDATE`` ... -- and a subscription may name a glob such as ``O:*``.
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """The application credential, under the neutral names.

        DOCUMENTED, the ``grant``: Clover's expiring-token apps rotate a
        single-use refresh token -- "refresh token is for single use and
        becomes invalid immediately after a new access_token and
        refresh_token pair is generated"
        (https://docs.clover.com/dev/docs/refresh-access-tokens).
        """
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-permission token, under the neutral names; the
        tenant is the merchant."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.merchant_id)

    @property
    def auth(self) -> dict[str, str]:
        """``Authorization: Bearer`` for the full-permission token."""
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}

    def path(self, suffix: str = "") -> str:
        """``/v3/merchants/{merchant_id}`` plus ``suffix``: every Clover
        resource lives under the merchant."""
        return f"/v3/merchants/{self.merchant_id}{suffix}"


@dataclass(frozen=True, slots=True)
class ToastSeed:
    """The Toast scenario: one restaurant, a menu with three items, dining
    options, a seeded open order with one check, two tokens and a webhook
    subscription. Toast scopes a request with a **header**, not a path
    segment, so there is no ``path()`` helper -- use :attr:`auth`, which
    carries the bearer and the ``Toast-Restaurant-External-ID`` header
    together, the way every restaurant-scoped call needs them."""

    client_id: str
    client_secret: str
    partner_guid: str
    #: Full scopes.
    access_token: str
    #: Reads only.
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
    #: The HMAC secret behind the ``Toast-Signature`` header.
    webhook_secret: str
    #: ``Toast-Restaurant-External-ID``, spelled as the vendor spells it.
    restaurant_header_name: str
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """The application credential, under the neutral names.

        DOCUMENTED, the ``grant``: Toast's authentication endpoint takes the
        client id and secret and answers a bearer token, and there is no
        refresh -- a client logs in again when the token expires
        (https://doc.toasttab.com/doc/devguide/authentication.html). That is
        why :class:`ToastSeed` has no ``refresh_token`` field and why
        :class:`Seed` does not promise one.
        """
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="client_credentials")

    @property
    def token(self) -> Token:
        """The seeded full-scope token, under the neutral names. No refresh
        token -- ``None``, matching ``credentials.grant`` -- and the tenant is
        the restaurant."""
        return Token(access_token=self.access_token, refresh_token=None, tenant_id=self.restaurant_guid)

    @property
    def restaurant_header(self) -> dict[str, str]:
        """Just the scoping header, for pairing with a token you minted."""
        return {self.restaurant_header_name: self.restaurant_guid}

    @property
    def auth(self) -> dict[str, str]:
        """Bearer plus the restaurant header -- what a restaurant-scoped
        call sends."""
        return {"Authorization": f"Bearer {self.access_token}", **self.restaurant_header}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}", **self.restaurant_header}

    @property
    def bearer_only(self) -> dict[str, str]:
        """The token without the restaurant header, for the endpoints that
        are not restaurant-scoped (and for asserting the refusal on the
        ones that are)."""
        return {"Authorization": f"Bearer {self.access_token}"}


@dataclass(frozen=True, slots=True)
class LightspeedSeed:
    """The Lightspeed scenario: one retailer, two outlets, a register in each,
    three payment types (one of them internal), six products in four families
    with stock at both outlets, one customer group and three customers, three
    sales over that catalogue -- parked, closed with a payment, and a layby --
    a pre-issued OAuth access and refresh pair, a read-only token, a personal
    token, and one webhook subscription on ``register_closure.create``.

    Lightspeed scopes a request to its retailer by **subdomain** --
    ``{domain_prefix}.retail.lightspeed.app`` -- and a unit serves exactly one
    retailer, so there is no tenant header and no tenant path segment. What a
    consumer needs instead is :attr:`api_path`, which prefixes a resource path
    with the version segment every route sits under.
    """

    client_id: str
    client_secret: str
    redirect_uri: str
    domain_prefix: str
    #: Full scopes: reads, both register actions, and webhooks.
    access_token: str
    #: Rotates the pair above. A refresh retires it and revokes that access
    #: token, which is the documented behaviour a consumer's session handling
    #: has to get right.
    refresh_token: str
    #: Reads only; every write path answers 403 to it.
    read_only_access_token: str
    #: A personal token -- Plus-plan only, created in the web application, so a
    #: unit can only ever be seeded with one. Full scopes, and no expiry.
    personal_access_token: str
    retailer_id: str
    retailer_name: str
    outlet_main_id: str
    outlet_second_id: str
    #: Seeded OPEN, so a close (and the webhook it fires) needs no setup.
    register_main_id: str
    #: Seeded CLOSED, so an open needs none either.
    register_second_id: str
    payment_type_cash_id: str
    payment_type_card_id: str
    #: ``internal: true``: absent from the payment-types list, because the
    #: ``payment_types:read`` scope is documented as excluding internal types.
    payment_type_internal_id: str
    #: A standalone product, and the SKU it answers ``GET /products?sku=`` on.
    product_trail_mix_id: str
    product_trail_mix_sku: str
    #: The second standalone product. Both it and the trail mix hold stock at
    #: both outlets, which is what lets the seeded sales draw on real levels.
    product_socks_id: str
    #: Seeded INACTIVE, so ``include_inactive`` on the inventory-levels report
    #: has something to include.
    product_bottle_id: str
    product_bottle_sku: str
    #: The family: a parent with ``has_variants`` and no stock of its own, and
    #: its two variants, which each hold stock at both outlets. ``?name=``
    #: selects the whole family.
    product_tee_id: str
    product_tee_small_id: str
    product_tee_large_id: str
    #: The retailer's one customer group. There is no route to create another.
    customer_group_id: str
    #: Filled in completely: addresses, custom fields, a non-zero balance.
    customer_ada_id: str
    #: A company and nothing else.
    customer_blake_id: str
    #: ``last_name`` is null, which is legal: the member is required AND
    #: nullable on the vendor's own schema.
    customer_noor_id: str
    #: The two reasons a ``CUSTOM`` stock adjustment may name, one of each
    #: sign. The tag that would create a third is deferred.
    adjustment_reason_found_id: str
    adjustment_reason_spoiled_id: str
    # -- sales (slice L2b of konyklabs/roadmap#94) --
    #: The cashier every seeded sale names as its ``source.author_id``. It is
    #: the retailer's id: nothing resolves it, because the Users tag is
    #: outside the issue's scoped surface, and a stock adjustment's
    #: ``user_id`` is the same id for the same reason.
    cashier_user_id: str
    #: Both outlets' ``default_tax_id``, and the ``tax.id`` on every seeded
    #: line item. Nothing resolves it either -- the Taxes tag is out of scope.
    tax_id: str
    #: The seeded sales name :attr:`product_trail_mix_id` and
    #: :attr:`product_socks_id` on their line items and
    #: :attr:`customer_ada_id` as their customer.
    #: ``state: "parked"`` -- still editable, so a ``PUT`` succeeds against it.
    sale_saved_id: str
    #: ``state: "closed"`` with a card payment on the open main register. The
    #: state is terminal, so a ``PUT`` is a 409; the return action is what
    #: works on it.
    sale_closed_id: str
    #: A layby: parked, carrying the ``layby`` attribute and a part payment.
    #: There is no ``LAYBY`` state in the 2026-07 schema.
    sale_layby_id: str
    webhook_subscription_id: str
    #: The HMAC secret behind the ``X-Signature`` header. Lightspeed signs with
    #: the application's own ``client_secret``: ``WebhookRequest`` carries no
    #: per-hook secret.
    webhook_secret: str
    #: ``/api/2026-07`` -- the version segment every resource route sits under.
    api_prefix: str
    #: The seven ``WebhookType`` values. Two of them (the consignment pair) are
    #: subscribable and never fired here; see the vendor's capabilities.
    event_types: tuple[str, ...]

    @property
    def credentials(self) -> Credentials:
        """The application credential, under the neutral names.

        DOCUMENTED, the ``grant``: Lightspeed issues a refresh token alongside
        the access token and a consumer rotates it -- "Using a refresh token
        will revoke the access token that was returned with it ... You must
        save this new refresh token and use it the next time"
        (https://x-series-api.lightspeedhq.com/docs/authorization).
        """
        return Credentials(app_id=self.client_id, app_secret=self.client_secret, grant="refresh_token")

    @property
    def token(self) -> Token:
        """The seeded full-scope pair, under the neutral names. The tenant is
        the retailer -- there is no narrower id a token can be scoped to."""
        return Token(access_token=self.access_token, refresh_token=self.refresh_token, tenant_id=self.retailer_id)

    @property
    def auth(self) -> dict[str, str]:
        """``Authorization: Bearer`` for the full-scope token. No second
        header: one flat ``bearerAuth`` scheme is the whole of this vendor's
        authentication."""
        return {"Authorization": f"Bearer {self.access_token}"}

    @property
    def read_only_auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.read_only_access_token}"}

    @property
    def personal_auth(self) -> dict[str, str]:
        """The personal token, which authenticates identically."""
        return {"Authorization": f"Bearer {self.personal_access_token}"}

    def api_path(self, suffix: str = "") -> str:
        """``/api/2026-07`` plus ``suffix``: every resource route sits under
        the version segment. The token endpoint does NOT -- it is
        ``/api/1.0/token`` -- so this helper deliberately does not reach it."""
        return f"{self.api_prefix}{suffix}"


# ---------------------------------------------------------------------------
# Seed overlays: the typed shape of ``unit(seed_overlay=...)``.
#
# One ``TypedDict(total=False)`` per vendor, whose keys are exactly that
# vendor's top-level seed collections. ``total=False`` because an overlay is a
# PARTIAL document by definition -- naming one collection is the ordinary case
# -- and the point of the type is the *other* direction: a checker rejects a
# key that is not a collection at all, which is the mistake an overlay has no
# other symptom for. A misspelled collection merges cleanly, hydrates nothing
# and looks like the fake ignoring the scenario; the unit refuses it at start
# (``core/config/overlay.py``) and these types move the same refusal to the
# editor.
#
# The VALUES are ``object``, not a per-collection model. A seed document's
# entities are the vendor's own shapes, they differ per collection, and typing
# them here would mean a second description of every vendor entity that could
# drift from the document. The key is the half a consumer gets wrong.
#
# ``_comment`` is deliberately absent from all four, though the unit accepts
# it: it is the document's own annotation, not a collection, and offering it as
# something to override would be a worse answer than not typing it.
# ---------------------------------------------------------------------------


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
    """The collections ``vendorfake/lightspeed/seed/default.seed.json``
    carries."""

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
"""What a vendor passed as a plain ``str`` accepts: any JSON object.

The honest answer for a vendor whose name is not a literal -- a parametrized
test, or one discovered through the entry-point group -- exactly as
:class:`Seed` is the honest answer for its ``.seed``. The collections are a
property of the vendor, and this call site does not know which vendor it has.
The unit still refuses an unknown collection at start; what is absent is the
checker's ability to say so first.
"""


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
        # Lightspeed signs with the application's own secret; there is no
        # per-subscription secret member on WebhookRequest.
        webhook_secret=config.client_secret,
        api_prefix=API_PREFIX,
        event_types=tuple(LIGHTSPEED_EVENT_TYPES),
    )


@dataclass(frozen=True, slots=True)
class _BuiltInSeed:
    """One shipped vendor's seed, as the two facts this module has about it.

    ONE ENTRY PER VENDOR, both halves together, because they are the same
    fact stated twice if they are apart: :attr:`build` reads a set of module
    constants, and :attr:`collections` is which of the seed document's
    top-level collections those constants are the shipped values *of*. A
    vendor whose builder starts reading another collection and whose entry is
    not updated silently reopens the divergence
    :func:`~vendorfake.testing.seed_collections_for`'s callers exist to
    refuse, so the table below is the one place either half is written.
    """

    build: Callable[[Mapping[str, object]], Seed]
    collections: frozenset[str]
    """The seed collections this vendor's seed object speaks for.

    Its *credentials* (the bearer tokens a consumer authenticates with) and
    its *identity* (the merchant, restaurant or retailer every scoped path is
    built from) -- the two the seed publishes as concrete strings, from this
    distribution's constants rather than from the document that was loaded.
    An overlay naming one of these would change what the unit hydrates
    without changing what ``.seed`` reports, which is a 401 or a 404 with
    nothing anywhere to explain it; ``vendorfake.testing`` refuses such an
    overlay when the unit starts.

    NOT every collection a constant is drawn from. ``.seed`` also carries
    catalog, order and location ids, and an overlay may still name those:
    the divergence is then a stale id in one field of a fixture, which the
    consumer sees as a 404 on the entity they themselves replaced. The
    credential and the identity are the two whose divergence is *silent* --
    every request fails, and none of them fails at the thing that was
    overridden.
    """


_BUILT_IN_SEEDS: Mapping[str, _BuiltInSeed] = {
    "square": _BuiltInSeed(build=_square, collections=frozenset({"tokens", "merchant"})),
    "clover": _BuiltInSeed(build=_clover, collections=frozenset({"tokens", "merchant"})),
    "toast": _BuiltInSeed(build=_toast, collections=frozenset({"tokens", "restaurant"})),
    # Lightspeed keeps its three credential kinds in three collections --
    # ``tokens`` (the full-scope and read-only OAuth pair), ``personal_tokens``
    # and ``refresh_tokens`` -- and its identity in ``retailer``. All four are
    # what `_lightspeed` above reads its constants for, so all four are bound.
    "lightspeed": _BuiltInSeed(
        build=_lightspeed,
        collections=frozenset({"tokens", "personal_tokens", "refresh_tokens", "retailer"}),
    ),
}
"""The four vendors this distribution ships, as data rather than a branch.

This module may name them: it is under ``testing/``, not ``core/`` or
``conformance/``, and its whole job is to know what the four shipped
scenarios contain (``tools/boundary.toml`` draws the line in the same place).
A vendor from the entry-point group is not here -- it publishes its own seed
through the ``SeedingVendor`` hook and declares its own collections through
:data:`SEED_COLLECTIONS_ATTR`.
"""

SEED_COLLECTIONS_ATTR = "seed_collections"
"""The optional attribute a :class:`~vendorfake.core.kernel.types.SeedingVendor`
declares its seed's collections in.

A vendor that publishes a seed through the hook knows, and this module cannot
know, which of *its* seed document's collections that seed is built from.
Declaring them -- ``seed_collections = frozenset({"tokens", "tenant"})`` on the
``VendorDefinition`` -- buys the same start-time refusal the shipped vendors
get. Left undeclared, an overlay is not refused: silence has to mean "this
vendor has not said", because making it mean "refuse everything" would break
every existing hook implementation, and making it mean "refuse the names the
shipped vendors use" would be this module guessing about a document it has
never seen.

Read with :func:`getattr` rather than added to the ``SeedingVendor``
protocol: the protocol is ``runtime_checkable`` and ``seed_for`` discovers it
with ``isinstance``, so a new required member would make every vendor that
implements only ``seed`` stop being a ``SeedingVendor`` at all -- it would
silently lose its seed rather than gain a refusal.
"""


_SEED_MEMBERS = ("credentials", "token", "auth", "read_only_auth", "event_types")
"""The five names :class:`Seed` requires, as data, for the hook's shape check.

``token`` joined the list with :class:`Token` (konyklabs/roadmap#101). A
third-party vendor's seed that predates it is refused here, by name, rather
than failing later on ``started.seed.token`` -- the same reasoning as the
other four, and the reason the check is data rather than ``isinstance``.

Written out rather than derived from ``Seed.__protocol_attrs__``: that
attribute is an implementation detail of ``typing`` with no compatibility
promise, and the error message wants a stable, readable list anyway.
"""


def _from_hook(definition: VendorDefinition, vendor: str, vendor_config: Mapping[str, object]) -> Seed | None:
    """The vendor's own seed, if it publishes one, checked before it escapes.

    ``SeedingVendor`` is declared in the core, which may not import this
    module, so its ``seed`` hook is annotated as returning ``object`` -- the
    narrowing to :class:`Seed` happens here, at the one point where the two
    layers meet. A hook that returns the wrong shape is named as a hook
    defect, on the vendor, at the moment the unit is built. Letting it
    through would surface later as an ``AttributeError`` on
    ``started.seed.credentials``, which reads like a bug in vendorfake.
    """
    from vendorfake.core.kernel.types import SeedingVendor

    if not isinstance(definition, SeedingVendor):
        return None
    hook = definition.seed
    if not callable(hook):
        # `runtime_checkable` on a method-only Protocol checks attribute
        # presence, not callability, so this is reachable: `seed` is a
        # generic name and this package's own convention for seed *data*
        # besides (every vendor ships a `seed/` subpackage; a profile
        # document carries a `"seed"` key), so a `VendorDefinition` with a
        # non-callable `seed` field is a realistic collision, not a
        # theoretical one. Named here, at the vendor, as a hook defect --
        # the same way a hook returning the wrong shape is below -- rather
        # than left to surface as a bare `TypeError: '...' object is not
        # callable` three frames inside this module, which reads like a
        # vendorfake bug and names nothing a vendor author can act on.
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
    """The seed object for ``vendor``, or ``None`` when it publishes none.

    Resolution order, and why it is this way round. A vendor that implements
    the :class:`~vendorfake.core.kernel.types.SeedingVendor` hook is asked
    first, because that is the vendor's own statement about itself;
    :data:`_BUILT_IN_SEEDS` above is only *this module's* knowledge of the
    four vendors shipped here. None of the four implements the hook, so the
    ordering changes nothing for them -- a test pins that -- and it means a
    third-party vendor is never shadowed by a name collision with a built-in.

    ``definition`` is optional so the signature stays what v0.1.0 callers
    passed. Given one, the lookup is free; without one the vendor is resolved
    by name, and a name that resolves to nothing yields ``None`` exactly as it
    did before the hook existed -- this function has never been the place a
    typo is reported, and making it start would move that message away from
    ``resolve_vendor``, which names the alternatives.
    """
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
    """Which seed collections ``vendor``'s seed object is built from.

    The companion of :func:`seed_for`, resolved by the same rule and in the
    same order -- the vendor's own hook first, the shipped table second -- so
    that the two can never answer about different seeds. Empty when the
    vendor publishes no seed, and empty for a hook that declares nothing;
    see :data:`SEED_COLLECTIONS_ATTR` for why silence is not a refusal.

    FOR: ``vendorfake.testing``'s start-time refusal of a seed overlay that
    would make ``started.seed`` describe a unit other than the one it is
    handed back with. It is separate from :func:`seed_for` rather than a
    field on the seed object because the answer is needed *before* the
    question "is this unit worth building" is settled, and because a seed is
    a consumer's fixture -- a set of collection names on it would be one more
    thing on a public object that nothing a consumer writes ever reads.
    """
    if definition is None:
        definition = _resolve_quietly(vendor)
    if definition is not None:
        from vendorfake.core.kernel.types import SeedingVendor

        if isinstance(definition, SeedingVendor):
            declared = getattr(definition, SEED_COLLECTIONS_ATTR, None)
            if declared is None or isinstance(declared, str) or not isinstance(declared, Iterable):
                # A `str` is iterable and would decompose into characters, so
                # it is rejected with the undeclared case rather than turned
                # into a set of letters that matches nothing and refuses
                # nothing -- silently, which is the failure mode this whole
                # function exists to remove.
                return frozenset()
            return frozenset(str(name) for name in declared)
    built_in = _BUILT_IN_SEEDS.get(vendor)
    return frozenset() if built_in is None else built_in.collections


def _resolve_quietly(vendor: str) -> VendorDefinition | None:
    """``vendor``'s definition, or ``None`` if there is no such vendor.

    The import is deferred: this module is imported by ``vendorfake.testing``,
    which a consumer's ``conftest`` imports, and the registry pulls in the
    control plane and the kernel behind it. Nothing here needs the registry
    until a caller actually asks for a seed without one in hand.

    ``resolve_vendor`` raises for an unknown name and that refusal is the
    right one *at the edge* -- but ``seed_for('nope', {})`` answered ``None``
    in v0.1.0 and callers rely on it, so the exception is swallowed rather
    than re-raised here.
    """
    from vendorfake.registry import resolve_vendor

    try:
        return resolve_vendor(vendor)
    except ValueError:
        return None
