"""The orders surface: CRUD, line items, and the two atomic calculators.

FOR: reproducing what a Clover consumer drives against ``/v3/merchants/{mId}/
orders`` with the documented shapes and the one documented behaviour a
Square-habituated consumer gets wrong first: **plain orders never total
themselves**.

====================  ==========================================================
CreateOrder           ``POST   /v3/merchants/{mId}/orders``
GetOrders             ``GET    /v3/merchants/{mId}/orders``
GetOrder              ``GET    /v3/merchants/{mId}/orders/{orderId}``
UpdateOrder           ``POST   /v3/merchants/{mId}/orders/{orderId}``  (POST, not PUT)
DeleteOrder           ``DELETE /v3/merchants/{mId}/orders/{orderId}``
CreateLineItem        ``POST   /v3/merchants/{mId}/orders/{orderId}/line_items``
BulkCreateLineItems   ``POST   /v3/merchants/{mId}/orders/{orderId}/bulk_line_items``
CreateAtomicOrder     ``POST   /v3/merchants/{mId}/atomic_order/orders``
CheckoutAtomicOrder   ``POST   /v3/merchants/{mId}/atomic_order/checkouts``
====================  ==========================================================
(https://docs.clover.com/dev/docs/creating-custom-orders,
https://docs.clover.com/dev/docs/orderupdateorder,
https://docs.clover.com/dev/docs/ordercreatelineitem,
https://docs.clover.com/dev/docs/orderbulkcreatelineitems,
https://docs.clover.com/dev/docs/ordercreateatomicorder,
https://docs.clover.com/dev/docs/ordergetorders)

Documented behaviour reproduced here
------------------------------------
* create takes the example body ``{"orderType":{"id"},"currency","total",
  "state":"Open"}`` and stores every field as sent; **no auto-totaling** --
  "Order totals are calculated dynamically and updated by the app the
  merchant uses... If your app modifies an order, it must update the total as
  well" -- so adding a line item leaves ``total`` exactly where it was;
* update is ``POST``, sparse: only the fields sent change;
* a line item needs "either a ``price`` or an ``item`` object with an
  inventory item ``id``"; ``unitQty`` is x1000; 3,000 line items per order;
* bulk create takes at most 100 and "Each item must include a price";
* the atomic endpoints take an ``orderCart``: ``/atomic_order/orders``
  creates the order *and* "calculate[s] the order totals";
  ``/atomic_order/checkouts`` calculates and creates nothing;
* lists use the ``{"elements": [...]}`` envelope with per-element ``href``,
  ``limit`` default 100 / max 1000, ``offset``; ``filter=<field><op><value>``
  on ``state``, ``createdTime``, ``modifiedTime``, ``total``,
  ``externalReferenceId``, ``id``; ``expand=`` up to three fields, dotted one
  level; nested arrays cap at 100 and are not pageable.

JUDGMENT, each labelled at its site
-----------------------------------
* **Delete is soft** (gap 5): ``DELETE`` sets ``deletedTime`` -- a documented
  filter field, which is the hook -- and the order then 404s and leaves the
  list. The 200 body is this package's; the docs publish none.
* **The state machine** (gap 6): ``open -> locked``, ``locked`` terminal; a
  state is compared case-insensitively and stored verbatim, because Clover's
  own pages write ``Open`` and ``open``; an order with no state (the
  documented null, "hidden") is treated as ``open`` for transition purposes.
  Any write to a ``locked`` order -- update, line item -- is a 400.
* **Filters**: ``filter=`` repeats, as Clover's list pages show
  (``filter=createdTime>=...&filter=createdTime<=...``), and the clauses are
  ANDed; every value is read through ``args.query_all``
  (konyklabs/roadmap#37). An unknown filter field is a 400; the docs list
  the fields and not the response to a wrong one. ``deletedTime`` is not
  filterable here because deleted orders are never listed.
* **Defaults on create**: a missing ``currency`` is the merchant's (an order
  cannot be denominated in something the seller does not take); a missing
  ``total`` is ``0`` (the field is client-owned, and the client said
  nothing); ``clientCreatedTime`` defaults to ``createdTime``.
* **Rounding** in the atomic total is half-up on cents (``model/order.py``).
* **Atomic responses** carry the full order -- line items, discounts and
  service charge expanded -- because that is what the tutorial's response
  shows and what a caller who just sent a cart is asking about.

THE ORDERING INVARIANT: **no 4xx leaves a journal entry.** Every refusal --
lock, cap, missing price, unknown item -- is computed before the write.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.clover.entities import (
    COL,
    MAX_LINE_ITEMS_PER_ORDER,
    ItemEntity,
    MerchantEntity,
    OrderEntity,
)
from vendorfake.clover.machine import ORDER_MACHINE, OrderState
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.order import (
    EXPANDABLE,
    AtomicOrderRequest,
    AtomicTotals,
    BulkLineItemsRequest,
    DiscountRequest,
    LineItemRequest,
    LineItemWire,
    OrderCartRequest,
    OrderCreateRequest,
    OrderPatchRequest,
    atomic_totals,
    project_order,
    supplied,
)
from vendorfake.clover.model.references import PrintEventRequest
from vendorfake.clover.surface.common import (
    CloverDeps,
    elements,
    expansions,
    int_param,
    merchant_row,
    page_window,
    require_merchant,
)
from vendorfake.clover.surface.inventory import item_tax_rates
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
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Collection, Entity
from vendorfake.core.util.json import compact

__all__ = ["BULK_MAX", "CAPABILITY", "CloverOrdersSurface", "order_routes"]

CAPABILITY = "orders"
"""The capability every route below belongs to."""

BULK_MAX = 100
""""max 100" line items per bulk request (orderbulkcreatelineitems)."""

_MACHINE = StateMachine(ORDER_MACHINE)

_CANNOT_BE_CLEARED = frozenset({"currency", "total"})
"""Required on the wire with no default: clearing either would store an
order the wire cannot project."""

_ATOMIC_EXPAND = frozenset(
    {"lineItems", "discounts", "serviceCharge", "customers", "lineItems.discounts", "lineItems.modifications"}
)

_FILTER_FIELDS: Mapping[str, str] = {
    "state": "state",
    "createdTime": "int",
    "modifiedTime": "int",
    "total": "int",
    "externalReferenceId": "str",
    "id": "str",
}
"""Filterable fields and their comparison kind. ``state`` compares
case-insensitively; ``int`` fields take ``=``, ``>=``, ``<=``; ``str`` fields
take ``=`` only."""


class CloverOrdersSurface:
    """The nine order routes, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        """Literal segments before parameters: ``atomic_order`` sits beside
        ``orders``, so the two collection paths never shadow each other."""
        base = "/v3/merchants/{mId}"
        return (
            Route(
                method="POST",
                path=f"{base}/atomic_order/orders",
                capability=CAPABILITY,
                handler=self.create_atomic_order,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="CreateAtomicOrder",
                summary="Create an order from an orderCart and calculate its total.",
            ),
            Route(
                method="POST",
                path=f"{base}/atomic_order/checkouts",
                capability=CAPABILITY,
                handler=self.checkout_atomic_order,
                auth="bearer",
                scopes=("ORDERS_R",),
                operation_id="CheckoutAtomicOrder",
                summary="Calculate an orderCart's total without creating anything.",
            ),
            Route(
                method="POST",
                path=f"{base}/orders",
                capability=CAPABILITY,
                handler=self.create_order,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="CreateOrder",
                summary="Create an order. Every field is client-owned, total included.",
                example_body={"currency": "USD", "total": 1500, "state": "open"},
            ),
            Route(
                method="GET",
                path=f"{base}/orders",
                capability=CAPABILITY,
                handler=self.list_orders,
                auth="bearer",
                scopes=("ORDERS_R",),
                operation_id="GetOrders",
                summary="Orders in the elements envelope: filter, expand, limit, offset.",
                pagination=PaginationSpec(style="offset", items_path="elements"),
            ),
            Route(
                method="GET",
                path=f"{base}/orders/{{orderId}}",
                capability=CAPABILITY,
                handler=self.get_order,
                auth="bearer",
                scopes=("ORDERS_R",),
                operation_id="GetOrder",
                summary="One order, with the requested expansions.",
            ),
            Route(
                method="POST",
                path=f"{base}/orders/{{orderId}}",
                capability=CAPABILITY,
                handler=self.update_order,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="UpdateOrder",
                summary="Sparse update (POST, not PUT); state moves through the order machine.",
            ),
            Route(
                method="DELETE",
                path=f"{base}/orders/{{orderId}}",
                capability=CAPABILITY,
                handler=self.delete_order,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="DeleteOrder",
                summary="Soft-delete: sets deletedTime; the order then 404s and leaves the list.",
            ),
            Route(
                method="POST",
                path=f"{base}/orders/{{orderId}}/line_items",
                capability=CAPABILITY,
                handler=self.create_line_item,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="CreateLineItem",
                summary="Add one line item (price or item.id required); the order total is untouched.",
            ),
            Route(
                method="POST",
                path=f"{base}/orders/{{orderId}}/bulk_line_items",
                capability=CAPABILITY,
                handler=self.bulk_create_line_items,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="BulkCreateLineItems",
                summary="Add up to 100 line items, each with a price.",
            ),
            Route(
                method="POST",
                path=f"{base}/print_event",
                capability=CAPABILITY,
                handler=self.create_print_event,
                auth="bearer",
                scopes=("ORDERS_W",),
                operation_id="CreatePrintEvent",
                summary="Ask the merchant's default order printer to print an order; journalled, no other effect.",
            ),
        )

    # -- POST /orders --------------------------------------------------------

    def create_order(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        request = validate_body(OrderCreateRequest, args.body())
        expand = expansions(args, EXPANDABLE)
        _check_state_value(request.state)
        _check_payment_state(request)
        _check_references(args.ctx, merchant_id, request.orderType, request.employee, request.customers, field="")
        merchant = _the_merchant(args.ctx, merchant_id)
        now = _now(args.ctx)
        entity = OrderEntity(
            id=self._deps.ids.order(),
            merchant_id=merchant_id,
            # JUDGMENT defaults; see the module docstring.
            currency=request.currency or merchant.currency,
            total=0 if request.total is None else request.total,
            state=request.state,
            paymentState=(request.paymentState.value if request.paymentState is not None else "OPEN"),
            payType=None if request.payType is None else request.payType.value,
            createdTime=now,
            modifiedTime=now,
            clientCreatedTime=now if request.clientCreatedTime is None else request.clientCreatedTime,
            title=request.title,
            note=request.note,
            externalReferenceId=request.externalReferenceId,
            testMode=request.testMode,
            taxRemoved=request.taxRemoved,
            manualTransaction=request.manualTransaction,
            groupLineItems=request.groupLineItems,
            orderType=None if request.orderType is None else request.orderType.wire(),
            employee=None if request.employee is None else {"id": request.employee.id},
            customers=tuple({"id": customer.id} for customer in request.customers or []),
        )
        stored = args.ctx.store.collection(COL.orders).insert(entity.to_entity(), {"operation_id": "CreateOrder"})
        return json_(project_order(stored, expand))

    # -- GET /orders ---------------------------------------------------------

    def list_orders(self, args: HandlerArgs) -> ReplyInit:
        """Insertion order, filtered, then windowed. Stable under inserts: a
        row created between two pages lands after the walk, never inside it,
        so pages never overlap. A soft delete between two pages drops that
        row from the union -- the list reports what exists now."""
        merchant_id = require_merchant(args)
        expand = expansions(args, EXPANDABLE)
        predicate = _filters(args.query_all("filter"))
        limit, offset = page_window(args)
        orders = [
            order
            for order in (OrderEntity.from_entity(e) for e in args.ctx.store.collection(COL.orders).all())
            if order.merchant_id == merchant_id and not order.is_deleted and predicate(order)
        ]
        page = orders[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [project_order(order.to_entity(), expand) for order in page],
                [f"{base}/v3/merchants/{merchant_id}/orders/{order.id}" for order in page],
            )
        )

    # -- GET /orders/{orderId} ----------------------------------------------

    def get_order(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        expand = expansions(args, EXPANDABLE)
        order = _require_order(args.ctx.store.collection(COL.orders), args.params["orderId"], merchant_id)
        return json_(project_order(order.to_entity(), expand))

    # -- POST /orders/{orderId} ---------------------------------------------

    def update_order(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        request = validate_body(OrderPatchRequest, args.body())
        expand = expansions(args, EXPANDABLE)
        orders = args.ctx.store.collection(COL.orders)
        current = _require_order(orders, args.params["orderId"], merchant_id)
        subject = f"Order {current.id}"
        for name in request.model_fields_set:
            if getattr(request, name) is None and name in _CANNOT_BE_CLEARED:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{name} cannot be cleared.",
                    field=name,
                )
        _check_payment_state(request)
        _check_references(args.ctx, merchant_id, request.orderType, request.employee, request.customers, field="")

        # Terminality first, then the move: "this order is locked" explains
        # "that move is not allowed". Compared on the lowercase canon, stored
        # verbatim (module docstring).
        _MACHINE.assert_mutable(_canonical(current.state), subject)
        if supplied(request, "state"):
            if request.state is None:
                raise UnitError(UnitErrorKind.INVALID_VALUE, detail="state cannot be cleared.", field="state")
            _MACHINE.assert_transition(_canonical(current.state), request.state.lower(), subject)

        now = _now(args.ctx)

        def mutate(draft: Entity) -> None:
            for name in request.model_fields_set:
                value = getattr(request, name)
                if value is None:
                    draft.pop(name, None)
                elif name == "orderType":
                    draft[name] = value.wire()
                elif name == "employee":
                    # Round-tripped as a reference: consumers attach it before
                    # paying, and a fake that dropped it would lose the field
                    # silently.
                    draft[name] = {"id": value.id}
                elif name == "customers":
                    draft[name] = [{"id": customer.id} for customer in value]
                elif name in ("paymentState", "payType"):
                    draft[name] = value.value
                else:
                    draft[name] = value
            draft["modifiedTime"] = now

        updated = orders.update(current.id, mutate, meta={"operation_id": "UpdateOrder"})
        return json_(project_order(updated, expand))

    # -- DELETE /orders/{orderId} -------------------------------------------

    def delete_order(self, args: HandlerArgs) -> ReplyInit:
        """Soft delete (JUDGMENT, module docstring). The body names what was
        deleted and when; Clover documents a 200 and no schema. A locked
        order is not deletable -- "any write to a locked order is a 400", and
        a paid order vanishing is the worse surprise (JUDGMENT)."""
        merchant_id = require_merchant(args)
        orders = args.ctx.store.collection(COL.orders)
        current = _require_order(orders, args.params["orderId"], merchant_id)
        _MACHINE.assert_mutable(_canonical(current.state), f"Order {current.id}")
        now = _now(args.ctx)

        def mutate(draft: Entity) -> None:
            draft["deletedTime"] = now
            draft["modifiedTime"] = now

        orders.update(current.id, mutate, meta={"operation_id": "DeleteOrder"})
        return json_({"id": current.id, "deletedTime": now})

    # -- POST /orders/{orderId}/line_items ----------------------------------

    def create_line_item(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        request = validate_body(LineItemRequest, args.body())
        orders = args.ctx.store.collection(COL.orders)
        current = _require_order(orders, args.params["orderId"], merchant_id)
        _MACHINE.assert_mutable(_canonical(current.state), f"Order {current.id}")
        # Capacity before the build: a refused request must not draw ids.
        _check_capacity(current, 1)
        line = self._build_line(args.ctx, request, field="")
        now = _now(args.ctx)

        def mutate(draft: Entity) -> None:
            draft["lineItems"] = [*draft.get("lineItems", []), line]
            draft["modifiedTime"] = now

        orders.update(current.id, mutate, meta={"operation_id": "CreateLineItem"})
        return json_(LineItemWire.model_validate(line).wire())

    # -- POST /orders/{orderId}/bulk_line_items -----------------------------

    def bulk_create_line_items(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        request = validate_body(BulkLineItemsRequest, args.body())
        orders = args.ctx.store.collection(COL.orders)
        current = _require_order(orders, args.params["orderId"], merchant_id)
        _MACHINE.assert_mutable(_canonical(current.state), f"Order {current.id}")
        if len(request.items) > BULK_MAX:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"bulk_line_items accepts at most {BULK_MAX} items per request.",
                field="items",
                info={"supplied": len(request.items), "max": BULK_MAX},
            )
        _check_capacity(current, len(request.items))
        lines: list[dict[str, Any]] = []
        for index, item in enumerate(request.items):
            if item.price is None:
                # "Each item must include a price" -- an item reference does
                # not stand in for it on this endpoint, unlike line_items.
                raise UnitError(
                    UnitErrorKind.MISSING_FIELD,
                    detail="Each item must include a price.",
                    field=f"items[{index}].price",
                )
            lines.append(self._build_line(args.ctx, item, field=f"items[{index}]."))
        now = _now(args.ctx)

        def mutate(draft: Entity) -> None:
            draft["lineItems"] = [*draft.get("lineItems", []), *lines]
            draft["modifiedTime"] = now

        orders.update(current.id, mutate, meta={"operation_id": "BulkCreateLineItems"})
        return json_({"items": [LineItemWire.model_validate(line).wire() for line in lines]})

    # -- POST /atomic_order/orders and /checkouts ---------------------------

    def create_atomic_order(self, args: HandlerArgs) -> ReplyInit:
        """ "Creates an order and calculates the order totals" -- the one
        create path that totals, and it does so exactly once, here. The
        answer is the stored order plus the documented totals block
        (``subtotal``, ``totalTaxAmount``, ``taxSummaries``) the checkout
        reference lists."""
        merchant_id = require_merchant(args)
        cart = validate_body(AtomicOrderRequest, args.body()).orderCart
        merchant = _the_merchant(args.ctx, merchant_id)
        lines, discounts, charge, totals = self._price_cart(args.ctx, merchant_id, cart)
        now = _now(args.ctx)
        entity = OrderEntity(
            id=self._deps.ids.order(),
            merchant_id=merchant_id,
            currency=cart.currency or merchant.currency,
            total=totals.total,
            # JUDGMENT: the docs recommend "manually setting the order state
            # value to Open"; an atomic order is created open.
            state=OrderState.OPEN.value,
            createdTime=now,
            modifiedTime=now,
            clientCreatedTime=now,
            title=cart.title,
            note=cart.note,
            externalReferenceId=cart.externalReferenceId,
            orderType=None if cart.orderType is None else cart.orderType.wire(),
            employee=None if cart.employee is None else {"id": cart.employee.id},
            customers=tuple({"id": customer.id} for customer in cart.customers or []),
            lineItems=tuple(lines),
            discounts=tuple(discounts),
            serviceCharge=charge,
        )
        stored = args.ctx.store.collection(COL.orders).insert(entity.to_entity(), {"operation_id": "CreateAtomicOrder"})
        return json_({**project_order(stored, _ATOMIC_EXPAND), **totals.wire()})

    def checkout_atomic_order(self, args: HandlerArgs) -> ReplyInit:
        """The calculator: the same arithmetic, nothing stored, nothing
        journalled. The answer is order-shaped without an id, because no
        order exists (its lines still draw ids from the unit's stream so the
        answer is line-shaped -- deterministic, and JUDGMENT-labelled here
        because it advances the stream without storing anything), plus the
        documented ``total``/``subtotal``/
        ``totalTaxAmount``/``taxSummaries`` block."""
        merchant_id = require_merchant(args)
        cart = validate_body(AtomicOrderRequest, args.body()).orderCart
        merchant = _the_merchant(args.ctx, merchant_id)
        lines, discounts, charge, totals = self._price_cart(args.ctx, merchant_id, cart)
        return json_(
            compact(
                {
                    "currency": cart.currency or merchant.currency,
                    **totals.wire(),
                    "title": cart.title,
                    "note": cart.note,
                    "externalReferenceId": cart.externalReferenceId,
                    "orderType": None if cart.orderType is None else cart.orderType.wire(),
                    "employee": None if cart.employee is None else {"id": cart.employee.id},
                    "customers": [{"id": customer.id} for customer in cart.customers or []] or None,
                    "lineItems": [LineItemWire.model_validate(line).wire() for line in lines] or None,
                    "discounts": discounts or None,
                    "serviceCharge": charge,
                }
            )
        )

    # -- POST /print_event ---------------------------------------------------

    def create_print_event(self, args: HandlerArgs) -> ReplyInit:
        """ "Submits the Printrequest" for one order
        (https://docs.clover.com/dev/reference/ordercreateprintevent-3). The
        response is the documented event -- ``id``, ``orderRef{id}``,
        ``state: CREATED``, ``createdTime``, ``modifiedTime``, ``printTime``
        (https://docs.clover.com/dev/docs/printing-orders-rest-api) -- minus
        ``deviceRef``: the real response names the firing device, and this
        unit has none (JUDGMENT: omitted rather than invented). The event is
        stored and journalled so a consumer can assert the print was asked
        for; nothing else happens."""
        merchant_id = require_merchant(args)
        request = validate_body(PrintEventRequest, args.body())
        _require_order(args.ctx.store.collection(COL.orders), request.orderRef.id, merchant_id)
        now = _now(args.ctx)
        event = {
            "id": self._deps.ids.print_event(),
            "orderRef": {"id": request.orderRef.id},
            "state": "CREATED",
            "createdTime": now,
            "modifiedTime": now,
            "printTime": now,
        }
        args.ctx.store.collection(COL.print_events).insert(event, {"operation_id": "CreatePrintEvent"})
        return json_(event)

    # -- shared --------------------------------------------------------------

    def _price_cart(
        self, ctx: UnitContext, merchant_id: str, cart: OrderCartRequest
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None, AtomicTotals]:
        """Resolve a cart's lines and their tax rates, validate its discounts,
        resolve a service charge that references the merchant's default, and
        compute the documented totals block."""
        if len(cart.lineItems) > MAX_LINE_ITEMS_PER_ORDER:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"An order can have at most {MAX_LINE_ITEMS_PER_ORDER} line items.",
                field="orderCart.lineItems",
                info={"supplied": len(cart.lineItems), "max": MAX_LINE_ITEMS_PER_ORDER},
            )
        _check_references(ctx, merchant_id, cart.orderType, cart.employee, cart.customers, field="orderCart.")
        lines: list[dict[str, Any]] = []
        line_rates: list[list[dict[str, Any]]] = []
        for index, line in enumerate(cart.lineItems):
            built, rates = self._build_line(ctx, line, field=f"orderCart.lineItems[{index}].", with_rates=True)
            lines.append(built)
            line_rates.append(rates)
        discounts = [_discount(d, f"orderCart.discounts[{i}].") for i, d in enumerate(cart.discounts or [])]
        charge = self._service_charge(ctx, cart)
        return lines, discounts, charge, atomic_totals(lines, line_rates, discounts, charge)

    def _service_charge(self, ctx: UnitContext, cart: OrderCartRequest) -> dict[str, Any] | None:
        """A cart's service charge: inline values, or the merchant's default
        when the cart references it by ``id`` (the ``GET
        /default_service_charge`` record). An unknown id is a 400."""
        requested = cart.serviceCharge
        if requested is None:
            return None
        if requested.id is not None:
            stored = ctx.store.collection(COL.service_charges).get(requested.id)
            if stored is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Service charge {requested.id} was not found.",
                    field="orderCart.serviceCharge.id",
                )
            return compact(
                {
                    "id": stored.get("id"),
                    "name": stored.get("name"),
                    "percentageDecimal": stored.get("percentageDecimal"),
                    "enabled": stored.get("enabled"),
                }
            )
        return compact(
            {
                "name": requested.name,
                "percentageDecimal": requested.percentageDecimal or 0,
                "enabled": requested.enabled,
            }
        )

    def _build_line(self, ctx: UnitContext, request: LineItemRequest, *, field: str, with_rates: bool = False) -> Any:
        """One stored line from a request: "either a price or an item object
        with an inventory item id". An item reference must resolve, and it
        supplies the price and name the request left out; a modification's
        modifier must resolve and supplies its price when ``amount`` is
        absent. With ``with_rates`` the line's tax rates come back too: the
        item's (its explicit associations or the merchant's defaults), or
        the line's own ``taxRates`` references for a bare-price line."""
        item: ItemEntity | None = None
        if request.item is not None:
            stored = ctx.store.collection(COL.items).get(request.item.id)
            if stored is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Inventory item {request.item.id} was not found.",
                    field=f"{field}item.id",
                )
            item = ItemEntity.from_entity(stored)
        if request.price is None and item is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="A line item must include either a price or an item object with an inventory item id.",
                field=f"{field}price",
            )
        price = request.price if request.price is not None else (item.price if item is not None else 0)
        name = request.name if request.name is not None else (item.name if item is not None else None)
        discounts = [_discount(d, f"{field}discounts[{i}].") for i, d in enumerate(request.discounts or [])]
        modifications = [
            self._modification(ctx, m, f"{field}modifications[{i}].") for i, m in enumerate(request.modifications or [])
        ]
        line = compact(
            {
                "id": self._deps.ids.line_item(),
                "name": name,
                "price": price,
                "note": request.note,
                "unitQty": request.unitQty,
                "printed": request.printed,
                "exchanged": False,
                "refunded": False,
                "item": None if item is None else {"id": item.id},
                "discounts": discounts or None,
                "modifications": modifications or None,
            }
        )
        if not with_rates:
            return line
        if item is not None:
            rates = item_tax_rates(ctx, item)
        else:
            tax_rates = ctx.store.collection(COL.tax_rates)
            rates = []
            for index, ref in enumerate(request.taxRates or []):
                found = tax_rates.get(ref.id)
                if found is None:
                    raise UnitError(
                        UnitErrorKind.INVALID_VALUE,
                        detail=f"Tax rate {ref.id} was not found.",
                        field=f"{field}taxRates[{index}].id",
                    )
                rates.append(dict(found))
        return line, rates

    def _modification(self, ctx: UnitContext, request: Any, field: str) -> dict[str, Any]:
        """A line modification, as the atomic tutorial shows one:
        ``{"modifier": {"id", "name", "available"}, "amount"}``."""
        stored = ctx.store.collection(COL.modifiers).get(request.modifier.id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Modifier {request.modifier.id} was not found.",
                field=f"{field}modifier.id",
            )
        price = stored.get("price")
        return compact(
            {
                "id": self._deps.ids.line_item(),
                "name": request.name if request.name is not None else stored.get("name"),
                "amount": request.amount if request.amount is not None else (price if isinstance(price, int) else 0),
                "modifier": compact(
                    {"id": stored.get("id"), "name": stored.get("name"), "available": stored.get("available", True)}
                ),
            }
        )


