"""The orders surface: prices, create, read, list, selections, void, discounts, delivery.

FOR: reproducing what a restaurant-ordering integration drives against
``/orders/v2`` with the documented shapes and the documented refusals -- above
all the prices contract: ``POST /prices`` and ``POST /orders`` compute the
same amounts from the same body, because they share ``model/build.py``.

====================  =====================================================
Prices                ``POST  /orders/v2/prices`` -- computes, persists nothing
CreateOrder           ``POST  /orders/v2/orders``
GetOrders (deprecated)``GET   /orders/v2/orders`` -- an array of order guids
OrdersBulk            ``GET   /orders/v2/ordersBulk`` -- full orders, paged
GetOrder              ``GET   /orders/v2/orders/{guid}``
VoidOrder             ``POST  /orders/v2/orders/{guid}/void``
AddSelections         ``POST  /orders/v2/orders/{o}/checks/{c}/selections``
ApplyCheckDiscount    ``POST  /orders/v2/orders/{o}/checks/{c}/appliedDiscounts``
ApplySelectionDiscount``POST  /orders/v2/orders/{o}/checks/{c}/selections/{s}/appliedDiscounts``
ApplicableDiscounts   ``POST  /orders/v2/applicableDiscounts``
UpdateDeliveryInfo    ``PATCH /orders/v2/orders/{o}/deliveryInfo``
====================  =====================================================
(toast-orders-api.yaml; apiCreatingOrders.html; apiOrderPrices.html;
apiVoidOrder.html; apiDiscountingOrders.html; apiOrdersGetDetailedInfoAboutOneOrder.html)

Documented behaviour reproduced here
------------------------------------
* the minimal create body is ``{diningOption{guid}, checks[{selections[{item{guid},
  quantity}]}]}``; Toast assigns every guid; ``externalId`` values must be
  unique across orders, checks and selections;
* ``/prices`` answers the same document with ``"guid": null`` and journals
  nothing;
* ``GET /orders/{guid}``: 400 "The GUID was malformed", 404 "The specified
  order was not found"; ``customer`` only with ``guest.pi:read`` and
  ``deliveryInfo`` only with ``delivery_info.address:read``; an ordering
  integration "can only retrieve orders ... if your integration submitted
  them" -- orders are scoped to the client that created them;
* ``GET /ordersBulk``: ``startDate``+``endDate`` (on ``modifiedDate``,
  inclusive/exclusive, after 2015-12-01) OR ``businessDate``; ``pageSize`` at
  most 100; full Order documents;
* void: ``{"selections":{"voidAll":true},"payments":{"voidAll":true}}``,
  "Each voidAll value must be set to true"; the same client that created the
  order; "Only OTHER payments may be voided"; the result is ``voided: true``,
  ``paymentStatus: "VOIDED"``, ``guestOrderStatus: "VOIDED"``, every
  selection voided, ``voidDate`` and ``voidBusinessDate``; "Once an order has
  been voided, it can not be updated";
* discounts: the AppliedDiscount shape and the ``/applicableDiscounts`` answer
  are the documented ones, field for field.

JUDGMENT, each labelled at its site
-----------------------------------
* **statuses the documentation names without a code**: a duplicate
  ``externalId`` is 400; a void by another client is 404 (the order is not
  visible to that client at all); voiding twice is 400 (``invalid_transition``);
  ``page`` counts from 0 (audit gap 7); supplying both a date range and a
  business date is 400;
* **the deprecated ``GET /orders``** answers every matching guid with no
  paging of any kind: none is documented (audit gap 12), so none is invented;
* **the 413 check-count limit** is undocumented and not enforced; **415** on
  a non-JSON content type is not enforced, because the core's body reader is
  deliberately content-type general;
* **a selection appended to a PAID check** is refused (400): the check's
  amounts would no longer match its payments and Toast documents no answer;
* **check-level discounts** take ``CHECK``-type discounts and selection-level
  ones ``ITEM``-type, each recomputed from the current amounts; a discount
  with promo codes needs a matching ``appliedPromoCode``;
* **``displayNumber``** is the order's position in the restaurant's history,
  as a string.

THE ORDERING INVARIANT: **no 4xx leaves a journal entry or draws an id.**
Every refusal is computed before the first write; ``/prices`` draws nothing
at all.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact
from vendorfake.toast.entities import COL, RestaurantEntity
from vendorfake.toast.machine import CHECK_MACHINE, GUEST_ORDER_MACHINE, CheckPaymentStatus, GuestOrderStatus
from vendorfake.toast.model.build import (
    MenuIndex,
    Minter,
    build_check,
    build_selection,
    retotal_check,
    selection_by_guid,
)
from vendorfake.toast.model.common import validate_body, validate_items
from vendorfake.toast.model.dates import business_date, parse_business_date, parse_rest_date
from vendorfake.toast.model.money import to_dollars
from vendorfake.toast.model.order import (
    AppliedDiscountRequest,
    DeliveryInfoRequest,
    OrderRequest,
    SelectionRequest,
    VoidRequest,
    project_order,
)
from vendorfake.toast.model.pricing import discount_amount, taxes_on
from vendorfake.toast.surface.common import RESTAURANT_AUTH, ToastDeps, int_param, is_guid, now_ms, require_restaurant
from vendorfake.toast.surface.payments import PaymentBatch, add_payment, covered_cents, payments_for, settle_order

__all__ = [
    "CAPABILITY",
    "GUID_MALFORMED",
    "MAX_PAGE_SIZE",
    "ONLY_OTHER_VOIDABLE",
    "ORDER_NOT_FOUND",
    "ToastOrdersSurface",
    "load_order",
    "order_routes",
    "reply_order",
]

CAPABILITY = "orders"

GUID_MALFORMED = "The GUID was malformed"
ORDER_NOT_FOUND = "The specified order was not found"
ONLY_OTHER_VOIDABLE = "Only OTHER payments may be voided"
VOIDED_IMMUTABLE = "Once an order has been voided, it can not be updated."
"""Documented phrases, verbatim."""

MAX_PAGE_SIZE = 100
EARLIEST_DATE_MS = 1448928000000  # 2015-12-01T00:00:00Z, "must be after 2015-12-01"

_CHECK_MACHINE = StateMachine(CHECK_MACHINE)
_ORDER_MACHINE = StateMachine(GUEST_ORDER_MACHINE)

EXAMPLE_ORDER: dict[str, Any] = {
    "entityType": "Order",
    "diningOption": {"guid": "5d0e2b11-0000-4000-8000-00000000d002", "entityType": "DiningOption"},
    "checks": [
        {
            "entityType": "Check",
            "selections": [
                {
                    "entityType": "MenuItemSelection",
                    "item": {"guid": "3c9a1f00-0000-4000-8000-00000000c201", "entityType": "MenuItem"},
                    "quantity": 1,
                }
            ],
        }
    ],
}
"""The documented minimal create body, aimed at the seeded take-out option and
the 8.99 soup. Published as ``example_body`` so the conformance suite can
commit a mutation without knowing this vendor."""


class ToastOrdersSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: ToastDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        base = "/orders/v2"

        def route(method: str, path: str, handler: Any, scope: str, op: str, summary: str, **extra: Any) -> Route:
            return Route(
                method=method,
                path=f"{base}{path}",
                capability=CAPABILITY,
                handler=handler,
                auth=RESTAURANT_AUTH,
                scopes=(scope,),
                operation_id=op,
                summary=summary,
                **extra,
            )

        return (
            route(
                "POST", "/prices", self.prices, "orders:write", "OrderPrices", "Price an order body; persists nothing."
            ),
            route(
                "POST",
                "/orders",
                self.create_order,
                "orders:write",
                "OrderCreate",
                "Create an order: guids assigned, amounts computed, payments accepted.",
                example_body=EXAMPLE_ORDER,
            ),
            route(
                "GET",
                "/orders",
                self.list_order_guids,
                "orders:read",
                "OrdersGet",
                "Deprecated: EVERY matching order guid, unpaged (none is documented; JUDGMENT, audit gap 12).",
            ),
            route(
                "GET",
                "/ordersBulk",
                self.orders_bulk,
                "orders:read",
                "OrdersBulkGet",
                "Full orders for a date range or business date; page from 0, pageSize <= 100.",
            ),
            route(
                "POST",
                "/applicableDiscounts",
                self.applicable_discounts,
                "orders:read",
                "ApplicableDiscounts",
                "Which discounts apply to an order body.",
            ),
            route(
                "GET",
                "/orders/{guid}",
                self.get_order,
                "orders:read",
                "OrderGet",
                "One order; 400 malformed guid, 404 unknown.",
            ),
            route(
                "POST",
                "/orders/{guid}/void",
                self.void_order,
                "orders:write",
                "OrderVoid",
                "Void an order and its OTHER payments; terminal.",
            ),
            route(
                "PATCH",
                "/orders/{guid}/deliveryInfo",
                self.update_delivery_info,
                "orders:write",
                "OrderDeliveryInfoPatch",
                "Update an order's deliveryInfo.",
            ),
            route(
                "POST",
                "/orders/{guid}/checks/{checkGuid}/selections",
                self.add_selections,
                "orders:write",
                "CheckSelectionsPost",
                "Append selections to an open check; amounts recomputed.",
            ),
            route(
                "POST",
                "/orders/{guid}/checks/{checkGuid}/appliedDiscounts",
                self.apply_check_discount,
                "orders:write",
                "CheckDiscountsPost",
                "Apply CHECK-type discounts to a check.",
            ),
            route(
                "POST",
                "/orders/{guid}/checks/{checkGuid}/selections/{selectionGuid}/appliedDiscounts",
                self.apply_selection_discount,
                "orders:write",
                "SelectionDiscountsPost",
                "Apply ITEM-type discounts to a selection.",
            ),
        )

    # -- POST /prices, POST /orders ------------------------------------------

    def prices(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        request = validate_body(OrderRequest, args.body())
        priced = self._build(args, restaurant, request, mint=None, display_number=None)
        return json_(project_order(priced, {}, **_scope_flags(args)))

    def create_order(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        request = validate_body(OrderRequest, args.body())
        orders = ctx.store.collection(COL.orders)
        _check_external_ids(orders, restaurant.id, request)
        count = sum(1 for row in orders.all() if row.get("restaurant_guid") == restaurant.id)
        display_number = str(count + 1)
        # A first build with no minter finds every refusal -- an unknown item,
        # a bad payment -- before a single id is drawn; the second build,
        # identical in every amount, draws them.
        rehearsal = self._build(args, restaurant, request, mint=None, display_number=display_number)
        pending = [(index, check.payments or []) for index, check in enumerate(request.checks)]
        rehearsal_batch = PaymentBatch()
        for index, payment_requests in pending:
            for i, payment in enumerate(payment_requests):
                add_payment(
                    ctx,
                    restaurant,
                    rehearsal,
                    rehearsal["checks"][index],
                    payment,
                    field=f"checks[{index}].payments[{i}].",
                    mint=None,
                    batch=rehearsal_batch,
                )
        entity = self._build(args, restaurant, request, mint=self._deps.ids.guid, display_number=display_number)
        entity["id"] = self._deps.ids.order()
        entity["client_id"] = _client_id(args)
        batch = PaymentBatch()
        docs = [
            add_payment(
                ctx,
                restaurant,
                entity,
                entity["checks"][index],
                payment,
                field=f"checks[{index}].payments[{i}].",
                mint=self._deps.ids.payment,
                batch=batch,
            )
            for index, payment_requests in pending
            for i, payment in enumerate(payment_requests)
        ]
        stored = orders.insert(entity, {"operation_id": "OrderCreate"})
        for doc in docs:
            ctx.store.collection(COL.payments).insert(doc, {"operation_id": "OrderCreate"})
        if docs:
            stored = orders.update(
                entity["id"], lambda draft: settle_order(draft, ctx), meta={"operation_id": "OrderCreate"}
            )
        return reply_order(args, stored)

    def _build(
        self,
        args: HandlerArgs,
        restaurant: RestaurantEntity,
        request: OrderRequest,
        *,
        mint: Minter,
        display_number: str | None,
    ) -> dict[str, Any]:
        ctx = args.ctx
        index = MenuIndex.from_store(ctx.store, restaurant.id)
        dining = ctx.store.collection(COL.dining_options).get(request.diningOption.guid)
        if dining is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Dining option {request.diningOption.guid} was not found.",
                field="diningOption.guid",
            )
        table = None
        service_area = None
        revenue_center = (
            None
            if request.revenueCenter is None
            else _reference(ctx, COL.revenue_centers, request.revenueCenter.guid, "RevenueCenter", "revenueCenter.guid")
        )
        if request.table is not None:
            stored_table = ctx.store.collection(COL.tables).get(request.table.guid)
            if stored_table is None:
                raise UnitError(
                    UnitErrorKind.NOT_FOUND, detail=f"Table {request.table.guid} was not found.", field="table.guid"
                )
            table = {"guid": request.table.guid, "entityType": "Table"}
            service_area = stored_table.get("serviceArea")
            revenue_center = revenue_center or stored_table.get("revenueCenter")
        now = now_ms(ctx)
        opened = now if request.openedDate is None else parse_rest_date(request.openedDate, field="openedDate")
        promised = None if request.promisedDate is None else parse_rest_date(request.promisedDate, field="promisedDate")
        checks = [
            build_check(index, check, now=now, mint=mint, field=f"checks[{i}].", display_number=display_number)
            for i, check in enumerate(request.checks)
        ]
        return compact(
            {
                "guid": None,
                "restaurant_guid": restaurant.id,
                "externalId": request.externalId,
                "openedDate": opened,
                "modifiedDate": now,
                "createdDate": now,
                "promisedDate": promised,
                "businessDate": business_date(
                    opened, time_zone=restaurant.time_zone, closeout_hour=restaurant.closeout_hour
                ),
                "channelGuid": request.channelGuid,
                "diningOption": {
                    "guid": request.diningOption.guid,
                    "entityType": "DiningOption",
                    "externalId": dining.get("externalId"),
                },
                "checks": checks,
                "table": table,
                "serviceArea": service_area,
                "revenueCenter": revenue_center,
                "server": None
                if request.server is None
                else {"guid": request.server.guid, "entityType": "RestaurantUser"},
                "source": "API",
                "approvalStatus": "APPROVED",
                "guestOrderStatus": GuestOrderStatus.RECEIVED.value,
                "voided": False,
                "numberOfGuests": request.numberOfGuests,
                "deliveryInfo": None if request.deliveryInfo is None else _complete_delivery_info(request.deliveryInfo),
                "curbsidePickupInfo": request.curbsidePickupInfo,
                "requiredPrepTime": request.requiredPrepTime,
                "pricingFeatures": list(request.pricingFeatures or []),
                "createdInTestMode": bool(request.createdInTestMode),
                "displayNumber": display_number,
                "appliedPackagingInfo": request.appliedPackagingInfo,
                "marketplaceFacilitatorTaxInfo": request.marketplaceFacilitatorTaxInfo,
                "thirdPartyProviderInfo": request.thirdPartyProviderInfo,
            }
        )

    # -- reads -----------------------------------------------------------------

    def get_order(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        return reply_order(args, load_order(args, restaurant, args.params["guid"]))

    def orders_bulk(self, args: HandlerArgs) -> ReplyInit:
        restaurant = require_restaurant(args)
        rows = _filtered_orders(args, restaurant)
        page = int_param(args.query("page") or "0", "page", minimum=0)
        size = int_param(args.query("pageSize") or str(MAX_PAGE_SIZE), "pageSize", minimum=1, maximum=MAX_PAGE_SIZE)
        window = rows[page * size : (page + 1) * size]
        return json_([_project(args, row) for row in window])

    def list_order_guids(self, args: HandlerArgs) -> ReplyInit:
        """Every matching guid, unpaged. JUDGMENT (audit gap 12): the
        deprecated endpoint documents no pagination at all, so none is
        invented -- a large scenario gets a large array."""
        restaurant = require_restaurant(args)
        return json_([str(row["id"]) for row in _filtered_orders(args, restaurant)])

    # -- mutations -------------------------------------------------------------

    def void_order(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        request = validate_body(VoidRequest, args.body())
        if not (request.selections.voidAll and request.payments.voidAll):
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Each voidAll value must be set to true.",
                field="selections.voidAll" if not request.selections.voidAll else "payments.voidAll",
            )
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        payments = payments_for(ctx, order)
        if any(p.get("type") != "OTHER" for p in payments.values()):
            raise UnitError(UnitErrorKind.INVALID_VALUE, detail=ONLY_OTHER_VOIDABLE, field="payments.voidAll")
        for check in order["checks"]:
            _CHECK_MACHINE.assert_transition(
                str(check["paymentStatus"]), CheckPaymentStatus.VOIDED.value, f"Check {check['guid']}"
            )
        _ORDER_MACHINE.assert_transition(
            str(order["guestOrderStatus"]), GuestOrderStatus.VOIDED.value, f"Order {order['id']}"
        )
        now = now_ms(ctx)
        void_business_date = business_date(now, time_zone=restaurant.time_zone, closeout_hour=restaurant.closeout_hour)

        def void_payment(draft: Entity) -> None:
            draft["paymentStatus"] = "VOIDED"
            draft["voidInfo"] = {"voidDate": now, "voidBusinessDate": void_business_date}

        def void(draft: Entity) -> None:
            draft["voided"] = True
            draft["voidDate"] = now
            draft["voidBusinessDate"] = void_business_date
            draft["guestOrderStatus"] = GuestOrderStatus.VOIDED.value
            draft["modifiedDate"] = now
            for check in draft["checks"]:
                check["paymentStatus"] = CheckPaymentStatus.VOIDED.value
                check["voided"] = True
                check["voidDate"] = now
                check["voidBusinessDate"] = void_business_date
                check["modifiedDate"] = now
                _void_selections(check.get("selections", []), now, void_business_date)

        for guid in payments:
            ctx.store.collection(COL.payments).update(guid, void_payment, meta={"operation_id": "OrderVoid"})
        updated = ctx.store.collection(COL.orders).update(order["id"], void, meta={"operation_id": "OrderVoid"})
        return reply_order(args, updated)

    def add_selections(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        requests = validate_items(SelectionRequest, args.json(), what="selections")
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        check = _check_of(order, args.params["checkGuid"])
        _CHECK_MACHINE.assert_mutable(str(check["paymentStatus"]), f"Check {check['guid']}")
        if check["paymentStatus"] in (CheckPaymentStatus.PAID.value, CheckPaymentStatus.CLOSED.value):
            # A settled check takes no more selections: PAID (a CREDIT tip
            # pending) or CLOSED (nothing due). CLOSED is reachable through
            # the API since an OTHER cover closes the check (roadmap#56), so
            # the guard names it too -- the history lens caught the PAID-only
            # check the machine change had silently widened.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Check {check['guid']} is {check['paymentStatus']}; a selection cannot be added to a settled check.",
                field="checkGuid",
            )
        index = MenuIndex.from_store(ctx.store, restaurant.id)
        now = now_ms(ctx)
        # Validate every selection with no mint first, so a refusal in the
        # third one draws no id for the first two.
        for i, request in enumerate(requests):
            build_selection(index, request, now=now, mint=None, field=f"[{i}].")
        built = [
            build_selection(index, request, now=now, mint=self._deps.ids.guid, field=f"[{i}].")
            for i, request in enumerate(requests)
        ]

        def append(draft: Entity) -> None:
            target = _check_of(draft, check["guid"])
            target["selections"] = [*target.get("selections", []), *built]
            target["modifiedDate"] = now
            retotal_check(target, index)
            draft["modifiedDate"] = now

        updated = ctx.store.collection(COL.orders).update(
            order["id"], append, meta={"operation_id": "CheckSelectionsPost"}
        )
        return reply_order(args, updated)

    def apply_check_discount(self, args: HandlerArgs) -> ReplyInit:
        return self._apply_discounts(args, selection_guid=None)

    def apply_selection_discount(self, args: HandlerArgs) -> ReplyInit:
        return self._apply_discounts(args, selection_guid=args.params["selectionGuid"])

    def _apply_discounts(self, args: HandlerArgs, *, selection_guid: str | None) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        requests = validate_items(AppliedDiscountRequest, args.json(), what="applied discounts")
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        check = _check_of(order, args.params["checkGuid"])
        _CHECK_MACHINE.assert_mutable(str(check["paymentStatus"]), f"Check {check['guid']}")
        index = MenuIndex.from_store(ctx.store, restaurant.id)
        wanted_type = "CHECK" if selection_guid is None else "ITEM"
        target = check if selection_guid is None else selection_by_guid(check, selection_guid)
        if target is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Selection {selection_guid} was not found on check {check['guid']}.",
                field="selectionGuid",
            )
        sources: list[tuple[Mapping[str, Any], AppliedDiscountRequest]] = []
        for i, request in enumerate(requests):
            source = index.discounts.get(request.discount.guid)
            if source is None or not source.get("active", True):
                raise UnitError(
                    UnitErrorKind.NOT_FOUND,
                    detail=f"Discount {request.discount.guid} was not found.",
                    field=f"[{i}].discount.guid",
                )
            if source.get("selectionType") != wanted_type:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Discount {request.discount.guid} is a {source.get('selectionType')} discount and cannot be applied at the {'check' if wanted_type == 'CHECK' else 'selection'} level.",
                    field=f"[{i}].discount.guid",
                )
            codes = [str(row.get("code")) for row in source.get("promoCodes", []) if isinstance(row, Mapping)]
            if codes and request.appliedPromoCode not in codes:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"Discount {request.discount.guid} requires a valid appliedPromoCode.",
                    field=f"[{i}].appliedPromoCode",
                )
            sources.append((source, request))
        now = now_ms(ctx)

        def built(mint: Any) -> list[dict[str, Any]]:
            return [
                {
                    "guid": None if mint is None else mint(),
                    "name": source.get("name"),
                    "discountAmount": 0,
                    "discount": {"guid": str(source["id"]), "entityType": "Discount"},
                    "triggers": []
                    if selection_guid is None
                    else [
                        {
                            "selection": {"guid": selection_guid, "entityType": "MenuItemSelection"},
                            "quantity": target.get("quantity"),
                        }
                    ],
                    "appliedPromoCode": request.appliedPromoCode,
                }
                for source, request in sources
            ]

        def apply_to(draft_check: dict[str, Any], rows: list[dict[str, Any]]) -> None:
            if selection_guid is None:
                draft_check["appliedDiscounts"] = [*draft_check.get("appliedDiscounts", []), *rows]
            else:
                selection = selection_by_guid(draft_check, selection_guid)
                assert selection is not None
                selection["appliedDiscounts"] = [*selection.get("appliedDiscounts", []), *rows]
                _reprice_selection(selection, index)
                selection["modifiedDate"] = now
            draft_check["modifiedDate"] = now
            retotal_check(draft_check, index)

        # THE INVARIANT (vendorfake#30 gate, finding 1, superseding the B2
        # PAID-only guard): a discount may not reduce totalAmount below what
        # the check's payments already cover -- which subsumes PAID (fully
        # covered) and a PARTIAL payment alike. Judged on a preview, before an
        # id is drawn or anything is written, so the refusal leaves no trace.
        covered = covered_cents(ctx, check)
        preview = copy.deepcopy(check)
        apply_to(preview, built(None))
        new_total = int(preview.get("totalAmount", 0))
        if new_total < covered:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"Check {check['guid']} already has {to_dollars(covered)} paid; a discount cannot reduce "
                    f"its totalAmount to {to_dollars(new_total)}, below what is already paid."
                ),
                field="checkGuid",
                info={"covered_cents": covered, "would_total_cents": new_total},
            )
        applied = built(self._deps.ids.applied_discount)

        def apply(draft: Entity) -> None:
            apply_to(_check_of(draft, check["guid"]), applied)
            # Re-settle so paymentStatus stays truthful: a discount that
            # brings the total down TO what is covered makes the check PAID.
            settle_order(draft, ctx)

        op = "CheckDiscountsPost" if selection_guid is None else "SelectionDiscountsPost"
        updated = ctx.store.collection(COL.orders).update(order["id"], apply, meta={"operation_id": op})
        return reply_order(args, updated)

    def applicable_discounts(self, args: HandlerArgs) -> ReplyInit:
        """Which active discounts could apply: ITEM ones to every selection of
        the body, CHECK ones to every check (JUDGMENT: no item restrictions
        are modelled). The answer's shape is the documented one."""
        restaurant = require_restaurant(args)
        request = validate_body(OrderRequest, args.body())
        body = args.body()
        selections: list[dict[str, Any]] = []
        checks: list[dict[str, Any]] = []
        for check_index, check in enumerate(request.checks):
            raw_check = body["checks"][check_index] if isinstance(body.get("checks"), list) else {}
            checks.append(
                {
                    "guid": raw_check.get("guid") if isinstance(raw_check, Mapping) else None,
                    "entityType": "CHECK",
                    "externalId": check.externalId,
                }
            )
            raw_selections = raw_check.get("selections", []) if isinstance(raw_check, Mapping) else []
            for selection_index, selection in enumerate(check.selections):
                raw_selection = raw_selections[selection_index] if selection_index < len(raw_selections) else {}
                selections.append(
                    {
                        "guid": raw_selection.get("guid") if isinstance(raw_selection, Mapping) else None,
                        "entityType": "SELECTION",
                        "externalId": selection.externalId,
                    }
                )
        answer = []
        for row in args.ctx.store.collection(COL.discounts).all():
            if not row.get("active", True):
                continue
            kind = row.get("selectionType")
            answer.append(
                {
                    "discount": {"guid": str(row["id"]), "entityType": "Discount"},
                    "applicableChecks": checks if kind == "CHECK" else [],
                    "applicableSelections": selections if kind == "ITEM" else [],
                }
            )
        del restaurant
        return json_(answer)

    def update_delivery_info(self, args: HandlerArgs) -> ReplyInit:
        ctx = args.ctx
        restaurant = require_restaurant(args)
        request = validate_body(DeliveryInfoRequest, args.body())
        order = load_order(args, restaurant, args.params["guid"])
        _assert_not_voided(order)
        now = now_ms(ctx)
        patch = request.model_dump(exclude_none=True)

        def update(draft: Entity) -> None:
            draft["deliveryInfo"] = {**draft.get("deliveryInfo", {}), **patch}
            draft["modifiedDate"] = now

        updated = ctx.store.collection(COL.orders).update(
            order["id"], update, meta={"operation_id": "OrderDeliveryInfoPatch"}
        )
        return reply_order(args, updated)


