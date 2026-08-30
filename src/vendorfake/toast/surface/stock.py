"""The stock API surface: what is out, what is counted, and updating either.

DOCUMENTED (toast-stock-api.yaml, apiUsingTheStockApi.html):

===========  =======================================================
Inventory    ``GET /stock/v1/inventory[?status=OUT_OF_STOCK|QUANTITY]`` --
             "ignores menu items that have a stock status of IN_STOCK"
Search       ``POST /stock/v1/inventory/search`` -- returns IN_STOCK too
Update       ``PUT /stock/v1/inventory/update`` -- an array of items by
             ``guid`` or ``multiLocationId``; ``QUANTITY`` needs
             ``quantity`` > 0; IN_STOCK / OUT_OF_STOCK must carry none
===========  =======================================================

All three require ``Toast-Restaurant-External-ID``. Modifier options have
stock rows of their own ("Modifiers are backed by the modifier's item
reference").

JUDGMENT, labelled (audit gap 4): **an order does not decrement a QUANTITY**
-- the page says ordering on the POS does, and nothing about the API -- and
**ordering an OUT_OF_STOCK item is refused with a 400 naming the item**
(``model/build.py``); a quantity reaching 0 through this API is refused
rather than auto-flipped (``quantity`` > 0 is the documented rule).
``PUT`` answers the updated rows (the page documents only the codes). An
update naming an unknown item is a 400 naming its index; a search naming one
answers an ``INVALID`` row (``model/stock.py``). ``versionId`` never changes:
"for future use".
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.state.store import Entity
from vendorfake.toast.entities import COL
from vendorfake.toast.model.common import validate_body, validate_items
from vendorfake.toast.model.stock import (
    STATUSES,
    StockSearchRequest,
    StockUpdateRequest,
    invalid_stock_row,
    project_stock,
)
from vendorfake.toast.surface.common import RESTAURANT_AUTH, ToastDeps, now_ms, require_restaurant

__all__ = ["CAPABILITY", "ToastStockSurface", "stock_routes"]

CAPABILITY = "stock"


class ToastStockSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/stock/v1/inventory",
                capability=CAPABILITY,
                handler=self.inventory,
                auth=RESTAURANT_AUTH,
                scopes=("stock:read",),
                operation_id="StockInventoryGet",
                summary="Every OUT_OF_STOCK or QUANTITY item (IN_STOCK omitted); ?status narrows to one.",
            ),
            Route(
                method="POST",
                path="/stock/v1/inventory/search",
                capability=CAPABILITY,
                handler=self.search,
                auth=RESTAURANT_AUTH,
                scopes=("stock:read",),
                operation_id="StockInventorySearch",
                summary="Stock for the named guids / multiLocationIds, IN_STOCK included; unknown ones are INVALID.",
            ),
            Route(
                method="PUT",
                path="/stock/v1/inventory/update",
                capability=CAPABILITY,
                handler=self.update,
                auth=RESTAURANT_AUTH,
                scopes=("stock:write",),
                operation_id="StockInventoryUpdate",
                summary="Set IN_STOCK, OUT_OF_STOCK or QUANTITY (with quantity > 0) on items.",
            ),
        )

    def inventory(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        wanted = args.query("status")
        if wanted is not None and wanted not in ("OUT_OF_STOCK", "QUANTITY"):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="status must be OUT_OF_STOCK or QUANTITY; IN_STOCK items are never listed here.",
                field="status",
                info={"supplied": wanted},
            )
        rows = [
            project_stock(row)
            for row in args.ctx.store.collection(COL.stock).all()
            if row.get("restaurant_guid") == restaurant.id
            and row.get("status") != "IN_STOCK"
            and (wanted is None or row.get("status") == wanted)
        ]
        return json_(rows)

    def search(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        request = validate_body(StockSearchRequest, args.body())
        stock = args.ctx.store.collection(COL.stock)
        by_multi = {
            str(row.get("multiLocationId")): row for row in stock.all() if row.get("restaurant_guid") == restaurant.id
        }
        answer: list[dict[str, Any]] = []
        for guid in request.guids:
            row = stock.get(guid)
            answer.append(
                project_stock(row)
                if row is not None and row.get("restaurant_guid") == restaurant.id
                else invalid_stock_row(guid, None)
            )
        for multi in request.multiLocationIds:
            row = by_multi.get(multi)
            answer.append(project_stock(row) if row is not None else invalid_stock_row(None, multi))
        return json_(answer)

    def update(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        requests = validate_items(StockUpdateRequest, args.json(), what="inventory items")
        stock = ctx.store.collection(COL.stock)
        by_multi = {
            str(row.get("multiLocationId")): row for row in stock.all() if row.get("restaurant_guid") == restaurant.id
        }
        targets: list[tuple[Entity, StockUpdateRequest]] = []
        for index, request in enumerate(requests):
            row = None
            if request.guid is not None:
                row = stock.get(request.guid)
                if row is not None and row.get("restaurant_guid") != restaurant.id:
                    row = None
            elif request.multiLocationId is not None:
                row = by_multi.get(request.multiLocationId)
            else:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail="Each item needs a guid or a multiLocationId.",
                    field=f"[{index}].guid",
                )
            if row is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"[{index}] names no menu item of this restaurant.",
                    field=f"[{index}].guid" if request.guid is not None else f"[{index}].multiLocationId",
                )
            if request.status == "QUANTITY":
                if request.quantity is None or request.quantity <= 0:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail="A QUANTITY status requires a quantity greater than 0.",
                        field=f"[{index}].quantity",
                    )
            elif request.quantity is not None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Do not include a quantity value for a {request.status} item.",
                    field=f"[{index}].quantity",
                )
            targets.append((row, request))
        now = now_ms(ctx)
        updated: list[dict[str, Any]] = []
        for row, request in targets:
            status = request.status
            quantity = float(request.quantity) if status == "QUANTITY" and request.quantity is not None else None

            def mutate(draft: Entity, status: str = status, quantity: float | None = quantity) -> None:
                draft["status"] = status
                if quantity is None:
                    draft.pop("quantity", None)
                else:
                    draft["quantity"] = quantity
                draft["modifiedDate"] = now

            updated.append(
                project_stock(stock.update(str(row["id"]), mutate, meta={"operation_id": "StockInventoryUpdate"}))
            )
        return json_(updated)

    @staticmethod
    def statuses() -> tuple[str, ...]:
        return STATUSES


def stock_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastStockSurface(deps).routes()
