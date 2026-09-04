"""Who a presented credential is: the bearer, and the restaurant it is acting for.

Turns an ``Authorization`` header, and on restaurant-scoped routes the
``Toast-Restaurant-External-ID`` header, into an
:class:`~vendorfake.core.kernel.types.AuthResult` the kernel can check
required scopes against.

DOCUMENTED: ``Authorization: Bearer <accessToken>`` on every call
(https://doc.toasttab.com/doc/devguide/authentication.html); 401 for an invalid/expired token
(apiResponsesAndErrors.html); 403 for a missing scope (``POST
/orders/v2/prices``, toast-orders-api.yaml) -- the kernel raises
``forbidden_scope`` and the error table maps it, so this adapter only reports
scopes.

DOCUMENTED: ``Toast-Restaurant-External-ID`` "cannot be the GUID of a
restaurant group" (apiOrdersGetDetailedInfoAboutOneOrder.html). ``bearer``
mode covers the routes that are not restaurant-scoped (partners,
restaurants); ``restaurant`` mode covers everything else; both are published
at ``GET /__unit/auth``.

JUDGMENT: a missing restaurant header is 400 (malformed request); an unknown
or management-group guid is 404, following ``POST /orders``' documented 404
for a missing referenced entity. The bearer is checked first, so a bad token
is a 401 regardless.

The token is looked up as an opaque string; a token this unit never minted is
unknown however well it is JWT-signed (``jwt.py``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from vendorfake.core.kernel.types import (
    AuthCredential,
    AuthMode,
    AuthResult,
    HandlerArgs,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.toast.entities import COL, RestaurantEntity, TokenEntity
from vendorfake.toast.surface.common import (
    BEARER_AUTH,
    RESTAURANT_AUTH,
    RESTAURANT_HEADER,
    RESTAURANT_META_KEY,
    ToastDeps,
    is_past_ms,
)

__all__ = ["BEARER_SCHEME", "ToastAuth"]

BEARER_SCHEME = "Bearer"


def _split_scheme(header: str) -> tuple[str, str]:
    scheme, separator, credential = header.partition(" ")
    return (scheme, credential.strip() if separator else "")


class ToastAuth:
    """Toast's authentication. Satisfies ``AuthAdapter``."""

    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def describe(self) -> Mapping[str, str]:
        return {
            "bearer": "Authorization: Bearer {accessToken} from POST /authentication/v1/authentication/login",
            "restaurant": (
                f"{RESTAURANT_HEADER}: {{restaurantGuid}} on every restaurant-scoped route; "
                "'It cannot be the GUID of a restaurant group.'"
            ),
            "scopes": " ".join(self._deps.config.scopes),
            "refusals": "401 token missing/invalid/expired; 403 missing scope; 400 header missing; 404 unknown restaurant.",
        }

    def credentials(self, ctx: UnitContext) -> Sequence[AuthCredential]:
        """Every live token, in both modes, read from the store so a just-minted
        token is offered and an expired one is not."""
        restaurant = _the_restaurant(ctx)
        offered: list[AuthCredential] = []
        for entity in ctx.store.collection(COL.tokens).all():
            token = TokenEntity.from_entity(entity)
            if is_past_ms(token.expires_at_ms, ctx.clock):
                continue
            bearer = {"authorization": f"{BEARER_SCHEME} {token.access_token}"}
            offered.append(
                AuthCredential(
                    label=token.id,
                    mode=BEARER_AUTH,
                    headers=bearer,
                    scopes=token.scopes,
                    summary=f"Partner token for client {token.client_id}; no restaurant header.",
                )
            )
            if restaurant is not None:
                offered.append(
                    AuthCredential(
                        label=f"{token.id}@{restaurant.id}",
                        mode=RESTAURANT_AUTH,
                        headers={**bearer, RESTAURANT_HEADER.lower(): restaurant.id},
                        scopes=token.scopes,
                        summary=f"The same token acting for restaurant {restaurant.id} ({restaurant.name}).",
                    )
                )
        return tuple(offered)

    def resolve(self, args: HandlerArgs, mode: AuthMode) -> AuthResult:
        header = args.header("authorization")
        if not header:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="An Authorization: Bearer header is required.",
                info={"reason": "no_authorization_header"},
            )
        scheme, value = _split_scheme(header)
        if scheme != BEARER_SCHEME or not value:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The Authorization header must use the Bearer scheme.",
                info={"reason": "not_a_bearer_header"},
            )
        found = args.ctx.store.collection(COL.tokens).find(lambda entity: entity.get("access_token") == value)
        if found is None:
            raise UnitError(
                UnitErrorKind.UNAUTHORIZED,
                detail="The access token is not valid.",
                info={"reason": "unknown_token"},
            )
        token = TokenEntity.from_entity(found)
        if is_past_ms(token.expires_at_ms, args.ctx.clock):
            raise UnitError(
                UnitErrorKind.TOKEN_EXPIRED,
                detail="The access token has expired; log in again.",
                info={"reason": "access_token_expired"},
            )
        meta: dict[str, object] = {"client_id": token.client_id}
        if mode == RESTAURANT_AUTH:
            meta[RESTAURANT_META_KEY] = self._restaurant_guid(args)
        return AuthResult(principal_id=token.partner_guid, scopes=token.scopes, token_id=token.id, meta=meta)

    def _restaurant_guid(self, args: HandlerArgs) -> str:
        """The documented header, resolved to a restaurant of this unit."""
        raw = args.header(RESTAURANT_HEADER)
        if raw is None or not raw.strip():
            raise UnitError(
                UnitErrorKind.BAD_REQUEST,
                detail=f"The {RESTAURANT_HEADER} header is required on this endpoint.",
                field=RESTAURANT_HEADER,
                info={"reason": "restaurant_header_missing"},
            )
        guid = raw.strip()
        store = args.ctx.store
        if store.collection(COL.restaurants).get(guid) is None:
            groups = {
                RestaurantEntity.from_entity(entity).management_group_guid
                for entity in store.collection(COL.restaurants).all()
            }
            reason = "restaurant_group_guid" if guid in groups else "unknown_restaurant"
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=(
                    f"{RESTAURANT_HEADER} {guid} is a restaurant group; it cannot be the GUID of a restaurant group."
                    if reason == "restaurant_group_guid"
                    else f"{RESTAURANT_HEADER} {guid} is not a restaurant connected to this client."
                ),
                field=RESTAURANT_HEADER,
                info={"reason": reason},
            )
        return guid


def _the_restaurant(ctx: UnitContext) -> RestaurantEntity | None:
    rows = ctx.store.collection(COL.restaurants).all()
    return RestaurantEntity.from_entity(rows[0]) if rows else None
