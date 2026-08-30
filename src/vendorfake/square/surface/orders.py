"""The Orders surface: the stateful heart of this vendor.

FOR: reproducing the six Orders operations a point-of-sale integration actually
drives, with the behaviours Square documents and consumers get wrong -- the
version field and optimistic concurrency, sparse updates, the four-state
lifecycle with two terminal states, and cursor pagination with a lifetime and a
query fingerprint.

=====================  =========================================================
CreateOrder            ``POST /v2/orders``
                       https://developer.squareup.com/reference/square/orders-api/create-order
CreateOrder (legacy)   ``POST /v2/locations/{location_id}/orders``
                       the path CreateOrder had before ``location_id`` moved
                       into the body; see :meth:`OrdersSurface.create_order_at_location`
RetrieveOrder          ``GET  /v2/orders/{order_id}``
                       https://developer.squareup.com/reference/square/orders-api/retrieve-order
UpdateOrder            ``PUT  /v2/orders/{order_id}``
                       https://developer.squareup.com/reference/square/orders-api/update-order
SearchOrders           ``POST /v2/orders/search``
                       https://developer.squareup.com/reference/square/orders-api/search-orders
BatchRetrieveOrders    ``POST /v2/orders/batch-retrieve``
                       https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders
PayOrder               ``POST /v2/orders/{order_id}/pay``
                       https://developer.squareup.com/reference/square/orders-api/pay-order
=====================  =========================================================

INVARIANT: **a rejected request changes nothing.** Every mutation goes through
``Collection.update``, which checks ``expect_version`` before it runs the
mutator and copies before it commits, so a version conflict, an illegal
transition or a bad line item leaves no entity change, no version bump and --
because the journal is the event source -- no webhook. Nothing here writes to
the store outside that call.

Absent, null and cleared
------------------------
UpdateOrder is sparse: "your request should only include the properties that
you want to add, update, or clear"
(https://developer.squareup.com/docs/orders-api/manage-orders/update-orders).
So a field the caller did not mention must survive untouched, and the only way
to know which those are is
:func:`~vendorfake.square.model.order.supplied`, i.e. Pydantic's
``model_fields_set``. Testing the parsed value against ``None`` collapses "not
mentioned" into "clear it", which silently wipes every optional on a
read-modify-write round trip -- the most common way an integration uses this
endpoint.

JUDGMENT -- **null clears an optional field.** Square documents exactly one way
to clear: name the field in ``fields_to_clear``. It says nothing about sending
``null``. This unit accepts both, which is what the reference does by accident
(its ``optionalString`` maps ``null`` to ``undefined``, and the spread that
merges a line-item patch then deletes the key). Making it a rule rather than an
accident lets it be stated: on the sparse ``order`` object and on a line-item
patch, a field that is present and null -- or an empty string -- is cleared,
exactly as if it had been named in ``fields_to_clear``. What cannot be cleared
is what an order cannot be without: ``state``, ``version``, and a line item's
``quantity`` and ``base_price_money``. Null on one of those is ``invalid_value``
naming the field, not a silent no-op.

The reference is narrower in two places and this file is deliberately wider,
because Square's notation is general and the reference's restriction was not:
``fields_to_clear`` accepts ``line_items[uid].name``,
``line_items[uid].catalog_object_id`` and ``line_items[uid].variation_name``
alongside the reference's single ``line_items[uid].note``; and
``"line_items": null`` empties the list where the reference raised
``invalid_value``. Everything else about ``fields_to_clear`` is Square's:
"line_items[coffee_uid].applied_discounts[discount_uid]" style paths, and a
clear that cannot be applied is **silently ignored while the version still
increments** -- "On a 200 response, Square has incremented the order version,
even if all requested property changes are ignored and no changes are actually
made."

The double-pay fix
------------------
The reference's ``assertTransition`` returns early when ``from === to``, so
PayOrder on an order that was already COMPLETED returned 200, replaced the
tenders and bumped the version -- a second payment against a closed order. The
core's state machine forbids a self-transition unless the state declares
``allow_self``, and neither terminal state can, so the same call is now
``invalid_transition``. See :mod:`vendorfake.square.machine` and
:mod:`vendorfake.core.state.machine`.

Zero-quantity lines
-------------------
"Line items with a quantity of `0` are automatically removed when paying for or
otherwise completing the order."
https://developer.squareup.com/reference/square/objects/OrderLineItem

Documented, and neither the reference nor the first cut of this file did it: a
line with ``"quantity": "0"`` survived PayOrder, so a consumer who sends one --
which is exactly what a cart UI does when a customer zeroes an item -- reads
back an order Square would not have returned. Both completion paths now drop
them; see :func:`_drop_zero_quantity_lines`.

Fulfillments
------------
An order carries ``fulfillments`` -- how the buyer receives it -- each with a
``type`` and a ``state`` that moves through the fulfillment machine in
:mod:`vendorfake.square.machine`. UpdateOrder merges them sparsely by ``uid``
exactly as it merges line items: a known uid updates in place, an unknown one
appends and must name a ``type``, a details field that is present and null is
cleared. A state change is asserted against the machine before anything is
written, so an illegal move is a 400 with no version bump.

JUDGMENT -- **a transition stamps the timestamp Square would stamp**, when the
caller did not supply it: ``placed_at`` on creation, ``accepted_at`` on
RESERVED, ``ready_at`` / ``packaged_at`` on PREPARED, ``picked_up_at`` /
``delivered_at`` / ``shipped_at`` on COMPLETED, ``canceled_at`` on CANCELED,
``failed_at`` on FAILED -- each in the details object named for the type. The
field descriptions on the three details pages say what each stamp records
("The timestamp indicating when the fulfillment was picked up by the
recipient"); that Square sets them itself on the corresponding move is the
reading, and it is NOT VERIFIED which of them Square would also accept from a
caller. See :func:`_stamp_transition`.

SHRINK (prototype): CalculateOrder and CloneOrder are not implemented -- they
add no state behaviour over the routes above. Taxes, discounts, service
charges, returns and refunds are not modelled, and a fulfillment's
``entries`` (``line_item_application: ENTRY_LIST``) is not; see
:mod:`vendorfake.square.model.order`. PayOrder's ``payment_ids`` resolve
against the Payments surface when they name stored payments and are otherwise
opaque references whose tender total is taken from the order; see
:meth:`OrdersSurface.pay_order`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    IdempotencySpec,
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
from vendorfake.square.seed.constants import SEED_LOCATION_ID
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
"""One instance, at module level, because a :class:`StateMachine` holds no
entity and no store -- it is the definition plus the two assertions over it."""

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
"""Which details stamp a transition sets when the caller did not. JUDGMENT;
see the module docstring."""

_CREATABLE_STATES: tuple[str, ...] = (OrderState.OPEN.value, OrderState.DRAFT.value)
"""CreateOrder accepts these two. The terminal pair cannot be a starting state:
"Completed orders are fully paid" and "Canceled orders are not paid" both
describe an order that has already been somewhere."""

_SORT_FIELDS: Mapping[str, str] = {
    "CREATED_AT": "created_at",
    "UPDATED_AT": "updated_at",
    "CLOSED_AT": "closed_at",
}
"""``sort_field`` to the entity field it orders by, and to the
``date_time_filter`` key that must accompany it."""

_SORT_ORDERS: tuple[str, ...] = ("ASC", "DESC")

_CLEARABLE_ORDER_FIELDS: frozenset[str] = frozenset({"reference_id", "customer_id", "ticket_name", "metadata"})
"""Order-level fields ``fields_to_clear`` and a null both remove."""

_CLEARABLE_LINE_FIELDS: tuple[str, ...] = ("name", "note", "catalog_object_id", "variation_name")
"""Line-item fields a null clears -- everything a line can be without."""

_LINE_ITEM_PATH = re.compile(r"^line_items\[([^\]]+)\](?:\.(.+))?$")
"""Square's bracket notation for a line item inside ``fields_to_clear``, e.g.
``line_items[coffee_uid]`` or ``line_items[coffee_uid].note``."""


@dataclass(frozen=True, slots=True)
class _LinePatch:
    """One sparse line-item change, split into what it sets and what it removes.

    Two mappings rather than one dict with a sentinel, because the merge has to
    ``pop`` a cleared key and never write ``None`` into it -- see the "absence
    is absence" invariant in :mod:`vendorfake.square.entities`.
    """

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
        """The literal paths come first.

        The router returns the first candidate whose method matches, so the two
        collection-level POSTs would work in any order -- but reading
        ``/v2/orders/search`` after ``/v2/orders/{order_id}`` invites the next
        person to add ``POST /v2/orders/{order_id}`` above them and shadow both.
        """
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
                # The minimum body CreateOrder accepts, published so that a
                # language-independent check can cause a committed mutation
                # rather than only observing seed inserts. "The order object
                # must include a location_id"
                # (https://developer.squareup.com/reference/square/orders-api/create-order),
                # and the id has to be one the scenario actually holds -- an
                # example naming an invented location would be an example the
                # route refuses, which is worse than publishing none.
                #
                # `idempotency_key` is deliberately absent: whoever sends this
                # body supplies their own, and a shipped constant would make
                # every caller of the example collide with every other.
                example_body={"order": {"location_id": SEED_LOCATION_ID}},
            ),
            Route(
                method="POST",
                path="/v2/locations/{location_id}/orders",
                capability=CAPABILITY,
                handler=self.create_order_at_location,
                auth="bearer",
                scopes=("ORDERS_WRITE",),
                # The same scope as CreateOrder: one key, one order, whichever
                # path a client used to send it.
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.create"),
                operation_id="CreateOrderAtLocation",
                summary="CreateOrder on its pre-2019 path; the location comes from the URL.",
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
                # "If you don't provide a new idempotency_key with each update
                # request, you get a 200 response but the returned order doesn't
                # reflect any of your updates." That is `replay`, not `conflict`.
                # https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.update", on_mismatch="replay"),
                operation_id="UpdateOrder",
                summary="Sparse update under optimistic concurrency.",
            ),
            Route(
                method="POST",
                path="/v2/orders/{order_id}/pay",
                capability=CAPABILITY,
                handler=self.pay_order,
                auth="bearer",
                scopes=("ORDERS_WRITE", "PAYMENTS_WRITE"),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="orders.pay", required=True),
                operation_id="PayOrder",
                summary="Pay an open order and move it to COMPLETED.",
            ),
        )

    # -- POST /v2/orders ----------------------------------------------------

    def create_order(self, args: HandlerArgs) -> ReplyInit:
        return self._create(args, validate_body(CreateOrderRequest, args.body()))

    # -- POST /v2/locations/{location_id}/orders ----------------------------

    def create_order_at_location(self, args: HandlerArgs) -> ReplyInit:
        """CreateOrder, on the path it had before the location moved into the body.

        Raw clients built against an older ``Square-Version`` still send
        ``POST /v2/locations/{location_id}/orders`` with the same body, and the
        current reference documents only the new path
        (https://developer.squareup.com/reference/square/orders-api/create-order).
        The move is recorded in Square's changelog
        (https://developer.squareup.com/docs/changelog/connect); the exact
        version that made it is NOT VERIFIED here.

        JUDGMENT -- the URL's location is authoritative. A body that omits
        ``order.location_id`` takes it from the path; a body that states a
        *different* one is refused with ``invalid_value`` rather than silently
        overridden either way, because an order created at a location the
        caller did not name in one of the two places is the bug this route
        would otherwise hide. Everything else -- validation, pricing, the
        idempotency scope -- is CreateOrder's, by delegation.
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

        # Validate the lines and the fulfillments before minting the order
        # id, so a refused create draws nothing from the id stream and the
        # next accepted one mints the id it would have minted anyway.
        line_items = self._new_line_items(args.ctx, spec.line_items or [], location.currency)
        fulfillments = self._new_fulfillments(spec.fulfillments or [], args.ctx.clock.iso_ms())
        entity = OrderEntity(
            id=self._deps.ids.order(),
            location_id=location.id,
            merchant_id=location.merchant_id,
            # The currency is the location's, never the request's: an order
            # cannot be denominated in something the seller does not take.
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

        # The version, before anything that mints: the store would refuse the
        # write at `expect_version` anyway, but by then `_line_patches` and
        # `_fulfillment_patches` have drawn uids for new entries, and a stale
        # write that shifts every later id is the drift PayOrder avoids by
        # minting inside its mutator. Same kind and wording as the store's.
        if patch.version != current.version:
            raise UnitError(
                UnitErrorKind.VERSION_CONFLICT,
                detail=(
                    f"Supplied version {patch.version} does not match the current version "
                    f"{current.version} of orders {order_id}."
                ),
                info={"collection": COL.orders, "id": order_id, "supplied": patch.version, "current": current.version},
            )

        # Terminality first, then the move: "this order is finished" explains
        # "that move is not allowed", and reporting them the other way round
        # tells a consumer to consult a lifecycle diagram they cannot use.
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

        def mutate(draft: Entity) -> None:
            if clears_line_items:
                draft["line_items"] = []
            elif patches is not None:
                draft["line_items"] = _merge_line_items(_lines_of(draft), patches)
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
            _apply_fields_to_clear(draft, request.fields_to_clear)
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
        """Search the merchant's orders, one location set at a time.

        ``location_ids`` is required: "Your request must include one or more
        `location_ids`. `SearchOrders` only returns the orders for those
        locations."
        https://developer.squareup.com/docs/orders-api/manage-orders/search-orders

        The reference typed it optional and answered 200 with every location's
        orders when it was omitted, which is the one shape Square will not
        answer -- so a consumer whose query is missing the field builds a page
        of results here and gets an error in production.

        JUDGMENT -- the status. Square publishes no error code for the omission;
        this unit answers its standard 400 ``MISSING_FIELD`` naming
        ``location_ids``, which is what every other absent required field on
        this surface answers.
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
                # "If you use the DateTimeFilter in a SearchOrders query, you
                # must set the sort_field in OrdersSort to the same field you
                # filter for."
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

        # Code point, never locale collation: `localeCompare` puts "a" before
        # "B" and Python's `sorted` does not, Square order ids are mixed case,
        # and the page order is on the wire. `reverse=` inverts the tie-break
        # too, which is what negating the whole comparison does in the
        # reference.
        sort_key = _SORT_FIELDS[sort_field]
        orders.sort(key=lambda order: (getattr(order, sort_key) or "", order.id), reverse=sort_order == "DESC")

        # The fingerprint is the whole request except paging, which is how the
        # cursor enforces "you must use the original query" while still letting
        # a caller change the page size.
        fingerprint = {name: value for name, value in body.items() if name not in ("cursor", "limit")}
        page = collection.paginate(
            orders,
            limit=request.limit,
            cursor=request.cursor,
            fingerprint=fingerprint,
            default_limit=SEARCH_DEFAULT_LIMIT,
            max_limit=SEARCH_MAX_LIMIT,
        )
        # `orders` and `order_entries` are the answer to the request rather
        # than properties of an object, so the one that was asked for is
        # present even when it is empty; see "Empty arrays, in one rule" in
        # :mod:`vendorfake.square.model.order`.
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
        """Retrieve many orders by id.

        "If a given order ID does not exist, the ID is ignored instead of
        generating an error", and the response's ``orders`` array holds "the
        requested orders, omitting any that don't exist"
        (https://developer.squareup.com/reference/square/orders-api/batch-retrieve-orders).
        That is a map-and-filter over the ids as sent, so a repeated id yields
        the order twice; Square documents nothing either way, and the literal
        reading is the one that needs no extra rule.

        JUDGMENT -- ``location_id`` is deprecated on Square's own request object
        and documented only as "omit it to retrieve orders within the scope of
        the current authorization's merchant ID". Supplying it here scopes the
        result to that location, which is what the name says; Square publishes
        no behaviour to defer to.
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
        """Pay an OPEN order with the payments named, and complete it.

        "The total of the `payment_ids` listed in the request must be equal to
        the order total. Orders with a total amount of `0` can be marked as
        paid by specifying an empty array of `payment_ids` in the request."
        https://developer.squareup.com/reference/square/orders-api/pay-order

        Two readings of ``payment_ids``, and the request decides which:

        * every id names a payment this unit holds -- the documented flow, a
          CreatePayment with ``autocomplete: false`` followed by PayOrder. Each
          must be APPROVED, at the order's location, and belong to this order
          or to none; their ``amount_money`` (tips aside) must sum to what is
          still due; and each is moved to COMPLETED through the payment
          machine after the order commits, which journals a ``payment.updated``
          apiece. An id listed twice is refused naming ``payment_ids`` -- a
          payment tenders once;
        * none does -- the opaque form this unit accepted before it had a
          Payments surface, kept so a scenario that never creates payments
          still completes orders. The first id's tender carries exactly what
          is still due and the rest zero, because there is nothing else to
          divide; an order with nothing due refuses the ids with 400
          (JUDGMENT: Square documents only the zero-total case, below).

        "Orders with a total amount of `0` can be marked as paid by specifying
        an empty array of `payment_ids`": an empty list on an order with
        nothing due completes it with no tender. Either way the order
        completes because the due reaches zero -- :func:`apply_tenders` is
        the one place that decides -- never by fiat of this route.

        A mix is refused naming the id that does not resolve: a caller who
        creates payments here and then names one that does not exist has made
        a mistake this unit must not paper over with a zero tender.
        """
        request = validate_body(PayOrderRequest, args.body())
        order_id = args.params["order_id"]
        orders = args.ctx.store.collection(COL.orders)
        current = require_order(orders, order_id)
        subject = f"Order {order_id}"

        if current.state == OrderState.DRAFT:
            # Its own error rather than the machine's, because Square publishes
            # the reason: "Draft orders can be updated, but cannot be paid or
            # fulfilled." https://developer.squareup.com/reference/square/enums/OrderState
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=(f"{subject} is in state DRAFT and cannot be paid. A DRAFT order cannot be paid or fulfilled."),
                field="state",
                info={"from": OrderState.DRAFT.value, "to": OrderState.COMPLETED.value},
            )
        # COMPLETED -> COMPLETED lands here and is refused, because the core's
        # machine does not allow a self-transition unless the state declares it
        # and a terminal state cannot. That is the double-payment the reference
        # answered with 200.
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
        # No ids and something due: the placeholder this unit has always
        # tendered under, kept for scenarios that never create payments. No
        # ids and nothing due is the documented zero-total case: no tender.
        opaque_ids = payment_ids or (["unit-payment"] if due > 0 else [])

        def mutate(draft: Entity) -> None:
            # Minted inside the mutator, so a version conflict -- which
            # `Collection.update` raises before calling this -- does not draw
            # from the id stream and leave two runs of one scenario numbering
            # their tenders differently.
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
                        # The first tender carries exactly what is due and the
                        # rest zero: with opaque ids the due is all there is
                        # to divide, and nothing may be tendered past it.
                        amount_money=Money(amount=due if index == 0 else 0, currency=current.currency),
                        payment_id=payment_id,
                    )
                    for index, payment_id in enumerate(opaque_ids)
                ]
            # Completion is `apply_tenders`' decision -- the due reaches zero
            # -- which both branches guarantee: the stored payments sum to it,
            # and the opaque tender is it.
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
        """Line items for CreateOrder: complete, or a 400 naming the index.

        Nothing is synthesised. A line with no quantity and a line with no
        price are both refused rather than defaulted, because the alternative
        is an order that is quietly worth nothing.
        """
        built: list[OrderLineItem] = []
        for index, item in enumerate(items):
            path = f"order.line_items[{index}]"
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
                # Pricing resolves from the catalog when the caller does not
                # override it -- the behaviour that makes seeded catalog data
                # worth having.
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
                    uid=item.uid or self._deps.ids.line_item_uid(),
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
        """Line items for UpdateOrder: only what the caller mentioned.

        An absent field stays absent so the merge preserves what is stored --
        synthesising a default here would silently zero a price the caller
        never named. A present-but-null optional is a *clear*, which is why the
        patch carries two collections rather than one dict.
        """
        patches: list[_LinePatch] = []
        for index, item in enumerate(items):
            path = f"order.line_items[{index}]"
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

            patches.append(
                _LinePatch(uid=item.uid or self._deps.ids.line_item_uid(), assign=assign, clear=tuple(clear))
            )
        return tuple(patches)

    # -- fulfillments -------------------------------------------------------

    def _new_fulfillments(self, items: Sequence[FulfillmentRequest], now: str) -> tuple[Fulfillment, ...]:
        """Fulfillments for CreateOrder: a type each, PROPOSED unless stated.

        A state other than PROPOSED on creation is accepted when the machine
        allows the move from PROPOSED -- a seller's own app can create an order
        that is already RESERVED -- and stamped as such a move would be.

        Every fulfillment is validated before any uid is minted, so a request
        refused on its third fulfillment draws nothing from the id stream and
        the next accepted request mints the uids it would have minted anyway
        -- the discipline PayOrder keeps for tender ids.
        """
        checked: list[tuple[str | None, str, str, dict[str, Any]]] = []
        seen: set[str] = set()
        for index, item in enumerate(items):
            path = f"order.fulfillments[{index}]"
            kind = _require_fulfillment_type(item.type, f"{path}.type")
            if item.uid is not None:
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
            details.setdefault("placed_at", now)
            if state != FulfillmentState.PROPOSED.value:
                _stamp_transition(details, kind, state, now)
            checked.append((item.uid, kind, state, details))
        return tuple(
            _fulfillment(uid or self._deps.ids.fulfillment_uid(), kind, state, details)
            for uid, kind, state, details in checked
        )

    def _fulfillment_patches(
        self, current: OrderEntity, items: Sequence[FulfillmentRequest]
    ) -> tuple[_FulfillmentPatch, ...]:
        """Fulfillments for UpdateOrder: only what the caller mentioned.

        Transitions are asserted *here*, against the stored state, before
        ``Collection.update`` runs -- so an illegal move is refused with no
        version bump, the same guarantee a bad line item has.

        A ``uid`` names an existing fulfillment, and one that names none is
        refused -- ``invalid_value`` on ``order.fulfillments[i].uid`` -- while
        an entry with **no** uid is a new fulfillment and is minted one.
        JUDGMENT, and deliberately unlike line items, where an unknown uid
        appends: a fulfillment is a thing in flight through a state machine,
        and a retry carrying a stale or mistyped uid that silently created a
        second PROPOSED fulfillment beside the one it meant to advance is the
        duplicate no kitchen can tell from a real second order. A line item
        under a caller-chosen uid is idempotent data with no such consequence.
        Square publishes no sentence for either case.

        Minting happens after every entry is validated, as on create.
        """
        by_uid = {f.uid: f for f in current.fulfillments}
        checked: list[tuple[str | None, dict[str, Any], dict[str, Any], tuple[str, ...]]] = []
        for index, item in enumerate(items):
            path = f"order.fulfillments[{index}]"
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
            _FulfillmentPatch(
                uid=uid or self._deps.ids.fulfillment_uid(),
                assign=assign,
                details_assign=details_assign,
                details_clear=details_clear,
            )
            for uid, assign, details_assign, details_clear in checked
        )


