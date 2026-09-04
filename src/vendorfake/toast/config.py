"""Toast's own settings, with the documented value beside each citation.

Turns a profile's ``vendor`` block into a typed object, ``extra="forbid"``.

DOCUMENTED (https://doc.toasttab.com/toast-api-specifications/toast-orders-api.yaml,
https://doc.toasttab.com/doc/devguide/apiClientAccounts.html): partner API
accounts carry *scopes* per client, not per token request.

JUDGMENT: undocumented scope names follow the documented ``<api>:<verb>``
pattern but are this project's own; do not pattern-match them against the real API.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.config.models import merged_over

__all__ = [
    "DEFAULT_SCOPES",
    "DOCUMENTED_SCOPES",
    "JUDGMENT_SCOPES",
    "READ_ONLY_SCOPES",
    "ToastConfig",
    "resolve_toast_config",
]

DOCUMENTED_SCOPES: tuple[str, ...] = (
    "orders:read",
    "orders.channel:read",
    "orders.payments:write",
    "guest.pi:read",
    "delivery_info.address:read",
)
"""The five scope strings the orders specification prints (toast-orders-api.yaml)."""

JUDGMENT_SCOPES: tuple[str, ...] = (
    "orders:write",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
    "stock:write",
)
"""This project's names for the scopes the specification does not print."""

DEFAULT_SCOPES: tuple[str, ...] = DOCUMENTED_SCOPES + JUDGMENT_SCOPES
"""The full scope set a partner client carries; every minted token inherits it."""

READ_ONLY_SCOPES: tuple[str, ...] = (
    "orders:read",
    "menus:read",
    "config:read",
    "restaurants:read",
    "partners:read",
    "stock:read",
)
"""A narrower set for testing "403 on the write path"; excludes ``guest.pi:read`` too."""

_DOCUMENTED_EXPIRES_IN_S = 19168
"""The one numeric token lifetime Toast prints (https://doc.toasttab.com/doc/devguide/authentication.html)."""


class ToastConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: JUDGMENT -- readable fake values; Toast documents the field names only.
    client_id: str = "unit-toast-client-id"
    client_secret: str = "unit-toast-client-secret"

    #: DOCUMENTED: JWT carries ``partner_guid`` (apiClientAccounts.html).
    partner_guid: str = "0f6c1b1e-0000-4000-8000-00000000a0a0"

    #: JUDGMENT -- HS256 secret this fake signs with; never verify one locally.
    jwt_signing_secret: str = "unit-toast-jwt-signing-secret"

    #: The scope set the partner client carries; every minted token inherits it.
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    #: JUDGMENT -- defaults to the login example's ``expiresIn``; no refresh flow.
    access_token_ttl_s: int = Field(default=_DOCUMENTED_EXPIRES_IN_S, gt=0)

    #: Emit the namespaced ``unit_error`` sidecar; a deliberate deviation, see errors.py.
    error_sidecar: bool = True

    #: DOCUMENTED alongside the ``X-Toast-RateLimit-*`` headers (https://doc.toasttab.com/doc/devguide/apiRateLimiting.html).
    retry_after_header: bool = True

    #: JUDGMENT -- accept ``http://`` callbacks; Toast requires HTTPS (https://doc.toasttab.com/doc/devguide/apiEndpointRequirements.html).
    allow_insecure_callbacks: bool = False

    #: DOCUMENTED: "5 or less (currently 5)" (https://doc.toasttab.com/doc/devguide/apiStockWebhook.html).
    low_quantity_threshold: float = Field(default=5.0, ge=0)

    def merged_with(self, block: Mapping[str, Any]) -> ToastConfig:
        """This config with ``block`` laid over it: the profile wins, an
        unknown key is still refused."""
        return merged_over(self, block)

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_s)

    @property
    def access_token_ttl_ms(self) -> int:
        return self.access_token_ttl_s * 1000


def resolve_toast_config(raw: dict[str, Any] | None = None) -> ToastConfig:
    """Build the config from a profile's ``vendor`` block; an empty block is legal."""
    return ToastConfig.model_validate(raw or {})
