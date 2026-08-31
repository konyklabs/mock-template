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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["CloverSeed", "SquareSeed", "seed_for"]


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


def seed_for(vendor: str, vendor_config: Mapping[str, object]) -> SquareSeed | CloverSeed | None:
    """The seed object for a built-in vendor, or ``None`` for one this module
    does not describe -- a third-party vendor publishes its own."""
    if vendor == "square":
        return _square(vendor_config)
    if vendor == "clover":
        return _clover(vendor_config)
    return None
