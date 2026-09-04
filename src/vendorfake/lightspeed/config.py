"""Lightspeed's own settings, with the documented value beside each citation.
Every setting is either DOCUMENTED or labelled JUDGMENT.

DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/scopes):
:data:`DOCUMENTED_SCOPES` is read from each operation's ``description``
annotation (the specification carries no OAuth2 scopes block, only a global
``bearerAuth`` scheme); :data:`DEFAULT_SCOPES` is what a full-scope token
carries.

``extra="forbid"``: an unknown key in a profile's ``vendor`` block is a
startup failure, never silently ignored.
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
    "READ_ONLY_SCOPES",
    "SCOPE_CUSTOMERS_READ",
    "SCOPE_CUSTOMERS_WRITE",
    "SCOPE_INVENTORY_READ",
    "SCOPE_INVENTORY_WRITE",
    "SCOPE_OUTLETS_READ",
    "SCOPE_PAYMENTS_READ",
    "SCOPE_PAYMENT_TYPES_READ",
    "SCOPE_PRODUCTS_READ",
    "SCOPE_PRODUCTS_WRITE",
    "SCOPE_REGISTERS_READ",
    "SCOPE_REGISTER_CLOSE",
    "SCOPE_REGISTER_OPEN",
    "SCOPE_RETAILER_READ",
    "SCOPE_SALES_READ",
    "SCOPE_SALES_WRITE",
    "SCOPE_USERS_READ",
    "SCOPE_WEBHOOKS",
    "LightspeedConfig",
    "resolve_lightspeed_config",
]

SCOPE_CUSTOMERS_READ = "customers:read"
SCOPE_CUSTOMERS_WRITE = "customers:write"
SCOPE_INVENTORY_READ = "inventory:read"
SCOPE_INVENTORY_WRITE = "inventory:write"
SCOPE_OUTLETS_READ = "outlets:read"
SCOPE_PAYMENTS_READ = "payments:read"
SCOPE_PAYMENT_TYPES_READ = "payment_types:read"
SCOPE_PRODUCTS_READ = "products:read"
SCOPE_PRODUCTS_WRITE = "products:write"
SCOPE_REGISTERS_READ = "registers:read"
SCOPE_REGISTER_CLOSE = "register:close"
SCOPE_REGISTER_OPEN = "register:open"
SCOPE_RETAILER_READ = "retailer:read"
SCOPE_SALES_READ = "sales:read"
SCOPE_SALES_WRITE = "sales:write"
SCOPE_USERS_READ = "users:read"
SCOPE_WEBHOOKS = "webhooks"
"""DOCUMENTED: seventeen scopes; ``webhooks`` is unqualified and
``inventory:write`` gates a read (``GET /stock_adjustments``), reproduced as documented."""

DOCUMENTED_SCOPES: tuple[str, ...] = (
    SCOPE_CUSTOMERS_READ,
    SCOPE_CUSTOMERS_WRITE,
    SCOPE_INVENTORY_READ,
    SCOPE_INVENTORY_WRITE,
    SCOPE_OUTLETS_READ,
    SCOPE_PAYMENTS_READ,
    SCOPE_PAYMENT_TYPES_READ,
    SCOPE_PRODUCTS_READ,
    SCOPE_PRODUCTS_WRITE,
    SCOPE_REGISTERS_READ,
    SCOPE_REGISTER_CLOSE,
    SCOPE_REGISTER_OPEN,
    SCOPE_RETAILER_READ,
    SCOPE_SALES_READ,
    SCOPE_SALES_WRITE,
    SCOPE_USERS_READ,
    SCOPE_WEBHOOKS,
)

DEFAULT_SCOPES: tuple[str, ...] = DOCUMENTED_SCOPES
"""Exactly :data:`DOCUMENTED_SCOPES`, nothing invented."""

READ_ONLY_SCOPES: tuple[str, ...] = (
    SCOPE_CUSTOMERS_READ,
    SCOPE_INVENTORY_READ,
    SCOPE_OUTLETS_READ,
    SCOPE_PAYMENTS_READ,
    SCOPE_PAYMENT_TYPES_READ,
    SCOPE_PRODUCTS_READ,
    SCOPE_REGISTERS_READ,
    SCOPE_RETAILER_READ,
    SCOPE_SALES_READ,
    SCOPE_USERS_READ,
)
"""A narrower set for testing 403-on-write: excludes every ``:write`` scope and
``webhooks`` -- also blocks ``GET /stock_adjustments`` (gated on ``inventory:write``)."""

_DOCUMENTED_EXPIRES_IN_S = 86400
"""DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/authorization):
``86400`` appears only in an example response, not a stated rule -- treated as JUDGMENT."""

_SPIKE_AUTH_CODE_TTL_MS = 10 * 60 * 1000
"""JUDGMENT, NOT VERIFIED: ten minutes, per the roadmap#75 spike reading; not re-confirmed since."""

