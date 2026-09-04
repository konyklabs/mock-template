"""The Orders surface: CreateOrder, RetrieveOrder, UpdateOrder, SearchOrders, BatchRetrieveOrders, PayOrder, and
the legacy ``POST /v2/locations/{location_id}/orders`` path.
https://developer.squareup.com/reference/square/orders-api/create-order https://developer.squareup.com/reference/square/orders-api/retrieve-order
https://developer.squareup.com/reference/square/orders-api/update-order https://developer.squareup.com/reference/square/orders-api/search-orders

INVARIANT: a rejected request changes nothing -- ``Collection.update`` checks ``expect_version`` before
committing. UpdateOrder is sparse (:func:`~vendorfake.square.model.order.supplied`); JUDGMENT: a present
``null`` is an explicit clear except on ``state``, ``version``, and a line item's
``quantity``/``base_price_money``, which refuse it as ``invalid_value``. INVARIANT: fulfillment stamps this
unit sets from its clock are volatile to the state digest (:data:`FULFILLMENT_STAMPS`), mirrored into
``supplied_stamps`` when caller-supplied.
JUDGMENT: a partly tendered OPEN order cannot shrink below what its tenders applied (``invalid_value``).
INVARIANT: PayOrder on an already-COMPLETED order is ``invalid_transition``; no terminal state allows a
self-transition. DOCUMENTED: a completing order's zero-quantity line items are dropped.
https://developer.squareup.com/reference/square/objects/OrderLineItem
JUDGMENT: an unsupplied fulfillment transition stamps the timestamp Square would stamp; NOT VERIFIED which of
them Square also accepts from a caller. SHRINK: CalculateOrder and CloneOrder are not implemented; taxes,
discounts, service charges, returns/refunds and a fulfillment's ``entries`` are not modelled.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
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
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Collection, Entity
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import js_parse_float
from vendorfake.square.entities import (
    COL,
    CatalogObjectEntity,
    Fulfillment,
    LocationEntity,
    Money,
    OrderEntity,
    OrderLineItem,
    PaymentEntity,
    Tender,
)
from vendorfake.square.ids import SquareIds
from vendorfake.square.machine import (
    FULFILLMENT_MACHINE,
    ORDER_MACHINE,
    PAYMENT_MACHINE,
    FulfillmentState,
    OrderState,
    PaymentState,
)
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.order import (
    FULFILLMENT_TYPES,
    BatchRetrieveOrdersRequest,
    CreateOrderRequest,
    FulfillmentRequest,
    LineItemRequest,
    PayOrderRequest,
    SearchOrdersRequest,
    UpdateOrderRequest,
    amount_due,
    order_total,
    project_order,
    project_order_entry,
    supplied,
)
from vendorfake.square.seed.constants import SEED_KIOSK_LOCATION_ID, SEED_LOCATION_ID, SEED_OPEN_ORDER_ID
from vendorfake.square.surface.common import SquareDeps, instant_ms

__all__ = [
    "CAPABILITY",
    "MAX_BATCH_ORDER_IDS",
    "MAX_LOCATION_IDS",
    "SEARCH_DEFAULT_LIMIT",
    "SEARCH_MAX_LIMIT",
    "OrdersSurface",
    "apply_tenders",
    "capture_payment",
    "order_routes",
    "require_order",
    "tender_for_payment",
]

CAPABILITY = "order-lifecycle"
"""The capability every route below belongs to."""

MAX_LOCATION_IDS = 10
"""``location_ids`` on SearchOrders: "Max: 10"."""

SEARCH_DEFAULT_LIMIT = 500
"""SearchOrders ``limit``: "Default: 500"."""

SEARCH_MAX_LIMIT = 1000
"""SearchOrders ``limit``: "Max: 1000"."""

MAX_BATCH_ORDER_IDS = 100
"""BatchRetrieveOrders: "A maximum of 100 orders can be retrieved per request."."""

_MACHINE = StateMachine(ORDER_MACHINE)
"""One instance at module level; a :class:`StateMachine` holds no entity or store."""

_FULFILLMENT_MACHINE = StateMachine(FULFILLMENT_MACHINE)
_PAYMENT_MACHINE = StateMachine(PAYMENT_MACHINE)

_DETAILS_KEY: Mapping[str, str] = {
    "PICKUP": "pickup_details",
    "DELIVERY": "delivery_details",
    "SHIPMENT": "shipment_details",
}
"""The details object each fulfillment type carries."""

_TRANSITION_STAMPS: Mapping[tuple[str, str], str] = {
    ("PICKUP", FulfillmentState.RESERVED.value): "accepted_at",
    ("PICKUP", FulfillmentState.PREPARED.value): "ready_at",
    ("PICKUP", FulfillmentState.COMPLETED.value): "picked_up_at",
    ("PICKUP", FulfillmentState.CANCELED.value): "canceled_at",
    ("DELIVERY", FulfillmentState.PREPARED.value): "ready_at",
    ("DELIVERY", FulfillmentState.COMPLETED.value): "delivered_at",
    ("DELIVERY", FulfillmentState.CANCELED.value): "canceled_at",
    ("SHIPMENT", FulfillmentState.PREPARED.value): "packaged_at",
    ("SHIPMENT", FulfillmentState.COMPLETED.value): "shipped_at",
    ("SHIPMENT", FulfillmentState.CANCELED.value): "canceled_at",
    ("SHIPMENT", FulfillmentState.FAILED.value): "failed_at",
}
"""Which details stamp a transition sets when the caller did not. JUDGMENT."""

FULFILLMENT_STAMPS: frozenset[str] = frozenset({"placed_at", *_TRANSITION_STAMPS.values()})
"""Every details stamp this unit can set from its clock; declared volatile to the state digest."""

_SHADOWED_STAMPS: frozenset[str] = FULFILLMENT_STAMPS | {"expires_at"}
"""Field names the digest treats as volatile at any depth. A caller-supplied value under one of
these names is mirrored into ``Fulfillment.supplied_stamps`` so the digest still sees it."""

_CREATABLE_STATES: tuple[str, ...] = (OrderState.OPEN.value, OrderState.DRAFT.value)
"""CreateOrder accepts these two; the terminal pair cannot be a starting state."""

_SORT_FIELDS: Mapping[str, str] = {
    "CREATED_AT": "created_at",
    "UPDATED_AT": "updated_at",
    "CLOSED_AT": "closed_at",
}
"""``sort_field`` to the entity field it orders by and the ``date_time_filter`` key it requires."""

_SORT_ORDERS: tuple[str, ...] = ("ASC", "DESC")

_CLEARABLE_ORDER_FIELDS: frozenset[str] = frozenset({"reference_id", "customer_id", "ticket_name", "metadata"})
"""Order-level fields ``fields_to_clear`` and a null both remove."""

_CLEARABLE_LINE_FIELDS: tuple[str, ...] = ("name", "note", "catalog_object_id", "variation_name")
"""Line-item fields a null clears -- everything a line can be without."""

_LINE_ITEM_PATH = re.compile(r"^line_items\[([^\]]+)\](?:\.(.+))?$")
"""Square's bracket notation for a line item inside ``fields_to_clear``, e.g. ``line_items[uid].note``."""