# ---------------------------------------------------------------------------
# Module-level helpers: pure, and testable without a unit.
# ---------------------------------------------------------------------------


def order_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The order routes for one vendor."""
    return CloverOrdersSurface(deps).routes()


def _now(ctx: UnitContext) -> int:
    return int(ctx.clock.now())


def _canonical(state: str | None) -> str:
    """The machine's reading of a stored state: lowercase, and an absent
    (null, "hidden") state reads as ``open`` for transition purposes
    (JUDGMENT, module docstring)."""
    return OrderState.OPEN.value if state is None else state.lower()


def _check_state_value(state: str | None) -> None:
    """A state on create must be one of the machine's, whatever its case."""
    if state is not None and state.lower() not in ORDER_MACHINE.states:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"'{state}' is not a valid state.",
            field="state",
            info={"allowed": list(ORDER_MACHINE.states)},
        )


def _check_payment_state(request: OrderCreateRequest) -> None:
    """JUDGMENT: ``paymentState`` is moved by payments, never set by a client
    write. Clover documents the values and nothing about who sets them; a
    client declaring an order PAID with no payment behind it would make the
    field a lie, so only the initial ``OPEN`` is accepted here."""
    if request.paymentState is not None and request.paymentState.value != "OPEN":
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="paymentState is set by payments (POST .../orders/{orderId}/payments), not by an order write.",
            field="paymentState",
        )