# ---------------------------------------------------------------------------
# Module-level helpers, shared with the payments surface.
# ---------------------------------------------------------------------------


def order_routes(deps: ToastDeps) -> tuple[Route, ...]:
    return ToastOrdersSurface(deps).routes()


def _client_id(args: HandlerArgs) -> str:
    meta = args.auth.meta if args.auth is not None else None
    value = None if meta is None else meta.get("client_id")
    return str(value) if isinstance(value, str) else ""


def _complete_delivery_info(info: Any) -> dict[str, Any] | None:
    """DOCUMENTED: ``DeliveryInfo`` requires ``address1``, ``city``, ``state``
    and ``zipCode`` (the orders specification's ``required`` list). An order
    created with a partial address is refused, field by field, so that the
    stored document is one the specification describes. Found by the fidelity
    validator (konyklabs/roadmap#56)."""
    document = dict(info.model_dump(exclude_none=True))
    if not document:
        return None
    for name in ("address1", "city", "state", "zipCode"):
        if not document.get(name):
            raise UnitError(
                UnitErrorKind.MISSING_FIELD, detail=f"deliveryInfo.{name} is required.", field=f"deliveryInfo.{name}"
            )
    return document


def _scope_flags(args: HandlerArgs) -> dict[str, bool]:
    scopes = set(args.auth.scopes) if args.auth is not None else set()
    return {"guest_pi": "guest.pi:read" in scopes, "delivery_address": "delivery_info.address:read" in scopes}


