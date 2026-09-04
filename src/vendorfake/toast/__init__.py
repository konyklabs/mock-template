"""Toast (REST v2/v3), as a vendorfake vendor.

Publishes ``VENDOR``, resolved through the ``vendorfake.vendors`` entry
point, plus the pieces a consumer or test legitimately imports directly.

INVARIANT: ``VENDOR`` is a fresh definition on every access -- a vendor owns
stateful, seeded id streams, so the attribute is the factory (via
:func:`__getattr__`) and ``vendorfake.toast.VENDOR is vendorfake.toast.VENDOR``
is False.

Nothing in this package imports a web framework, and nothing in it is
imported by the core.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import VendorDefinition
from vendorfake.toast.auth import ToastAuth
from vendorfake.toast.capabilities import TOAST_CAPABILITIES, TOAST_NOT_MODELED, TOAST_NOT_SUPPORTED
from vendorfake.toast.config import DEFAULT_SCOPES, ToastConfig, resolve_toast_config
from vendorfake.toast.entities import COL
from vendorfake.toast.errors import TOAST_ERROR_TABLE, ToastErrorShaper
from vendorfake.toast.ids import ToastIds, ToastRequestIds
from vendorfake.toast.machine import (
    CHECK_MACHINE,
    CHECK_MACHINE_NAME,
    GUEST_ORDER_MACHINE,
    GUEST_ORDER_MACHINE_NAME,
    CheckPaymentStatus,
    GuestOrderStatus,
)
from vendorfake.toast.model.dates import business_date, parse_rest_date, rest_date, webhook_date
from vendorfake.toast.model.money import to_cents, to_dollars
from vendorfake.toast.retry import TOAST_RETRY_SCHEDULE_MS, TOAST_TIMEOUT_MS
from vendorfake.toast.signer import SIGNATURE_HEADER, ToastWebhookSigner, toast_signature, verify_toast_signature
from vendorfake.toast.vendor import TOAST_MAGIC, TOAST_ROLES, ToastVendor, create_toast_vendor

__all__ = [
    "CHECK_MACHINE",
    "CHECK_MACHINE_NAME",
    "COL",
    "DEFAULT_SCOPES",
    "GUEST_ORDER_MACHINE",
    "GUEST_ORDER_MACHINE_NAME",
    "SIGNATURE_HEADER",
    "TOAST_CAPABILITIES",
    "TOAST_ERROR_TABLE",
    "TOAST_MAGIC",
    "TOAST_NOT_MODELED",
    "TOAST_NOT_SUPPORTED",
    "TOAST_RETRY_SCHEDULE_MS",
    "TOAST_ROLES",
    "TOAST_TIMEOUT_MS",
    "VENDOR",
    "CheckPaymentStatus",
    "GuestOrderStatus",
    "ToastAuth",
    "ToastConfig",
    "ToastErrorShaper",
    "ToastIds",
    "ToastRequestIds",
    "ToastVendor",
    "ToastWebhookSigner",
    "business_date",
    "create_toast_vendor",
    "parse_rest_date",
    "resolve_toast_config",
    "rest_date",
    "to_cents",
    "to_dollars",
    "toast_signature",
    "verify_toast_signature",
    "webhook_date",
]


def __getattr__(name: str) -> VendorDefinition:
    """``VENDOR``, minted per access."""
    if name == "VENDOR":
        return create_toast_vendor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