@dataclass(frozen=True, slots=True)
class _LinePatch:
    """One sparse line-item change: what it sets and what it removes, as two mappings rather than
    one dict with a sentinel, since the merge must ``pop`` a cleared key and never write ``None``."""

    uid: str
    assign: dict[str, Any] = dataclass_field(default_factory=dict)
    clear: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _FulfillmentPatch:
    """One sparse fulfillment change: top-level assignments, and per-details
    assignments and clears, in the same two-collection shape as a line patch."""

    uid: str
    assign: dict[str, Any] = dataclass_field(default_factory=dict)
    details_assign: dict[str, Any] = dataclass_field(default_factory=dict)
    details_clear: tuple[str, ...] = ()


class OrdersSurface:
    """The seven Orders routes, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """The literal paths come first: the router returns the first candidate whose method matches, so
        ``/v2/orders/search`` must not sit after ``/v2/orders/{order_id}`` and risk being shadowed."""
        return (
            Route(
                method="POST",
                path="/v2/orders",
                capability=CAPABILITY,
                handler=self.create_order,
                auth="bearer",
                scopes=("ORDERS_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.create"),
                operation_id="CreateOrder",
                summary="Create an order. Idempotent on idempotency_key.",
                # Minimal accepted body; `idempotency_key` omitted so callers of the example don't collide.
                example_body={"order": {"location_id": SEED_LOCATION_ID}},
            ),
            Route(
                method="POST",
                path="/v2/locations/{location_id}/orders",
                capability=CAPABILITY,
                handler=self.create_order_at_location,
                auth="bearer",
                scopes=("ORDERS_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.create"),
                operation_id="CreateOrderAtLocation",
                summary="CreateOrder on its pre-2019 path; the location comes from the URL.",
                # Empty order: the location is authoritative from the URL.
                example_body={"order": {}},
                example_params={"location_id": SEED_LOCATION_ID},
            ),
            Route(
                method="POST",
                path="/v2/orders/search",
                capability=CAPABILITY,
                handler=self.search_orders,
                auth="bearer",
                scopes=("ORDERS_READ",),
                operation_id="SearchOrders",
                summary="Filtered, sorted, cursor-paginated order search.",
                # Both seeded locations are named so the page-walk example isn't a one-row listing.
                example_body={"location_ids": [SEED_LOCATION_ID, SEED_KIOSK_LOCATION_ID]},
                pagination=PaginationSpec(style="cursor", where="body", items_path="orders"),
            ),
            Route(
                method="POST",
                path="/v2/orders/batch-retrieve",
                capability=CAPABILITY,
                handler=self.batch_retrieve_orders,
                auth="bearer",
                scopes=("ORDERS_READ",),
                operation_id="BatchRetrieveOrders",
                summary="Retrieve up to 100 orders by id, ignoring ids that do not exist.",
            ),
            Route(
                method="GET",
                path="/v2/orders/{order_id}",
                capability=CAPABILITY,
                handler=self.retrieve_order,
                auth="bearer",
                scopes=("ORDERS_READ",),
                operation_id="RetrieveOrder",
                summary="Retrieve one order, reflecting every committed mutation.",
            ),
            Route(
                method="PUT",
                path="/v2/orders/{order_id}",
                capability=CAPABILITY,
                handler=self.update_order,
                auth="bearer",
                scopes=("ORDERS_WRITE",),
                # DOCUMENTED: a repeated idempotency_key replays the prior response rather than conflicting.
                # https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.update", on_mismatch="replay"),
                operation_id="UpdateOrder",
                summary="Sparse update under optimistic concurrency.",
                # Smallest accepted update: version alone, against the seeded open order at version 1.
                example_body={"order": {"version": 1}},
                example_params={"order_id": SEED_OPEN_ORDER_ID},
            ),
            Route(
                method="POST",
                path="/v2/orders/{order_id}/pay",
                capability=CAPABILITY,
                handler=self.pay_order,
                auth="bearer",
                scopes=("ORDERS_WRITE", "PAYMENTS_WRITE"),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.pay", required=True),
                # No example_body: a working PayOrder is pinned to a version/total other checks move.
                example_params={"order_id": SEED_OPEN_ORDER_ID},
                operation_id="PayOrder",
                summary="Pay an open order and move it to COMPLETED.",
            ),
        )

    # -- POST /v2/orders ----------------------------------------------------

    def create_order(self, args: HandlerArgs) -> ReplyInit:
        return self._create(args, validate_body(CreateOrderRequest, args.body()))

    # -- POST /v2/locations/{location_id}/orders ----------------------------

    def create_order_at_location(self, args: HandlerArgs) -> ReplyInit:
        """CreateOrder on the pre-2019 path where the location lived in the URL. NOT VERIFIED which
        ``Square-Version`` moved it -- the move is recorded in Square's changelog
        (https://developer.squareup.com/docs/changelog/connect).
        https://developer.squareup.com/reference/square/orders-api/create-order

        JUDGMENT: the URL's location is authoritative; a body naming a different one is refused as
        ``invalid_value`` rather than silently overridden. Everything else delegates to CreateOrder.
        """
        raw = args.body()
        order = raw.get("order")
        if not isinstance(order, Mapping):
            raise UnitError(UnitErrorKind.MISSING_FIELD, detail="order is required.", field="order")
        path_location = args.params["location_id"]
        stated = order.get("location_id")
        if stated is not None and stated != path_location:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"order.location_id {stated!r} does not match the location in the path, {path_location!r}.",
                field="order.location_id",
                info={"path": path_location, "body": stated},
            )
        merged = {**raw, "order": {**order, "location_id": path_location}}
        return self._create(args, validate_body(CreateOrderRequest, merged))

    def _create(self, args: HandlerArgs, request: CreateOrderRequest) -> ReplyInit:
        spec = request.order
        location = _require_location(args.ctx, spec.location_id)

        state = spec.state or OrderState.OPEN.value
        if state not in _CREATABLE_STATES:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(f"An order cannot be created in state {state}. CreateOrder accepts OPEN (default) or DRAFT."),
                field="order.state",
                info={"allowed": list(_CREATABLE_STATES)},
            )

        # Validate everything before minting anything, so a refused create draws nothing from the id stream.
        checked_lines = self._new_line_items(args.ctx, spec.line_items or [], location.currency)
        checked_fulfillments = self._new_fulfillments(spec.fulfillments or [], args.ctx.clock.iso_ms())
        line_items = tuple(
            line if line.uid else replace(line, uid=self._deps.ids.line_item_uid()) for line in checked_lines
        )
        fulfillments = tuple(
            f if f.uid else replace(f, uid=self._deps.ids.fulfillment_uid()) for f in checked_fulfillments
        )
        entity = OrderEntity(
            id=self._deps.ids.order(),
            location_id=location.id,
            merchant_id=location.merchant_id,
            # Currency is the location's, never the request's: a seller can't be paid in what it doesn't take.
            currency=location.currency,
            state=state,
            line_items=line_items,
            fulfillments=fulfillments,
            reference_id=spec.reference_id,
            customer_id=spec.customer_id,
            ticket_name=spec.ticket_name,
            source_name=None if spec.source is None else spec.source.name,
            metadata=spec.metadata,
        ).to_entity()
        stored = args.ctx.store.collection(COL.orders).insert(entity, {"operation_id": "CreateOrder"})
        return json_({"order": project_order(OrderEntity.from_entity(stored))})

    # -- GET /v2/orders/{order_id} -----------------------------------------

    def retrieve_order(self, args: HandlerArgs) -> ReplyInit:
        orders = args.ctx.store.collection(COL.orders)
        order = require_order(orders, args.params["order_id"])
        return json_({"order": project_order(order)})

    # -- PUT /v2/orders/{order_id} -----------------------------------------

    def update_order(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(UpdateOrderRequest, args.body())
        patch = request.order
        order_id = args.params["order_id"]
        orders = args.ctx.store.collection(COL.orders)
        current = require_order(orders, order_id)
        subject = f"Order {order_id}"

        if patch.version is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="Your request must include the order.version property set to the current version of the order.",
                field="order.version",
            )

        # Checked before anything mints, since a stale write would shift the ids drawn for new entries.
        if patch.version != current.version:
            raise UnitError(
                UnitErrorKind.VERSION_CONFLICT,
                detail=(
                    f"Supplied version {patch.version} does not match the current version "
                    f"{current.version} of orders {order_id}."
                ),
                info={"collection": COL.orders, "id": order_id, "supplied": patch.version, "current": current.version},
            )

        # Terminality checked first: "this order is finished" explains "that move is not allowed", not vice versa.
        _MACHINE.assert_mutable(current.state, subject)
        next_state = patch.state
        if supplied(patch, "state"):
            if next_state is None:
                raise _cannot_be_cleared("order.state")
            _MACHINE.assert_transition(current.state, next_state, subject)

        location = _require_location(args.ctx, current.location_id)
        patches: tuple[_LinePatch, ...] | None = None
        if supplied(patch, "line_items"):
            patches = self._line_patches(args.ctx, patch.line_items or [], location.currency)

        clears_line_items = patch.line_items is None and supplied(patch, "line_items")

        fulfillment_patches: tuple[_FulfillmentPatch, ...] | None = None
        if supplied(patch, "fulfillments"):
            if patch.fulfillments is None:
                raise _cannot_be_cleared("order.fulfillments")
            fulfillment_patches = self._fulfillment_patches(current, patch.fulfillments)
        now = args.ctx.clock.iso_ms()

        # Dry-run the line changes on a copy first, so the tendered floor is checked before any uid is minted.
        probe = orders.require(order_id)
        taken = {str(line.get("uid", "")) for line in _lines_of(probe)}
        probed = _placeholders(patches, taken)
        _apply_line_changes(probe, clears_line_items, probed, request.fields_to_clear, fresh=_fresh(patches, probed))
        _assert_tendered_floor(OrderEntity.from_entity(probe), subject)
        minted: tuple[_LinePatch, ...] | None = None
        if patches is not None:
            minted = tuple(p if p.uid else replace(p, uid=self._deps.ids.line_item_uid()) for p in patches)
        fresh = _fresh(patches, minted)
        patches = minted
        if fulfillment_patches is not None:
            fulfillment_patches = tuple(
                p if p.uid else replace(p, uid=self._deps.ids.fulfillment_uid()) for p in fulfillment_patches
            )

        def mutate(draft: Entity) -> None:
            _apply_line_changes(draft, clears_line_items, patches, request.fields_to_clear, fresh=fresh)
            if fulfillment_patches is not None:
                merged = _merge_fulfillments(_fulfillments_of(draft), fulfillment_patches, now)
                if merged:
                    draft["fulfillments"] = merged
                else:
                    draft.pop("fulfillments", None)
            _assign_or_clear(draft, patch, "reference_id")
            _assign_or_clear(draft, patch, "customer_id")
            _assign_or_clear(draft, patch, "ticket_name")
            _assign_or_clear(draft, patch, "metadata")
            _apply_order_fields_to_clear(draft, request.fields_to_clear)
            if next_state is not None and next_state != draft["state"]:
                draft["state"] = next_state
                if _MACHINE.is_terminal(next_state):
                    draft["closed_at"] = args.ctx.clock.iso_ms()
                if next_state == OrderState.COMPLETED.value:
                    _drop_zero_quantity_lines(draft)

        updated = orders.update(
            order_id,
            mutate,
            expect_version=patch.version,
            meta={"operation_id": "UpdateOrder"},
        )
        return json_({"order": project_order(OrderEntity.from_entity(updated))})

    # -- POST /v2/orders/search --------------------------------------------

    def search_orders(self, args: HandlerArgs) -> ReplyInit:
        """Search the merchant's orders, one location set at a time. ``location_ids`` is required.
        https://developer.squareup.com/docs/orders-api/manage-orders/search-orders
        JUDGMENT: Square publishes no error code for the omission; this unit answers its standard 400
        ``MISSING_FIELD`` naming ``location_ids``.
        """
        body = args.body()
        request = validate_body(SearchOrdersRequest, body)
        if not request.location_ids:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="Your request must include one or more location_ids.",
                field="location_ids",
            )
        if len(request.location_ids) > MAX_LOCATION_IDS:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Max: {MAX_LOCATION_IDS} location IDs.",
                field="location_ids",
            )

        query = request.query
        sort = None if query is None else query.sort
        sort_field = (sort.sort_field if sort is not None and sort.sort_field else "CREATED_AT").upper()
        sort_order = (sort.sort_order if sort is not None and sort.sort_order else "DESC").upper()
        if sort_field not in _SORT_FIELDS:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="sort_field must be CREATED_AT, UPDATED_AT or CLOSED_AT.",
                field="query.sort.sort_field",
                info={"allowed": list(_SORT_FIELDS)},
            )
        if sort_order not in _SORT_ORDERS:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="sort_order must be ASC or DESC.",
                field="query.sort.sort_order",
                info={"allowed": list(_SORT_ORDERS)},
            )

        filters = None if query is None else query.filter
        date_filter = None if filters is None else filters.date_time_filter
        if date_filter is not None:
            for wire_name, expected in _SORT_FIELDS.items():
                # DOCUMENTED: a date_time_filter requires sort_field set to the same field.
                # https://developer.squareup.com/reference/square/objects/SearchOrdersDateTimeFilter
                if supplied(date_filter, expected) and wire_name != sort_field:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail=f"A date_time_filter on {expected} requires sort_field {wire_name}.",
                        field="query.sort.sort_field",
                    )

        collection = args.ctx.store.collection(COL.orders)
        orders = [OrderEntity.from_entity(entity) for entity in collection.all()]
        wanted = set(request.location_ids)
        orders = [order for order in orders if order.location_id in wanted]
        states = None if filters is None or filters.state_filter is None else set(filters.state_filter.states)
        if states is not None:
            orders = [order for order in orders if order.state in states]
        if date_filter is not None:
            key = _SORT_FIELDS[sort_field]
            bounds = getattr(date_filter, key)
            if bounds is not None:
                orders = [order for order in orders if _within(getattr(order, key), bounds.start_at, bounds.end_at)]

        # Code point order, not locale collation: order ids are mixed case and the page order is on the wire.
        sort_key = _SORT_FIELDS[sort_field]
        orders.sort(key=lambda order: (getattr(order, sort_key) or "", order.id), reverse=sort_order == "DESC")

        # The fingerprint is the whole request except paging, so a cursor stays valid across page-size changes.
        fingerprint = {name: value for name, value in body.items() if name not in ("cursor", "limit")}
        page = collection.paginate(
            orders,
            limit=request.limit,
            cursor=request.cursor,
            fingerprint=fingerprint,
            default_limit=SEARCH_DEFAULT_LIMIT,
            max_limit=SEARCH_MAX_LIMIT,
        )
        # `orders`/`order_entries`: whichever was asked for is present even when empty.
        return json_(
            compact(
                {
                    "orders": None if request.return_entries else [project_order(o) for o in page.items],
                    "order_entries": [project_order_entry(o) for o in page.items] if request.return_entries else None,
                    # "The last page of the result set doesn't include a cursor."
                    "cursor": page.cursor,
                }
            )
        )

    # -- POST /v2/orders/batch-retrieve ------------------------------------

    def batch_retrieve_orders(self, args: HandlerArgs) -> ReplyInit:
        """Retrieve many orders by id. DOCUMENTED: a missing id is ignored rather than erroring; a repeated id
        yields the order twice. https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders
        JUDGMENT: ``location_id`` (deprecated on Square's request object) scopes the result to that location
        when supplied.
        """
        request = validate_body(BatchRetrieveOrdersRequest, args.body())
        if len(request.order_ids) > MAX_BATCH_ORDER_IDS:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"A maximum of {MAX_BATCH_ORDER_IDS} orders can be retrieved per request.",
                field="order_ids",
                info={"supplied": len(request.order_ids), "maximum": MAX_BATCH_ORDER_IDS},
            )
        orders = args.ctx.store.collection(COL.orders)
        found: list[dict[str, Any]] = []
        for order_id in request.order_ids:
            stored = orders.get(order_id)
            if stored is None:
                continue
            order = OrderEntity.from_entity(stored)
            if request.location_id is not None and order.location_id != request.location_id:
                continue
            found.append(project_order(order))
        return json_({"orders": found})

    # -- POST /v2/orders/{order_id}/pay ------------------------------------

    def pay_order(self, args: HandlerArgs) -> ReplyInit:
        """Pay an OPEN order with the payments named, and complete it. DOCUMENTED: the ``payment_ids`` total must
        equal the order total; an order totaling 0 can be paid with an empty array.
        https://developer.squareup.com/reference/square/orders-api/pay-order

        Two readings of ``payment_ids``: each may name a payment this unit holds (APPROVED, at the order's
        location, unowned or owned by this order, summing to what is due -- moved to COMPLETED and journalled;
        a repeated id is refused), or none may resolve (the opaque form: the first id's tender carries what is
        due, the rest zero). JUDGMENT: nothing-due refuses opaque ids with 400; a mix of resolving and
        unresolving ids is refused naming the bad one. Completion is always :func:`apply_tenders`' decision,
        never this route's fiat.
        """
        request = validate_body(PayOrderRequest, args.body())
        order_id = args.params["order_id"]
        orders = args.ctx.store.collection(COL.orders)
        current = require_order(orders, order_id)
        subject = f"Order {order_id}"

        if current.state == OrderState.DRAFT:
            # DOCUMENTED: "Draft orders can be updated, but cannot be paid or fulfilled."
            # https://developer.squareup.com/reference/square/enums/OrderState
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=(f"{subject} is in state DRAFT and cannot be paid. A DRAFT order cannot be paid or fulfilled."),
                field="state",
                info={"from": OrderState.DRAFT.value, "to": OrderState.COMPLETED.value},
            )
        # COMPLETED -> COMPLETED is refused here; no terminal state allows a self-transition.
        _MACHINE.assert_transition(current.state, OrderState.COMPLETED.value, subject)

        total = order_total(current)
        due = amount_due(current)
        payment_ids = list(request.payment_ids or [])
        payments = args.ctx.store.collection(COL.payments)
        stored = _resolve_payments(payments, payment_ids, current)
        if stored is not None:
            paid = sum(p.amount_money.amount for p in stored)
            if paid != due:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"The payments' amount_money sum to {paid} but {due} is due on the order.",
                    field="payment_ids",
                    info={"payments_total": paid, "order_total": total, "due": due},
                )
        elif payment_ids and due == 0:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Nothing is due on {subject}; its tenders already cover the total.",
                field="payment_ids",
                info={"order_total": total, "due": due},
            )
        # No ids + something due: the placeholder tender kept for scenarios with no Payments surface use.
        # No ids + nothing due is the documented zero-total case: no tender.
        opaque_ids = payment_ids or (["unit-payment"] if due > 0 else [])

        def mutate(draft: Entity) -> None:
            # Minted inside the mutator so a version conflict (raised before this runs) draws nothing.
            now = args.ctx.clock.iso_ms()
            if stored is not None:
                tenders = [tender_for_payment(self._deps.ids, current, payment, now) for payment in stored]
            else:
                tenders = [
                    Tender(
                        id=self._deps.ids.tender(),
                        location_id=current.location_id,
                        transaction_id=current.id,
                        created_at=now,
                        # First tender carries exactly what's due, rest zero.
                        amount_money=Money(amount=due if index == 0 else 0, currency=current.currency),
                        payment_id=payment_id,
                    )
                    for index, payment_id in enumerate(opaque_ids)
                ]
            # Completion is `apply_tenders`' decision (due reaches zero); both branches guarantee that.
            apply_tenders(draft, tenders, now)

        updated = orders.update(
            order_id,
            mutate,
            expect_version=request.order_version,
            meta={"operation_id": "PayOrder"},
        )
        for payment in stored or ():
            capture_payment(payments, payment, order_id, "PayOrder")
        return json_({"order": project_order(OrderEntity.from_entity(updated))})

    # -- line items ---------------------------------------------------------

    def _new_line_items(
        self, ctx: UnitContext, items: Sequence[LineItemRequest], currency: str
    ) -> tuple[OrderLineItem, ...]:
        """Line items for CreateOrder: complete, or a 400 naming the index; nothing is synthesised (no quantity
        or price is refused rather than defaulted). Uids are minted only after the whole request passes, so a
        refusal draws nothing from the id stream.
        """
        built: list[OrderLineItem] = []
        for index, item in enumerate(items):
            path = f"order.line_items[{index}]"
            if item.uid is not None:
                _require_uid_unreserved(item.uid, f"{path}.uid")
            if not item.quantity:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail="quantity is required on every line item.",
                    field=f"{path}.quantity",
                )
            price = None if item.base_price_money is None else item.base_price_money.entity(currency)
            name = item.name
            variation_name = item.variation_name
            if item.catalog_object_id:
                variation = _require_variation(ctx, item.catalog_object_id, f"{path}.catalog_object_id")
                # Pricing resolves from the catalog when the caller does not override it.
                price = price or variation.price_money
                variation_name = variation_name or variation.variation_name
                name = name or _catalog_item_name(ctx, variation)
            if price is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail="A line item needs either base_price_money or a catalog_object_id with a fixed price.",
                    field=f"{path}.base_price_money",
                )
            built.append(
                OrderLineItem(
                    uid=item.uid or "",
                    quantity=item.quantity,
                    base_price_money=price,
                    name=name,
                    note=item.note,
                    catalog_object_id=item.catalog_object_id,
                    variation_name=variation_name,
                )
            )
        return tuple(built)

    def _line_patches(
        self, ctx: UnitContext, items: Sequence[LineItemRequest], currency: str
    ) -> tuple[_LinePatch, ...]:
        """Line items for UpdateOrder: only what the caller mentioned; an absent field stays absent, a
        present-but-null optional is a clear (hence two collections, not one dict with a sentinel). A new line
        comes back with an empty uid, minted after the dry run; see :meth:`update_order`.
        """
        patches: list[_LinePatch] = []
        for index, item in enumerate(items):
            path = f"order.line_items[{index}]"
            if item.uid is not None:
                _require_uid_unreserved(item.uid, f"{path}.uid")
            assign: dict[str, Any] = {}
            clear: list[str] = []

            if supplied(item, "quantity"):
                if not item.quantity:
                    raise _cannot_be_cleared(f"{path}.quantity")
                assign["quantity"] = item.quantity
            price: Money | None = None
            if supplied(item, "base_price_money"):
                if item.base_price_money is None:
                    raise _cannot_be_cleared(f"{path}.base_price_money")
                price = item.base_price_money.entity(currency)

            for name in _CLEARABLE_LINE_FIELDS:
                if not supplied(item, name):
                    continue
                value = getattr(item, name)
                if value:
                    assign[name] = value
                else:
                    clear.append(name)

            if item.catalog_object_id:
                variation = _require_variation(ctx, item.catalog_object_id, f"{path}.catalog_object_id")
                if price is None:
                    price = variation.price_money
                if not supplied(item, "variation_name") and variation.variation_name is not None:
                    assign["variation_name"] = variation.variation_name
                if not supplied(item, "name"):
                    item_name = _catalog_item_name(ctx, variation)
                    if item_name is not None:
                        assign["name"] = item_name
            if price is not None:
                assign["base_price_money"] = price.to_entity()

            patches.append(_LinePatch(uid=item.uid or "", assign=assign, clear=tuple(clear)))
        return tuple(patches)

    # -- fulfillments -------------------------------------------------------

    def _new_fulfillments(self, items: Sequence[FulfillmentRequest], now: str) -> tuple[Fulfillment, ...]:
        """Fulfillments for CreateOrder: a type each, PROPOSED unless stated (a seller's own app may create an
        order already RESERVED, stamped as that move would be). An unnamed uid is minted only after the whole
        request passes.
        """
        checked: list[tuple[str | None, str, str, dict[str, Any], tuple[tuple[str, Any], ...] | None]] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            path = f"order.fulfillments[{index}]"
            kind = _require_fulfillment_type(item.type, f"{path}.type")
            if item.uid is not None:
                _require_uid_unreserved(item.uid, f"{path}.uid")
                if item.uid in seen:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail=f"Fulfillment uid {item.uid} appears twice.",
                        field=f"{path}.uid",
                    )
                seen.add(item.uid)
            state = (item.state or FulfillmentState.PROPOSED.value).upper()
            if state != FulfillmentState.PROPOSED.value:
                _FULFILLMENT_MACHINE.assert_transition(FulfillmentState.PROPOSED.value, state, f"{path}")
            details = _details_assignments(item, kind, f"{path}")[0]
            supplied = _supplied_stamps(None, details, ())
            details.setdefault("placed_at", now)
            if state != FulfillmentState.PROPOSED.value:
                _stamp_transition(details, kind, state, now)
            checked.append((item.uid, kind, state, details, supplied))
        return tuple(
            _fulfillment(uid or "", kind, state, details, supplied) for uid, kind, state, details, supplied in checked
        )

    def _fulfillment_patches(
        self, current: OrderEntity, items: Sequence[FulfillmentRequest]
    ) -> tuple[_FulfillmentPatch, ...]:
        """Fulfillments for UpdateOrder: only what the caller mentioned; transitions are asserted here, against
        stored state, so an illegal move is refused with no version bump. JUDGMENT: an unknown ``uid`` is
        refused rather than appended (unlike a line item) so a stale retry can't silently create a duplicate
        PROPOSED fulfillment. A new entry's uid is minted after the whole request passes, as on create.
        """
        by_uid = {f.uid: f for f in current.fulfillments}
        checked: list[tuple[str | None, dict[str, Any], dict[str, Any], tuple[str, ...]]] = []
        for index, item in enumerate(items):
            path = f"order.fulfillments[{index}]"
            if item.uid is not None:
                _require_uid_unreserved(item.uid, f"{path}.uid")
            prior = None if item.uid is None else by_uid.get(item.uid)
            assign: dict[str, Any] = {}
            if item.uid is not None and prior is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=(
                        f"{path}.uid {item.uid} names no fulfillment on this order. Omit uid to add a new "
                        "fulfillment; a uid is minted for it."
                    ),
                    field=f"{path}.uid",
                    info={"known": [f.uid for f in current.fulfillments]},
                )
            if prior is None:
                kind = _require_fulfillment_type(item.type, f"{path}.type")
                assign["type"] = kind
                state = (item.state or FulfillmentState.PROPOSED.value).upper()
                if state != FulfillmentState.PROPOSED.value:
                    _FULFILLMENT_MACHINE.assert_transition(FulfillmentState.PROPOSED.value, state, path)
                assign["state"] = state
            else:
                kind = prior.type
                if supplied(item, "type") and item.type is not None and item.type.upper() != kind:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail=f"Fulfillment {prior.uid} is a {kind} fulfillment; its type cannot change.",
                        field=f"{path}.type",
                    )
                if supplied(item, "state"):
                    if item.state is None:
                        raise _cannot_be_cleared(f"{path}.state")
                    state = item.state.upper()
                    _FULFILLMENT_MACHINE.assert_transition(prior.state, state, f"Fulfillment {prior.uid}")
                    assign["state"] = state
            details_assign, details_clear = _details_assignments(item, kind, path)
            checked.append((item.uid, assign, details_assign, tuple(details_clear)))
        return tuple(
            _FulfillmentPatch(uid=uid or "", assign=assign, details_assign=details_assign, details_clear=details_clear)
            for uid, assign, details_assign, details_clear in checked
        )


# ---------------------------------------------------------------------------
# Module-level helpers: pure where they can be, and testable on their own.
# ---------------------------------------------------------------------------


def order_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Orders routes for one vendor."""
    return OrdersSurface(deps).routes()


def _cannot_be_cleared(field: str) -> UnitError:
    """A null on a field an order cannot be without; refused rather than silently ignored so a caller who
    meant to clear something doesn't believe they had.
    """
    return UnitError(
        UnitErrorKind.INVALID_VALUE,
        detail=f"{field} cannot be cleared; an order requires it.",
        field=field,
    )


def require_order(orders: Collection, order_id: str) -> OrderEntity:
    """The order, or Square's own wording for a miss."""
    stored = orders.get(order_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.NOT_FOUND,
            detail=f"Order {order_id} was not found.",
            field="order_id",
        )
    return OrderEntity.from_entity(stored)


def _require_location(ctx: UnitContext, location_id: str) -> LocationEntity:
    """The location, or a 400 that lists the ones this unit has. ``invalid_value`` and not ``not_found``: the
    order doesn't exist yet, so what's wrong is the value the caller sent."""
    locations = ctx.store.collection(COL.locations)
    stored = locations.get(location_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Location {location_id} does not exist for this merchant.",
            field="order.location_id",
            info={"known": [str(entity["id"]) for entity in locations.all()]},
        )
    return LocationEntity.from_entity(stored)


def _require_variation(ctx: UnitContext, catalog_object_id: str, field: str) -> CatalogObjectEntity:
    """The ITEM_VARIATION a line item names, or a 400 naming the field."""
    stored = ctx.store.collection(COL.catalog).get(catalog_object_id)
    variation = None if stored is None else CatalogObjectEntity.from_entity(stored)
    if variation is None or not variation.is_variation:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"catalog_object_id {catalog_object_id} is not an ITEM_VARIATION in this catalog.",
            field=field,
        )
    return variation


def _catalog_item_name(ctx: UnitContext, variation: CatalogObjectEntity) -> str | None:
    """The parent ITEM's name, which is what Square puts in ``line_item.name``."""
    if variation.item_id is None:
        return None
    parent = ctx.store.collection(COL.catalog).get(variation.item_id)
    return None if parent is None else CatalogObjectEntity.from_entity(parent).item_name


def _lines_of(draft: Entity) -> list[dict[str, Any]]:
    """The stored line items of a draft entity, as dicts."""
    stored = draft.get("line_items")
    if not isinstance(stored, list):
        return []
    return [dict(line) for line in stored if isinstance(line, dict)]


def _merge_line_items(existing: Sequence[dict[str, Any]], patches: Sequence[_LinePatch]) -> list[dict[str, Any]]:
    """Sparse merge by uid: a known uid updates in place, an unknown one appends. "In place" means position too --
    a renamed line must not move to the end, since line order is on the wire and a receipt reads top to bottom."""
    by_uid: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for line in existing:
        uid = str(line.get("uid", ""))
        by_uid[uid] = line
        order.append(uid)
    for patch in patches:
        prior = by_uid.get(patch.uid)
        if prior is None:
            if "quantity" not in patch.assign or "base_price_money" not in patch.assign:
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail=f"Line item {patch.uid} is new, so it needs a quantity and a price.",
                    field="order.line_items",
                )
            by_uid[patch.uid] = {"uid": patch.uid, **patch.assign}
            order.append(patch.uid)
            continue
        merged = {**prior, **patch.assign}
        for name in patch.clear:
            merged.pop(name, None)
        by_uid[patch.uid] = merged
    return [by_uid[uid] for uid in order]


