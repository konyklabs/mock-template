"""The shapes this vendor stores, and the collections it stores them in.

FOR: giving the surfaces one typed reading of every stored entity, so that the
name of a stored field is written down once instead of being spelled as a
dictionary key in each handler that touches it.

INVARIANT: **absence is absence.** A field never set is *missing* from the
entity dict, never present as ``None``: every :meth:`to_entity` drops unset
optionals through the core's ``compact()``, and a field is cleared with
``pop`` and never with ``= None``. The entity digest, the journal's
``changed`` list and the wire projections all depend on it, exactly as in the
Square package.

Time, in this package's entities
--------------------------------
Every stored instant is **epoch milliseconds**, matching both the core clock
(``Clock.now()`` is ms) and Clover's own entity timestamps (``createdTime``,
``modifiedTime``, ``ts`` are documented ms). The two OAuth expirations are
stored as ``access_token_expiration_ms`` / ``refresh_token_expiration_ms`` --
the ``_ms`` suffix is load-bearing, because the *wire* fields they project to
(``access_token_expiration``, documented Unix **seconds**) differ by a factor
of 1000 and an unsuffixed name would invite exactly that bug. The one
conversion lives in ``surface/common.py``'s :func:`~.common.wire_seconds`.

The stored model is this unit's own; internal bookkeeping fields
(``used_at_ms``, ``refresh_used_at_ms``) are snake_case like the Square
package's, while fields Clover itself names keep Clover's camelCase.

There is deliberately no ``revoked_at``: Clover's v2 OAuth documents no revoke
endpoint at all (the audit found authorize, token, refresh and the unmodelled
migrate), so a revocation state would be a state nothing can enter. A rotated
refresh token is recorded with ``refresh_used_at_ms`` instead -- "Refresh
token is for single use and becomes invalid immediately after a new
access_token and refresh_token pair is generated"
(https://docs.clover.com/dev/docs/refresh-access-tokens) -- and the *access*
token on that record stays valid until its own expiry (JUDGMENT: the docs are
silent on prior access tokens, and inventing revocation teaches consumers an
invalidation rule Clover does not publish).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = [
    "COL",
    "AuthorizationCodeEntity",
    "CloverCollections",
    "MerchantEntity",
    "TokenEntity",
]


@dataclass(frozen=True, slots=True)
class CloverCollections:
    """The store collections this vendor uses, named once.

    Orders and items join in PR C; the seed scenario in PR E.
    """

    merchants: str = "merchants"
    codes: str = "authorization_codes"
    tokens: str = "tokens"

    def names(self) -> tuple[str, ...]:
        """Every collection name, in declaration order."""
        return (self.merchants, self.codes, self.tokens)


COL = CloverCollections()
"""The one place a collection name is spelled."""


# ---------------------------------------------------------------------------
# Readers. Tolerant on type, strict on presence: a stored entity is produced by
# this package, so a wrong type is a defect here rather than bad input, and
# coercing it quietly beats raising from inside a projection.
# ---------------------------------------------------------------------------


def _str(value: Any, default: str = "") -> str:
    return default if value is None else str(value)


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _int(value: Any, default: int = 0) -> int:
    return default if not isinstance(value, int) or isinstance(value, bool) else value


def _opt_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(str(item) for item in value)
    return ()


@dataclass(frozen=True, slots=True)
class MerchantEntity:
    """The merchant this unit represents. One per unit, in practice.

    PR B needs only the identity the authorize redirect carries; PR C's
    merchant surface and PR E's seed extend this with the owner and address.
    """

    id: str
    name: str

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> MerchantEntity:
        return cls(id=_str(entity["id"]), name=_str(entity.get("name")))

    def to_entity(self) -> Entity:
        return {"id": self.id, "name": self.name}


@dataclass(frozen=True, slots=True)
class AuthorizationCodeEntity:
    """An issued authorization code. ``id`` is the opaque code value itself.

    Single-use with a ten-minute expiry -- both JUDGMENT: Clover documents
    neither a code lifetime nor its reuse behaviour (the high-trust flow page
    shows only the redirect carrying ``code``); single-use is RFC 6749's own
    rule and the TTL is :attr:`~vendorfake.clover.config.CloverConfig.authorization_code_ttl_ms`.
    ``code_challenge`` is set when the authorize request carried one, which is
    what routes the exchange down the PKCE path.
    """

    id: str
    client_id: str
    merchant_id: str
    #: Epoch ms; the instant itself is already too late (see `is_past_ms`).
    expires_at_ms: int
    code_challenge: str | None = None
    used_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> AuthorizationCodeEntity:
        return cls(
            id=_str(entity["id"]),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            expires_at_ms=_int(entity.get("expires_at_ms")),
            code_challenge=_opt_str(entity.get("code_challenge")),
            used_at_ms=_opt_int(entity.get("used_at_ms")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "expires_at_ms": self.expires_at_ms,
                "code_challenge": self.code_challenge,
                "used_at_ms": self.used_at_ms,
            }
        )


@dataclass(frozen=True, slots=True)
class TokenEntity:
    """One issued access token and the refresh token that came with it.

    ``refresh_used_at_ms`` is the single-use rotation mark: set when this
    record's refresh token is exchanged for a new pair, at which point the
    refresh token is dead and the access token lives on to its own expiry.
    See the module docstring for the provenance of both halves.
    """

    id: str
    access_token: str
    refresh_token: str
    client_id: str
    merchant_id: str
    #: Epoch ms. Projected to the documented Unix-seconds wire fields by
    #: ``surface/common.py``; the ``_ms`` suffix is why nobody mixes them up.
    access_token_expiration_ms: int
    refresh_token_expiration_ms: int
    permissions: tuple[str, ...] = ()
    createdTime: int | None = None
    refresh_used_at_ms: int | None = None

    @classmethod
    def from_entity(cls, entity: Mapping[str, Any]) -> TokenEntity:
        return cls(
            id=_str(entity["id"]),
            access_token=_str(entity.get("access_token")),
            refresh_token=_str(entity.get("refresh_token")),
            client_id=_str(entity.get("client_id")),
            merchant_id=_str(entity.get("merchant_id")),
            access_token_expiration_ms=_int(entity.get("access_token_expiration_ms")),
            refresh_token_expiration_ms=_int(entity.get("refresh_token_expiration_ms")),
            permissions=_str_tuple(entity.get("permissions")),
            createdTime=_opt_int(entity.get("createdTime")),
            refresh_used_at_ms=_opt_int(entity.get("refresh_used_at_ms")),
        )

    def to_entity(self) -> Entity:
        return compact(
            {
                "id": self.id,
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "client_id": self.client_id,
                "merchant_id": self.merchant_id,
                "access_token_expiration_ms": self.access_token_expiration_ms,
                "refresh_token_expiration_ms": self.refresh_token_expiration_ms,
                "permissions": list(self.permissions),
                "createdTime": self.createdTime,
                "refresh_used_at_ms": self.refresh_used_at_ms,
            }
        )