# ---------------------------------------------------------------------------
# Module-level helpers: pure where they can be, and testable on their own.
# ---------------------------------------------------------------------------


def order_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Orders routes for one vendor."""
    return OrdersSurface(deps).routes()


def _cannot_be_cleared(field: str) -> UnitError:
    """A null on a field an order cannot be without.

    Refused rather than ignored: silently dropping it would leave a caller who
    meant to clear something believing they had.
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
    """The location, or a 400 that lists the ones this unit has.

    ``invalid_value`` and not ``not_found``: the order does not exist yet, so
    what is wrong is the value the caller sent. The ``known`` list is what makes
    the fake usable without reading the seed document.
    """
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
    """Sparse merge by uid: a known uid updates in place, an unknown one appends.

    In place means *position* as well as identity -- a patch that renames a line
    must not move it to the end of the order, because the line order is on the
    wire and a receipt reads top to bottom.
    """
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
    """Apply one sparse order-level field.

    Absent: nothing happens. Present and truthy: assigned. Present and null or
    empty: **popped**, never set to ``None`` -- see the "absence is absence"
    invariant in :mod:`vendorfake.square.entities`, which the entity digest,
    the journal's ``changed`` list and the wire projection all depend on.
    """
    if not supplied(patch, name):
        return
    value = getattr(patch, name)
    if value:
        draft[name] = value
    else:
        draft.pop(name, None)


