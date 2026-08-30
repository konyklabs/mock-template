"""The order lifecycle, as data the core's state machine enforces.

FOR: stating which values Square's ``OrderState`` may hold and which moves
between them are legal, once, as a table -- so that the rule is enforced by the
core, published at ``GET /__unit/machines``, and probeable at
``POST /__unit/machines/probe`` without any consumer importing this module.

INVARIANT: **terminal means no outgoing edges, and nothing else.** The core
derives terminality from an empty ``to`` tuple rather than storing a flag
beside it, so ``COMPLETED`` and ``CANCELED`` cannot drift into being
"terminal" while still listing a transition.

Values, verbatim, from
https://developer.squareup.com/reference/square/enums/OrderState:

    DRAFT     "Draft orders can be updated, but cannot be paid or fulfilled."
    OPEN      "Open orders can be updated."
    COMPLETED "Completed orders are fully paid. This is a terminal state."
    CANCELED  "Canceled orders are not paid. This is a terminal state."

Transitions: CreateOrder defaults to OPEN
(https://developer.squareup.com/docs/orders-api/create-orders, and the
CreateOrder success example shows ``"state": "OPEN"`` with ``"version": 1``);
DRAFT is moved to OPEN by UpdateOrder (same page); PayOrder moves an order to
COMPLETED (https://developer.squareup.com/reference/square/orders-api/pay-order);
and https://developer.squareup.com/reference/square/orders-api/update-order
states verbatim "Orders with a COMPLETED or CANCELED state cannot be updated",
which is why both terminal states have no outgoing edges here.

JUDGMENT: Square publishes no exhaustive transition matrix and no error code
for an illegal transition. ``DRAFT -> CANCELED`` is this project's reading,
carried from the reference, that an unpaid draft can be abandoned.

Self-transitions
----------------
The core forbids a self-transition unless the state declares ``allow_self``,
which is a deliberate departure from the reference -- ``assertTransition``
there returns early on ``from === to``, so paying an order that is already
COMPLETED succeeded, replaced the tenders and bumped the version again. That is
a double payment the lifecycle existed to prevent.

``DRAFT`` and ``OPEN`` declare ``allow_self`` and the two terminal states
cannot. The reason is Square's own update shape rather than convenience:
UpdateOrder takes the order object, and the documented way to use it is to send
back the order you read with the fields you want changed, including its
``version``. A consumer that echoes ``"state": "OPEN"`` on an order that is
already OPEN is making a legal, documented request -- "Open orders can be
updated" -- and refusing it would break ordinary round-trip updates. Nothing
equivalent applies to the terminal states: an update to a COMPLETED order is
refused by the documented sentence above whether or not it names a state, so
the double-pay path stays closed.
"""

from __future__ import annotations

from enum import StrEnum

from vendorfake.core.state.machine import MachineDef, StateDef

__all__ = [
    "FULFILLMENT_MACHINE",
    "FULFILLMENT_MACHINE_NAME",
    "ORDER_MACHINE",
    "ORDER_MACHINE_NAME",
    "PAYMENT_MACHINE",
    "PAYMENT_MACHINE_NAME",
    "FulfillmentState",
    "OrderState",
    "PaymentState",
]


class OrderState(StrEnum):
    """The four documented ``OrderState`` values, and no others."""

    DRAFT = "DRAFT"
    OPEN = "OPEN"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


ORDER_MACHINE_NAME = "order"
"""The key this machine is registered under in ``VendorDefinition.machines``.

Named here rather than spelled at the registration site so that the control
plane's ``{"machine": "order"}`` probe body and the vendor's mapping cannot
drift apart.
"""

ORDER_MACHINE = MachineDef(
    field="state",
    initial=OrderState.OPEN.value,
    states={
        OrderState.DRAFT.value: StateDef(
            summary="Not yet payable or fulfillable.",
            to=(OrderState.OPEN.value, OrderState.CANCELED.value),
            allow_self=True,
        ),
        OrderState.OPEN.value: StateDef(
            summary="Updatable and payable.",
            to=(OrderState.COMPLETED.value, OrderState.CANCELED.value),
            allow_self=True,
        ),
        OrderState.COMPLETED.value: StateDef(summary="Fully paid. Terminal."),
        OrderState.CANCELED.value: StateDef(summary="Not paid. Terminal."),
    },
)
"""The order lifecycle. ``COMPLETED`` and ``CANCELED`` list no transitions,
which is what makes them terminal."""


class FulfillmentState(StrEnum):
    """The six documented ``FulfillmentState`` values.
    https://developer.squareup.com/reference/square/enums/FulfillmentState
    """

    PROPOSED = "PROPOSED"
    RESERVED = "RESERVED"
    PREPARED = "PREPARED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