def _check_references(
    ctx: UnitContext, merchant_id: str, order_type: Any, employee: Any, customers: Any, *, field: str
) -> None:
    """``orderType``, ``employee`` and ``customers`` must name records of
    the path merchant, exactly as a line item's ``item`` and a payment's
    ``employee`` must (JUDGMENT: consistent refusal, 400 naming the field;
    Clover documents no answer to a dangling reference). Another merchant's
    row is as absent as none (``merchant_row``)."""
    checks = [
        (order_type, COL.order_types, f"{field}orderType.id", "Order type"),
        (employee, COL.employees, f"{field}employee.id", "Employee"),
    ]
    for ref, collection, path, label in checks:
        if ref is not None and merchant_row(ctx, collection, ref.id, merchant_id) is None:
            raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{label} {ref.id} was not found.", field=path)
    for index, ref in enumerate(customers or []):
        if merchant_row(ctx, COL.customers, ref.id, merchant_id) is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Customer {ref.id} was not found.",
                field=f"{field}customers[{index}].id",
            )


def _check_capacity(order: OrderEntity, adding: int) -> None:
    if len(order.lineItems) + adding > MAX_LINE_ITEMS_PER_ORDER:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"An order can have at most {MAX_LINE_ITEMS_PER_ORDER} line items.",
            field="lineItems",
            info={"current": len(order.lineItems), "adding": adding, "max": MAX_LINE_ITEMS_PER_ORDER},
        )