def _assign_or_clear(draft: Entity, patch: Any, name: str) -> None:
    """Apply one sparse order-level field: absent does nothing, truthy assigns, null/empty pops (never sets
    ``None``) -- the "absence is absence" invariant in :mod:`vendorfake.square.entities`."""
    if not supplied(patch, name):
        return
    value = getattr(patch, name)
    if value:
        draft[name] = value
    else:
        draft.pop(name, None)


RESERVED_UID_PREFIX = "#"
"""A caller-supplied line or fulfillment uid may not start with this. JUDGMENT: Square reserves ``#`` for
client-chosen temporary catalog-upsert ids, so a caller-chosen ``#...`` uid here is never legitimate."""


def _require_uid_unreserved(uid: str, field: str) -> None:
    if uid.startswith(RESERVED_UID_PREFIX):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field} may not start with {RESERVED_UID_PREFIX!r}; that prefix is reserved for temporary ids.",
            field=field,
            info={"supplied": uid},
        )


def _placeholders(patches: tuple[_LinePatch, ...] | None, taken: set[str]) -> tuple[_LinePatch, ...] | None:
    """The patches with a distinct placeholder uid on each new line, for the dry merge. Each placeholder carries
    a NUL byte (no request uid can) lengthened until unused, so it never collides."""
    if patches is None:
        return None
    used = set(taken) | {p.uid for p in patches if p.uid}
    out: list[_LinePatch] = []
    for index, patch in enumerate(patches):
        if patch.uid:
            out.append(patch)
            continue
        candidate = f"\x00new{index}"
        while candidate in used:
            candidate += "\x00"
        used.add(candidate)
        out.append(replace(patch, uid=candidate))
    return tuple(out)


