"""Square's own settings, with the documented value beside each citation.

FOR: turning a profile's ``vendor`` block -- arbitrary JSON as far as the core
is concerned -- into a typed object whose every default carries the Square
documentation URL it came from.

INVARIANT: **a setting is either documented or labelled.** Every TTL below
quotes the sentence it comes from. The three that Square does not publish
(``application_id``, ``application_secret``, ``redirect_uri``) are obviously
fake values, and the two switches (``error_sidecar``, ``retry_after_header``)
govern deliberate deviations from Square's wire format, so both can be turned
off by a consumer who wants only what Square really sends.

Two departures from the reference's ``resolveSquareConfig``, both deliberate:

``extra="forbid"``
    The reference reads keys out of a ``Record<string, unknown>`` and ignores
    anything it does not recognise, so ``{"aplicationId": "..."}`` silently
    leaves the default in place and the consumer debugs an OAuth flow that
    authenticates against a secret they think they replaced. Here an unknown
    key in a profile's ``vendor`` block is a startup failure naming the key.

``environment`` is a strict literal
    The reference writes ``str('environment', 'Sandbox') === 'Production' ?
    'Production' : 'Sandbox'``, so ``"production"`` -- the spelling every shell
    script uses -- quietly becomes Sandbox and the webhook
    ``square-environment`` header then says the opposite of what the operator
    asked for. Here it is refused.

TTLs stay ``_ms`` integers on the wire so a profile document remains
diff-comparable against the reference's, and are read in code through the
``timedelta`` properties, where the unit is unmistakable.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_SCOPES",
    "SQUARE_API_VERSION",
    "WEBHOOK_SUBSCRIPTIONS_SCOPE",
    "SquareConfig",
    "resolve_square_config",
]

_DAY_MS = 24 * 60 * 60 * 1000

SQUARE_API_VERSION = "2026-08-19"
"""The ``Square-Version`` this unit claims to implement.

Reported on every response, and the value the freshness job compares against
the latest published version to report drift.
https://developer.squareup.com/docs/build-basics/versioning-overview
"""

DEFAULT_SCOPES: tuple[str, ...] = (
    "MERCHANT_PROFILE_READ",
    "PAYMENTS_READ",
    "SETTLEMENTS_READ",
    "BANK_ACCOUNTS_READ",
)
"""The scope set Square grants when ``GET /oauth2/authorize`` carries no
``scope`` parameter.
https://developer.squareup.com/reference/square/oauth-api/authorize
"""

WEBHOOK_SUBSCRIPTIONS_SCOPE = "DEVELOPER_APPLICATION_WEBHOOKS_WRITE"
"""The permission the Webhook Subscriptions surface requires, and the one name
in this file that is **not** taken from Square's permissions reference.

What Square publishes, and what it does not:

* the permissions reference
  (https://developer.squareup.com/docs/oauth-api/square-permissions) lists no
  webhook permission at all -- the whole vocabulary is seller-scoped, from
  ``BANK_ACCOUNTS_READ`` to ``VENDOR_WRITE``;
* the Webhook Subscriptions API guide states the reason:
  "Because webhook subscriptions are owned by the application and not by any
  one seller, you cannot use OAuth access tokens with the Webhook
  Subscriptions API. You must use the application's personal access token."
  https://developer.squareup.com/docs/webhooks/webhook-subscriptions-api
* Square's own refusal on that API names this permission, and Square staff
  confirm it applies to the application credential rather than to an OAuth
  grant: "The WebhookSubscription endpoint can only be called with your
  personal access token. Not an OAuth access token."
  https://developer.squareup.com/forums/t/attempting-to-register-webhooks/7954

JUDGMENT -- **this unit models "application credential, not seller grant" as a
scope, because a scope is the only authorization axis it has.** There is no
personal-access-token principal here; every ``bearer`` credential is a token
record. So the one permission name Square's own error uses becomes the gate,
and it is deliberately absent from :data:`DEFAULT_SCOPES` and from the
read-only seeded token: an OAuth grant that asked for nothing special cannot
reach these routes, which is the outcome Square's rule produces. It is granted
to the full seeded token, which is this unit's stand-in for the credential a
developer already holds.

The alternative -- a separate ``personal-access-token`` auth mode -- models
Square more exactly and is a larger change than a scope declaration; recorded
here so the choice is visible rather than assumed.

There is no ``..._READ`` counterpart on purpose. Square names exactly one
permission for this API and it is the write one; inventing a read sibling would
be this fake asserting a permission Square does not publish. All six routes
therefore name this single scope, which also matches the guide's framing of the
API as application administration rather than seller data.
"""


class SquareConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake, and shaped like Square's sandbox credentials so that a
    #: consumer who leaks one into a log can see at a glance it is not real.
    application_id: str = "sandbox-sq0idb-unit-square-application"
    application_secret: str = "sandbox-sq0csb-unit-square-secret"
    redirect_uri: str = "https://example.test/oauth/callback"

    #: Sent as the ``square-environment`` delivery header.
    #: https://developer.squareup.com/docs/webhooks/build-with-webhooks
    environment: Literal["Sandbox", "Production"] = "Sandbox"

    api_version: str = SQUARE_API_VERSION

    #: Emit the namespaced ``unit_error`` sidecar beside Square's ``errors``
    #: array. A deliberate deviation from Square's wire format; see errors.py.
    error_sidecar: bool = True

    #: Emit ``retry-after`` on a 429. Square documents no such header -- the
    #: guidance is client-side exponential backoff with jitter -- so a consumer
    #: who learns to trust it against this fake would busy-loop against the real
    #: API. On by default because it is useful for testing one's own backoff,
    #: and switchable because the fidelity argument is real. JUDGMENT.
    retry_after_header: bool = True

    #: "Square OAuth access tokens expire after 30 days."
    #: https://developer.squareup.com/docs/oauth-api/overview
    access_token_ttl_ms: int = Field(default=30 * _DAY_MS, gt=0)

    #: "Indicates whether the returned access token should expire in 24 hours."
    #: https://developer.squareup.com/reference/square/oauth-api/obtain-token
    short_lived_ttl_ms: int = Field(default=_DAY_MS, gt=0)

    #: "Refresh tokens obtained using the PKCE flow are single-use tokens and
    #: expire after 90 days." (Code-flow refresh tokens do not expire, which is
    #: why there is no constant for them.)
    #: https://developer.squareup.com/docs/oauth-api/overview
    pkce_refresh_ttl_ms: int = Field(default=90 * _DAY_MS, gt=0)

    #: "The authorization code expires 5 minutes after the Square authorization
    #: page generates the code."
    #: https://developer.squareup.com/docs/oauth-api/overview
    authorization_code_ttl_ms: int = Field(default=5 * 60 * 1000, gt=0)

    default_scopes: tuple[str, ...] = DEFAULT_SCOPES

    def merged_with(self, block: Mapping[str, Any]) -> SquareConfig:
        """This config with ``block`` laid over it.

        The profile wins over the base, which is the precedence every other
        layer in this project uses: defaults under the vendor's own values,
        those under the profile document, that under the environment. An
        unknown key in ``block`` is still refused, because the merge revalidates
        rather than patching field by field.
        """
        return SquareConfig.model_validate({**self.model_dump(), **dict(block)})

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
    """Build the config from a profile's ``vendor`` block.

    A thin function rather than a bare constructor call so that the one place
    that knows an empty block is legal is not every caller.
    """
    return SquareConfig.model_validate(raw or {})