def _project(args: HandlerArgs, stored: Mapping[str, Any]) -> dict[str, Any]:
    return project_order(stored, payments_for(args.ctx, stored), **_scope_flags(args))


def reply_order(args: HandlerArgs, stored: Mapping[str, Any]) -> ReplyInit:
    return json_(_project(args, stored))


def load_order(args: HandlerArgs, restaurant: RestaurantEntity, guid: str) -> Entity:
    """The order, visible to this client at this restaurant, or the documented
    400 (malformed guid) / 404 (not found)."""
    if not is_guid(guid):
        raise UnitError(UnitErrorKind.BAD_REQUEST, detail=GUID_MALFORMED, field="guid")
    stored = args.ctx.store.collection(COL.orders).get(guid)
    if stored is None or stored.get("restaurant_guid") != restaurant.id or stored.get("client_id") != _client_id(args):
        raise UnitError(UnitErrorKind.NOT_FOUND, detail=ORDER_NOT_FOUND, field="guid")
    return stored


def _assert_not_voided(order: Mapping[str, Any]) -> None:
    if order.get("voided"):
        raise UnitError(
            UnitErrorKind.INVALID_TRANSITION,
            detail=VOIDED_IMMUTABLE,
            field="guid",
            info={"from": "VOIDED", "terminal": True},
        )


