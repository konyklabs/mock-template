"""The Sales tag: the list, one sale, create, update, and the return action.

DOCUMENTED, all five operations of the tag and every scope read out of the
operation's own ``description`` annotation:

=================================================  =====================  =====================================
``GET /sales``                                     ``ListSales``          ``sales:read``
``POST /sales``                                    ``CreateSale``         ``sales:write``
``GET /sales/{sale_id}``                           ``GetSaleByID``        ``sales:read``
``PUT /sales/{sale_id}``                           ``UpdateSale``         ``sales:write``
``POST /sales/{sale_id}/actions/return``           ``initReturnSale``     ``sales:write`` **and** ``users:read``
=================================================  =====================  =====================================

The return operation's pair is the vendor's own: "🔒 Requires: ``sales:write``
``users:read`` scopes". ``users:read`` is on the 58-scope reference page and
is required here even though the Users tag itself is out of scope -- the
operation's description names it, so this unit requires it.

``ListSales`` DECLARES ONLY THREE PARAMETERS -- ``after``, ``before`` and
``page_size``. It declares no ``deleted`` and no resource filter of any kind:
no ``status``, no ``outlet_id``, no ``customer_id``, no date range. The
description says so outright -- "To search for sales, please have a look at our
Search endpoint on what is supported" -- and ``GET /search`` is outside issue
#94's scoped surface. So this route accepts the same
``after``/``before``/``page_size``/``deleted`` reader every list in this
package shares (``deleted`` selects nothing, because the Sales tag has no
delete operation and nothing here ever sets ``deleted_at``) and adds no filter
the vendor does not publish. Inventing ``?status=closed`` would teach a
consumer a query the real API answers with an unfiltered page.

WHAT FIRES. Every create, every update and every return writes the ``sales``
collection, and ``events.py`` maps that collection to ``sale.update``. The
return fires it **twice** -- once for the new return sale, once for the
original whose ``return.return_sale_ids`` gains it -- which is the shape the
webhooks page describes when it says ``sale.update`` "may fire multiple times
for layby/account sales"
(https://x-series-api.lightspeedhq.com/docs/webhooks). Closing a sale that
draws stock fires ``inventory.update`` as well, one per inventory record the
sale moved.

THE PAYMENT ERRORS ARE THE VENDOR'S OWN SHAPE, WITH THIS PROJECT'S CODES.
``PaymentErrorResponse`` -- ``{"error": {"code": <int>, "message": <str>}}`` --
is the only named error schema in the whole 373-schema document, and no
operation in the served document references it (checked: zero ``$ref`` s to it
across all 201 operations). So the shape is documented and its *use here* is
JUDGMENT: this unit answers it for the failures that are about a payment
rather than about the sale's own fields, which is the only class of failure the
schema's name fits. The integer codes are this project's -- Lightspeed
publishes no error-code list anywhere -- and live in ``model/error.py`` so
there is one table rather than literals down this file.

THE STATE MACHINE is ``machine.py``'s, enforced by the core. ``closed`` and
``voided`` are terminal, so ``PUT`` on a closed sale is a 409
``invalid_transition``; the return route is the documented carve-out and says
so at its own site.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact
from vendorfake.lightspeed.config import (
    SCOPE_SALES_READ,
    SCOPE_SALES_WRITE,
    SCOPE_USERS_READ,
)
from vendorfake.lightspeed.entities import COL, PaymentTypeEntity, RegisterEntity, SaleEntity
from vendorfake.lightspeed.machine import SALE_MACHINE, SaleState
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.error import PAYMENT_ERROR_INFO_KEY, PaymentErrorCode
from vendorfake.lightspeed.model.sale import (
    SaleLineItemRequest,
    SaleRequest,
    SaleUpdateRequest,
    build_line_item,
    build_payment,
    project_sale,
)
from vendorfake.lightspeed.model.scalars import decimal_text
from vendorfake.lightspeed.paths import CREATE_SALE, GET_SALE_BY_ID, INIT_RETURN_SALE, LIST_SALES, UPDATE_SALE
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, stamp_version, wire_time
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select, single

__all__ = ["CAPABILITY", "LightspeedSalesSurface", "payment_error", "sale_routes"]

CAPABILITY = "sales"

_MACHINE = StateMachine(SALE_MACHINE)
"""Module level, like Toast's: a machine holds no state, so one instance
enforces every sale on every unit in the process."""

_INVENTORY_LEVEL = "current_inventory_level"
"""``Inventory.current_inventory_level`` -- the member the schema names. The
inventory collection belongs to the sibling slice of konyklabs/roadmap#94; this
surface only ever decrements a row that is already there."""


def payment_error(
    kind: UnitErrorKind,
    code: PaymentErrorCode,
    message: str,
    *,
    field: str,
) -> UnitError:
    """A refusal that reaches the wire as ``PaymentErrorResponse``.

    ``field`` still travels, in the ``unit_error`` sidecar rather than in the
    body: the vendor's schema has room for a code and a message and nothing
    else, and dropping the field name would make a payment failure the one
    refusal on this surface a consumer cannot locate.
    """
    return UnitError(
        kind,
        detail=message,
        field=field,
        info={PAYMENT_ERROR_INFO_KEY: int(code)},
    )


class LightspeedSalesSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=LIST_SALES,
                capability=CAPABILITY,
                handler=self.list_sales,
                auth=BEARER_AUTH,
                scopes=(SCOPE_SALES_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListSales",
                summary="Sales, ascending by version; after/before/page_size. No resource filter is documented.",
            ),
            # THE PUBLISHED EXAMPLE LIVES HERE, and that placement is the point
            # of it. A conformance check that knows no vendor needs one route
            # it can drive successfully, repeatedly, that COMMITS and ANNOUNCES
            # -- C18 drives it three times on one unit with the webhooks
            # capability on, off and on again. Creating a sale is the only such
            # route this vendor has: the register actions commit and announce
            # but each is one-shot (opening an open register is a 409), and the
            # webhook CRUD writes the core's subscription collection, which the
            # journal listener excludes by design.
            #
            # The example is deliberately a PARKED sale with no payments: a
            # payment requires an open register, and an example body whose
            # success depends on the state of another entity is an example that
            # fails for a reason that has nothing to do with the contract
            # asking. The line item is real, so the mutation is a real sale.
            Route(
                method="POST",
                path=CREATE_SALE,
                capability=CAPABILITY,
                handler=self.create_sale,
                auth=BEARER_AUTH,
                scopes=(SCOPE_SALES_WRITE,),
                example_body=_example_body(),
                operation_id="CreateSale",
                summary="Create a sale; line items and payments are inline. Fires sale.update.",
            ),
            Route(
                method="GET",
                path=GET_SALE_BY_ID,
                capability=CAPABILITY,
                handler=self.get_sale,
                auth=BEARER_AUTH,
                scopes=(SCOPE_SALES_READ,),
                operation_id="GetSaleByID",
                summary="One sale by id.",
            ),
            Route(
                method="PUT",
                path=UPDATE_SALE,
                capability=CAPABILITY,
                handler=self.update_sale,
                auth=BEARER_AUTH,
                scopes=(SCOPE_SALES_WRITE,),
                operation_id="UpdateSale",
                summary="Replace a sale's editable attributes. 409 once it is closed or voided.",
            ),
            Route(
                method="POST",
                path=INIT_RETURN_SALE,
                capability=CAPABILITY,
                handler=self.return_sale,
                auth=BEARER_AUTH,
                scopes=(SCOPE_SALES_WRITE, SCOPE_USERS_READ),
                operation_id="initReturnSale",
                summary="Open a return against a closed sale; answers the new parked return sale.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_sales(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        names = _payment_type_names(args.ctx)
        rows = select(args.ctx.store.collection(COL.sales).all(), query)
        return json_(envelope([project_sale(row, names=names) for row in rows]))

    def get_sale(self, args: HandlerArgs) -> ReplyInit:
        stored = self._require(args)
        return json_(single(project_sale(stored, names=_payment_type_names(args.ctx))))

    # -- writes -------------------------------------------------------------

    def create_sale(self, args: HandlerArgs) -> ReplyInit:
        # The body first, for the reason the register actions record: a body
        # that is not valid JSON is malformed whatever it was addressed to.
        request = validate_body(SaleRequest, args.body())
        ctx = args.ctx
        sales = ctx.store.collection(COL.sales)
        sale_id = request.id or self._deps.ids.sale()
        if request.id is not None and sales.get(request.id) is not None:
            # DOCUMENTED that the id may be caller-supplied ("User-provided
            # sale ID. If not included, one will be generated"); JUDGMENT that
            # re-using one is a 409 rather than an overwrite. A POST that
            # silently replaced an existing sale would let a retried request
            # destroy the sale its first attempt created.
            raise UnitError(
                UnitErrorKind.CONFLICT,
                detail=f"Sale {request.id} already exists; update it with PUT /sales/{{sale_id}}.",
                field="id",
            )
        # The initial state is the machine's, so creating a sale straight into
        # `closed` (the one-request POS flow) is validated as the edge it is.
        _MACHINE.assert_transition(SaleState.PARKED.value, request.state, f"Sale {sale_id}")
        built = self._build(ctx, request, sale_id=sale_id, previous=None)
        stored = sales.insert(built.to_entity(), {"operation_id": "CreateSale"})
        if built.state == SaleState.CLOSED.value:
            self._draw_stock(ctx, built, direction=-1)
        return json_(single(project_sale(stored, names=_payment_type_names(ctx))))

    def update_sale(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(SaleUpdateRequest, args.body())
        ctx = args.ctx
        stored = self._require(args)
        previous = SaleEntity.from_entity(stored)
        subject = f"Sale {previous.id}"
        # `assert_mutable` before `assert_transition`, as the core's docstring
        # asks: "this is finished" explains "that move is not allowed".
        _MACHINE.assert_mutable(previous.state, subject)
        _MACHINE.assert_transition(previous.state, request.state, subject)
        built = self._build(ctx, request, sale_id=previous.id, previous=previous)
        deps = self._deps
        entity = built.to_entity()

        def mutate(draft: Entity) -> None:
            draft.clear()
            draft.update(entity)
            stamp_version(draft, deps)

        updated = ctx.store.collection(COL.sales).update(previous.id, mutate, meta={"operation_id": "UpdateSale"})
        if built.state == SaleState.CLOSED.value and previous.state != SaleState.CLOSED.value:
            self._draw_stock(ctx, built, direction=-1)
        return json_(single(project_sale(updated, names=_payment_type_names(ctx))))

    def return_sale(self, args: HandlerArgs) -> ReplyInit:
        """Open a return against a closed sale.

        DOCUMENTED: "Initializes a return for an existing **closed** sale and
        returns the newly created SAVED return sale. Use this endpoint to start
        the return workflow before adding refund payments or finalizing the
        returned items." So the answer is a NEW sale, it is editable (the
        refund payments come later, through ``PUT``), and the original is not
        edited into a return.

        THE ONE CARVE-OUT FROM THE STATE MACHINE. ``closed`` is terminal, so
        nothing else may write a closed sale -- but this operation is documented
        to work on exactly one, and it does write the original: its
        ``return.return_sale_ids`` gains the new sale's id, which is the member
        ``SaleReturn`` exists to carry ("IDs of return sales created from this
        sale"). The machine is not asked, deliberately and only here.

        JUDGMENT: the new sale's line items are the original's with the sign of
        every quantity flipped and ``is_return`` set, which is what
        ``initReturnSale``'s own response example shows (``"quantity": -1``,
        ``"is_return": true``, ``"total_price": -200`` on each line). It carries
        no payments: the documented workflow adds the refund afterwards.
        """
        ctx = args.ctx
        stored = self._require(args)
        original = SaleEntity.from_entity(stored)
        if original.state != SaleState.CLOSED.value:
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=(f"Sale {original.id} is {original.state}; a return can only be opened against a closed sale."),
                field="sale_id",
                info={"state": original.state},
            )
        deps = self._deps
        sales = ctx.store.collection(COL.sales)
        now = wire_time(ctx.clock)
        return_id = deps.ids.sale()
        lines = [
            {
                **line,
                "id": deps.ids.sale_line_item(),
                "quantity": -float(line.get("quantity", 0) or 0),
                "is_return": True,
            }
            for line in original.line_items
        ]
        register_id = str(original.source.get("register_id", "")) or None
        returned = SaleEntity(
            id=return_id,
            state=SaleState.PARKED.value,
            source=dict(original.source),
            line_items=lines,
            payments=[],
            attributes=original.attributes,
            customer_id=original.customer_id,
            note=original.note,
            invoice_number=self._invoice_number(ctx, register_id, None),
            receipt_number=self._invoice_number(ctx, register_id, None),
            date=now,
            is_return=True,
            original_sale_id=original.id,
            object_version=deps.versions.bump(),
        )
        created = sales.insert(returned.to_entity(), {"operation_id": "initReturnSale"})

        def link(draft: Entity) -> None:
            existing = draft.get("return")
            block = dict(existing) if isinstance(existing, Mapping) else {}
            block["return_sale_ids"] = [*block.get("return_sale_ids", []), return_id]
            draft["return"] = block
            stamp_version(draft, deps)

        # The SECOND sale.update of this request. See the module docstring.
        sales.update(original.id, link, meta={"operation_id": "initReturnSale"})
        return json_(single(project_sale(created, names=_payment_type_names(ctx))))

    # -- building a sale ----------------------------------------------------

    def _build(
        self,
        ctx: UnitContext,
        request: SaleUpdateRequest,
        *,
        sale_id: str,
        previous: SaleEntity | None,
    ) -> SaleEntity:
        """One validated request as the sale it describes.

        A PUT REPLACES. ``SaleUpdateRequest`` is ``SaleRequestBase`` unchanged
        -- the whole editable document, with ``line_items`` and ``payments``
        inline -- so an update states the sale it wants rather than patching
        the one that is there. JUDGMENT, and the alternative is worse: with the
        arrays inline and no PATCH operation anywhere in the tag, merging would
        leave a caller no way to REMOVE a line item.

        What survives an update is what a caller cannot send: the id, the
        creation instant, and the return links.
        """
        deps = self._deps
        now = wire_time(ctx.clock)
        register = self._register(ctx, request.source.register_id)
        outlet_id = self._outlet_id(request, register)
        self._check_outlet(request, outlet_id)
        self._check_customer(ctx, request.customer_id)
        lines = self._line_items(ctx, request.line_items, previous=previous)
        payments = self._payments(ctx, request, register=register, now=now)
        return SaleEntity(
            id=sale_id,
            state=request.state,
            source=_source(request, outlet_id=outlet_id),
            line_items=lines,
            payments=payments,
            attributes=tuple(request.attributes),
            customer_id=request.customer_id,
            note=request.note,
            short_code=request.short_code,
            invoice_number=(
                request.invoice_number
                or (previous.invoice_number if previous is not None else None)
                or self._invoice_number(ctx, request.source.register_id, sale_id)
            ),
            receipt_number=(
                (previous.receipt_number if previous is not None else None)
                or self._invoice_number(ctx, request.source.register_id, sale_id)
            ),
            accounts_transaction_id=request.accounts_transaction_id,
            date=request.date or (previous.date if previous is not None else None) or now,
            is_return=previous.is_return if previous is not None else False,
            original_sale_id=previous.original_sale_id if previous is not None else None,
            return_sale_ids=previous.return_sale_ids if previous is not None else (),
            object_version=deps.versions.bump(),
        )

    def _line_items(
        self,
        ctx: UnitContext,
        requested: Sequence[SaleLineItemRequest],
        *,
        previous: SaleEntity | None,
    ) -> list[dict[str, Any]]:
        """Every line validated before any id is minted.

        The two-pass shape is the same one the Toast package uses and for the
        same reason: a refusal in the third line must not have drawn ids for the
        first two, or two otherwise identical scenarios stop producing the same
        transcript.

        ``SaleLineItem.id`` is documented as "Existing line item ID. If included
        in the POST request it will cause an update instead of a creating a new
        object", so a caller's own id is kept as given.
        """
        products = ctx.store.collection(COL.products)
        known = {row["id"] for row in products.all()}
        is_return = previous is not None and previous.is_return
        for index, line in enumerate(requested):
            field = f"line_items[{index}]"
            if known and line.product.id not in known:
                # Checked only when the scenario HAS products: the products
                # collection is the sibling slice's, and a unit seeded without
                # one must not start refusing every sale. JUDGMENT, and it is
                # the guard rather than the check that is judged -- an unknown
                # product on a unit that knows its products is a 422.
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"{field}.product.id {line.product.id!r} is not a product.",
                    field=f"{field}.product.id",
                )
            build_line_item(line, line_id="", sequence=index, field=field, is_return=is_return)
        return [
            build_line_item(
                line,
                line_id=line.id or self._deps.ids.sale_line_item(),
                sequence=index,
                field=f"line_items[{index}]",
                is_return=is_return,
            )
            for index, line in enumerate(requested)
        ]

    def _payments(
        self,
        ctx: UnitContext,
        request: SaleUpdateRequest,
        *,
        register: RegisterEntity | None,
        now: str,
    ) -> list[dict[str, Any]]:
        """Every payment resolved to a payment type and an open register.

        THE OPEN-REGISTER RULE is the one payment rule the vendor states
        plainly, in the scope it gates the action with: ``register:open`` is
        "Open a register **to create sales and payments**"
        (https://x-series-api.lightspeedhq.com/docs/scopes). A till that is
        closed does not take money, and a fake that accepted the payment anyway
        would let a consumer's end-of-day reconciliation pass on data a real
        retailer could never produce.
        """
        types = {row["id"]: PaymentTypeEntity.from_entity(row) for row in ctx.store.collection(COL.payment_types).all()}
        built: list[dict[str, Any]] = []
        for index, payment in enumerate(request.payments):
            field = f"payments[{index}]"
            payment_type_id = payment.type.config_id
            if payment_type_id not in types:
                raise payment_error(
                    UnitErrorKind.INVALID_VALUE,
                    PaymentErrorCode.UNKNOWN_PAYMENT_TYPE,
                    f"{field}.type.config_id {payment_type_id!r} is not a payment type of this retailer.",
                    field=f"{field}.type.config_id",
                )
            named = payment.source.register_id if payment.source is not None else None
            register_id = named or (register.id if register is not None else None)
            if register_id is None:
                raise payment_error(
                    UnitErrorKind.INVALID_VALUE,
                    PaymentErrorCode.REGISTER_REQUIRED,
                    (
                        f"{field} names no register: set {field}.source.register_id, or source.register_id on the "
                        f"sale. A payment is taken at a till."
                    ),
                    field=f"{field}.source.register_id",
                )
            on = register if register is not None and register.id == register_id else self._register(ctx, register_id)
            if on is None:
                raise payment_error(
                    UnitErrorKind.INVALID_VALUE,
                    PaymentErrorCode.UNKNOWN_REGISTER,
                    f"{field} names register {register_id!r}, which is not a register of this retailer.",
                    field=f"{field}.source.register_id",
                )
            if not on.is_open:
                raise payment_error(
                    UnitErrorKind.INVALID_TRANSITION,
                    PaymentErrorCode.REGISTER_NOT_OPEN,
                    f"Register {on.id} is not open; a closed register takes no payments.",
                    field=f"{field}.source.register_id",
                )
            built.append(
                build_payment(
                    payment,
                    payment_id=payment.id or self._deps.ids.sale_payment(),
                    payment_type_id=payment_type_id,
                    register_id=on.id,
                    date=payment.date or request.date or now,
                    field=field,
                    # A refund is a negative payment; the amount is signed on
                    # a return sale and never on an ordinary one.
                    allow_negative=_is_refund(request),
                )
            )
        return built

    # -- inventory ----------------------------------------------------------

    def _draw_stock(self, ctx: UnitContext, sale: SaleEntity, *, direction: int) -> None:
        """Move the outlet's stock by the sale's line quantities, if there is
        any stock to move.

        GUARDED ON THE COLLECTION BEING POPULATED, deliberately: ``inventory``
        belongs to the sibling slice of konyklabs/roadmap#94 and a unit whose
        scenario carries no inventory records must still be able to close a
        sale. Once the two slices are merged this branch stops being reachable
        for the shipped scenario and the decrement is simply what closing does.

        Each updated record is its own journal entry, so a sale of three
        tracked products fires three ``inventory.update`` deliveries -- one per
        record that actually moved, which is what the event names.

        A RETURN GIVES STOCK BACK for free: its line quantities are negative,
        so the same subtraction adds.
        """
        inventory = ctx.store.collection(COL.inventory)
        rows = inventory.all()
        if not rows:
            return
        sale_outlet = str(sale.source.get("outlet_id", "")) or None
        index = {(str(row.get("product_id", "")), str(row.get("outlet_id", ""))): str(row["id"]) for row in rows}
        deps = self._deps
        for line in sale.line_items:
            outlet_id = str(line.get("fulfilment_outlet_id") or sale_outlet or "")
            record_id = index.get((str(line.get("product_id", "")), outlet_id))
            if record_id is None:
                continue
            quantity = Decimal(str(line.get("quantity", 0) or 0))
            inventory.update(record_id, _decrement(quantity * direction, deps), meta={"operation_id": "SaleStockMove"})

    # -- helpers ------------------------------------------------------------

    def _require(self, args: HandlerArgs) -> dict[str, Any]:
        sale_id = args.params["sale_id"]
        stored = args.ctx.store.collection(COL.sales).get(sale_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Sale {sale_id} was not found.", field="sale_id")
        return stored

    def _register(self, ctx: UnitContext, register_id: str | None) -> RegisterEntity | None:
        if register_id is None:
            return None
        stored = ctx.store.collection(COL.registers).get(register_id)
        return None if stored is None else RegisterEntity.from_entity(stored)

    def _outlet_id(self, request: SaleUpdateRequest, register: RegisterEntity | None) -> str | None:
        """Which outlet this sale belongs to.

        JUDGMENT on the ORDER, because the vendor documents the two inputs and
        not their precedence: the register's own outlet first (a sale rung up
        at a till happened at that till's outlet), then
        ``fulfillment_outlet_id``, documented as "The default outlet that
        should fulfill this sale when a line item does not specify its own
        ``fulfilment_outlet_id``". ``SaleResponseSource.outlet_id`` is where the
        answer is published.
        """
        if register is not None:
            return register.outlet_id
        return request.fulfillment_outlet_id

    def _check_outlet(self, request: SaleUpdateRequest, outlet_id: str | None) -> None:
        """A sale that CLOSES must say which outlet each of its lines comes out
        of, because closing is what moves stock.

        JUDGMENT, and it is a refusal rather than a silent no-op. Neither
        ``SaleRequestSource`` (``author_id`` is its one required member) nor
        ``SaleRequestBase`` makes an outlet required, so a body carrying no
        ``source.register_id``, no ``fulfillment_outlet_id`` and no line-level
        ``fulfilment_outlet_id`` is schema-legal -- and closing it used to
        answer 200 while moving nothing and firing no ``inventory.update``. A
        consumer's stock-decrement test then passed while exercising nothing,
        which is a failure wearing the shape of a success on the one side
        effect closing a sale has. So an unresolvable outlet joins the other
        unresolvable references on this surface (``product.id``,
        ``customer_id``, ``payments[n].type.config_id``) and is a 422.

        ONLY ON A CLOSE, and only for a line that resolves to nothing: a
        parked or pending sale moves no stock, and a line naming its own
        ``fulfilment_outlet_id`` resolves whatever the sale does.
        """
        if request.state != SaleState.CLOSED.value:
            return
        for index, line in enumerate(request.line_items):
            if line.fulfilment_outlet_id or outlet_id:
                continue
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"line_items[{index}] resolves to no outlet, so closing this sale could not move its "
                    f"stock. Name a register in source.register_id, a sale-level fulfillment_outlet_id, or "
                    f"this line's own fulfilment_outlet_id."
                ),
                field=f"line_items[{index}].fulfilment_outlet_id",
            )

    def _check_customer(self, ctx: UnitContext, customer_id: str | None) -> None:
        """A named customer must exist -- when the scenario knows any customers.

        The same guard the product check carries, for the same reason: the
        ``customers`` collection is the sibling slice's.
        """
        if customer_id is None:
            return
        customers = ctx.store.collection(COL.customers)
        rows = customers.all()
        if rows and customers.get(customer_id) is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"customer_id {customer_id!r} is not a customer of this retailer.",
                field="customer_id",
            )

    def _invoice_number(self, ctx: UnitContext, register_id: str | None, sale_id: str | None) -> str | None:
        """``{invoice_prefix}{sequence}{invoice_suffix}`` off the register.

        DOCUMENTED: ``SaleRequestBase.invoice_number`` is "The invoice number
        for the sale. If left null it will be populated by Lightspeed with the
        next available invoice number", and ``Register`` carries
        ``invoice_prefix``, ``invoice_sequence`` and ``invoice_suffix``, whose
        own description says a provided number "should use the prefix and suffix
        defined for the register".

        JUDGMENT: the sequence is the register's ``invoice_sequence`` plus the
        number of sales already attributed to that register, and the register's
        own field is NOT bumped. Bumping it would make every sale a second
        mutation of a second entity -- a second journal entry and a second
        version draw for a counter no route reads back -- and the derived form
        gives the same ascending sequence deterministically.
        """
        register = self._register(ctx, register_id)
        if register is None:
            return None
        taken = sum(
            1
            for row in ctx.store.collection(COL.sales).all()
            if str(_source_of(row).get("register_id", "")) == register.id and row.get("id") != sale_id
        )
        return f"{register.invoice_prefix}{register.invoice_sequence + taken}{register.invoice_suffix}"


def _decrement(quantity: Decimal, deps: LightspeedDeps) -> Callable[[Entity], None]:
    """A store mutator that moves one inventory record's level by ``quantity``.

    A factory rather than a closure written in the loop: a mutator built inside
    a ``for`` would capture the loop variable and every record would be moved by
    the last line item's quantity.

    THE LEVEL IS DECIMAL TEXT, both read and written, which is
    ``InventoryEntity``'s own storage shape and what
    ``surface/inventory.py::_move_stock`` writes for a stock adjustment: the
    two mutators have to agree, or a sale and an adjustment would leave the
    same record in two different spellings and the projection would only
    understand one. It is projected to a JSON number by ``model/inventory.py``.
    """

    def mutate(draft: Entity) -> None:
        level = Decimal(str(draft.get(_INVENTORY_LEVEL, "0") or "0"))
        draft[_INVENTORY_LEVEL] = decimal_text(level + quantity, field=_INVENTORY_LEVEL, allow_negative=True)
        stamp_version(draft, deps)

    return mutate


def _is_refund(request: SaleUpdateRequest) -> bool:
    """Whether this body describes a refund, i.e. carries a negative line.

    A refund's payments are negative, and the only way this unit sees one is a
    body whose line quantities already are -- which is what the return route
    produced. Read off the request rather than off the stored sale so that a
    caller updating a return sale to add its refund payment (the documented
    workflow: "start the return workflow before adding refund payments") is not
    refused for sending a negative amount.
    """
    return any(
        isinstance(line.quantity, int | float) and not isinstance(line.quantity, bool) and line.quantity < 0
        for line in request.line_items
    )


def _source(request: SaleUpdateRequest, *, outlet_id: str | None) -> dict[str, Any]:
    """The stored ``source`` block. ``author_id`` is stored flat and projected
    as ``SaleResponseSource.author`` -- ``{"id": ...}`` -- because that is where
    the request puts it and where the response puts it, respectively."""
    return compact(
        {
            "author_id": request.source.author_id,
            "register_id": request.source.register_id,
            "outlet_id": outlet_id,
            "id": request.source.id,
            "type": request.source.type,
        }
    )


def _source_of(entity: Mapping[str, Any]) -> Mapping[str, Any]:
    source = entity.get("source")
    return source if isinstance(source, Mapping) else {}


def _payment_type_names(ctx: UnitContext) -> dict[str, str]:
    """Payment type id to name, for ``PaymentTypeDetails.name`` on every
    projected payment."""
    return {str(row["id"]): str(row.get("name", "")) for row in ctx.store.collection(COL.payment_types).all()}


def _example_body() -> dict[str, Any]:
    """The body ``POST /sales`` publishes. Built from the seed constants, so a
    conformance run drives a sale against entities the shipped scenario really
    has -- and so an edit to the scenario cannot leave the example naming an id
    that is gone."""
    from vendorfake.lightspeed.seed.constants import (
        SEED_PRODUCT_TRAIL_MIX_ID,
        SEED_REGISTER_MAIN_ID,
        SEED_TAX_ID,
        SEED_USER_ID,
    )

    return {
        "state": SaleState.PARKED.value,
        "source": {"author_id": SEED_USER_ID, "register_id": SEED_REGISTER_MAIN_ID},
        "line_items": [
            {
                "product": {"id": SEED_PRODUCT_TRAIL_MIX_ID},
                "quantity": 1,
                # The trail mix's own catalogue price, tax-exclusive, with the
                # unit tax beside it: 10.87 + 1.63 = the 12.50 the product
                # lists at. Nothing makes a line price match the catalogue --
                # a sale records what was charged -- but a PUBLISHED example
                # that disagrees with the shipped scenario reads as a bug.
                "pricing": {"price": 10.87},
                "tax": {"id": SEED_TAX_ID, "amount": 1.63},
            }
        ],
    }


def sale_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedSalesSurface(deps).routes()
