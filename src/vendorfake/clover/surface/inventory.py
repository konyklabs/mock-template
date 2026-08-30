"""The inventory surface: items, their expansions, and modifiers.

FOR: the reference data a line item points at -- items by id, with the tax
rates and modifier groups a consumer needs to price and customise them.

=================  ==============================================================
CreateItem         ``POST /v3/merchants/{mId}/items``
                   https://docs.clover.com/dev/reference/inventorycreateitem
GetItems           ``GET  /v3/merchants/{mId}/items``
                   https://docs.clover.com/dev/reference/inventorygetitems
GetItem            ``GET  /v3/merchants/{mId}/items/{itemId}``
UpdateItem         ``POST /v3/merchants/{mId}/items/{itemId}``
                   https://docs.clover.com/dev/reference/inventoryupdateitem
GetModifiers       ``GET  /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers``
                   https://docs.clover.com/dev/reference/modifiergetmodifiersbygroup
GetModifier        ``GET  /v3/merchants/{mId}/modifier_groups/{modGroupId}/modifiers/{modId}``
                   https://docs.clover.com/dev/docs/manage-item-modifiers-availability
=================  ==============================================================

Documented behaviour reproduced here: create requires ``name`` and ``price``
(a missing one is a 400 naming the field); the response carries the fields of
the verbatim create example with the defaults labelled on
``model/inventory.py``; update is ``POST`` and sparse; the list is the
``{"elements": [...]}`` envelope with ``limit`` (default 100, max 1000) and
``offset``; ``expand=modifierGroups`` shows an item's groups as
``modifierGroups.elements[]`` -- the exact call a menu-sync consumer makes;
a modifier carries ``available`` ("True (default) -- the modifier stock is
available") and ``price`` in cents.

JUDGMENT, labelled at its site: the 404 bodies are this package's envelope
(Clover documents none); modifier groups and modifiers are read-only seed
data here (``CLOVER_NOT_MODELED``).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.clover.entities import COL, ItemEntity
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.inventory import ITEM_EXPANDABLE, ItemCreateRequest, ItemPatchRequest, project_item
from vendorfake.clover.surface.common import (
    CloverDeps,
    elements,
    expansions,
    page_window,
    require_merchant,
)
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverInventorySurface", "inventory_routes", "item_tax_rates"]

CAPABILITY = "inventory"
"""The capability every route below belongs to."""


class CloverInventorySurface:
    """The item and modifier routes, bound to one vendor."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        base = "/v3/merchants/{mId}"
        return (
            Route(
                method="POST",
                path=f"{base}/items",
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
                path=f"{base}/items",
                capability=CAPABILITY,
                handler=self.list_items,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetItems",
                summary="Inventory items, offset-paginated in the elements envelope; expand=modifierGroups,taxRates.",
            ),
            Route(
                method="GET",
                path=f"{base}/items/{{itemId}}",
                capability=CAPABILITY,
                handler=self.get_item,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetItem",
                summary="One inventory item by id, with the requested expansions.",
            ),
            Route(
                method="POST",
                path=f"{base}/items/{{itemId}}",
                capability=CAPABILITY,
                handler=self.update_item,
                auth="bearer",
                scopes=("INVENTORY_W",),
                operation_id="UpdateItem",
                summary="Sparse update of an item (POST); flipping `available` is the sold-out path.",
            ),
            Route(
                method="GET",
                path=f"{base}/modifier_groups/{{modGroupId}}/modifiers",
                capability=CAPABILITY,
                handler=self.list_modifiers,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetModifiers",
                summary="The modifiers of one modifier group.",
            ),
            Route(
                method="GET",
                path=f"{base}/modifier_groups/{{modGroupId}}/modifiers/{{modId}}",
                capability=CAPABILITY,
                handler=self.get_modifier,
                auth="bearer",
                scopes=("INVENTORY_R",),
                operation_id="GetModifier",
                summary="One modifier, with its `available` flag.",
            ),
        )

    # -- items ---------------------------------------------------------------

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
        return json_(self._project(args.ctx, stored, frozenset()))

    def list_items(self, args: HandlerArgs) -> ReplyInit:
        """Offset/limit over insertion order, which is stable and therefore
        overlap-free between pages."""
        merchant_id = require_merchant(args)
        expand = expansions(args, ITEM_EXPANDABLE)
        limit, offset = page_window(args)
        items = args.ctx.store.collection(COL.items).all()[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [self._project(args.ctx, item, expand) for item in items],
                [f"{base}/v3/merchants/{merchant_id}/items/{item['id']}" for item in items],
            )
        )

    def get_item(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        expand = expansions(args, ITEM_EXPANDABLE)
        return json_(self._project(args.ctx, _require_item(args), expand))

    def update_item(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        request = validate_body(ItemPatchRequest, args.body())
        current = _require_item(args)
        now = int(args.ctx.clock.now())

        def mutate(draft: Entity) -> None:
            for name in request.model_fields_set:
                value = getattr(request, name)
                if value is None:
                    draft.pop(name, None)
                elif name == "priceType":
                    draft[name] = value.value
                else:
                    draft[name] = value
            draft["modifiedTime"] = now

        updated = args.ctx.store.collection(COL.items).update(
            str(current["id"]), mutate, meta={"operation_id": "UpdateItem"}
        )
        return json_(self._project(args.ctx, updated, frozenset()))

    # -- modifiers -----------------------------------------------------------

    def list_modifiers(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        group_id = _require_group(args)
        modifiers = args.ctx.store.collection(COL.modifiers).filter(lambda entity: _group_of(entity) == group_id)
        base = self._deps.config.base_url
        return json_(
            elements(
                [_project_modifier(entity) for entity in modifiers],
                [
                    f"{base}/v3/merchants/{merchant_id}/modifier_groups/{group_id}/modifiers/{entity['id']}"
                    for entity in modifiers
                ],
            )
        )

    def get_modifier(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        group_id = _require_group(args)
        modifier_id = args.params["modId"]
        stored = args.ctx.store.collection(COL.modifiers).get(modifier_id)
        if stored is None or _group_of(stored) != group_id:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Modifier {modifier_id} was not found in modifier group {group_id}.",
                field="modId",
            )
        return json_(_project_modifier(stored))

    # -- shared --------------------------------------------------------------

    def _project(self, ctx: UnitContext, entity: Mapping[str, Any], expand: frozenset[str]) -> dict[str, Any]:
        item = ItemEntity.from_entity(entity)
        groups: list[Mapping[str, Any]] = []
        if "modifierGroups" in expand:
            collection = ctx.store.collection(COL.modifier_groups)
            groups = [g for g in (collection.get(gid) for gid in item.modifierGroupIds) if g is not None]
        rates: Sequence[Mapping[str, Any]] = item_tax_rates(ctx, item) if "taxRates" in expand else ()
        return project_item(entity, expand, modifier_groups=groups, tax_rates=rates)


def inventory_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The inventory routes for one vendor."""
    return CloverInventorySurface(deps).routes()


def item_tax_rates(ctx: UnitContext, item: ItemEntity) -> list[dict[str, Any]]:
    """The tax rates that apply to an item: the merchant's defaults when
    ``defaultTaxRates`` is true, its explicit associations otherwise
    (https://docs.clover.com/dev/reference/taxratecreateordeletetaxrateitems)."""
    collection = ctx.store.collection(COL.tax_rates)
    if item.defaultTaxRates:
        return [dict(rate) for rate in collection.filter(lambda entity: entity.get("isDefault") is True)]
    found = (collection.get(str(ref.get("id"))) for ref in item.taxRates)
    return [dict(rate) for rate in found if rate is not None]


def _require_item(args: HandlerArgs) -> Entity:
    item_id = args.params["itemId"]
    stored = args.ctx.store.collection(COL.items).get(item_id)
    if stored is None:
        raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Item {item_id} was not found.", field="itemId")
    return stored


def _require_group(args: HandlerArgs) -> str:
    group_id = args.params["modGroupId"]
    if args.ctx.store.collection(COL.modifier_groups).get(group_id) is None:
        raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Modifier group {group_id} was not found.", field="modGroupId")
    return group_id


def _group_of(modifier: Mapping[str, Any]) -> str | None:
    group = modifier.get("modifierGroup")
    return str(group.get("id")) if isinstance(group, Mapping) and group.get("id") is not None else None


def _project_modifier(entity: Mapping[str, Any]) -> dict[str, Any]:
    """``{id, name, price, available, modifierGroup{id}}`` -- the documented
    fields of a modifier (modifiergetmodifiersbygroup)."""
    return compact(
        {
            "id": entity.get("id"),
            "name": entity.get("name"),
            "alternateName": entity.get("alternateName"),
            "price": entity.get("price"),
            "available": entity.get("available", True),
            "modifierGroup": entity.get("modifierGroup"),
        }
    )