def _check_of(order: Mapping[str, Any], check_guid: str) -> dict[str, Any]:
    for check in order.get("checks", []):
        if isinstance(check, dict) and check.get("guid") == check_guid:
            return check
    raise UnitError(
        UnitErrorKind.NOT_FOUND, detail=f"Check {check_guid} was not found on this order.", field="checkGuid"
    )


def _reference(ctx: UnitContext, collection: str, guid: str, entity_type: str, field: str) -> dict[str, str]:
    if ctx.store.collection(collection).get(guid) is None:
        raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"{entity_type} {guid} was not found.", field=field)
    return {"guid": guid, "entityType": entity_type}


def _check_external_ids(orders: Any, restaurant_guid: str, request: OrderRequest) -> None:
    """ "The externalId values for the Order, Check, and Selection objects must
    be unique" -- across the restaurant's history, and within the body."""
    wanted: list[str] = []
    if request.externalId:
        wanted.append(request.externalId)
    for check in request.checks:
        if check.externalId:
            wanted.append(check.externalId)
        wanted.extend(s.externalId for s in check.selections if s.externalId)
    if len(wanted) != len(set(wanted)):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="The externalId values for the Order, Check, and Selection objects must be unique.",
            field="externalId",
        )
    if not wanted:
        return
    taken: set[str] = set()
    for row in orders.all():
        if row.get("restaurant_guid") != restaurant_guid:
            continue
        taken.update(_external_ids_of(row))
    clash = [value for value in wanted if value in taken]
    if clash:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"externalId {clash[0]!r} is already used by an existing order, check or selection.",
            field="externalId",
            info={"duplicates": clash},
        )


