"""The Retailers tag: one route, one retailer.

DOCUMENTED (``GET /retailer``, operationId ``GetRetailer``, tag "Retailers"):
"This endpoint returns information about the retailer", answering
``RetailerResponse`` -- ``{"data": {...}}`` around one ``Retailer``. The
operation's description carries ``🔒 Requires: `retailer:read`
`payment_types:read` scopes``: **two** scopes, which is unusual enough to be
worth stating -- most operations name one, and the machine extraction that
produced ``surface.txt`` matched none for this operation at all because its
annotation names a pair. Both are required here, so a token holding only
``retailer:read`` gets the 403.

A unit serves exactly ONE retailer -- tenancy in this API is the per-retailer
subdomain (``{domain_prefix}.retail.lightspeed.app``) and there is no route
that takes a retailer id. So this route reads the single seeded row and never
takes a path parameter.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route
from vendorfake.lightspeed.config import SCOPE_PAYMENT_TYPES_READ, SCOPE_RETAILER_READ
from vendorfake.lightspeed.model.retailer import project_retailer
from vendorfake.lightspeed.paths import GET_RETAILER
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, require_retailer
from vendorfake.lightspeed.versioning import single

__all__ = ["CAPABILITY", "LightspeedRetailerSurface", "retailer_routes"]

CAPABILITY = "retailer"


class LightspeedRetailerSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=GET_RETAILER,
                capability=CAPABILITY,
                handler=self.get_retailer,
                auth=BEARER_AUTH,
                scopes=(SCOPE_RETAILER_READ, SCOPE_PAYMENT_TYPES_READ),
                operation_id="GetRetailer",
                summary="Information about this retailer: currency, timezone, country and domain prefix.",
            ),
        )

    def get_retailer(self, args: HandlerArgs) -> ReplyInit:
        return json_(single(project_retailer(require_retailer(args.ctx))))


def retailer_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedRetailerSurface(deps).routes()