def _apply_fields_to_clear(draft: Entity, paths: Sequence[str]) -> None:
    """Square's dot/bracket clear notation.

    ``reference_id``, ``line_items``, ``line_items[uid]``,
    ``line_items[uid].note``. Anything else -- a read-only property, a field
    this unit does not model, a typo -- is **silently ignored and the version
    still increments**, which is Square's documented behaviour and not an
    oversight: "On a 200 response, Square has incremented the order version,
    even if all requested property changes are ignored and no changes are
    actually made."
    https://developer.squareup.com/docs/orders-api/manage-orders/update-orders
    """
    for path in paths:
        match = _LINE_ITEM_PATH.match(path)
        if match is not None:
            uid, sub = match.group(1), match.group(2)
            lines = _lines_of(draft)
            if sub is None:
                draft["line_items"] = [line for line in lines if line.get("uid") != uid]
            elif sub in _CLEARABLE_LINE_FIELDS:
                for line in lines:
                    if line.get("uid") == uid:
                        line.pop(sub, None)
                draft["line_items"] = lines
            continue
        if path == "line_items":
            draft["line_items"] = []
        elif path in _CLEARABLE_ORDER_FIELDS:
            draft.pop(path, None)


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
    """The details fields a request sets and the ones it clears, for the
    details object the fulfillment's type carries.

    A details object for a *different* type is refused: a PICKUP fulfillment
    with ``delivery_details`` is a request Square's object cannot represent,
    and storing it would echo a shape no consumer could act on.
    """
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


