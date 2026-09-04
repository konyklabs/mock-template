"""The Inventory surface: stock counts per variation and location.

FOR: giving ``inventory.count.updated`` something to fire on, and answering
the two reads an integration makes when it marks an item sold out or checks
what is left -- through the routes Square documents rather than a control
channel.

=============================  ===============================================
BatchChangeInventory           ``POST /v2/inventory/changes/batch-create``
                               https://developer.squareup.com/reference/square/inventory-api/batch-change-inventory
BatchRetrieveInventoryCounts   ``POST /v2/inventory/counts/batch-retrieve``
                               https://developer.squareup.com/reference/square/inventory-api/batch-retrieve-inventory-counts
RetrieveInventoryCount         ``GET  /v2/inventory/{catalog_object_id}``
                               https://developer.squareup.com/reference/square/inventory-api/retrieve-inventory-count
=============================  ===============================================

INVARIANT: **a batch is validated whole before its first write, and before
its first id.** Every change names a variation and a location that exist, a
decimal quantity and states this unit can apply; the first that does not is a
400 naming ``changes[i].<field>`` and nothing has been written -- so a consumer
never reads back half a batch, and no ``inventory.count.updated`` fires for a
request that failed. The change ids in the echo are minted after that
validation, so a rejected batch draws nothing from the id stream and the next
accepted one mints what it would have minted anyway. Each count that does change is one journalled update,
hence one event, which is what the webhook page shows: an array of counts,
one per change.

What a change does -- JUDGMENT, and where the boundary is
--------------------------------------------------------
Square tracks a count per ``InventoryState``; this unit tracks ``IN_STOCK``
only, which is the number an ordering integration reads. So:

* a ``PHYSICAL_COUNT`` sets the ``IN_STOCK`` quantity ("the quantity of an
  item variation that is physically present"); a count in any other state
  is refused;
* an ``ADJUSTMENT`` **into** ``IN_STOCK`` (``from_state`` ``NONE``,
  ``RECEIVED_FROM_VENDOR``, ``RETURNED_BY_CUSTOMER``, ...) adds its quantity,
  and one **out of** ``IN_STOCK`` (``to_state`` ``SOLD``, ``WASTE``, ...)
  subtracts it. Both states must be documented ``InventoryState`` values;
  an adjustment that neither enters nor leaves ``IN_STOCK`` is refused,
  since it would change nothing this unit holds and Square publishes no
  answer for it;
* a ``TRANSFER`` is refused: SHRINK, stated.

A count may go negative. Square allows it -- an oversold variation reads
``"-2"`` -- and the alternative is a fake that forbids a state the real API
produces. ``ignore_unchanged_counts`` (default true) is honoured literally: a
physical count equal to the current quantity writes nothing, journals
nothing and fires nothing, and is still echoed in ``changes``.

SHRINK (prototype): changes are not persisted -- ``BatchRetrieveInventoryChanges``,
``RetrieveInventoryAdjustment``, ``RetrieveInventoryPhysicalCount`` and
``RetrieveInventoryTransfer`` are absent -- and the deprecated
``RetrieveInventoryChanges`` GET is not implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    IdempotencySpec,
    PaginationSpec,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.state.store import Collection, Entity
from vendorfake.core.util.json import compact
from vendorfake.square.entities import COL, CatalogObjectEntity, InventoryCountEntity, inventory_count_id
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.inventory import (
    CHANGE_TYPES,
    IN_STOCK,
    INVENTORY_STATES,
    BatchChangeInventoryRequest,
    BatchRetrieveInventoryCountsRequest,
    format_quantity,
    parse_quantity,
    project_inventory_count,
)
from vendorfake.square.seed.constants import SEED_LOCATION_ID, TEA_MUG_VARIATION_ID
from vendorfake.square.surface.common import SquareDeps, instant_ms

__all__ = ["CAPABILITY", "COUNTS_DEFAULT_LIMIT", "COUNTS_MAX_LIMIT", "InventorySurface", "inventory_routes"]

CAPABILITY = "inventory"
"""The capability every route below belongs to."""

COUNTS_DEFAULT_LIMIT = 100
COUNTS_MAX_LIMIT = 1000
"""Page bounds for the two reads. Square documents ``limit`` as "Min 1" and
publishes no default or maximum; both numbers are JUDGMENT, matching
ListCatalog's."""


@dataclass(frozen=True, slots=True)
class _Planned:
    """One change, resolved: the count it touches, the quantity it leaves,
    and the body of the ``changes`` echo it produces -- without its id, which
    is minted only once every change in the batch has been validated."""

    count_id: str
    catalog_object_id: str
    location_id: str
    quantity: Decimal
    kind: str
    inner: dict[str, Any]
    #: A physical count equal to the current quantity, under
    #: ``ignore_unchanged_counts``: echoed, never written.
    unchanged: bool = False


