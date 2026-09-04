"""Lightspeed's own settings, with the documented value beside each citation.

FOR: turning a profile's ``vendor`` block -- arbitrary JSON as far as the core
is concerned -- into a typed object whose every default carries the Lightspeed
documentation pointer it came from, or a JUDGMENT label where Lightspeed
publishes nothing.

INVARIANT: **a setting is either documented or labelled.** Lightspeed
publishes exactly one numeric token lifetime (an example, not a rule) and no
refresh-token lifetime at all; both are recorded as such below. The rate-limit
formula and window ARE documented and are held here as knobs only because a
profile has to be able to widen the quota for a long test run.

Scopes
------
DOCUMENTED: the authoritative list is the 58-scope reference page
(https://x-series-api.lightspeedhq.com/docs/scopes). The specification carries
no OAuth2 scopes block at all -- one flat ``bearerAuth`` HTTP-bearer scheme,
applied globally -- and per-operation requirements appear only as free text in
each operation's ``description``, in the literal pattern
``"\\n\\n\\U0001f512 Requires: `scope:name` scope"``. Every scope this package
requires on a route was read out of that annotation for that operation; none
is invented. :data:`DOCUMENTED_SCOPES` is the subset the in-scope surface
needs, and :data:`DEFAULT_SCOPES` is what a full-scope token carries.

Like the other vendor configs, ``extra="forbid"``: an unknown key in a
profile's ``vendor`` block is a startup failure naming the key, never a default
silently left in place.
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
    "SCOPE_OUTLETS_READ",
    "SCOPE_PAYMENTS_READ",
    "SCOPE_PAYMENT_TYPES_READ",
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

SCOPE_OUTLETS_READ = "outlets:read"
SCOPE_PAYMENTS_READ = "payments:read"
SCOPE_PAYMENT_TYPES_READ = "payment_types:read"
SCOPE_REGISTERS_READ = "registers:read"
SCOPE_REGISTER_CLOSE = "register:close"
SCOPE_REGISTER_OPEN = "register:open"
SCOPE_RETAILER_READ = "retailer:read"
SCOPE_WEBHOOKS = "webhooks"
# -- sales (slice L2b of konyklabs/roadmap#94) -------------------------------
SCOPE_SALES_READ = "sales:read"
SCOPE_SALES_WRITE = "sales:write"
SCOPE_USERS_READ = "users:read"
"""The eleven scopes this package's surface is gated on, each read out of the
operation's own ``description`` annotation in ``api-2026-07`` and each present
on the 58-scope reference page. ``webhooks`` really is unqualified -- there is
no ``webhooks:read``/``webhooks:write`` pair.