def _fulfillment(uid: str, kind: str, state: str, details: dict[str, Any]) -> Fulfillment:
    """A fulfillment whose one details object is the one its type carries."""
    return Fulfillment(
        uid=uid,
        type=kind,
        state=state,
        pickup_details=details if kind == "PICKUP" else None,
        delivery_details=details if kind == "DELIVERY" else None,
        shipment_details=details if kind == "SHIPMENT" else None,
    )


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
    """Sparse merge by uid, in place, with the details merged one level down.

    Position is preserved as it is for line items. A transition stamps its
    details field when the patch did not set it, and ``placed_at`` is stamped
    on a fulfillment that is new.
    """
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
            details.setdefault("placed_at", now)
            state = str(patch.assign.get("state", FulfillmentState.PROPOSED.value))
            if state != FulfillmentState.PROPOSED.value:
                _stamp_transition(details, kind, state, now)
            by_uid[patch.uid] = _fulfillment(patch.uid, kind, state, details).to_entity()
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
        by_uid[patch.uid] = merged
    return [by_uid[uid] for uid in order]


def _resolve_payments(
    payments: Collection, payment_ids: Sequence[str], order: OrderEntity
) -> list[PaymentEntity] | None:
    """The stored payments ``payment_ids`` name, or ``None`` when none is stored.

    See :meth:`OrdersSurface.pay_order` for the two readings and why a mix is
    refused.
    """
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
    """Move a payment to COMPLETED through the payment machine, journalled.

    The one place a payment's ``status`` is written to COMPLETED, for PayOrder
    and for the Payments surface alike, so that the machine's rule -- no
    edge out of a terminal state, no self-transition -- is asserted on every
    capture and a payment cannot be captured twice. ``order_id`` is bound
    when the capture attaches a previously unattached payment to an order.
    """
    _PAYMENT_MACHINE.assert_transition(payment.status, PaymentState.COMPLETED.value, f"Payment {payment.id}")

    def mutate(draft: Entity) -> None:
        draft["status"] = PaymentState.COMPLETED.value
        if order_id is not None:
            draft["order_id"] = order_id

    return payments.update(payment.id, mutate, meta={"operation_id": operation_id})