def _fresh(named: tuple[_LinePatch, ...] | None, assigned: tuple[_LinePatch, ...] | None) -> frozenset[str]:
    """The uids this request assigned to lines the caller left unnamed -- placeholders on the dry run, minted
    uids on the commit. ``named``/``assigned`` are the same patches before/after assignment."""
    if named is None or assigned is None:
        return frozenset()
    return frozenset(after.uid for before, after in zip(named, assigned, strict=True) if not before.uid)


def _apply_line_changes(
    draft: Entity,
    clears_line_items: bool,
    patches: tuple[_LinePatch, ...] | None,
    fields_to_clear: Sequence[str],
    *,
    fresh: frozenset[str],
) -> None:
    """Every change an update makes to ``line_items``, in one place, so the dry run and the real mutator cannot
    disagree. ``fresh`` holds uids this request itself assigned to unnamed new lines; a clear naming one is
    ignored, matching Square's "an inapplicable clear is silently ignored."
    """
    if clears_line_items:
        draft["line_items"] = []
    elif patches is not None:
        draft["line_items"] = _merge_line_items(_lines_of(draft), patches)
    _apply_line_fields_to_clear(draft, fields_to_clear, fresh=fresh)


def _assert_tendered_floor(order: OrderEntity, subject: str) -> None:
    """Refuse a merged order whose lines total less than its tenders already applied."""
    applied = sum(tender.applied for tender in order.tenders)
    total = order_total(order)
    if total < applied:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=(
                f"{subject} cannot be reduced to {total}: its tenders have already applied {applied}. "
                "An order cannot be reduced below what has been tendered."
            ),
            field="order.line_items",
            info={"order_total": total, "tendered": applied},
        )


