"""The inventory surface, and the merchant record beside it.

FOR: the reference data an order points at -- items by id, and the merchant
every path is scoped to.

===============  ===============================================================
GetMerchant      ``GET  /v3/merchants/{mId}``
                 https://docs.clover.com/dev/docs/merchantgetmerchant
CreateItem       ``POST /v3/merchants/{mId}/items``
                 https://docs.clover.com/dev/docs/inventorycreateitem
GetItems         ``GET  /v3/merchants/{mId}/items``
                 https://docs.clover.com/dev/docs/inventorygetitems
GetItem          ``GET  /v3/merchants/{mId}/items/{itemId}``
===============  ===============================================================

Documented behaviour reproduced here: create requires ``name`` and ``price``
(a missing one is a 400 naming the field); the response carries the fields of
the verbatim create example, with the defaults labelled on
``model/inventory.py``; the list is the ``{"elements": [...]}`` envelope with
``limit`` (default 100, max 1000) and ``offset``.

JUDGMENT, labelled at its site: the merchant record lives under the
``inventory`` capability rather than one of its own -- it is a single
read-only route over the same reference data, and a one-route capability
would exist only to be switched off. The 404 body for an unknown item is this
package's envelope; Clover documents none.
"""

from __future__ import annotations

from vendorfake.clover.entities import COL, ItemEntity, MerchantEntity
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.inventory import ItemCreateRequest, project_item
from vendorfake.clover.surface.common import CloverDeps, elements, page_window, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverInventorySurface", "inventory_routes"]

CAPABILITY = "inventory"
"""The capability every route below belongs to, the merchant read included."""


class CloverInventorySurface:
    """The three item routes and the merchant read, bound to one vendor."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/v3/merchants/{mId}",
                capability=CAPABILITY,
                handler=self.get_merchant,
                auth="bearer",
                scopes=("MERCHANT_R",),
                operation_id="GetMerchant",
                summary="The merchant record: id, name, owner and address.",
            ),
            Route(
                method="POST",
                path="/v3/merchants/{mId}/items",
                capability=CAPABILITY,
                handler=self.create_item,
                auth="bearer",
                scopes=("INVENTORY_W",),
                operation_id="CreateItem",
                summary="Create an inventory item; name and price are required.",
                example_body={"name": "Craft Beer", "price": 750},
            ),
            Route(
                method="GET",
                path="/v3/merchants/{mId}/items",
                capability=CAPABILITY,
                handler=self.list_items,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetItems",
                summary="Inventory items, offset-paginated in the elements envelope.",
            ),
            Route(
                method="GET",
                path="/v3/merchants/{mId}/items/{itemId}",
                capability=CAPABILITY,
                handler=self.get_item,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetItem",
                summary="One inventory item by id.",
            ),
        )

    # -- GET /v3/merchants/{mId} -------------------------------------------

    def get_merchant(self, args: HandlerArgs) -> ReplyInit:
        """``id``, ``name``, ``owner{...}`` and ``address{...}`` from the store.

        The nested documents are emitted as stored: the merchant reference
        lists both as objects with undocumented contents, and this unit adds
        no shape of its own beyond what the scenario seeded.
        """
        merchant_id = require_merchant(args)
        stored = args.ctx.store.collection(COL.merchants).get(merchant_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=f"The bearer resolved to merchant {merchant_id}, which is not in the store.",
            )
        merchant = MerchantEntity.from_entity(stored)
        return json_(
            compact({"id": merchant.id, "name": merchant.name, "owner": merchant.owner, "address": merchant.address})
        )

    # -- POST /v3/merchants/{mId}/items ------------------------------------

    def create_item(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        request = validate_body(ItemCreateRequest, args.body())
        defaults = ItemEntity(id="", name="", price=0)
        entity = ItemEntity(
            id=self._deps.ids.item(),
            name=request.name,
            price=request.price,
            hidden=defaults.hidden if request.hidden is None else request.hidden,
            available=defaults.available if request.available is None else request.available,
            priceType=defaults.priceType if request.priceType is None else request.priceType.value,
            defaultTaxRates=defaults.defaultTaxRates if request.defaultTaxRates is None else request.defaultTaxRates,
            isRevenue=defaults.isRevenue if request.isRevenue is None else request.isRevenue,
            sku=request.sku,
            code=request.code,
            modifiedTime=int(args.ctx.clock.now()),
        )
        stored = args.ctx.store.collection(COL.items).insert(entity.to_entity(), {"operation_id": "CreateItem"})
        return json_(project_item(stored))

    # -- GET /v3/merchants/{mId}/items -------------------------------------

    def list_items(self, args: HandlerArgs) -> ReplyInit:
        """Offset/limit over insertion order, which is stable and therefore
        overlap-free between pages."""
        merchant_id = require_merchant(args)
        limit, offset = page_window(args)
        items = args.ctx.store.collection(COL.items).all()[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [project_item(item) for item in items],
                [f"{base}/v3/merchants/{merchant_id}/items/{item['id']}" for item in items],
            )
        )

    # -- GET /v3/merchants/{mId}/items/{itemId} ----------------------------

    def get_item(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        item_id = args.params["itemId"]
        stored = args.ctx.store.collection(COL.items).get(item_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Item {item_id} was not found.", field="itemId")
        return json_(project_item(stored))


def inventory_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The inventory and merchant routes for one vendor."""
    return CloverInventorySurface(deps).routes()