def _discount(request: DiscountRequest, field: str) -> dict[str, Any]:
    """A stored discount: ``amount`` or ``percentage``, one of them required
    (JUDGMENT on refusing an empty discount; Clover documents both forms and
    nothing about neither)."""
    if request.amount is None and request.percentage is None:
        raise UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail="A discount must carry either an amount (negative cents) or a percentage.",
            field=f"{field}amount",
        )
    return compact({"name": request.name, "amount": request.amount, "percentage": request.percentage})


def _the_merchant(ctx: UnitContext, merchant_id: str) -> MerchantEntity:
    stored = ctx.store.collection(COL.merchants).get(merchant_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.INTERNAL,
            detail=f"The bearer resolved to merchant {merchant_id}, which is not in the store.",
        )
    return MerchantEntity.from_entity(stored)


def _require_order(orders: Collection, order_id: str, merchant_id: str) -> OrderEntity:
    """The order, or a 404. A soft-deleted order and another merchant's
    order are both "not found": neither exists from this caller's side."""
    stored = orders.get(order_id)
    if stored is not None:
        order = OrderEntity.from_entity(stored)
        if order.merchant_id == merchant_id and not order.is_deleted:
            return order
    raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Order {order_id} was not found.", field="orderId")


def _filters(raws: Sequence[str]) -> Any:
    """Every ``filter=`` clause ANDed into one predicate; none keeps every order."""
    clauses = [_filter(raw) for raw in raws]
    return lambda order: all(clause(order) for clause in clauses)