def _apply_line_fields_to_clear(draft: Entity, paths: Sequence[str], *, fresh: frozenset[str] = frozenset()) -> None:
    """The ``line_items`` half of :func:`_apply_fields_to_clear`. A path
    naming a uid in ``fresh`` is skipped; see :func:`_apply_line_changes`."""
    for path in paths:
        match = _LINE_ITEM_PATH.match(path)
        if match is not None:
            uid, sub = match.group(1), match.group(2)
            if uid in fresh:
                continue
            lines = _lines_of(draft)
            if sub is None:
                draft["line_items"] = [line for line in lines if line.get("uid") != uid]
            elif sub in _CLEARABLE_LINE_FIELDS:
                for line in lines:
                    if line.get("uid") == uid:
                        line.pop(sub, None)
                draft["line_items"] = lines
        elif path == "line_items":
            draft["line_items"] = []


def _apply_order_fields_to_clear(draft: Entity, paths: Sequence[str]) -> None:
    """The order-level half of :func:`_apply_fields_to_clear`."""
    for path in paths:
        if _LINE_ITEM_PATH.match(path) is None and path in _CLEARABLE_ORDER_FIELDS:
            draft.pop(path, None)


def _apply_fields_to_clear(draft: Entity, paths: Sequence[str]) -> None:
    """Square's dot/bracket clear notation (``reference_id``, ``line_items``, ``line_items[uid]``,
    ``line_items[uid].note``). DOCUMENTED: anything else is silently ignored while the version still
    increments. https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    _apply_line_fields_to_clear(draft, paths)
    _apply_order_fields_to_clear(draft, paths)


def _require_fulfillment_type(raw: str | None, field: str) -> str:
    if not raw:
        raise UnitError(UnitErrorKind.MISSING_FIELD, detail="A new fulfillment needs a type.", field=field)
    kind = raw.upper()
    if kind not in FULFILLMENT_TYPES:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"type must be one of {', '.join(FULFILLMENT_TYPES)}.",
            field=field,
            info={"allowed": list(FULFILLMENT_TYPES)},
        )
    return kind


def _details_assignments(item: FulfillmentRequest, kind: str, path: str) -> tuple[dict[str, Any], list[str]]:
    """The details fields a request sets and the ones it clears, for the details object the fulfillment's type
    carries. A details object for a different type is refused, since Square's object cannot represent it."""
    wanted = _DETAILS_KEY[kind]
    for other in _DETAILS_KEY.values():
        if other != wanted and supplied(item, other) and getattr(item, other) is not None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"{path}.{other} does not apply to a {kind} fulfillment; send {wanted}.",
                field=f"{path}.{other}",
            )
    details = getattr(item, wanted)
    assign: dict[str, Any] = {}
    clear: list[str] = []
    if details is None:
        return assign, clear
    for name in details.model_fields_set:
        value = getattr(details, name)
        if value is None:
            clear.append(name)
        elif hasattr(value, "model_dump"):
            assign[name] = compact(value.model_dump())
        else:
            assign[name] = value
    return assign, clear


