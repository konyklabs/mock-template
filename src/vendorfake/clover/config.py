"""Clover's own settings, with the documented value beside each citation, or
a JUDGMENT label. Permissions are app-level and fixed at the dashboard, not
per-token scopes (https://docs.clover.com/dev/docs/oauth-flows-in-clover).
``extra="forbid"``: an unknown key is a startup failure.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.config.models import merged_over

__all__ = [
    "DEFAULT_PERMISSIONS",
    "CloverConfig",
    "resolve_clover_config",
]

_DAY_MS = 24 * 60 * 60 * 1000
_MINUTE_MS = 60 * 1000

DEFAULT_PERMISSIONS: tuple[str, ...] = (
    "ORDERS_R",
    "ORDERS_W",
    "INVENTORY_R",
    "INVENTORY_W",
    "MERCHANT_R",
    "EMPLOYEES_R",
    "CUSTOMERS_R",
    "CUSTOMERS_W",
    "PAYMENTS_W",
)
"""The permission set the modelled surface needs. JUDGMENT: modelled on the
Clover dashboard's per-API read/write toggles
(https://docs.clover.com/dev/docs/permissions); Clover publishes no string
vocabulary for permissions."""


class CloverConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake: Clover's own example app-id shape.
    client_id: str = "UNITCLOVERAPP"
    #: JUDGMENT -- an obviously fake readable value, not a real UUID shape.
    client_secret: str = "unit-clover-app-secret"
    redirect_uri: str = "https://example.test/oauth/callback"

    #: Clover's documented sandbox host. JUDGMENT on emitting it at all.
    base_url: str = "https://apisandbox.dev.clover.com"

    #: The permission set every minted token inherits.
    permissions: tuple[str, ...] = DEFAULT_PERMISSIONS

    #: Emit the namespaced ``unit_error`` sidecar; see errors.py.
    error_sidecar: bool = True

    #: Emit ``retry-after`` on every 429, not only documented trips. JUDGMENT. https://docs.clover.com/dev/docs/api-usage-rate-limits
    retry_after_header: bool = True

    #: "OAuth access_tokens expire in 30 minutes."
    #: https://docs.clover.com/dev/docs/oauth-and-tokens-faqs
    access_token_ttl_ms: int = Field(default=30 * _MINUTE_MS, gt=0)

    #: JUDGMENT -- no numeric refresh-token lifetime is documented. https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token
    refresh_token_ttl_ms: int = Field(default=365 * _DAY_MS, gt=0)

    #: Accept ``http://`` callbacks though Clover documents HTTPS-only. JUDGMENT. https://docs.clover.com/dev/docs/webhooks
    allow_insecure_callbacks: bool = False

    #: JUDGMENT -- Clover documents no authorization-code expiry. https://docs.clover.com/dev/docs/high-trust-app-auth-flow
    authorization_code_ttl_ms: int = Field(default=10 * _MINUTE_MS, gt=0)

    def merged_with(self, block: Mapping[str, Any]) -> CloverConfig:
        """This config with ``block`` laid over it."""
        return merged_over(self, block)

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.access_token_ttl_ms)

    @property
    def refresh_token_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.refresh_token_ttl_ms)

    @property
    def authorization_code_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.authorization_code_ttl_ms)


def resolve_clover_config(raw: dict[str, Any] | None = None) -> CloverConfig:
    """Build the config from a profile's ``vendor`` block."""
    return CloverConfig.model_validate(raw or {})