def _filter(raw: str) -> Any:
    """One ``filter=<field><op><value>`` as a predicate over orders, or a 400.

    ``>=`` and ``<=`` are tried before ``=`` so ``total>=1500`` is not read
    as the field ``total>`` equal to ``1500``; a bare ``>`` or ``<`` is
    named as unsupported rather than reported as "no operator".
    """
    for op in (">=", "<=", "="):
        if op in raw:
            field, value = raw.split(op, 1)
            break
    else:
        detail = (
            f"filter {raw!r} uses an unsupported operator; use =, >= or <=."
            if any(symbol in raw for symbol in ("<", ">", "!"))
            else f"filter {raw!r} has no operator; use <field>=<value>, >= or <=."
        )
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=detail, field="filter")
    field = field.strip()
    value = value.strip()
    kind = _FILTER_FIELDS.get(field)
    if kind is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"{field!r} is not a filterable field.",
            field="filter",
            info={"allowed": list(_FILTER_FIELDS)},
        )
    if kind == "int":
        bound = int_param(value, "filter")

        def numeric(order: OrderEntity) -> bool:
            actual = getattr(order, field)
            if actual is None:
                return False
            if op == ">=":
                return bool(actual >= bound)
            if op == "<=":
                return bool(actual <= bound)
            return bool(actual == bound)

        return numeric
    if op != "=":
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail=f"{field} filters take '=' only.", field="filter")
    if kind == "state":
        return lambda order: (order.state or "").lower() == value.lower()
    return lambda order: getattr(order, field) == value
