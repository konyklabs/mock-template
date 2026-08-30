"""Square (Connect v2), as a vendorfake vendor.

FOR: publishing one name -- ``VENDOR`` -- that the registry resolves through the
``vendorfake.vendors`` entry point, plus the handful of pieces a consumer or a
test legitimately imports directly: the error table, the order machine, the id
shapes, the wire projections and the webhook signature helpers -- the last of
those because a consumer verifying a delivery should run *one* implementation
of the scheme rather than copying it out of a docstring.

INVARIANT: **``VENDOR`` is a fresh definition on every access.** A vendor owns
a stateful, seeded id stream; two units sharing one would interleave their
draws and neither would reproduce its own ids. The registry resolves a module
attribute rather than calling a factory, so the attribute *is* the factory,
through :func:`__getattr__`. ``vendorfake.square.VENDOR is
vendorfake.square.VENDOR`` is therefore False, which is stated here because it
is the one surprising thing in this package.

Nothing in this package imports a web framework, and nothing in it is imported
by the core. A vendor supplies data -- routes, tables, machines -- and the core
supplies behaviour.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import VendorDefinition
from vendorfake.square.auth import SquareAuth
from vendorfake.square.capabilities import SQUARE_CAPABILITIES, SQUARE_NOT_SUPPORTED
from vendorfake.square.config import DEFAULT_SCOPES, SQUARE_API_VERSION, SquareConfig, resolve_square_config
from vendorfake.square.entities import COL
from vendorfake.square.errors import SQUARE_ERROR_TABLE, ErrorCategory, ErrorCode, SquareErrorShaper
from vendorfake.square.events import SQUARE_EVENT_TYPES, SquareEventMapper
from vendorfake.square.ids import SquareIds
from vendorfake.square.machine import (
    FULFILLMENT_MACHINE,
    FULFILLMENT_MACHINE_NAME,
    ORDER_MACHINE,
    ORDER_MACHINE_NAME,
    FulfillmentState,
    OrderState,
)
from vendorfake.square.model.oauth import SQUARE_GRANT_TYPES, SUPPORTED_GRANT_TYPES
from vendorfake.square.model.order import project_order, project_order_entry
from vendorfake.square.retry import SQUARE_RETRY_SCHEDULE_MS
from vendorfake.square.seed import SeedDocument, hydrate_square
from vendorfake.square.signer import SquareWebhookSigner, square_signature, verify_square_signature
from vendorfake.square.surface.oauth import oauth_routes
from vendorfake.square.vendor import SQUARE_MAGIC, SQUARE_SCOPES, SquareVendor, create_square_vendor

__all__ = [
    "COL",
    "DEFAULT_SCOPES",
    "FULFILLMENT_MACHINE",
    "FULFILLMENT_MACHINE_NAME",
    "ORDER_MACHINE",
    "ORDER_MACHINE_NAME",
    "SQUARE_API_VERSION",
    "SQUARE_CAPABILITIES",
    "SQUARE_ERROR_TABLE",
    "SQUARE_EVENT_TYPES",
    "SQUARE_GRANT_TYPES",
    "SQUARE_MAGIC",
    "SQUARE_NOT_SUPPORTED",
    "SQUARE_RETRY_SCHEDULE_MS",
    "SQUARE_SCOPES",
    "SUPPORTED_GRANT_TYPES",
    "VENDOR",
    "ErrorCategory",
    "ErrorCode",
    "FulfillmentState",
    "OrderState",
    "SeedDocument",
    "SquareAuth",
    "SquareConfig",
    "SquareErrorShaper",
    "SquareEventMapper",
    "SquareIds",
    "SquareVendor",
    "SquareWebhookSigner",
    "create_square_vendor",
    "hydrate_square",
    "oauth_routes",
    "project_order",
    "project_order_entry",
    "resolve_square_config",
    "square_signature",
    "verify_square_signature",
]


def __getattr__(name: str) -> VendorDefinition:
    """``VENDOR``, minted per access. See the module docstring for why.

    Any other missing name raises ``AttributeError`` as usual, so a typo does
    not silently return a vendor.
    """
    if name == "VENDOR":
        return create_square_vendor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