def _fulfillment(
    uid: str, kind: str, state: str, details: dict[str, Any], supplied: tuple[tuple[str, Any], ...] | None
) -> Fulfillment:
    """A fulfillment whose one details object is the one its type carries."""
    return Fulfillment(
        uid=uid,
        type=kind,
        state=state,
        pickup_details=details if kind == "PICKUP" else None,
        delivery_details=details if kind == "DELIVERY" else None,
        shipment_details=details if kind == "SHIPMENT" else None,
        supplied_stamps=supplied,
    )


def _supplied_stamps(prior: Any, assign: Mapping[str, Any], clear: Sequence[str]) -> tuple[tuple[str, Any], ...] | None:
    """The caller-supplied values for volatile stamp names after this request, as pairs sorted by name (or
    ``None`` if empty) -- pairs, not a mapping, since the digest scrubs a volatile name at any depth."""
    mirror: dict[str, Any] = {}
    if isinstance(prior, list | tuple):
        mirror.update({str(pair[0]): pair[1] for pair in prior if isinstance(pair, list | tuple) and len(pair) == 2})
    mirror.update({k: v for k, v in assign.items() if k in _SHADOWED_STAMPS})
    for name in clear:
        mirror.pop(name, None)
    return tuple(sorted(mirror.items())) or None