def _external_ids_of(order: Mapping[str, Any]) -> set[str]:
    found: set[str] = set()
    if order.get("externalId"):
        found.add(str(order["externalId"]))
    for check in order.get("checks", []):
        if check.get("externalId"):
            found.add(str(check["externalId"]))
        stack = list(check.get("selections", []))
        while stack:
            selection = stack.pop()
            if selection.get("externalId"):
                found.add(str(selection["externalId"]))
            stack.extend(selection.get("modifiers", []))
    return found


def _filtered_orders(args: HandlerArgs, restaurant: RestaurantEntity) -> list[Entity]:
    """``startDate``+``endDate`` on ``modifiedDate`` (inclusive, exclusive) or
    ``businessDate``; one or the other, documented for ``/ordersBulk``."""
    start, end, business = args.query("startDate"), args.query("endDate"), args.query("businessDate")
    if business is not None and (start is not None or end is not None):
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="Specify either startDate and endDate, or businessDate, not both.",
            field="businessDate",
        )
    predicate: Callable[[Mapping[str, Any]], bool]
    if business is not None:
        wanted = parse_business_date(business, field="businessDate")
        predicate = lambda row: row.get("businessDate") == wanted  # noqa: E731
    elif start is not None and end is not None:
        start_ms = parse_rest_date(start, field="startDate")
        end_ms = parse_rest_date(end, field="endDate")
        if start_ms < EARLIEST_DATE_MS:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE, detail="startDate must be after 2015-12-01.", field="startDate"
            )
        if end_ms <= start_ms:
            raise UnitError(UnitErrorKind.INVALID_VALUE, detail="endDate must be after startDate.", field="endDate")
        predicate = lambda row: start_ms <= int(row.get("modifiedDate", 0)) < end_ms  # noqa: E731
    else:
        raise UnitError(
            UnitErrorKind.MISSING_FIELD,
            detail="startDate and endDate, or businessDate, are required.",
            field="startDate",
        )
    client = _client_id(args)
    return [
        row
        for row in args.ctx.store.collection(COL.orders).all()
        if row.get("restaurant_guid") == restaurant.id and row.get("client_id") == client and predicate(row)
    ]


