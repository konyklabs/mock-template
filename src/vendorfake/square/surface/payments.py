"""The Payments surface: external payments against orders. CreatePayment, GetPayment, CompletePayment,
CancelPayment let an integration run the payment cycle without a card, nonce or processor (``source_id``
is always EXTERNAL). https://developer.squareup.com/reference/square/payments-api/create-payment
https://developer.squareup.com/reference/square/payments-api/get-payment https://developer.squareup.com/reference/square/payments-api/complete-payment
https://developer.squareup.com/reference/square/payments-api/cancel-payment

INVARIANT: a payment moves to COMPLETED before its order is tendered -- ``payment.created``,
``payment.updated``, ``order.updated`` in that order. Order rules: DRAFT and terminal orders cannot be
paid; ``amount_money`` may not exceed what is due, checked again at capture (JUDGMENT, NOT VERIFIED)
(https://developer.squareup.com/docs/orders-api/pay-for-orders); ``tip_money`` is never counted against
what is due, per Square's ``Tender`` (https://developer.squareup.com/reference/square/objects/Tender);
a payment's location must match its order's (JUDGMENT). https://developer.squareup.com/reference/square/enums/OrderState

SHRINK (prototype): PENDING, FAILED, refunds, UpdatePayment, ListPayments and the delay/auto-cancel clock
are not modelled.
"""

from __future__ import annotations

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
from vendorfake.square.entities import COL, LocationEntity, Money, OrderEntity, PaymentEntity
from vendorfake.square.machine import ORDER_MACHINE, PAYMENT_MACHINE, OrderState, PaymentState
from vendorfake.square.model.common import validate_body
from vendorfake.square.model.order import amount_due
from vendorfake.square.model.payment import (
    EXTERNAL_PAYMENT_TYPES,
    EXTERNAL_SOURCE_ID,
    CancelPaymentRequest,
    CompletePaymentRequest,
    CreatePaymentRequest,
    project_payment,
    version_token_of,
)
from vendorfake.square.surface.common import SquareDeps
from vendorfake.square.surface.orders import apply_tenders, capture_payment, require_order, tender_for_payment

__all__ = ["CAPABILITY", "PaymentsSurface", "payment_routes"]

CAPABILITY = "payments"
"""The capability every route below belongs to."""

_MACHINE = StateMachine(PAYMENT_MACHINE)
_ORDER_MACHINE = StateMachine(ORDER_MACHINE)