def tender_for_payment(ids: SquareIds, order: OrderEntity, payment: PaymentEntity, now: str) -> Tender:
    """The tender a completed payment adds to its order.

    JUDGMENT -- ``type`` is ``OTHER`` for an external payment. Square's
    ``TenderType`` (https://developer.squareup.com/reference/square/enums/TenderType)
    has no ``EXTERNAL`` member and documents no mapping from a payment's
    ``source_type``; ``OTHER`` is the member that claims nothing false.
    """
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
    """Append ``tenders`` to an order draft and complete it if it is now paid.

    Shared by PayOrder and by the Payments surface, so "an order is COMPLETED
    when the tenders cover its total" is one rule -- and "cover" is
    :func:`~vendorfake.square.model.order.amount_due` reaching zero, which
    counts what a tender applies to the order and not its tip. The guide's
    flow -- "Square marks the order as COMPLETED" once payments covering the
    total are completed -- is the reading followed
    (https://developer.squareup.com/docs/orders-api/pay-for-orders); the
    exact wording is NOT VERIFIED and a partial payment leaves the order OPEN
    with the remainder in ``net_amount_due_money``.
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
    """Completing an order removes the lines whose quantity is zero.

    "Line items with a quantity of `0` are automatically removed when paying
    for or otherwise completing the order."
    https://developer.squareup.com/reference/square/objects/OrderLineItem

    Called from PayOrder and from the UpdateOrder transition into COMPLETED --
    "otherwise completing" is what that second call site is. A transition into
    CANCELED does not remove anything: the sentence says *completing*, and a
    canceled order's lines are the record of what was not sold.

    Removing them changes no total, because a line whose quantity is zero
    already contributes ``round(base * 0) = 0``; what changes is the array a
    consumer reads back, which is the thing the sentence is about. The quantity
    is parsed with the same ``parseFloat`` port the money projection uses, so
    ``"0"``, ``"0.0"`` and ``"0.00"`` are all the documented zero, and junk --
    which yields no number at all -- is left alone rather than swept up.
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
    """Square's range semantics: start inclusive, end exclusive.

    An order with no value for the field being filtered is **excluded** -- an
    open order has no ``closed_at``, and "closed between Monday and Tuesday"
    must not match it.
    """
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
