"""The order lifecycle, as data the core's state machine enforces.

Values and terminal states are Square's documented ``OrderState``
(https://developer.squareup.com/reference/square/enums/OrderState): DRAFT and OPEN are updatable; COMPLETED
and CANCELED are terminal, with no outgoing edges, since the core derives terminality from an empty ``to``
tuple. CreateOrder starts at OPEN (https://developer.squareup.com/docs/orders-api/create-orders); PayOrder
moves an order to COMPLETED (https://developer.squareup.com/reference/square/orders-api/pay-order).
JUDGMENT: DRAFT -> CANCELED, since Square publishes no exhaustive transition matrix. DRAFT and OPEN allow a
self-transition -- UpdateOrder is a read-modify-write and echoing the current state back is legal -- the
terminal states do not, since Square already refuses any update to them
(https://developer.squareup.com/reference/square/orders-api/update-order).
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
"""The key this machine is registered under in ``VendorDefinition.machines``,
kept here so the control plane's probe body can't drift from it."""

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
"""The fulfillment lifecycle
(https://developer.squareup.com/reference/square/enums/FulfillmentState).
JUDGMENT: a forward move may skip states (documented as states an integration
*may* report -- https://developer.squareup.com/docs/orders-api/manage-fulfillments) and FAILED is reachable from every non-terminal state; every
non-terminal state allows a self-transition since UpdateOrder is a
read-modify-write of the whole fulfillment.
"""


class PaymentState(StrEnum):
    """``Payment.status`` values this unit holds: APPROVED, COMPLETED,
    CANCELED, FAILED (https://developer.squareup.com/reference/square/objects/Payment).
    PENDING is omitted -- this unit takes only already-approved external payments.
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
"""The payment lifecycle: APPROVED, then COMPLETED or CANCELED, per
``autocomplete`` on CreatePayment
(https://developer.squareup.com/reference/square/payments-api/create-payment).
Neither terminal state allows a self-transition -- completing or cancelling
twice is ``invalid_transition``. FAILED is declared for the documented status
set but no route enters it.
"""