class PaymentsSurface:
    """The four Payments routes, bound to one vendor's config and id stream."""

    __slots__ = ("_deps",)

    def __init__(self, deps: SquareDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="POST",
                path="/v2/payments",
                capability=CAPABILITY,
                handler=self.create_payment,
                auth="bearer",
                scopes=("PAYMENTS_WRITE",),
                idempotency=IdempotencySpec(key_path="idempotency_key", scope="payments.create", required=True),
                # The one source this unit takes, with an external payment's two required fields.
                example_body={
                    "source_id": "EXTERNAL",
                    "amount_money": {"amount": 500, "currency": "USD"},
                    "external_details": {"type": "OTHER", "source": "Seller-recorded external payment"},
                },
                operation_id="CreatePayment",
                summary="Take an EXTERNAL payment, optionally against an order; autocomplete by default.",
            ),
            Route(
                method="GET",
                path="/v2/payments/{payment_id}",
                capability=CAPABILITY,
                handler=self.get_payment,
                auth="bearer",
                scopes=("PAYMENTS_READ",),
                operation_id="GetPayment",
                summary="Retrieve one payment.",
            ),
            Route(
                method="POST",
                path="/v2/payments/{payment_id}/complete",
                capability=CAPABILITY,
                handler=self.complete_payment,
                auth="bearer",
                scopes=("PAYMENTS_WRITE",),
                operation_id="CompletePayment",
                summary="Capture an APPROVED payment and tender its order.",
            ),
            Route(
                method="POST",
                path="/v2/payments/{payment_id}/cancel",
                capability=CAPABILITY,
                handler=self.cancel_payment,
                auth="bearer",
                scopes=("PAYMENTS_WRITE",),
                operation_id="CancelPayment",
                summary="Void an APPROVED payment.",
            ),
        )

    # -- POST /v2/payments --------------------------------------------------

    def create_payment(self, args: HandlerArgs) -> ReplyInit:
        request = validate_body(CreatePaymentRequest, args.body())
        if request.source_id != EXTERNAL_SOURCE_ID:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=(
                    f"source_id must be {EXTERNAL_SOURCE_ID!r}: this unit takes external payments only, "
                    "and models no card nonce, card on file, gift card or wallet source."
                ),
                field="source_id",
                info={"supported": [EXTERNAL_SOURCE_ID]},
            )
        external = request.external_details
        if external is None:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="external_details is required for an EXTERNAL payment.",
                field="external_details",
            )
        if external.type.upper() not in EXTERNAL_PAYMENT_TYPES:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"external_details.type must be one of {', '.join(EXTERNAL_PAYMENT_TYPES)}.",
                field="external_details.type",
                info={"allowed": list(EXTERNAL_PAYMENT_TYPES)},
            )
        if request.amount_money.amount <= 0:
            # JUDGMENT: no minimum; a zero/negative amount records nothing a tender could carry.
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail="amount_money.amount must be positive.",
                field="amount_money.amount",
            )
        if request.tip_money is not None and request.tip_money.amount < 0:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE, detail="tip_money.amount cannot be negative.", field="tip_money.amount"
            )

        ctx = args.ctx
        orders = ctx.store.collection(COL.orders)
        order: OrderEntity | None = None
        if request.order_id is not None:
            order = _payable_order(orders, request.order_id)
            _require_within_due(order, request.amount_money.amount, UnitErrorKind.INVALID_VALUE)
        location = _resolve_location(ctx, request.location_id, order)
        currency = location.currency
        if request.amount_money.currency is not None and request.amount_money.currency != currency:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"amount_money.currency must be {currency}, the location's currency.",
                field="amount_money.currency",
            )

        payments = ctx.store.collection(COL.payments)
        entity = PaymentEntity(
            id=self._deps.ids.payment(),
            location_id=location.id,
            merchant_id=location.merchant_id,
            amount_money=Money(amount=request.amount_money.amount, currency=currency),
            tip_money=None if request.tip_money is None else Money(amount=request.tip_money.amount, currency=currency),
            status=PaymentState.APPROVED.value,
            order_id=None if order is None else order.id,
            customer_id=request.customer_id,
            reference_id=request.reference_id,
            note=request.note,
            external_type=external.type.upper(),
            external_source=external.source,
            external_source_id=external.source_id,
        ).to_entity()
        stored = payments.insert(entity, {"operation_id": "CreatePayment"})
        if request.autocomplete:
            stored = self._capture(ctx, payments, PaymentEntity.from_entity(stored), "CreatePayment")
        return json_({"payment": self._project(PaymentEntity.from_entity(stored))})

    # -- GET /v2/payments/{payment_id} --------------------------------------

    def get_payment(self, args: HandlerArgs) -> ReplyInit:
        payment = _require_payment(args.ctx.store.collection(COL.payments), args.params["payment_id"])
        return json_({"payment": self._project(payment)})

    # -- POST /v2/payments/{payment_id}/complete ----------------------------

    def complete_payment(self, args: HandlerArgs) -> ReplyInit:
        """Capture an APPROVED payment; the transition is asserted first, so a COMPLETED/CANCELED
        payment refuses with no version bump and no second tender."""
        request = validate_body(CompletePaymentRequest, args.body())
        payments = args.ctx.store.collection(COL.payments)
        payment = _require_payment(payments, args.params["payment_id"])
        _check_version_token(payment, request.version_token)
        _MACHINE.assert_transition(payment.status, PaymentState.COMPLETED.value, f"Payment {payment.id}")
        if payment.order_id is not None:
            # Refuse before writing, so a finished or over-paid order leaves the payment APPROVED.
            order = _payable_order(args.ctx.store.collection(COL.orders), payment.order_id)
            _require_within_due(order, payment.amount_money.amount, UnitErrorKind.CONFLICT)
        stored = self._capture(args.ctx, payments, payment, "CompletePayment")
        return json_({"payment": self._project(PaymentEntity.from_entity(stored))})

    # -- POST /v2/payments/{payment_id}/cancel ------------------------------

    def cancel_payment(self, args: HandlerArgs) -> ReplyInit:
        """Void an APPROVED payment; a COMPLETED one is refused since a refund is not modelled here."""
        validate_body(CancelPaymentRequest, args.body())
        payments = args.ctx.store.collection(COL.payments)
        payment = _require_payment(payments, args.params["payment_id"])
        _MACHINE.assert_transition(payment.status, PaymentState.CANCELED.value, f"Payment {payment.id}")

        def mutate(draft: Entity) -> None:
            draft["status"] = PaymentState.CANCELED.value

        stored = payments.update(payment.id, mutate, meta={"operation_id": "CancelPayment"})
        return json_({"payment": self._project(PaymentEntity.from_entity(stored))})

    # -- internals ----------------------------------------------------------

    def _capture(self, ctx: UnitContext, payments: Collection, payment: PaymentEntity, operation_id: str) -> Entity:
        """Move ``payment`` to COMPLETED, then tender its order if it has one."""
        stored = capture_payment(payments, payment, None, operation_id)
        completed = PaymentEntity.from_entity(stored)
        if completed.order_id is not None:
            orders = ctx.store.collection(COL.orders)
            order = require_order(orders, completed.order_id)

            def tender(draft: Entity) -> None:
                now = ctx.clock.iso_ms()
                apply_tenders(draft, [tender_for_payment(self._deps.ids, order, completed, now)], now)

            orders.update(order.id, tender, meta={"operation_id": operation_id})
        return stored

    def _project(self, payment: PaymentEntity) -> dict[str, object]:
        return project_payment(payment, self._deps.config.application_id)


