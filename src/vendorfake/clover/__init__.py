"""Clover (REST v3), as a vendorfake vendor.

FOR: publishing one name -- ``VENDOR`` -- that the registry resolves through
the ``vendorfake.vendors`` entry point, plus the pieces a consumer or a test
legitimately imports directly: the error table, the order machine, the id
stream, the wire models and the configuration.

INVARIANT: **``VENDOR`` is a fresh definition on every access.** A vendor owns
a stateful, seeded id stream; two units sharing one would interleave their
draws and neither would reproduce its own ids. The registry resolves a module
attribute rather than calling a factory, so the attribute *is* the factory,
through :func:`__getattr__`. ``vendorfake.clover.VENDOR is
vendorfake.clover.VENDOR`` is therefore False, which is stated here because it
is the one surprising thing in this package.

Nothing in this package imports a web framework, and nothing in it is imported
by the core. A vendor supplies data -- routes, tables, machines -- and the
core supplies behaviour.
"""

from __future__ import annotations

from vendorfake.clover.auth import CloverAuth
from vendorfake.clover.capabilities import CLOVER_CAPABILITIES, CLOVER_NOT_MODELED, CLOVER_NOT_SUPPORTED
from vendorfake.clover.config import DEFAULT_PERMISSIONS, CloverConfig, resolve_clover_config
from vendorfake.clover.entities import COL
from vendorfake.clover.errors import CLOVER_ERROR_TABLE, CloverErrorShaper
from vendorfake.clover.events import CLOVER_EVENT_TYPES, CloverEventMapper
from vendorfake.clover.ids import CloverIds
from vendorfake.clover.machine import ORDER_MACHINE, ORDER_MACHINE_NAME, OrderState
from vendorfake.clover.model.inventory import ItemWire, PriceType, project_item
from vendorfake.clover.model.merchant import AddressWire, MerchantWire, OwnerWire
from vendorfake.clover.model.oauth import RefreshRequest, TokenRequest, TokenResponse
from vendorfake.clover.model.order import (
    DiscountWire,
    ItemRefWire,
    LineItemWire,
    OrderTypeRefWire,
    OrderWire,
    PaymentState,
    PayType,
    ServiceChargeWire,
    atomic_total,
    project_order,
)
from vendorfake.clover.retry import CLOVER_RETRY_SCHEDULE_MS
from vendorfake.clover.signer import AUTH_HEADER, CloverWebhookSigner, verify_clover_auth
from vendorfake.clover.surface.customers import customer_routes
from vendorfake.clover.surface.inventory import inventory_routes
from vendorfake.clover.surface.merchant import merchant_routes
from vendorfake.clover.surface.oauth import FAILED_CODE_MESSAGE, oauth_routes
from vendorfake.clover.surface.orders import order_routes
from vendorfake.clover.surface.payments import payment_routes
from vendorfake.clover.surface.webhooks import webhook_routes
from vendorfake.clover.vendor import CLOVER_MAGIC, CLOVER_ROLES, CloverVendor, create_clover_vendor
from vendorfake.core.kernel.types import VendorDefinition

__all__ = [
    "AUTH_HEADER",
    "CLOVER_CAPABILITIES",
    "CLOVER_ERROR_TABLE",
    "CLOVER_EVENT_TYPES",
    "CLOVER_MAGIC",
    "CLOVER_NOT_MODELED",
    "CLOVER_NOT_SUPPORTED",
    "CLOVER_RETRY_SCHEDULE_MS",
    "CLOVER_ROLES",
    "COL",
    "DEFAULT_PERMISSIONS",
    "FAILED_CODE_MESSAGE",
    "ORDER_MACHINE",
    "ORDER_MACHINE_NAME",
    "VENDOR",
    "AddressWire",
    "CloverAuth",
    "CloverConfig",
    "CloverErrorShaper",
    "CloverEventMapper",
    "CloverIds",
    "CloverVendor",
    "CloverWebhookSigner",
    "DiscountWire",
    "ItemRefWire",
    "ItemWire",
    "LineItemWire",
    "MerchantWire",
    "OrderState",
    "OrderTypeRefWire",
    "OrderWire",
    "OwnerWire",
    "PayType",
    "PaymentState",
    "PriceType",
    "RefreshRequest",
    "ServiceChargeWire",
    "TokenRequest",
    "TokenResponse",
    "atomic_total",
    "create_clover_vendor",
    "customer_routes",
    "inventory_routes",
    "merchant_routes",
    "oauth_routes",
    "order_routes",
    "payment_routes",
    "project_item",
    "project_order",
    "resolve_clover_config",
    "verify_clover_auth",
    "webhook_routes",
]


def __getattr__(name: str) -> VendorDefinition:
    """``VENDOR``, minted per access. See the module docstring for why.

    Any other missing name raises ``AttributeError`` as usual, so a typo does
    not silently return a vendor.
    """
    if name == "VENDOR":
        return create_clover_vendor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