_ECHO_KEY: Mapping[str, str] = {"PHYSICAL_COUNT": "physical_count", "ADJUSTMENT": "adjustment"}


class InventorySurface:
    """The three Inventory routes, bound to one vendor's id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """The literal four-segment paths cannot collide with the
        two-segment ``/v2/inventory/{catalog_object_id}``; listed writes-first
        because the read is meaningless until something is counted."""
        return (
            Route(
                method="POST",
                path="/v2/inventory/changes/batch-create",
                capability=CAPABILITY,
                handler=self.batch_change,
                auth="bearer",
                scopes=("INVENTORY_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="inventory.batch-create", required=True),
                # One physical count of a seeded variation at the seeded
                # location; occurred_at is fixed so the example is one request,
                # not a template.
                example_body={
                    "changes": [
                        {
                            "type": "PHYSICAL_COUNT",
                            "physical_count": {
                                "catalog_object_id": TEA_MUG_VARIATION_ID,
                                "location_id": SEED_LOCATION_ID,
                                "state": "IN_STOCK",
                                "quantity": "9",
                                "occurred_at": "2026-08-30T12:00:00.000Z",
                            },
                        }
                    ]
                },
                operation_id="BatchChangeInventory",
                summary="Apply physical counts and adjustments to IN_STOCK counts.",
            ),
            Route(
                method="POST",
                path="/v2/inventory/counts/batch-retrieve",
                capability=CAPABILITY,
                handler=self.batch_retrieve_counts,
                auth="bearer",
                scopes=("INVENTORY_READ",),
                operation_id="BatchRetrieveInventoryCounts",
                summary="Counts filtered by object, location, state or change time.",
                pagination=PaginationSpec(
                    style="cursor",
                    where="body",
                    items_path="counts",
                    walkable=False,
                    unwalkable_reason=(
                        "An InventoryCount has no id: Square keys it on the (object, location, "
                        "state) triple and documents no identifier, and the identity walk compares "
                        "rows by one declared path -- naming any single field would false-fail on "
                        "two locations counting one object."
                    ),
                ),
            ),
            Route(
                method="GET",
                path="/v2/inventory/{catalog_object_id}",
                capability=CAPABILITY,
                handler=self.retrieve_count,
                auth="bearer",
                scopes=("INVENTORY_READ",),
                operation_id="RetrieveInventoryCount",
                summary="One variation's counts across locations.",
                pagination=PaginationSpec(
                    style="cursor",
                    items_path="counts",
                    walkable=False,
                    unwalkable_reason=(
                        "The rows are InventoryCounts, which carry no per-row identifier, and the "
                        "documented endpoint reads no limit parameter, so pages cannot be narrowed "
                        "to force a boundary."
                    ),
                ),
            ),
        )

    # -- POST /v2/inventory/changes/batch-create ----------------------------

    def batch_change(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(BatchChangeInventoryRequest, args.body())
        ctx = args.ctx
        counts = ctx.store.collection(COL.inventory_counts)
        now = ctx.clock.iso_ms()
        planned: list[_Planned] = []
        # Later changes in one batch see earlier ones: two adjustments to one
        # variation apply in order, as they would at Square.
        pending: dict[str, Decimal] = {}
        for index, change in enumerate(request.changes):
            path = f"changes[{index}]"
            kind = change.type.upper()
            if kind not in CHANGE_TYPES:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{path}.type must be one of {', '.join(CHANGE_TYPES)}.",
                    field=f"{path}.type",
                    info={"allowed": list(CHANGE_TYPES)},
                )
            if kind == "TRANSFER":
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{path}: TRANSFER changes are not modelled by this unit; use ADJUSTMENT.",
                    field=f"{path}.type",
                )
            if kind == "PHYSICAL_COUNT":
                planned.append(
                    self._plan_physical_count(ctx, counts, change.physical_count, path, pending, now, request)
                )
            else:
                planned.append(self._plan_adjustment(ctx, counts, change.adjustment, path, pending, now))

        # Validation is over; mint the change ids in request order.
        echoes = [
            {"type": plan.kind, _ECHO_KEY[plan.kind]: {"id": self._deps.ids.inventory_change(), **plan.inner}}
            for plan in planned
        ]

        written: dict[str, Entity] = {}
        for plan in planned:
            if plan.unchanged:
                continue
            quantity = format_quantity(plan.quantity)
            current = counts.get(plan.count_id)
            if current is None:
                written[plan.count_id] = counts.insert(
                    InventoryCountEntity(
                        catalog_object_id=plan.catalog_object_id,
                        location_id=plan.location_id,
                        quantity=quantity,
                        calculated_at=now,
                    ).to_entity(),
                    {"operation_id": "BatchChangeInventory"},
                )
                continue

            def mutate(draft: Entity, quantity: str = quantity) -> None:
                draft["quantity"] = quantity
                draft["calculated_at"] = now

            written[plan.count_id] = counts.update(plan.count_id, mutate, meta={"operation_id": "BatchChangeInventory"})

        touched = list(dict.fromkeys(plan.count_id for plan in planned))
        return json_(
            {
                "counts": [
                    project_inventory_count(written[count_id] if count_id in written else counts.require(count_id))
                    for count_id in touched
                    if count_id in written or counts.has(count_id)
                ],
                "changes": echoes,
            }
        )

    def _plan_physical_count(
        self,
        ctx: UnitContext,
        counts: Collection,
        spec: Any,
        path: str,
        pending: dict[str, Decimal],
        now: str,
        request: BatchChangeInventoryRequest,
    ) -> _Planned:
        if spec is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=f"{path}.physical_count is required for a PHYSICAL_COUNT change.",
                field=f"{path}.physical_count",
            )
        field = f"{path}.physical_count"
        state = (spec.state or IN_STOCK).upper()
        if state != IN_STOCK:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{field}.state must be IN_STOCK; this unit tracks no other state as a count.",
                field=f"{field}.state",
            )
        _require_variation(ctx, spec.catalog_object_id, f"{field}.catalog_object_id")
        _require_location(ctx, spec.location_id, f"{field}.location_id")
        quantity = parse_quantity(spec.quantity, f"{field}.quantity")
        count_id = inventory_count_id(spec.catalog_object_id, spec.location_id)
        current = _current_quantity(counts, count_id, pending)
        unchanged = request.ignore_unchanged_counts and current is not None and current == quantity
        pending[count_id] = quantity
        inner = compact(
            {
                "reference_id": spec.reference_id,
                "catalog_object_id": spec.catalog_object_id,
                "catalog_object_type": "ITEM_VARIATION",
                "state": IN_STOCK,
                "location_id": spec.location_id,
                "quantity": format_quantity(quantity),
                "occurred_at": spec.occurred_at or now,
                "created_at": now,
            }
        )
        return _Planned(
            count_id=count_id,
            catalog_object_id=spec.catalog_object_id,
            location_id=spec.location_id,
            quantity=quantity,
            kind="PHYSICAL_COUNT",
            inner=inner,
            unchanged=unchanged,
        )

    def _plan_adjustment(
        self,
        ctx: UnitContext,
        counts: Collection,
        spec: Any,
        path: str,
        pending: dict[str, Decimal],
        now: str,
    ) -> _Planned:
        if spec is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail=f"{path}.adjustment is required for an ADJUSTMENT change.",
                field=f"{path}.adjustment",
            )
        field = f"{path}.adjustment"
        from_state = spec.from_state.upper()
        to_state = spec.to_state.upper()
        for name, value in (("from_state", from_state), ("to_state", to_state)):
            if value not in INVENTORY_STATES:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{field}.{name} must be a documented InventoryState.",
                    field=f"{field}.{name}",
                    info={"allowed": list(INVENTORY_STATES)},
                )
        if (from_state == IN_STOCK) == (to_state == IN_STOCK):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"{field} must move quantity into or out of IN_STOCK; "
                    f"{from_state} -> {to_state} changes no count this unit holds."
                ),
                field=f"{field}.to_state",
            )
        _require_variation(ctx, spec.catalog_object_id, f"{field}.catalog_object_id")
        _require_location(ctx, spec.location_id, f"{field}.location_id")
        delta = parse_quantity(spec.quantity, f"{field}.quantity")
        if delta < 0:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{field}.quantity cannot be negative; swap from_state and to_state instead.",
                field=f"{field}.quantity",
            )
        count_id = inventory_count_id(spec.catalog_object_id, spec.location_id)
        current = _current_quantity(counts, count_id, pending) or Decimal(0)
        quantity = current + delta if to_state == IN_STOCK else current - delta
        pending[count_id] = quantity
        inner = compact(
            {
                "reference_id": spec.reference_id,
                "from_state": from_state,
                "to_state": to_state,
                "location_id": spec.location_id,
                "catalog_object_id": spec.catalog_object_id,
                "catalog_object_type": "ITEM_VARIATION",
                "quantity": format_quantity(delta),
                "occurred_at": spec.occurred_at or now,
                "created_at": now,
            }
        )
        return _Planned(
            count_id=count_id,
            catalog_object_id=spec.catalog_object_id,
            location_id=spec.location_id,
            quantity=quantity,
            kind="ADJUSTMENT",
            inner=inner,
        )

    # -- POST /v2/inventory/counts/batch-retrieve ---------------------------

    def batch_retrieve_counts(self, args: HandlerArgs) -> ReplyInit:
        body = args.body()
        request = validate_body(BatchRetrieveInventoryCountsRequest, body)
        if request.limit is not None and request.limit < 1:
            raise UnitError(UnitErrorKind.INVALID_VALUE, detail="limit must be at least 1.", field="limit")
        objects = set(request.catalog_object_ids or [])
        locations = set(request.location_ids or [])
        states = {state.upper() for state in request.states or []}
        after = instant_ms(request.updated_after)
        return self._page(
            args,
            body,
            lambda entity: (
                (not objects or entity.get("catalog_object_id") in objects)
                and (not locations or entity.get("location_id") in locations)
                and (not states or str(entity.get("state", IN_STOCK)) in states)
                and _calculated_after(entity, after)
            ),
            limit=request.limit,
            cursor=request.cursor,
        )

    # -- GET /v2/inventory/{catalog_object_id} ------------------------------

    def retrieve_count(self, args: HandlerArgs) -> ReplyInit:
        """One variation across locations. "location_ids: The Location IDs
        to look up as a comma-separated list." An object with no counts is
        an empty array, not a 404: Square's count is "calculated", and a
        variation nobody has counted has none."""
        catalog_object_id = args.params["catalog_object_id"]
        raw_locations = args.query("location_ids")
        locations = {part.strip() for part in raw_locations.split(",") if part.strip()} if raw_locations else set()
        return self._page(
            args,
            {"catalog_object_id": catalog_object_id, "location_ids": sorted(locations)},
            lambda entity: (
                entity.get("catalog_object_id") == catalog_object_id
                and (not locations or entity.get("location_id") in locations)
            ),
            limit=None,
            cursor=args.query("cursor"),
        )

    def _page(
        self,
        args: HandlerArgs,
        fingerprint_source: Mapping[str, Any],
        keep: Any,
        *,
        limit: int | None,
        cursor: str | None,
    ) -> ReplyInit:
        collection = args.ctx.store.collection(COL.inventory_counts)
        matching = sorted((entity for entity in collection.all() if keep(entity)), key=lambda e: str(e["id"]))
        fingerprint = {name: value for name, value in fingerprint_source.items() if name not in ("cursor", "limit")}
        page = collection.paginate(
            matching,
            limit=limit,
            cursor=cursor,
            fingerprint=fingerprint,
            default_limit=COUNTS_DEFAULT_LIMIT,
            max_limit=COUNTS_MAX_LIMIT,
        )
        return json_(
            compact(
                {
                    "counts": [project_inventory_count(entity) for entity in page.items],
                    "cursor": page.cursor,
                }
            )
        )


def inventory_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Inventory routes for one vendor."""
    return InventorySurface(deps).routes()