def _void_selections(selections: list[dict[str, Any]], now: int, void_business_date: int) -> None:
    for selection in selections:
        selection["voided"] = True
        selection["voidDate"] = now
        selection["voidBusinessDate"] = void_business_date
        selection["modifiedDate"] = now
        _void_selections(selection.get("modifiers", []), now, void_business_date)


def _reprice_selection(selection: dict[str, Any], index: MenuIndex) -> None:
    """Item-level discounts come off ``preDiscountPrice``; tax follows the
    discounted price (JUDGMENT, ``model/pricing.py``)."""
    base = int(selection.get("preDiscountPrice", 0))
    taken = 0
    for applied in selection.get("appliedDiscounts", []):
        source = index.discounts.get(str(applied.get("discount", {}).get("guid", "")))
        if source is not None:
            applied["discountAmount"] = discount_amount(max(0, base - taken), source)
        taken += int(applied.get("discountAmount", 0))
    selection["price"] = max(0, base - taken)
    selection["receiptLinePrice"] = selection["price"]
    rates = [index.tax_rates[g] for g in selection.get("_rates", []) if g in index.tax_rates]
    selection["appliedTaxes"] = taxes_on(selection["price"], rates, owner=str(selection.get("guid", "")))
    selection["tax"] = sum(int(t["taxAmount"]) for t in selection["appliedTaxes"])
