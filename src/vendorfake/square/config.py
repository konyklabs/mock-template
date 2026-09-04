"""Square's own settings, each default carrying the documentation URL it came from where Square publishes one.
INVARIANT: a setting is either documented or labelled JUDGMENT; an unknown key in a profile's ``vendor`` block is
a startup failure naming the key, and ``environment`` accepts only the literal ``"Sandbox"``/``"Production"``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from vendorfake.core.config.models import merged_over

__all__ = [
    "DEFAULT_SCOPES",
    "SQUARE_API_VERSION",
    "WEBHOOK_SUBSCRIPTIONS_SCOPE",
    "SquareConfig",
    "resolve_square_config",
]

_DAY_MS = 24 * 60 * 60 * 1000

SQUARE_API_VERSION = "2026-08-19"
"""The ``Square-Version`` this unit claims to implement; reported on every response and checked for drift.
https://developer.squareup.com/docs/build-basics/versioning-overview"""

DEFAULT_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "PAYMENTS_READ",
    "SETTLEMENTS_READ",
    "BANK_ACCOUNTS_READ",
)
"""The scope set Square grants when ``GET /oauth2/authorize`` carries no ``scope`` parameter.
https://developer.squareup.com/reference/square/oauth-api/authorize"""

WEBHOOK_SUBSCRIPTIONS_SCOPE = "DEVELOPER_APPLICATION_WEBHOOKS_WRITE"
"""The permission Webhook Subscriptions requires; unlike every other scope here, absent from Square's permissions
reference (https://developer.squareup.com/docs/oauth-api/square-permissions). DOCUMENTED: it requires the application's personal access token, not an OAuth token.
https://developer.squareup.com/docs/webhooks/webhook-subscriptions-api https://developer.squareup.com/forums/t/attempting-to-register-webhooks/7954
JUDGMENT: modeled as a scope granted to the seeded token, since this unit has no personal-access-token principal.
"""


class SquareConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake; shaped like Square's sandbox credential format so a leak reads as fake.
    application_id: str = "sandbox-sq0idb-unit-square-application"
    application_secret: str = "sandbox-sq0csb-unit-square-secret"
    redirect_uri: str = "https://example.test/oauth/callback"

    #: Sent as ``square-environment``. https://developer.squareup.com/docs/webhooks/build-with-webhooks
    environment: Literal["Sandbox", "Production"] = "Sandbox"

    api_version: str = SQUARE_API_VERSION

    #: Emit the namespaced ``unit_error`` sidecar beside Square's ``errors`` array; a deliberate wire-format deviation (see errors.py).
    error_sidecar: bool = True

    #: JUDGMENT: emit ``retry-after`` on a 429 though Square documents none; switchable since trusting it against the real API would be wrong.
    retry_after_header: bool = True

    #: DOCUMENTED: access tokens "expire after 30 days." https://developer.squareup.com/docs/oauth-api/overview
    access_token_ttl_ms: int = Field(default=30 * _DAY_MS, gt=0)

    #: DOCUMENTED: short-lived tokens "expire in 24 hours." https://developer.squareup.com/reference/square/oauth-api/obtain-token
    short_lived_ttl_ms: int = Field(default=_DAY_MS, gt=0)

    #: DOCUMENTED: PKCE refresh tokens "expire after 90 days"; code-flow refresh tokens don't expire. https://developer.squareup.com/docs/oauth-api/overview
    pkce_refresh_ttl_ms: int = Field(default=90 * _DAY_MS, gt=0)

    #: DOCUMENTED: authorization code "expires 5 minutes after" generation. https://developer.squareup.com/docs/oauth-api/overview
    authorization_code_ttl_ms: int = Field(default=5 * 60 * 1000, gt=0)

    default_scopes: tuple[str, ...] = DEFAULT_SCOPES

    def merged_with(self, block: Mapping[str, Any]) -> SquareConfig:
        """This config with ``block`` laid over it: the profile wins, an unknown key is still refused
        (core's :func:`~vendorfake.core.config.models.merged_over`)."""
        return merged_over(self, block)

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.access_token_ttl_ms)

    @property
    def short_lived_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.short_lived_ttl_ms)

    @property
    def pkce_refresh_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.pkce_refresh_ttl_ms)

    @property
    def authorization_code_ttl(self) -> timedelta:
        return timedelta(milliseconds=self.authorization_code_ttl_ms)


def resolve_square_config(raw: dict[str, Any] | None = None) -> SquareConfig:
    """Build the config from a profile's ``vendor`` block; a thin wrapper so an empty block being legal
    is known in one place."""
    return SquareConfig.model_validate(raw or {})
