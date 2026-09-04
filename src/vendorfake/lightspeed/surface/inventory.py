"""The Inventory tag: four reads, and the batch that moves stock.

DOCUMENTED, six of the tag's ten operations:

===============================================  ================================  ===================
``POST /inventory``                              ``ListInventoryRecords``          ``inventory:read``
``GET /inventory/{product_id}``                  ``ListProductInventoryRecords``   ``inventory:read``
``POST /inventory_levels``                       ``ListInventoryLevels``           ``inventory:read``
``GET /inventory_levels/{product_id}``           ``ListProductInventoryLevels``    ``inventory:read``
``GET /stock_adjustments``                       ``ListStockAdjustments``          ``inventory:write``
``POST /stock_adjustments``                      ``CreateStockAdjustments``        ``inventory:write``
===============================================  ================================  ===================

``GET /stock_adjustments`` is gated on ``inventory:WRITE``. That is the
vendor's own annotation on a read operation, reproduced rather than corrected,
and it is why the scenario's read-only token cannot see the adjustment log.

The four left out are ``POST /inventory/reorder_points`` and the three
``custom_inventory_adjustment_reasons`` operations; ``capabilities.py`` records
why each.

**The two response shapes are different, and neither was chosen here.** The
four reads declare a bare JSON array; the adjustment list declares
``StockAdjustmentCollection``, which is the ``{"data": ..., "version": ...}``
envelope. See ``model/inventory.py`` for the whole note.

WHAT FIRES ``inventory.update``: any change to a row in the ``inventory``
collection. A stock adjustment moves a level, so it fires one per row moved --
never one per adjustment, because two adjustments against the same product and
outlet in one batch move one row twice and the journal has two entries for it.
Creating a product with an ``inventory`` payload fires them too
(``surface/products.py``). The adjustment log itself fires NOTHING: no value of
the documented ``WebhookType`` enum names a stock adjustment.

THE BATCH IS ALL-OR-NOTHING, and that is JUDGMENT. The operation is documented
as "Creates one or more stock adjustments in a single batch (1-1000 items per
request)" and says nothing about partial failure; ``StockAdjustmentBatchResponse``
has one member and no per-item status. So every item is validated before any is
applied, and a batch whose fifth item names an unknown product moves no stock
at all. The alternative -- committing four adjustments and then answering 422 --
would leave a consumer with no way to find out which four.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    PaginationSpec,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.lightspeed.config import SCOPE_INVENTORY_READ, SCOPE_INVENTORY_WRITE
from vendorfake.lightspeed.entities import (
    COL,
    AdjustmentReasonEntity,
    InventoryEntity,
    StockAdjustmentEntity,
)
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.inventory import (
    CUSTOM_REASON,
    MAX_ADJUSTMENTS_PER_BATCH,
    STOCK_ADJUSTMENT_REASONS,
    CreateStockAdjustmentItem,
    CreateStockAdjustmentsRequest,
    InventoryLevelsRequest,
    InventoryRequest,
    check_reason_sign,
    project_inventory,
    project_inventory_level,
    project_stock_adjustment,
)
from vendorfake.lightspeed.model.scalars import decimal_text
from vendorfake.lightspeed.paths import (
    CREATE_STOCK_ADJUSTMENTS,
    LIST_INVENTORY_LEVELS,
    LIST_INVENTORY_RECORDS,
    LIST_PRODUCT_INVENTORY_LEVELS,
    LIST_PRODUCT_INVENTORY_RECORDS,
    LIST_STOCK_ADJUSTMENTS,
)
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, require_retailer, stamp_version
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select, version_of

__all__ = [
    "BARE_ARRAY_PAGINATION",
    "CAPABILITY",
    "DEFAULT_PRODUCT_PAGE_SIZE",
    "MAX_PRODUCT_PAGE_SIZE",
    "LightspeedInventorySurface",
    "inventory_routes",
]

CAPABILITY = "inventory"

DEFAULT_PRODUCT_PAGE_SIZE = 1000
MAX_PRODUCT_PAGE_SIZE = 5000
"""DOCUMENTED on ``GET /inventory/{product_id}``'s ``page_size``:
``"default": 1000, "maximum": 5000``. This is the one list in the package whose
ceiling the vendor states, so it is the vendor's number rather than
``versioning.MAX_PAGE_SIZE``."""

_UNWALKABLE = (
    "The 200 body is a bare JSON array, not the {data, version} envelope, so there is no version.max in "
    "the response for a caller -- or this walk -- to read the next `after` out of; a caller pages by "
    "taking the highest `version` among the rows it received. Two of the four also take their page "
    "parameters in the request body under names of their own (`size`, `offset`), and an InventoryLevel "
    "row carries no id at all, so rows cannot be compared across pages either."
)

BARE_ARRAY_PAGINATION = PaginationSpec(
    style="cursor",
    items_path="",
    limit_param="page_size",
    cursor_param="after",
    next_cursor_path="",
    id_path="id",
    walkable=False,
    unwalkable_reason=_UNWALKABLE,
)
"""What the four bare-array inventory reads publish: they page, and the walk
cannot drive them. ``items_path`` is empty because the rows ARE the document.
Declared rather than omitted, so a paginating route is excused on the record."""


class LightspeedInventorySurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="POST",
                path=LIST_INVENTORY_RECORDS,
                capability=CAPABILITY,
                handler=self.list_inventory_records,
                auth=BEARER_AUTH,
                scopes=(SCOPE_INVENTORY_READ,),
                pagination=BARE_ARRAY_PAGINATION,
                operation_id="ListInventoryRecords",
                summary="A POST that READS: inventory records for this retailer, as a bare array.",
            ),
            Route(
                method="GET",
                path=LIST_PRODUCT_INVENTORY_RECORDS,
                capability=CAPABILITY,
                handler=self.list_product_inventory_records,
                auth=BEARER_AUTH,
                scopes=(SCOPE_INVENTORY_READ,),
                pagination=BARE_ARRAY_PAGINATION,
                operation_id="ListProductInventoryRecords",
                summary="One product's inventory records at every outlet, as a bare array.",
            ),
            Route(
                method="POST",
                path=LIST_INVENTORY_LEVELS,
                capability=CAPABILITY,
                handler=self.list_inventory_levels,
                auth=BEARER_AUTH,
                scopes=(SCOPE_INVENTORY_READ,),
                pagination=BARE_ARRAY_PAGINATION,
                operation_id="ListInventoryLevels",
                summary="A POST that READS: the denormalised InventoryLevel report, as a bare array.",
            ),
            Route(
                method="GET",
                path=LIST_PRODUCT_INVENTORY_LEVELS,
                capability=CAPABILITY,
                handler=self.list_product_inventory_levels,
                auth=BEARER_AUTH,
                scopes=(SCOPE_INVENTORY_READ,),
                pagination=BARE_ARRAY_PAGINATION,
                operation_id="ListProductInventoryLevels",
                summary="One product's InventoryLevel rows, as a bare array.",
            ),
            Route(
                method="GET",
                path=LIST_STOCK_ADJUSTMENTS,
                capability=CAPABILITY,
                handler=self.list_stock_adjustments,
                auth=BEARER_AUTH,
                # DOCUMENTED, and worth a second look: a READ gated on the
                # WRITE scope. The operation's own annotation says
                # `inventory:write`.
                scopes=(SCOPE_INVENTORY_WRITE,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListStockAdjustments",
                summary="The adjustment log, in the {data, version} envelope; gated on inventory:write.",
            ),
            Route(
                method="POST",
                path=CREATE_STOCK_ADJUSTMENTS,
                capability=CAPABILITY,
                handler=self.create_stock_adjustments,
                auth=BEARER_AUTH,
                scopes=(SCOPE_INVENTORY_WRITE,),
                operation_id="CreateStockAdjustments",
                summary="Move stock: 1-1000 adjustments, all or nothing, 201. Fires inventory.update per row.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_inventory_records(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(InventoryRequest, args.body() or {})
        rows = [
            row
            for row in args.ctx.store.collection(COL.inventory).all()
            if (request.include_deleted or row.get("deleted_at") is None)
            and (request.product_id is None or row.get("product_id") == request.product_id)
            and (request.variants or not self._is_variant(args.ctx, str(row.get("product_id"))))
        ]
        chosen = _window(
            rows,
            after=request.after,
            before=request.before,
            size=request.size,
            descending=_is_descending(request.sort_direction),
        )
        return json_([project_inventory(row) for row in chosen])

    def list_product_inventory_records(self, args: HandlerArgs) -> ReplyInit:
        product = self._require_product(args)
        include_deleted = _bool_query(args, "include_deleted")
        size = _page_size(args)
        rows = [
            row
            for row in args.ctx.store.collection(COL.inventory).all()
            if row.get("product_id") == product["id"] and (include_deleted or row.get("deleted_at") is None)
        ]
        if _bool_query(args, "variants"):
            rows.extend(self._variant_rows(args.ctx, str(product["id"]), include_deleted=include_deleted))
        chosen = _window(
            rows,
            after=_int_query(args, "after"),
            before=_int_query(args, "before"),
            size=size,
            descending=_is_descending(args.query("order_direction")),
        )
        return json_([project_inventory(row) for row in chosen])

    def list_inventory_levels(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(InventoryLevelsRequest, args.body() or {})
        products = {str(row["id"]): row for row in args.ctx.store.collection(COL.products).all()}
        rows: list[dict[str, Any]] = []
        for row in args.ctx.store.collection(COL.inventory).all():
            product = products.get(str(row.get("product_id")))
            if product is None or row.get("deleted_at") is not None:
                continue
            if not request.include_inactive and not _is_active(product):
                continue
            if request.location_ids and row.get("outlet_id") not in request.location_ids:
                continue
            if request.product_ids and row.get("product_id") not in request.product_ids:
                continue
            root = product.get("variant_parent_id") or product["id"]
            if request.root_product_ids and root not in request.root_product_ids:
                continue
            rows.append(row)
        rows.sort(key=version_of)
        offset = request.offset or 0
        window = rows[offset : None if request.size is None else offset + request.size]
        return json_([project_inventory_level(row, products[str(row["product_id"])]) for row in window])

    def list_product_inventory_levels(self, args: HandlerArgs) -> ReplyInit:
        product = self._require_product(args)
        include_inactive = _bool_query(args, "include_inactive")
        if not include_inactive and not _is_active(product):
            return json_([])
        rows = sorted(
            (
                row
                for row in args.ctx.store.collection(COL.inventory).all()
                if row.get("product_id") == product["id"] and row.get("deleted_at") is None
            ),
            key=version_of,
        )
        return json_([project_inventory_level(row, product) for row in rows])

    def list_stock_adjustments(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        rows = select(args.ctx.store.collection(COL.stock_adjustments).all(), query)
        return json_(envelope([project_stock_adjustment(row) for row in rows]))

    # -- writes -------------------------------------------------------------

    def create_stock_adjustments(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(CreateStockAdjustmentsRequest, args.body())
        if len(request.stock_adjustments) > MAX_ADJUSTMENTS_PER_BATCH:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"stock_adjustments takes at most {MAX_ADJUSTMENTS_PER_BATCH} items in one batch.",
                field="stock_adjustments",
                info={"supplied": len(request.stock_adjustments)},
            )
        planned = [self._plan(args.ctx, item, index) for index, item in enumerate(request.stock_adjustments)]
        user_id = str(require_retailer(args.ctx)["id"])
        adjustments = args.ctx.store.collection(COL.stock_adjustments)
        created: list[dict[str, Any]] = []
        for item, quantity in planned:
            self._move_stock(args.ctx, item, quantity)
            row = StockAdjustmentEntity(
                id=self._deps.ids.stock_adjustment(),
                product_id=item.product_id,
                outlet_id=item.outlet_id,
                quantity=decimal_text(item.quantity, field="quantity", allow_negative=True),
                reason=item.reason,
                user_id=user_id,
                custom_inventory_adjustment_reason_id=item.custom_inventory_adjustment_reason_id,
                object_version=self._deps.versions.bump(),
            )
            created.append(
                project_stock_adjustment(
                    adjustments.insert(row.to_entity(), {"operation_id": "CreateStockAdjustments"})
                )
            )
        # DOCUMENTED 201 and `StockAdjustmentBatchResponse`, which is `data`
        # alone -- no `version` envelope on the create, unlike the list.
        return json_({"data": created}, 201)

    # -- helpers ------------------------------------------------------------

    def _plan(
        self, ctx: UnitContext, item: CreateStockAdjustmentItem, index: int
    ) -> tuple[CreateStockAdjustmentItem, Decimal]:
        """One item checked against the world, before any of the batch commits."""
        where = f"stock_adjustments[{index}]"
        if ctx.store.collection(COL.products).get(item.product_id) is None:
            raise _refuse(f"{where}.product_id", f"{item.product_id!r} is not a product of this retailer.")
        if ctx.store.collection(COL.outlets).get(item.outlet_id) is None:
            raise _refuse(f"{where}.outlet_id", f"{item.outlet_id!r} is not an outlet of this retailer.")
        if item.reason not in STOCK_ADJUSTMENT_REASONS:
            raise _refuse(
                f"{where}.reason",
                f"must be one of: {', '.join(STOCK_ADJUSTMENT_REASONS)}.",
            )
        reason_type = self._custom_reason_type(ctx, item, where)
        quantity = Decimal(decimal_text(item.quantity, field=f"{where}.quantity", allow_negative=True))
        check_reason_sign(
            reason=item.reason,
            quantity=quantity,
            field=f"{where}.quantity",
            custom_reason_type=reason_type,
        )
        return item, quantity

    def _custom_reason_type(self, ctx: UnitContext, item: CreateStockAdjustmentItem, where: str) -> str | None:
        """``POSITIVE``/``NEGATIVE`` for a ``CUSTOM`` adjustment, or ``None``.

        JUDGMENT, twice over, and both because the Custom Inventory Adjustment
        Reasons tag is deferred (``capabilities.py``): a ``CUSTOM`` reason must
        name one of the two the scenario seeds, and a custom reason id sent
        WITH a built-in reason is refused rather than ignored -- it would have
        no effect, and a caller who sent one meant something by it.
        """
        supplied = item.custom_inventory_adjustment_reason_id
        if item.reason != CUSTOM_REASON:
            if supplied is not None:
                raise _refuse(
                    f"{where}.custom_inventory_adjustment_reason_id",
                    f"is only meaningful with reason {CUSTOM_REASON!r}; this item's reason is {item.reason!r}.",
                )
            return None
        if supplied is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=(f"{where}.custom_inventory_adjustment_reason_id is required when reason is {CUSTOM_REASON!r}."),
                field=f"{where}.custom_inventory_adjustment_reason_id",
            )
        stored = ctx.store.collection(COL.adjustment_reasons).get(supplied)
        if stored is None:
            raise _refuse(
                f"{where}.custom_inventory_adjustment_reason_id",
                f"{supplied!r} is not a custom inventory adjustment reason of this retailer.",
            )
        reason = AdjustmentReasonEntity.from_entity(stored)
        if not reason.enabled:
            raise _refuse(
                f"{where}.custom_inventory_adjustment_reason_id",
                f"the reason {reason.name!r} is disabled.",
            )
        return reason.type

    def _move_stock(self, ctx: UnitContext, item: CreateStockAdjustmentItem, quantity: Decimal) -> None:
        """Add ``quantity`` to the row for this product and outlet, creating
        the row when the product has never been stocked there."""
        inventory = ctx.store.collection(COL.inventory)
        existing = inventory.find(
            lambda row: row.get("product_id") == item.product_id and row.get("outlet_id") == item.outlet_id
        )
        deps = self._deps
        if existing is None:
            entity = InventoryEntity(
                id=deps.ids.inventory(),
                product_id=item.product_id,
                outlet_id=item.outlet_id,
                current_inventory_level=decimal_text(quantity, field="quantity", allow_negative=True),
                object_version=deps.versions.bump(),
            )
            inventory.insert(entity.to_entity(), {"operation_id": "CreateStockAdjustments"})
            return

        def mutate(draft: dict[str, Any]) -> None:
            level = Decimal(str(draft.get("current_inventory_level", "0")))
            draft["current_inventory_level"] = decimal_text(
                level + quantity, field="current_inventory_level", allow_negative=True
            )
            stamp_version(draft, deps)

        inventory.update(str(existing["id"]), mutate, meta={"operation_id": "CreateStockAdjustments"})

    def _require_product(self, args: HandlerArgs) -> dict[str, Any]:
        """The product a ``{product_id}`` inventory read names.

        JUDGMENT: an unknown id is a 404, although both operations declare only
        a 200. An empty array would be the same answer a real product with no
        stock gives, and those are different facts.
        """
        product_id = args.params["product_id"]
        stored = args.ctx.store.collection(COL.products).get(product_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Product {product_id} was not found.", field="product_id")
        return stored

    def _is_variant(self, ctx: UnitContext, product_id: str) -> bool:
        stored = ctx.store.collection(COL.products).get(product_id)
        return bool(stored and stored.get("variant_parent_id"))

    def _variant_rows(self, ctx: UnitContext, parent_id: str, *, include_deleted: bool) -> list[dict[str, Any]]:
        """The inventory of every variant of ``parent_id``: what the documented
        ``variants`` parameter asks for."""
        children = {
            str(row["id"])
            for row in ctx.store.collection(COL.products).all()
            if row.get("variant_parent_id") == parent_id
        }
        return [
            row
            for row in ctx.store.collection(COL.inventory).all()
            if str(row.get("product_id")) in children and (include_deleted or row.get("deleted_at") is None)
        ]


def _window(
    rows: Sequence[Mapping[str, Any]],
    *,
    after: int | None,
    before: int | None,
    size: int | None,
    descending: bool,
) -> list[dict[str, Any]]:
    """``after``/``before``/``size`` over the version, in the asked direction.

    The same exclusive-``after``, inclusive-``before`` reading
    ``versioning.select`` uses; the ordering is this operation's own
    ``sort_direction``/``order_direction`` parameter, which the version-cursor
    lists do not have.
    """
    chosen = [
        dict(row) for row in rows if version_of(row) > (after or 0) and (before is None or version_of(row) <= before)
    ]
    chosen.sort(key=version_of, reverse=descending)
    return chosen if size is None else chosen[:size]


def _is_active(product: Mapping[str, Any]) -> bool:
    document = product.get("document")
    if not isinstance(document, Mapping):
        return True
    return bool(document.get("active", True))


def _is_descending(direction: str | None) -> bool:
    return (direction or "asc").strip().lower() == "desc"


def _bool_query(args: HandlerArgs, name: str) -> bool:
    raw = args.query(name)
    return raw is not None and raw.strip().lower() in {"1", "true", "yes"}


def _int_query(args: HandlerArgs, name: str) -> int | None:
    raw = args.query(name)
    if raw is None:
        return None
    text = raw.strip()
    if not text.lstrip("-").isdigit():
        raise _refuse(name, "must be an integer version number.")
    return int(text)


def _page_size(args: HandlerArgs) -> int:
    """``page_size`` with the vendor's own default and ceiling for this route."""
    raw = args.query("page_size")
    if raw is None:
        return DEFAULT_PRODUCT_PAGE_SIZE
    text = raw.strip()
    if not text.isdigit() or int(text) < 1:
        raise _refuse("page_size", "must be 1 or more.")
    return min(int(text), MAX_PRODUCT_PAGE_SIZE)


def _refuse(field: str, message: str) -> UnitError:
    return UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} {message}", field=field)


def inventory_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedInventorySurface(deps).routes()
