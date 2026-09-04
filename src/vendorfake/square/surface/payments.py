"""The Payments surface: external payments against orders.

FOR: letting an ordering integration run the order cycle it runs against
Square -- create a payment for an order, capture it on close, void it before
cancelling -- with the order's tenders and state moving as they would, and
without a card, a nonce or a processor.

===============  =============================================================
CreatePayment    ``POST /v2/payments``
                 https://developer.squareup.com/reference/square/payments-api/create-payment
GetPayment       ``GET  /v2/payments/{payment_id}``
                 https://developer.squareup.com/reference/square/payments-api/get-payment
CompletePayment  ``POST /v2/payments/{payment_id}/complete``
                 https://developer.squareup.com/reference/square/payments-api/complete-payment
CancelPayment    ``POST /v2/payments/{payment_id}/cancel``
                 https://developer.squareup.com/reference/square/payments-api/cancel-payment
===============  =============================================================

INVARIANT: **a payment and its order move in one request, and the payment
moves first.** CreatePayment with ``autocomplete`` inserts the payment, moves
it to COMPLETED, and only then appends a tender to the order -- three
journal entries, ``payment.created``, ``payment.updated``, ``order.updated``,
in that order -- so a subscriber that hears ``order.updated`` can already
retrieve the COMPLETED payment it names. The order update runs without an
expected version: CreatePayment takes no order version, and the order's own
optimistic concurrency belongs to UpdateOrder.

SHRINK (prototype): ``source_id`` must be ``EXTERNAL``. A card nonce
(``cnon:...``), a card on file, a gift card, a wallet -- every source that
would need a processor, a customer or a card vault -- is refused with
``invalid_value`` naming the field, and the refusal says why. ``PENDING``,
``FAILED``, refunds, ``UpdatePayment``, ``ListPayments`` and the delay /
auto-cancel clock are not modelled; see :mod:`vendorfake.square.model.payment`.

The order rules, and where each comes from
------------------------------------------
* an order in DRAFT cannot be paid: "Draft orders can be updated, but cannot
  be paid or fulfilled" (https://developer.squareup.com/reference/square/enums/OrderState);
* a terminal order cannot be paid: the order machine has no edge out of
  COMPLETED or CANCELED;
* ``amount_money`` must not exceed what is due -- JUDGMENT. Square's guide
  has the payment "for the order total"
  (https://developer.squareup.com/docs/orders-api/pay-for-orders) and
  documents nothing for an overpayment through this route; refusing it is
  the reading under which the tenders always reconcile to the order, and a
  partial payment is accepted because split tender is real. ``tip_money`` is
  on top of the amount and never counts against what is due: a tender's
  ``amount_money`` is "the total amount of the tender, including
  `tip_money`" (https://developer.squareup.com/reference/square/objects/Tender),
  and the order reports the tips it collected in ``total_tip_money``;
* **the check is made again at capture.** A hold (``autocomplete: false``)
  is *not* reserved against the due -- two holds for the whole order both
  create -- so CompletePayment re-checks ``amount_money`` against what is
  due at that moment and refuses with ``conflict`` (409) when the order has
  since been paid past it. JUDGMENT, twice: Square publishes neither whether
  an approved payment reserves the order's due nor the error a late capture
  gets, and 409 is chosen because the request was well-formed and what
  changed is the order. NOT VERIFIED;
* the payment's location is the order's. A ``location_id`` naming a
  different one is refused rather than either being silently preferred --
  JUDGMENT, the same rule the legacy CreateOrder path applies.
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
                # The one source this unit takes ("source_id should be
                # EXTERNAL"), with the two fields an external payment needs.
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
            # JUDGMENT: Square publishes no minimum for an external payment;
            # a zero or negative one records nothing a tender could carry.
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
        """Capture. "Completes (captures) a payment. By default, payments are
        set to complete immediately after they are created."

        The transition is asserted before anything moves, so completing a
        COMPLETED or a CANCELED payment is ``invalid_transition`` with no
        version bump -- and no second tender on the order.
        """
        request = validate_body(CompletePaymentRequest, args.body())
        payments = args.ctx.store.collection(COL.payments)
        payment = _require_payment(payments, args.params["payment_id"])
        _check_version_token(payment, request.version_token)
        _MACHINE.assert_transition(payment.status, PaymentState.COMPLETED.value, f"Payment {payment.id}")
        if payment.order_id is not None:
            # Refuse before writing: an order that can no longer take a tender
            # -- finished, or since paid past what this hold would apply --
            # must leave the payment APPROVED, not captured against nothing.
            order = _payable_order(args.ctx.store.collection(COL.orders), payment.order_id)
            _require_within_due(order, payment.amount_money.amount, UnitErrorKind.CONFLICT)
        stored = self._capture(args.ctx, payments, payment, "CompletePayment")
        return json_({"payment": self._project(PaymentEntity.from_entity(stored))})

    # -- POST /v2/payments/{payment_id}/cancel ------------------------------

    def cancel_payment(self, args: HandlerArgs) -> ReplyInit:
        """Void. "Cancels (voids) a payment. You can use this endpoint to
        cancel a payment with the APPROVED status."

        A COMPLETED payment is refused: Square's sentence names APPROVED, and
        the way back from a capture is a refund, which this unit does not
        model.
        """
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
        """Move ``payment`` to COMPLETED through the machine, then tender its
        order if it has one. The status write is the shared
        :func:`~vendorfake.square.surface.orders.capture_payment`, so a
        second capture is refused here exactly as it is on PayOrder."""
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
    """The order a payment names, if it can still take one.

    ``invalid_value`` on the id when it does not exist -- the payment does not
    exist yet, so what is wrong is the value sent -- and the order machine's
    own refusal when it does but is finished.
    """
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
    """The location a payment is recorded at.

    The order's when there is an order; otherwise the one requested, or the
    merchant's main location -- "If not specified, the main location is
    used." -- which is the first seeded one, as on RetrieveMerchant.
    """
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
    """``amount`` may not exceed what is due on ``order``; ``kind`` is the
    caller's reading of whose fault that is -- the request's value on create,
    a conflict with the order's later state on capture."""
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
