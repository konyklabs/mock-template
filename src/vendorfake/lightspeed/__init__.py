"""Lightspeed Retail X-Series (API 2026-07), as a vendorfake vendor.

FOR: publishing one name -- ``VENDOR`` -- that the registry resolves through
the ``vendorfake.vendors`` entry point, plus the pieces a consumer or a test
legitimately imports directly.

INVARIANT: **``VENDOR`` is a fresh definition on every access.** A vendor owns
stateful, seeded id streams, a version counter and a rate-limit window; the
attribute *is* the factory, through :func:`__getattr__`, so
``vendorfake.lightspeed.VENDOR is vendorfake.lightspeed.VENDOR`` is False.

Nothing in this package imports a web framework, and nothing in it is imported
by the core.
"""

from __future__ import annotations

from vendorfake.core.kernel.types import VendorDefinition
from vendorfake.lightspeed.auth import KIND_OAUTH, KIND_PERSONAL, LightspeedAuth
from vendorfake.lightspeed.capabilities import (
    LIGHTSPEED_CAPABILITIES,
    LIGHTSPEED_NOT_MODELED,
    LIGHTSPEED_NOT_SUPPORTED,
)
from vendorfake.lightspeed.config import DEFAULT_SCOPES, READ_ONLY_SCOPES, LightspeedConfig, resolve_lightspeed_config
from vendorfake.lightspeed.entities import COL, OBJECT_VERSION
from vendorfake.lightspeed.errors import LIGHTSPEED_ERROR_TABLE, LightspeedErrorShaper, http_date
from vendorfake.lightspeed.events import LIGHTSPEED_EVENT_TYPES
from vendorfake.lightspeed.ids import LightspeedCredentialIds, LightspeedIds
from vendorfake.lightspeed.model.money import to_amount, to_minor
from vendorfake.lightspeed.ratelimit import LightspeedRateLimiter
from vendorfake.lightspeed.retry import LIGHTSPEED_RETRY_SCHEDULE_MS, LIGHTSPEED_TIMEOUT_MS
from vendorfake.lightspeed.signer import (
    SIGNATURE_HEADER,
    LightspeedWebhookSigner,
    lightspeed_signature,
    signature_header_value,
    verify_lightspeed_signature,
)
from vendorfake.lightspeed.vendor import (
    LIGHTSPEED_MAGIC,
    LIGHTSPEED_ROLES,
    LightspeedVendor,
    create_lightspeed_vendor,
)
from vendorfake.lightspeed.versioning import FIRST_VERSION, LightspeedVersions

__all__ = [
    "COL",
    "DEFAULT_SCOPES",
    "FIRST_VERSION",
    "KIND_OAUTH",
    "KIND_PERSONAL",
    "LIGHTSPEED_CAPABILITIES",
    "LIGHTSPEED_ERROR_TABLE",
    "LIGHTSPEED_EVENT_TYPES",
    "LIGHTSPEED_MAGIC",
    "LIGHTSPEED_NOT_MODELED",
    "LIGHTSPEED_NOT_SUPPORTED",
    "LIGHTSPEED_RETRY_SCHEDULE_MS",
    "LIGHTSPEED_ROLES",
    "LIGHTSPEED_TIMEOUT_MS",
    "OBJECT_VERSION",
    "READ_ONLY_SCOPES",
    "SIGNATURE_HEADER",
    "VENDOR",
    "LightspeedAuth",
    "LightspeedConfig",
    "LightspeedCredentialIds",
    "LightspeedErrorShaper",
    "LightspeedIds",
    "LightspeedRateLimiter",
    "LightspeedVendor",
    "LightspeedVersions",
    "LightspeedWebhookSigner",
    "create_lightspeed_vendor",
    "http_date",
    "lightspeed_signature",
    "resolve_lightspeed_config",
    "signature_header_value",
    "to_amount",
    "to_minor",
    "verify_lightspeed_signature",
]


def __getattr__(name: str) -> VendorDefinition:
    """``VENDOR``, minted per access."""
    if name == "VENDOR":
        return create_lightspeed_vendor()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