The three sales scopes are the Sales tag's, and ``users:read`` is on the list
because ``initReturnSale``'s description names a PAIR -- "🔒 Requires:
``sales:write`` ``users:read`` scopes" -- exactly as ``CloseRegister``'s does.
The reference page's own wording for the two sale scopes: ``sales:read`` is
"Read all sales and payments in your account" and ``sales:write`` is "Create
sales and payments, and adjust, void or return sales", which is where the
verbs this surface implements come from."""

DOCUMENTED_SCOPES: tuple[str, ...] = (
    SCOPE_OUTLETS_READ,
    SCOPE_PAYMENTS_READ,
    SCOPE_PAYMENT_TYPES_READ,
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
"""The full scope set this unit's application carries; every minted token
inherits it. It is exactly the documented set -- no scope here is invented,
because the vendor publishes the whole vocabulary and the annotation that maps
it to operations."""

READ_ONLY_SCOPES: tuple[str, ...] = (
    SCOPE_OUTLETS_READ,
    SCOPE_PAYMENTS_READ,
    SCOPE_PAYMENT_TYPES_READ,
    SCOPE_REGISTERS_READ,
    SCOPE_RETAILER_READ,
    SCOPE_SALES_READ,
    SCOPE_USERS_READ,
)
"""A narrower set the scenario hands a second token, so "403 on the write path"
is testable without minting anything: no ``register:open``/``register:close``,
no ``sales:write`` and no ``webhooks``."""

_DOCUMENTED_EXPIRES_IN_S = 86400
"""``"expires_in": "86400"`` -- the one numeric lifetime the authorization
page prints, and it prints it inside an EXAMPLE response rather than as a
stated rule (https://x-series-api.lightspeedhq.com/docs/authorization). Treat
24 hours as JUDGMENT sourced from an example, never as a promise."""

_SPIKE_AUTH_CODE_TTL_MS = 10 * 60 * 1000
"""JUDGMENT, and UNCONFIRMED. Ten minutes, single use, is the figure the
roadmap#75 spike read off the same authorization page; the deeper pass that
produced ``facts.txt`` did not re-quote that sentence, so it is carried here as
a labelled choice rather than as a documented value."""

_DOCUMENTED_WINDOW_MS = 300_000
"""DOCUMENTED: "The rate limiter is currently based on a 5 minute (300 seconds)
window" (https://x-series-api.lightspeedhq.com/docs/rate_limiting). A fixed
window, not a leaky bucket -- that mechanism belongs to the separate R-Series
product line."""

_DOCUMENTED_QUOTA_PER_REGISTER = 300
_DOCUMENTED_QUOTA_BASE = 50
"""DOCUMENTED: the quota is "300 x <number of registers> + 50" per retailer per
application (same page)."""


class LightspeedConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake. The authorization page documents the field names
    #: (``client_id``, ``client_secret``) and no example values, so the shape
    #: is a readable stand-in a consumer can see is not real (JUDGMENT).
    client_id: str = "unit-lightspeed-client-id"
    client_secret: str = "unit-lightspeed-client-secret"

    #: Where ``GET /connect`` sends the caller back when the request supplies
    #: no ``redirect_uri``. JUDGMENT on the value; the parameter itself is
    #: documented on the authorize URL.
    redirect_uri: str = "https://consumer.example/callback"

    #: DOCUMENTED: tenancy is a per-retailer subdomain prefix, and the token
    #: response echoes it as ``domain_prefix``. One unit serves ONE retailer.
    domain_prefix: str = "unit-lightspeed"

    #: The scope set the application carries; every minted token inherits it.
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    #: JUDGMENT -- see :data:`_DOCUMENTED_EXPIRES_IN_S`. The docs' own example
    #: shows 86400 seconds and states no standard lifetime.
    access_token_ttl_s: int = Field(default=_DOCUMENTED_EXPIRES_IN_S, gt=0)

    #: JUDGMENT -- see :data:`_SPIKE_AUTH_CODE_TTL_MS`.
    authorization_code_ttl_ms: int = Field(default=_SPIKE_AUTH_CODE_TTL_MS, gt=0)

    #: DOCUMENTED window and formula; held as knobs so a profile can widen the
    #: quota for a long run rather than switch the limiter off, which no
    #: Lightspeed retailer can do either.
    rate_limit_window_ms: int = Field(default=_DOCUMENTED_WINDOW_MS, gt=0)
    rate_limit_per_register: int = Field(default=_DOCUMENTED_QUOTA_PER_REGISTER, ge=0)
    rate_limit_base: int = Field(default=_DOCUMENTED_QUOTA_BASE, ge=0)

    #: Emit the namespaced ``unit_error`` sidecar beside the error body. A
    #: deliberate deviation from the wire format; see errors.py.
    error_sidecar: bool = True

    #: Emit ``Retry-After`` on a 429. DOCUMENTED, and documented as an
    #: RFC 1123 HTTP-date rather than delta-seconds
    #: (https://x-series-api.lightspeedhq.com/docs/rate_limiting).
    retry_after_header: bool = True

    #: The ``environment`` field on an outbound delivery. DOCUMENTED as a
    #: field and never as a value ("environment or domain_prefix may be
    #: present but are not guaranteed to be"), so the value is JUDGMENT:
    #: ``production`` is where a real retailer's deliveries come from, and it
    #: is a knob rather than a constant so a consumer who switches on it can
    #: drive both branches.
    webhook_environment: str = "production"

    #: Accept ``http://`` webhook URLs. The specification's ``WebhookRequest``
    #: types ``url`` as a string with ``minLength: 3`` and names no scheme, so
    #: this defaults to ``True`` -- refusing would be this project inventing a
    #: rule the vendor does not publish. JUDGMENT, switchable for a consumer
    #: who wants the stricter behaviour.
    allow_insecure_callbacks: bool = True

    def merged_with(self, block: Mapping[str, Any]) -> LightspeedConfig:
        """This config with ``block`` laid over it: the profile wins, an
        unknown key is still refused. The idiom is the core's
        :func:`~vendorfake.core.config.models.merged_over`."""
        return merged_over(self, block)

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_s)

    @property
    def access_token_ttl_ms(self) -> int:
        return self.access_token_ttl_s * 1000

    def rate_limit_quota(self, registers: int) -> int:
        """``300 x registers + 50`` -- the documented formula, with the two
        numbers read from this config so a profile can widen it."""
        return self.rate_limit_per_register * registers + self.rate_limit_base


def resolve_lightspeed_config(raw: dict[str, Any] | None = None) -> LightspeedConfig:
    """Build the config from a profile's ``vendor`` block; an empty block is legal."""
    return LightspeedConfig.model_validate(raw or {})
