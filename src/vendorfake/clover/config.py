"""Clover's own settings, with the documented value beside each citation.

FOR: turning a profile's ``vendor`` block -- arbitrary JSON as far as the core
is concerned -- into a typed object whose every default carries the Clover
documentation URL it came from, or a JUDGMENT label where Clover publishes
nothing.

INVARIANT: **a setting is either documented or labelled.** The one TTL Clover
states numerically is quoted verbatim below; the other two are this project's
reading of examples and are labelled as such. The credentials are obviously
fake values, and the two switches (``error_sidecar``, ``retry_after_header``)
govern deliberate deviations from Clover's wire format, so both can be turned
off by a consumer who wants only what Clover really sends.

Permissions, not scopes
-----------------------
Clover grants an app a fixed permission set, configured in the developer
dashboard rather than requested per token: "All REST API endpoints require an
OAuth-generated access_token with specific permissions"
(https://docs.clover.com/dev/docs/oauth-flows-in-clover). There is no
``scope`` parameter anywhere in the documented authorize or token exchange.
The model here: the app carries a permission set from this config, every
minted token inherits it, and routes declare the permissions they require.

JUDGMENT -- **the permission names themselves are this project's.** Clover's
docs describe permissions in dashboard prose ("read orders", "write
inventory") and publish no machine vocabulary for them. The
``ORDERS_R``-style names below are modelled on the dashboard's read/write
toggles and are not Clover strings; a consumer must not pattern-match on them
against the real API.

Like ``SquareConfig``, ``extra="forbid"``: an unknown key in a profile's
``vendor`` block is a startup failure naming the key, never a default
silently left in place. TTLs stay ``_ms`` integers on the wire so a profile
document remains diff-comparable, and are read in code through the
``timedelta`` properties.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
)
"""The permission set the modelled surface needs.

JUDGMENT -- names are this project's, modelled on the Clover dashboard's
per-API read/write toggles (https://docs.clover.com/dev/docs/permissions);
Clover publishes no string vocabulary for permissions. See the module
docstring.
"""


class CloverConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake, and exactly 13 uppercase characters because every app id
    #: in Clover's own examples is (``DRKVJT2ZRRRSC`` on
    #: https://docs.clover.com/dev/docs/webhooks) -- so it passes a consumer's
    #: shape assertions while never being mistaken for a real credential.
    client_id: str = "UNITCLOVERAPP"
    #: JUDGMENT -- real Clover app secrets in legacy examples are UUID-shaped,
    #: but an obviously fake readable value is chosen deliberately, matching
    #: the Square package's ``sandbox-...-unit-square-secret`` convention: a
    #: consumer who leaks one into a log can see at a glance it is not real.
    client_secret: str = "unit-clover-app-secret"
    redirect_uri: str = "https://example.test/oauth/callback"

    #: The permission set the app was "installed" with; every minted token
    #: inherits it. See the module docstring for why this is a fixed set
    #: rather than a per-token scope request.
    permissions: tuple[str, ...] = DEFAULT_PERMISSIONS

    #: Emit the namespaced ``unit_error`` sidecar beside Clover's envelope.
    #: A deliberate deviation from Clover's wire format; see errors.py.
    error_sidecar: bool = True

    #: Emit ``retry-after`` on a 429. Clover documents the header only on
    #: concurrent-rate-limit trips ("429 responses for concurrent rate limits
    #: also include a retry-after header",
    #: https://docs.clover.com/dev/docs/api-usage-rate-limits); this unit's
    #: 429s are chaos-injected and carry no concurrency accounting, so sending
    #: it on all of them is a convenience, switchable because the fidelity
    #: argument is real. JUDGMENT.
    retry_after_header: bool = True

    #: "OAuth access_tokens expire in 30 minutes."
    #: https://docs.clover.com/dev/docs/oauth-and-tokens-faqs
    access_token_ttl_ms: int = Field(default=30 * _MINUTE_MS, gt=0)

    #: JUDGMENT -- Clover states no refresh-token lifetime numerically. The
    #: documented example response has ``access_token_expiration: 1677875430``
    #: and ``refresh_token_expiration: 1709497830``
    #: (https://docs.clover.com/dev/docs/generate-oauth-expiring-access-and-refresh-token),
    #: which are 366 days apart; 365 days is this project's reading of that
    #: example, not a documented rule.
    refresh_token_ttl_ms: int = Field(default=365 * _DAY_MS, gt=0)

    #: JUDGMENT -- Clover documents no authorization-code expiry at all
    #: (https://docs.clover.com/dev/docs/high-trust-app-auth-flow shows the
    #: redirect carrying ``code`` and says nothing about its lifetime).
    #: Ten minutes is a common industry default (RFC 6749 s4.1.2 recommends a
    #: maximum of ten minutes) and is chosen so that an expired-code path is
    #: testable at all.
    authorization_code_ttl_ms: int = Field(default=10 * _MINUTE_MS, gt=0)

    def merged_with(self, block: Mapping[str, Any]) -> CloverConfig:
        """This config with ``block`` laid over it.

        The profile wins over the base, the precedence every other layer in
        this project uses. An unknown key in ``block`` is still refused,
        because the merge revalidates rather than patching field by field.
        """
        return CloverConfig.model_validate({**self.model_dump(), **dict(block)})

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
    """Build the config from a profile's ``vendor`` block.

    A thin function rather than a bare constructor call so that the one place
    that knows an empty block is legal is not every caller.
    """
    return CloverConfig.model_validate(raw or {})
