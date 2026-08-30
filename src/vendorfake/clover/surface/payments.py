"""The payments surface: an external-tender payment record on an order.

``POST /v3/merchants/{mId}/orders/{orderId}/payments`` --
https://docs.clover.com/dev/reference/ordercreatepaymentfororder:
"Payment must include a `positive amount` and a valid `tender ID`. Note:
This endpoint references external tenders and logs them for bookkeeping
purposes. This is not for Clover credits/debit tenders."

The record answered is the documented payment object -- ``id``,
``order{id}``, ``tender{href, id}``, ``amount`` ("Total amount paid"),
``tipAmount``, ``taxAmount`` ("Tax amount paid"), ``cashbackAmount``,
``employee{id}``, ``createdTime``, ``clientCreatedTime``, ``modifiedTime``,
``offline`` (default false), ``result`` (enum SUCCESS|FAIL|INITIATED|VOIDED|
VOIDING|VOID_FAILED|AUTH|AUTH_COMPLETED|DISCOUNT|OFFLINE_RETRYING|PENDING),
``note`` -- in the shape the get-all-payments guide shows verbatim
(https://docs.clover.com/dev/docs/get-all-payments). A bookkeeping record
always succeeds, so ``result`` is ``SUCCESS``.

What paying does to the order, and its provenance:

* the order is **locked** -- "locked is automatically set by Clover" when a
  payment is taken (https://docs.clover.com/dev/docs/creating-custom-orders),
  and it moves there through the order machine, so an order that cannot be
  locked cannot be paid;
* ``paymentState`` becomes ``PAID`` when the payments on the order cover its
  ``total``, ``PARTIALLY_PAID`` otherwise -- JUDGMENT: the values are
  documented, the rule that picks one is not;
* the payment is recorded on the order as a ``payments[]`` reference and in
  the payments collection, both journalled as ``CreatePayment`` -- one
  request, one operation, two writes.

JUDGMENT: an unknown ``tender.id`` or ``employee.id`` is a 400 naming the
field; a non-positive ``amount`` is a 400 quoting the documented sentence.
Every refusal precedes both writes.
"""

from __future__ import annotations

from typing import Any

from vendorfake.clover.entities import COL, OrderEntity
from vendorfake.clover.machine import ORDER_MACHINE, OrderState
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.references import PaymentCreateRequest
from vendorfake.clover.surface.common import CloverDeps, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.state.machine import StateMachine
from vendorfake.core.state.store import Entity
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverPaymentsSurface", "payment_routes"]

CAPABILITY = "payments"

_MACHINE = StateMachine(ORDER_MACHINE)


class CloverPaymentsSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="POST",
                path="/v3/merchants/{mId}/orders/{orderId}/payments",
                capability=CAPABILITY,
                handler=self.create_payment,
                auth="bearer",
                scopes=("PAYMENTS_W",),
                operation_id="CreatePayment",
                summary="Record an external-tender payment; locks the order and sets paymentState.",
            ),
        )

    def create_payment(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        request = validate_body(PaymentCreateRequest, args.body())
        ctx = args.ctx
        orders = ctx.store.collection(COL.orders)
        order_id = args.params["orderId"]
        stored = orders.get(order_id)
        if stored is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Order {order_id} was not found.", field="orderId")
        order = OrderEntity.from_entity(stored)
        if order.merchant_id != merchant_id or order.is_deleted:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail=f"Order {order_id} was not found.", field="orderId")

        if request.amount <= 0:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="Payment must include a positive amount and a valid tender ID.",
                field="amount",
            )
        if ctx.store.collection(COL.tenders).get(request.tender.id) is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Tender {request.tender.id} was not found; a merchant's tenders are at /tenders.",
                field="tender.id",
            )
        if request.employee is not None and ctx.store.collection(COL.employees).get(request.employee.id) is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Employee {request.employee.id} was not found.",
                field="employee.id",
            )
        # The move to locked is the machine's to allow; an order already
        # locked (a second payment) stays there.
        current_state = OrderState.OPEN.value if order.state is None else order.state.lower()
        if current_state != OrderState.LOCKED.value:
            _MACHINE.assert_transition(current_state, OrderState.LOCKED.value, f"Order {order.id}")

        now = int(ctx.clock.now())
        payment_id = self._deps.ids.payment()
        base = self._deps.config.base_url
        payment = compact(
            {
                "id": payment_id,
                "order": {"id": order.id},
                "tender": {
                    "href": f"{base}/v3/merchants/{merchant_id}/tenders/{request.tender.id}",
                    "id": request.tender.id,
                },
                "amount": request.amount,
                "tipAmount": request.tipAmount,
                "taxAmount": request.taxAmount,
                "cashbackAmount": 0,
                "employee": None if request.employee is None else {"id": request.employee.id},
                "createdTime": now,
                "clientCreatedTime": now,
                "modifiedTime": now,
                "offline": request.offline,
                "result": "SUCCESS",
                "note": request.note,
            }
        )
        paid_so_far = _paid(ctx, order) + request.amount
        payment_state = "PAID" if paid_so_far >= order.total else "PARTIALLY_PAID"

        def mutate(draft: Entity) -> None:
            draft["payments"] = [*draft.get("payments", []), {"id": payment_id}]
            draft["paymentState"] = payment_state
            if current_state != OrderState.LOCKED.value:
                draft["state"] = OrderState.LOCKED.value
            draft["modifiedTime"] = now

        ctx.store.collection(COL.payments).insert(payment, {"operation_id": "CreatePayment"})
        orders.update(order.id, mutate, meta={"operation_id": "CreatePayment"})
        return json_(_project(payment))


def payment_routes(deps: CloverDeps) -> tuple[Route, ...]:
    return CloverPaymentsSurface(deps).routes()


def _paid(ctx: Any, order: OrderEntity) -> int:
    payments = ctx.store.collection(COL.payments)
    total = 0
    for ref in order.payments:
        found = payments.get(str(ref.get("id")))
        amount = None if found is None else found.get("amount")
        if isinstance(amount, int) and not isinstance(amount, bool):
            total += amount
    return total


def _project(entity: dict[str, Any]) -> dict[str, Any]:
    return compact({k: v for k, v in entity.items() if k not in ("version", "created_at", "updated_at")})