FULFILLMENT_MACHINE_NAME = "fulfillment"
"""The key the fulfillment machine is registered under."""

FULFILLMENT_MACHINE = MachineDef(
    field="state",
    initial=FulfillmentState.PROPOSED.value,
    states={
        FulfillmentState.PROPOSED.value: StateDef(
            summary="Proposed by the buyer; not yet accepted by the seller.",
            to=(
                FulfillmentState.RESERVED.value,
                FulfillmentState.PREPARED.value,
                FulfillmentState.COMPLETED.value,
                FulfillmentState.CANCELED.value,
                FulfillmentState.FAILED.value,
            ),
            allow_self=True,
        ),
        FulfillmentState.RESERVED.value: StateDef(
            summary="Accepted by the seller; being prepared.",
            to=(
                FulfillmentState.PREPARED.value,
                FulfillmentState.COMPLETED.value,
                FulfillmentState.CANCELED.value,
                FulfillmentState.FAILED.value,
            ),
            allow_self=True,
        ),
        FulfillmentState.PREPARED.value: StateDef(
            summary="Ready for the buyer, the courier or the carrier.",
            to=(
                FulfillmentState.COMPLETED.value,
                FulfillmentState.CANCELED.value,
                FulfillmentState.FAILED.value,
            ),
            allow_self=True,
        ),
        FulfillmentState.COMPLETED.value: StateDef(summary="Picked up, delivered or shipped. Terminal."),
        FulfillmentState.CANCELED.value: StateDef(summary="Canceled. Terminal."),
        FulfillmentState.FAILED.value: StateDef(summary="Could not be completed. Terminal."),
    },
)
"""The fulfillment lifecycle.

The states and their meanings are Square's
(https://developer.squareup.com/reference/square/enums/FulfillmentState), and
the forward path -- PROPOSED, RESERVED, PREPARED, COMPLETED, with CANCELED
reachable until completion -- is the one the fulfillments guide walks through
(https://developer.squareup.com/docs/orders-api/manage-fulfillments).

JUDGMENT, twice. First, a forward move may **skip** states: PROPOSED straight
to COMPLETED is accepted, because the guide describes the states an
integration *may* report and publishes no rule that each must be visited, and
a counter-service order that is accepted, made and handed over in one motion
has no RESERVED moment to report. Second, FAILED is reachable from every
non-terminal state for the same reason. Neither is verified against an error
Square would return for the corresponding move; a consumer must not read this
unit's acceptance of a skip as Square's.

The three terminal states list no transitions, which is what makes them
terminal, and every non-terminal state allows a self-transition because
UpdateOrder is a read-modify-write of the whole fulfillment -- echoing the
current state back with a changed ``picked_up_at`` is the ordinary case, not
an error.
"""


class PaymentState(StrEnum):
    """The ``Payment.status`` values this unit can hold.

    "Indicates whether the payment is APPROVED, PENDING, COMPLETED, CANCELED,
    or FAILED." https://developer.squareup.com/reference/square/objects/Payment
    ``PENDING`` is absent: it is the state of a card payment awaiting the
    processor, and this unit takes external payments only, which are approved
    the moment they are recorded.
    """

    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"
    FAILED = "FAILED"


PAYMENT_MACHINE_NAME = "payment"

PAYMENT_MACHINE = MachineDef(
    field="status",
    initial=PaymentState.APPROVED.value,
    states={
        PaymentState.APPROVED.value: StateDef(
            summary="Authorised and held; capture with CompletePayment or void with CancelPayment.",
            to=(PaymentState.COMPLETED.value, PaymentState.CANCELED.value),
        ),
        PaymentState.COMPLETED.value: StateDef(summary="Captured. Terminal."),
        PaymentState.CANCELED.value: StateDef(summary="Voided before capture. Terminal."),
        PaymentState.FAILED.value: StateDef(summary="Could not be taken. Terminal, and never entered here."),
    },
)
"""The payment lifecycle.

"If set to `false`, this payment will be held in an approved state until
either explicitly completed (captured) or canceled (voided)" -- ``autocomplete``
on CreatePayment
(https://developer.squareup.com/reference/square/payments-api/create-payment)
-- is the whole machine: APPROVED, then COMPLETED or CANCELED. A payment
created with ``autocomplete`` true starts in APPROVED and is moved to COMPLETED
in the same request, so the journal shows the capture as its own update.

Neither terminal state allows a self-transition: completing a COMPLETED
payment or cancelling a CANCELED one is ``invalid_transition``, for the same
reason a second PayOrder is. ``FAILED`` is declared so the published machine
matches the documented status set, and no route enters it.
"""