_DOCUMENTED_WINDOW_MS = 300_000
"""DOCUMENTED (https://x-series-api.lightspeedhq.com/docs/rate_limiting): a fixed 5-minute window, not a leaky bucket."""

_DOCUMENTED_QUOTA_PER_REGISTER = 300
_DOCUMENTED_QUOTA_BASE = 50
"""DOCUMENTED: quota is "300 x <registers> + 50" per retailer per application."""


class LightspeedConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: JUDGMENT placeholders -- the authorization page names the fields but gives no example values.
    client_id: str = "unit-lightspeed-client-id"
    client_secret: str = "unit-lightspeed-client-secret"

    #: JUDGMENT default for ``GET /connect``'s fallback; the parameter is documented on the authorize URL.
    redirect_uri: str = "https://consumer.example/callback"

    #: DOCUMENTED: per-retailer subdomain prefix, echoed as ``domain_prefix`` in the token response.
    domain_prefix: str = "unit-lightspeed"

    #: The scope set the application carries; every minted token inherits it.
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    #: JUDGMENT -- see :data:`_DOCUMENTED_EXPIRES_IN_S`.
    access_token_ttl_s: int = Field(default=_DOCUMENTED_EXPIRES_IN_S, gt=0)

    #: JUDGMENT -- see :data:`_SPIKE_AUTH_CODE_TTL_MS`.
    authorization_code_ttl_ms: int = Field(default=_SPIKE_AUTH_CODE_TTL_MS, gt=0)

    #: DOCUMENTED window and formula, held as knobs so a profile can widen the quota for a long run.
    rate_limit_window_ms: int = Field(default=_DOCUMENTED_WINDOW_MS, gt=0)
    rate_limit_per_register: int = Field(default=_DOCUMENTED_QUOTA_PER_REGISTER, ge=0)
    rate_limit_base: int = Field(default=_DOCUMENTED_QUOTA_BASE, ge=0)

    #: JUDGMENT: derives the price a create request omitted (exactly one of
    #: ``price_including_tax``/``price_excluding_tax`` is required); 0.15 is NZ GST, the seed's country.
    product_tax_rate: str = "0.15"

    #: Emit the namespaced ``unit_error`` sidecar beside the error body (a deviation from the wire format).
    error_sidecar: bool = True

    #: DOCUMENTED: emit ``Retry-After`` on a 429 as an RFC 1123 HTTP-date, not
    #: delta-seconds (https://x-series-api.lightspeedhq.com/docs/rate_limiting).
    retry_after_header: bool = True

    #: JUDGMENT: unconstrained ``environment`` field; ``production`` is what a real retailer sends.
    webhook_environment: str = "production"

    #: JUDGMENT: accept ``http://`` since ``WebhookRequest.url`` names no scheme; switchable for a stricter consumer.
    allow_insecure_callbacks: bool = True

    def merged_with(self, block: Mapping[str, Any]) -> LightspeedConfig:
        """This config with ``block`` layered over it via :func:`merged_over`; an unknown key is still refused."""
        return merged_over(self, block)

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_s)

    @property
    def access_token_ttl_ms(self) -> int:
        return self.access_token_ttl_s * 1000

    def rate_limit_quota(self, registers: int) -> int:
        """``300 x registers + 50``, the documented formula, using this config's knobs."""
        return self.rate_limit_per_register * registers + self.rate_limit_base


def resolve_lightspeed_config(raw: dict[str, Any] | None = None) -> LightspeedConfig:
    """Build the config from a profile's ``vendor`` block; an empty block is legal."""
    return LightspeedConfig.model_validate(raw or {})
