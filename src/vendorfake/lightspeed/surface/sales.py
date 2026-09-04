"""The Sales tag: list, one sale, create, update, and the return action.

DOCUMENTED: scopes come from each operation's own ``description``
(``initReturnSale`` also needs ``users:read``); ``ListSales`` takes only
``after``/``before``/``page_size``, since its description points searches at
``GET /search`` instead.

Create, update and return all write ``sales`` and fire ``sale.update`` (a
return fires it twice, per the webhooks page's note on layby/account sales,
https://x-series-api.lightspeedhq.com/docs/webhooks); closing a sale that
draws stock also fires ``inventory.update`` per record moved.

JUDGMENT: ``PaymentErrorResponse`` is documented but unreferenced by any
operation; used here for payment-specific failures, with this project's own
codes (``model/error.py``). ``closed``/``voided`` are terminal in the state
machine (``machine.py``); the return route is the documented carve-out.
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
from vendorfake.lightspeed.model.money import to_minor
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
#: Stateless, so one module-level instance enforces every sale on every unit.

_INVENTORY_LEVEL = "current_inventory_level"
#: ``Inventory.current_inventory_level`` -- the member the schema names.


def payment_error(
    kind: UnitErrorKind,
    code: PaymentErrorCode,
    message: str,
    *,
    field: str,
) -> UnitError:
    """A refusal that reaches the wire as ``PaymentErrorResponse``; ``field``
    travels in the ``unit_error`` sidecar, since that schema has room for only
    a code and a message."""
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
            # The published example (C18's repeatable commit+announce route)
            # is a PARKED sale with a real line item but no payments, so its
            # success doesn't depend on register state.
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
        # Body validated first: malformed JSON is malformed regardless of state
        request = validate_body(SaleRequest, args.body())
        ctx = args.ctx
        sales = ctx.store.collection(COL.sales)
        sale_id = request.id or self._deps.ids.sale()
        if request.id is not None and sales.get(request.id) is not None:
            # DOCUMENTED: id may be caller-supplied. JUDGMENT: reusing an
            # existing one is a 409, not a silent overwrite.
            raise UnitError(
                UnitErrorKind.CONFLICT,
                detail=f"Sale {request.id} already exists; update it with PUT /sales/{{sale_id}}.",
                field="id",
            )
        # Creating straight into `closed` (one-request POS flow) is just another transition.
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
        # assert_mutable before assert_transition: "finished" explains "that move is not allowed".
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
        """Open a return against a closed sale. DOCUMENTED: creates a new,
        editable PARKED return sale (refund payments come via ``PUT`` later);
        the original gains the new id in ``return.return_sale_ids`` -- the one
        write allowed against a closed sale. JUDGMENT: line items mirror the
        original with quantities negated and ``is_return`` set."""
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
        """One validated request as the sale it describes. A PUT replaces the
        whole document (no PATCH exists in this tag), so what survives
        regardless is only the id, the creation instant, and return links."""
        deps = self._deps
        now = wire_time(ctx.clock)
        register = self._register(ctx, request.source.register_id)
        outlet_id = self._outlet_id(request, register)
        self._check_outlet(ctx, request, outlet_id)
        self._check_customer(ctx, request.customer_id)
        lines = self._line_items(ctx, request.line_items, previous=previous)
        payments = self._payments(ctx, request, register=register, now=now, previous=previous)
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
        """Every line validated before any id is minted (two-pass, like
        Toast's, so a refusal partway through draws no ids). DOCUMENTED: a
        caller's own ``SaleLineItem.id`` is kept as given."""
        products = ctx.store.collection(COL.products)
        known = {row["id"] for row in products.all()}
        is_return = previous is not None and previous.is_return
        for index, line in enumerate(requested):
            field = f"line_items[{index}]"
            if known and line.product.id not in known:
                # JUDGMENT: only checked when the scenario has products, so a unit seeded without any is not refused.
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
        previous: SaleEntity | None,
    ) -> list[dict[str, Any]]:
        """Every payment resolved to a payment type and an open register.
        DOCUMENTED: a closed register takes no payments (``register:open``,
        https://x-series-api.lightspeedhq.com/docs/scopes). JUDGMENT: an
        id-less payment on a replacing PUT reuses a stored payment's id when
        type and minor-unit amount both match (amount-and-type, not position,
        each claimed at most once), so an actually-changed payment mints a
        fresh id rather than letting a register closure's counted-ids guard
        miss money already counted."""
        types = {row["id"]: PaymentTypeEntity.from_entity(row) for row in ctx.store.collection(COL.payment_types).all()}
        carried = list(previous.payments) if previous is not None else []
        kept: set[str] = {payment.id for payment in request.payments if payment.id}
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
            # A refund's amount is signed; an ordinary sale's never is.
            allow_negative = _is_refund(request)
            amount_minor = to_minor(payment.amount, field=f"{field}.amount", allow_negative=allow_negative)
            payment_id = (
                payment.id
                or _carried_payment_id(carried, payment_type_id=payment_type_id, amount_minor=amount_minor, taken=kept)
                or self._deps.ids.sale_payment()
            )
            kept.add(payment_id)
            built.append(
                build_payment(
                    payment,
                    payment_id=payment_id,
                    payment_type_id=payment_type_id,
                    register_id=on.id,
                    date=payment.date or request.date or now,
                    field=field,
                    allow_negative=allow_negative,
                )
            )
        return built

    # -- inventory ----------------------------------------------------------

    def _draw_stock(self, ctx: UnitContext, sale: SaleEntity, *, direction: int) -> None:
        """Move the outlet's stock by the sale's line quantities, if any
        inventory exists to move (guarded, so a unit seeded without it can
        still close sales). Each moved record fires its own
        ``inventory.update``; a return's negative quantities give stock back
        for free."""
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
        """Which outlet this sale belongs to: JUDGMENT on the order -- the
        register's own outlet first, then ``fulfillment_outlet_id`` -- since
        the vendor documents both inputs but not their precedence."""
        if register is not None:
            return register.outlet_id
        return request.fulfillment_outlet_id

    def _check_outlet(self, ctx: UnitContext, request: SaleUpdateRequest, outlet_id: str | None) -> None:
        """A sale that CLOSES must resolve every line to a real outlet, since
        closing is what moves stock. JUDGMENT: an unresolvable outlet is a
        422, resolved against ``outlets`` (not just checked for presence) so a
        stale or foreign id cannot echo back a 200 that moves nothing. Guarded
        on the collection being populated; checked only on a close."""
        if request.state != SaleState.CLOSED.value:
            return
        outlets = {str(row["id"]) for row in ctx.store.collection(COL.outlets).all()}
        if outlets and request.fulfillment_outlet_id is not None and request.fulfillment_outlet_id not in outlets:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(f"fulfillment_outlet_id {request.fulfillment_outlet_id!r} is not an outlet of this retailer."),
                field="fulfillment_outlet_id",
            )
        for index, line in enumerate(request.line_items):
            if line.fulfilment_outlet_id and outlets and line.fulfilment_outlet_id not in outlets:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=(
                        f"line_items[{index}].fulfilment_outlet_id {line.fulfilment_outlet_id!r} is not an "
                        f"outlet of this retailer."
                    ),
                    field=f"line_items[{index}].fulfilment_outlet_id",
                )
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
        """A named customer must exist, when the scenario knows any customers."""
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
        DOCUMENTED: left null, Lightspeed fills it in from the register's own
        prefix/suffix/sequence. JUDGMENT: the sequence is derived (register's
        ``invoice_sequence`` plus sales already attributed to it) rather than
        bumping the register's own counter, which nothing reads back."""
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
    """A store mutator that moves one inventory record's level by ``quantity``
    (a factory, not a loop-body closure, so each captures its own quantity).
    The level is stored and read as decimal text, matching
    ``surface/inventory.py``'s stock-adjustment mutator."""

    def mutate(draft: Entity) -> None:
        level = Decimal(str(draft.get(_INVENTORY_LEVEL, "0") or "0"))
        draft[_INVENTORY_LEVEL] = decimal_text(level + quantity, field=_INVENTORY_LEVEL, allow_negative=True)
        stamp_version(draft, deps)

    return mutate


