"""Retailers tag: one route, one retailer.

DOCUMENTED (``GET /retailer``, ``GetRetailer``): answers ``{"data": {...}}``
around one ``Retailer``; requires both ``retailer:read`` and
``payment_types:read`` scopes, so a token holding only the first gets a 403.

A unit serves exactly one retailer -- tenancy is the per-retailer subdomain --
so this route takes no path parameter and reads the single seeded row.
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