def _stamp_transition(details: dict[str, Any], kind: str, state: str, now: str) -> None:
    """Set the stamp a transition implies, unless the caller already did.
    JUDGMENT; see the module docstring."""
    stamp = _TRANSITION_STAMPS.get((kind, state))
    if stamp is not None and not details.get(stamp):
        details[stamp] = now


def _fulfillments_of(draft: Entity) -> list[dict[str, Any]]:
    stored = draft.get("fulfillments")
    if not isinstance(stored, list):
        return []
    return [dict(f) for f in stored if isinstance(f, dict)]


def _merge_fulfillments(
    existing: Sequence[dict[str, Any]], patches: Sequence[_FulfillmentPatch], now: str
) -> list[dict[str, Any]]:
    """Sparse merge by uid, in place, with the details merged one level down (position preserved, as for line
    items); a transition stamps its details field when the patch didn't set it."""
    by_uid: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for f in existing:
        uid = str(f.get("uid", ""))
        by_uid[uid] = f
        order.append(uid)
    for patch in patches:
        prior = by_uid.get(patch.uid)
        if prior is None:
            kind = str(patch.assign["type"])
            details = dict(patch.details_assign)
            supplied = _supplied_stamps(None, details, ())
            details.setdefault("placed_at", now)
            state = str(patch.assign.get("state", FulfillmentState.PROPOSED.value))
            if state != FulfillmentState.PROPOSED.value:
                _stamp_transition(details, kind, state, now)
            by_uid[patch.uid] = _fulfillment(patch.uid, kind, state, details, supplied).to_entity()
            order.append(patch.uid)
            continue
        merged = {**prior, **patch.assign}
        kind = str(merged.get("type", "PICKUP"))
        key = _DETAILS_KEY.get(kind, "pickup_details")
        stored_details = merged.get(key)
        details = dict(stored_details) if isinstance(stored_details, dict) else {}
        details.update(patch.details_assign)
        for name in patch.details_clear:
            details.pop(name, None)
        new_state = patch.assign.get("state")
        if new_state is not None and new_state != prior.get("state"):
            _stamp_transition(details, kind, str(new_state), now)
        if details:
            merged[key] = details
        else:
            merged.pop(key, None)
        supplied = _supplied_stamps(prior.get("supplied_stamps"), patch.details_assign, patch.details_clear)
        if supplied:
            merged["supplied_stamps"] = [list(pair) for pair in supplied]
        else:
            merged.pop("supplied_stamps", None)
        by_uid[patch.uid] = merged
    return [by_uid[uid] for uid in order]


