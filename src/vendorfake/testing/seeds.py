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

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

__all__ = ["CloverSeed", "Credentials", "Seed", "SquareSeed", "ToastSeed", "seed_for"]


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


class Seed(Protocol):
    """What every vendor's seed has, whichever vendor it is.

    FOR: a consumer parametrized over vendors, and for
    :class:`~vendorfake.testing.StartedUnit`'s fallback type when the vendor
    is a plain ``str`` rather than a literal. Reading a field through this
    protocol needs no ``isinstance`` and no per-vendor helper.

    Deliberately small, and deliberately without ``refresh_token``: Square
    and Clover have one and Toast does not, so putting it here would either
    lie about Toast or force a fake value onto it. A consumer that needs the
    refresh branch reads :attr:`Credentials.grant`, which is the real vendor
    difference rather than an artefact of this package.
    """

    @property
    def credentials(self) -> Credentials: ...

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


def seed_for(vendor: str, vendor_config: Mapping[str, object]) -> SquareSeed | CloverSeed | ToastSeed | None:
    """The seed object for a built-in vendor, or ``None`` for one this module
    does not describe -- a third-party vendor publishes its own."""
    if vendor == "square":
        return _square(vendor_config)
    if vendor == "clover":
        return _clover(vendor_config)
    if vendor == "toast":
        return _toast(vendor_config)
    return None