def payment_routes(deps: SquareDeps) -> tuple[Route, ...]:
    """The Payments routes for one vendor."""
    return PaymentsSurface(deps).routes()


def _require_payment(payments: Collection, payment_id: str) -> PaymentEntity:
    stored = payments.get(payment_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.NOT_FOUND,
            detail=f"Payment {payment_id} was not found.",
            field="payment_id",
        )
    return PaymentEntity.from_entity(stored)


def _payable_order(orders: Collection, order_id: str) -> OrderEntity:
    """The order a payment names, if it can still take one -- ``invalid_value`` if it doesn't exist,
    the machine's own refusal if it's finished."""
    stored = orders.get(order_id)
    if stored is None:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"Order {order_id} does not exist.",
            field="order_id",
        )
    order = OrderEntity.from_entity(stored)
    if order.state == OrderState.DRAFT.value:
        raise UnitError(
            UnitErrorKind.INVALID_TRANSITION,
            detail=f"Order {order_id} is in state DRAFT and cannot be paid. A DRAFT order cannot be paid or fulfilled.",
            field="order_id",
            info={"from": OrderState.DRAFT.value, "to": OrderState.COMPLETED.value},
        )
    _ORDER_MACHINE.assert_mutable(order.state, f"Order {order_id}")
    return order


def _resolve_location(ctx: UnitContext, requested: str | None, order: OrderEntity | None) -> LocationEntity:
    """The location a payment records at: the order's if there is one, else requested, else the
    merchant's first-seeded location."""
    locations = ctx.store.collection(COL.locations)
    if order is not None:
        if requested is not None and requested != order.location_id:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"location_id {requested} does not match order {order.id}, which is at {order.location_id}.",
                field="location_id",
                info={"order_location_id": order.location_id},
            )
        return LocationEntity.from_entity(locations.require(order.location_id))
    if requested is not None:
        stored = locations.get(requested)
        if stored is None:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"Location {requested} does not exist for this merchant.",
                field="location_id",
                info={"known": [str(entity["id"]) for entity in locations.all()]},
            )
        return LocationEntity.from_entity(stored)
    everything = locations.all()
    if not everything:
        raise UnitError(UnitErrorKind.INVALID_VALUE, detail="This merchant has no locations.", field="location_id")
    return LocationEntity.from_entity(everything[0])


def _require_within_due(order: OrderEntity, amount: int, kind: UnitErrorKind) -> None:
    """``amount`` may not exceed what is due; ``kind`` distinguishes a bad request (create) from a
    later conflict (capture)."""
    due = amount_due(order)
    if amount > due:
        raise UnitError(
            kind,
            detail=f"amount_money.amount {amount} exceeds the {due} due on order {order.id}.",
            field="amount_money.amount",
            info={"due": due, "order_id": order.id},
        )


def _check_version_token(payment: PaymentEntity, supplied: str | None) -> None:
    """The documented VERSION_MISMATCH when a caller's token is stale."""
    if supplied is None:
        return
    current = version_token_of(payment)
    if supplied != current:
        raise UnitError(
            UnitErrorKind.VERSION_CONFLICT,
            detail=f"The supplied version_token does not identify the current version of payment {payment.id}.",
            field="version_token",
            info={"id": payment.id, "current": current},
        )
