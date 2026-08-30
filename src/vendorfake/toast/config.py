"""Toast's own settings, with the documented value beside each citation.

FOR: turning a profile's ``vendor`` block -- arbitrary JSON as far as the core
is concerned -- into a typed object whose every default carries the Toast
documentation URL it came from, or a JUDGMENT label where Toast publishes
nothing.

INVARIANT: **a setting is either documented or labelled.** Toast publishes one
numeric token lifetime -- an example, not a rule -- and it is quoted below as
exactly that. The credentials are obviously fake values, and the switches
(``error_sidecar``, ``retry_after_header``, ``allow_insecure_callbacks``)
govern deliberate deviations from Toast's wire format, so each can be turned
off by a consumer who wants only what Toast really sends.

Scopes
------
Toast's partner API accounts carry *scopes*: the orders specification names
``orders:read``, ``orders.channel:read``, ``orders.payments:write``,
``guest.pi:read`` and ``delivery_info.address:read``, and the ``/prices``
endpoint documents a 403 for a missing scope
(https://doc.toasttab.com/toast-api-specifications/toast-orders-api.yaml,
https://doc.toasttab.com/doc/devguide/apiClientAccounts.html). Restaurants opt
in to a partner individually, so the scope set is a property of the client
rather than of a token request; every token minted here inherits the client's
set, and a route declares the scopes it requires.

JUDGMENT -- **the scope names the specification does not print are this
project's.** ``orders:write``, ``menus:read``, ``config:read``,
``restaurants:read``, ``partners:read``, ``stock:read`` and ``stock:write``
follow the ``<api>:<verb>`` pattern of the five documented names and are not
Toast strings; a consumer must not pattern-match on them against the real API.

Like the other vendor configs, ``extra="forbid"``: an unknown key in a
profile's ``vendor`` block is a startup failure naming the key, never a default
silently left in place.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

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
"""This project's names for the scopes the specification does not print. See
the module docstring."""

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
"""A narrower set a scenario can hand a second client, so "403 on the write
path" is testable without minting anything. No ``guest.pi:read`` either: a
token without it sees no ``customer`` block on an order, which is the other
documented scope effect."""

_DOCUMENTED_EXPIRES_IN_S = 19168
"""``"expiresIn": 19168`` -- the one numeric lifetime Toast prints, in the
login example on https://doc.toasttab.com/doc/devguide/authentication.html."""


class ToastConfig(BaseModel):
    """The ``vendor`` block of a profile, resolved."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: Obviously fake. Toast documents the field names (``clientId``,
    #: ``clientSecret``) and no example values, so the shape is a readable
    #: stand-in a consumer can see is not real (JUDGMENT on the shape).
    client_id: str = "unit-toast-client-id"
    client_secret: str = "unit-toast-client-secret"

    #: The ``partner_guid`` claim every minted JWT carries -- "the JWT carries
    #: partner_guid or management_set_guid" (apiClientAccounts.html). A partner
    #: account, so it is the partner's guid. The value is this scenario's.
    partner_guid: str = "0f6c1b1e-0000-4000-8000-00000000a0a0"

    #: JUDGMENT -- the secret the fake signs its JWTs with (HS256). Toast's
    #: tokens are opaque to a consumer, who must never verify one locally; a
    #: readable fake secret keeps that true here while still producing a
    #: three-segment token a JWT-aware client can decode.
    jwt_signing_secret: str = "unit-toast-jwt-signing-secret"

    #: The scope set the partner client carries; every minted token inherits
    #: it. See the module docstring for the documented/JUDGMENT split.
    scopes: tuple[str, ...] = DEFAULT_SCOPES

    #: JUDGMENT -- Toast states no token lifetime as a rule; the login
    #: example's ``expiresIn`` is 19168 seconds (about 5.3 hours) and that
    #: one number is the whole evidence. There is no refresh flow
    #: ("refreshToken ... for internal use only"): a consumer logs in again.
    access_token_ttl_s: int = Field(default=_DOCUMENTED_EXPIRES_IN_S, gt=0)

    #: Emit the namespaced ``unit_error`` sidecar beside Toast's ErrorMessage.
    #: A deliberate deviation from Toast's wire format; see errors.py.
    error_sidecar: bool = True

    #: Emit ``Retry-After`` on a 429. Documented on
    #: https://doc.toasttab.com/doc/devguide/apiRateLimiting.html together
    #: with the three ``X-Toast-RateLimit-*`` headers; switchable because this
    #: unit's 429s are chaos-injected and the header value is JUDGMENT.
    retry_after_header: bool = True

    #: Accept ``http://`` callback URLs at the webhook subscription stand-in.
    #: Toast documents HTTPS with TLS 1.2+ as a hard requirement
    #: (https://doc.toasttab.com/doc/devguide/apiEndpointRequirements.html).
    #: JUDGMENT -- a fake-only affordance for a receiver on ``localhost`` with
    #: no certificate; leaving it off keeps the documented rule visible.
    allow_insecure_callbacks: bool = False

    #: "low_quantity ... when the quantity is 5 or less (currently 5)"
    #: (https://doc.toasttab.com/doc/devguide/apiStockWebhook.html). Held in
    #: the config because the page says "currently".
    low_quantity_threshold: float = Field(default=5.0, ge=0)

    def merged_with(self, block: Mapping[str, Any]) -> ToastConfig:
        """This config with ``block`` laid over it; an unknown key is still
        refused, because the merge revalidates rather than patching."""
        return ToastConfig.model_validate({**self.model_dump(), **dict(block)})

    @property
    def access_token_ttl(self) -> timedelta:
        return timedelta(seconds=self.access_token_ttl_s)

    @property
    def access_token_ttl_ms(self) -> int:
        return self.access_token_ttl_s * 1000


def resolve_toast_config(raw: dict[str, Any] | None = None) -> ToastConfig:
    """Build the config from a profile's ``vendor`` block; an empty block is legal."""
    return ToastConfig.model_validate(raw or {})