def _require_variation(ctx: UnitContext, catalog_object_id: str, field: str) -> None:
    """Counts are kept per ITEM_VARIATION: "catalog_object_type: The
    CatalogObjectType of the CatalogObject being tracked. Tracking is only
    supported for the `ITEM_VARIATION` type." """
    stored = ctx.store.collection(COL.catalog).get(catalog_object_id)
    if stored is None or not CatalogObjectEntity.from_entity(stored).is_variation:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} {catalog_object_id} is not an ITEM_VARIATION in this catalog.",
            field=field,
        )


def _require_location(ctx: UnitContext, location_id: str, field: str) -> None:
    locations = ctx.store.collection(COL.locations)
    if locations.get(location_id) is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} {location_id} does not exist for this merchant.",
            field=field,
            info={"known": [str(entity["id"]) for entity in locations.all()]},
        )


def _current_quantity(counts: Collection, count_id: str, pending: Mapping[str, Decimal]) -> Decimal | None:
    """The quantity a change starts from: an earlier change in the same
    batch, else the stored count, else nothing counted yet."""
    if count_id in pending:
        return pending[count_id]
    stored = counts.get(count_id)
    if stored is None:
        return None
    return Decimal(InventoryCountEntity.from_entity(stored).quantity)


def _calculated_after(entity: Mapping[str, Any], after: float | None) -> bool:
    if after is None:
        return True
    stamp = entity.get("calculated_at") or entity.get("updated_at")
    at = instant_ms(None if stamp is None else str(stamp))
    return at is not None and at > after