def _is_refund(request: SaleUpdateRequest) -> bool:
    """Whether this body describes a refund, i.e. carries a negative line.
    Read off the request rather than the stored sale, so adding a refund
    payment to a return sale is not itself refused for a negative amount."""
    return any(
        isinstance(line.quantity, int | float) and not isinstance(line.quantity, bool) and line.quantity < 0
        for line in request.line_items
    )


def _carried_payment_id(
    carried: Sequence[Mapping[str, Any]],
    *,
    payment_type_id: str,
    amount_minor: int,
    taken: set[str],
) -> str | None:
    """The stored id an id-less payment inherits: first stored payment
    matching on type and amount, not already claimed. See ``_payments``."""
    for stored in carried:
        stored_id = str(stored.get("id", ""))
        if not stored_id or stored_id in taken:
            continue
        if str(stored.get("payment_type_id", "")) != payment_type_id:
            continue
        if stored.get("amount_minor") != amount_minor:
            continue
        return stored_id
    return None


def _source(request: SaleUpdateRequest, *, outlet_id: str | None) -> dict[str, Any]:
    """The stored ``source`` block. ``author_id`` is stored flat and projected
    as ``SaleResponseSource.author`` -- ``{"id": ...}``."""
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
    """The body ``POST /sales`` publishes, built from the seed constants so it
    always names entities the shipped scenario really has."""
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
                # 10.87 + 1.63 tax = the product's 12.50 catalogue price.
                "pricing": {"price": 10.87},
                "tax": {"id": SEED_TAX_ID, "amount": 1.63},
            }
        ],
    }


def sale_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedSalesSurface(deps).routes()