def _resolve_payments(
    payments: Collection, payment_ids: Sequence[str], order: OrderEntity
) -> list[PaymentEntity] | None:
    """The stored payments ``payment_ids`` name, or ``None`` when none is stored. See
    :meth:`OrdersSurface.pay_order` for the two readings and why a mix is refused."""
    found: list[PaymentEntity | None] = [
        None if (raw := payments.get(pid)) is None else PaymentEntity.from_entity(raw) for pid in payment_ids
    ]
    if not any(p is not None for p in found):
        return None
    seen: set[str] = set()
    resolved: list[PaymentEntity] = []
    for pid, payment in zip(payment_ids, found, strict=True):
        if pid in seen:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment {pid} is listed more than once; a payment can tender an order only once.",
                field="payment_ids",
                info={"payment_id": pid},
            )
        seen.add(pid)
        if payment is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment {pid} was not found.",
                field="payment_ids",
                info={"payment_id": pid},
            )
        if payment.status != PaymentState.APPROVED.value:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment {pid} is {payment.status}; only an APPROVED payment can pay an order.",
                field="payment_ids",
                info={"payment_id": pid, "status": payment.status},
            )
        if payment.order_id is not None and payment.order_id != order.id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment {pid} belongs to order {payment.order_id}, not {order.id}.",
                field="payment_ids",
                info={"payment_id": pid, "order_id": payment.order_id},
            )
        if payment.location_id != order.location_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Payment {pid} was taken at location {payment.location_id}, not the order's {order.location_id}.",
                field="payment_ids",
                info={
                    "payment_id": pid,
                    "payment_location_id": payment.location_id,
                    "order_location_id": order.location_id,
                },
            )
        resolved.append(payment)
    return resolved


def capture_payment(payments: Collection, payment: PaymentEntity, order_id: str | None, operation_id: str) -> Entity:
    """Move a payment to COMPLETED through the payment machine, journalled; the one place ``status`` is written
    to COMPLETED, so the terminal/self-transition rule is asserted on every capture."""
    _PAYMENT_MACHINE.assert_transition(payment.status, PaymentState.COMPLETED.value, f"Payment {payment.id}")

    def mutate(draft: Entity) -> None:
        draft["status"] = PaymentState.COMPLETED.value
        if order_id is not None:
            draft["order_id"] = order_id

    return payments.update(payment.id, mutate, meta={"operation_id": operation_id})


def tender_for_payment(ids: SquareIds, order: OrderEntity, payment: PaymentEntity, now: str) -> Tender:
    """The tender a completed payment adds to its order. JUDGMENT: ``type`` is ``OTHER`` for an external payment,
    since Square's ``TenderType`` (https://developer.squareup.com/reference/square/enums/TenderType) has no
    ``EXTERNAL`` member or mapping from ``source_type``."""
    return Tender(
        id=ids.tender(),
        location_id=order.location_id,
        transaction_id=order.id,
        created_at=now,
        # "The total amount of the tender, including `tip_money`."
        amount_money=Money(amount=payment.total, currency=order.currency),
        type="OTHER" if payment.source_type == "EXTERNAL" else "CARD",
        payment_id=payment.id,
        tip_money=None
        if payment.tip_money is None
        else Money(amount=payment.tip_money.amount, currency=order.currency),
    )


def apply_tenders(draft: Entity, tenders: Sequence[Tender], now: str) -> None:
    """Append ``tenders`` to an order draft and complete it once paid -- shared by PayOrder and the Payments
    surface, via :func:`~vendorfake.square.model.order.amount_due` reaching zero (counting what a tender
    applies, not its tip). NOT VERIFIED: a partial payment leaves the order OPEN with the remainder in
    ``net_amount_due_money``. https://developer.squareup.com/docs/orders-api/pay-for-orders
    """
    existing = draft.get("tenders")
    stored = [dict(t) for t in existing if isinstance(t, dict)] if isinstance(existing, list) else []
    stored.extend(tender.to_entity() for tender in tenders)
    draft["tenders"] = stored
    if draft.get("state") == OrderState.OPEN.value and amount_due(OrderEntity.from_entity(draft)) == 0:
        _complete_order(draft, now)


def _complete_order(draft: Entity, now: str) -> None:
    """The terminal move into COMPLETED, however it was reached."""
    if draft.get("state") == OrderState.COMPLETED.value:
        return
    draft["state"] = OrderState.COMPLETED.value
    draft["closed_at"] = now
    _drop_zero_quantity_lines(draft)


def _drop_zero_quantity_lines(draft: Entity) -> None:
    """Completing an order removes lines whose quantity is zero. DOCUMENTED: "removed when paying for or
    otherwise completing the order"; a CANCELED transition does not, since the sentence says completing.
    https://developer.squareup.com/reference/square/objects/OrderLineItem
    Quantity is parsed like the money projection's numbers, so ``"0"``/``"0.0"``/``"0.00"`` all count and
    junk is left alone.
    """
    lines = _lines_of(draft)
    kept = [line for line in lines if not _is_zero_quantity(line)]
    if len(kept) != len(lines):
        draft["line_items"] = kept


def _is_zero_quantity(line: Mapping[str, Any]) -> bool:
    """Whether a stored line's ``quantity`` is the documented literal zero."""
    raw = line.get("quantity")
    if not isinstance(raw, str):
        return False
    quantity = js_parse_float(raw)
    return quantity == 0.0


def _within(value: str | None, start_at: str | None, end_at: str | None) -> bool:
    """Square's range semantics: start inclusive, end exclusive. A value-less order is excluded -- an open order
    has no ``closed_at``, and "closed between Monday and Tuesday" must not match it."""
    if not value:
        return False
    at = instant_ms(value)
    if at is None:
        return True
    start = instant_ms(start_at)
    if start is not None and at < start:
        return False
    end = instant_ms(end_at)
    return not (end is not None and at >= end)
