"""The two Toast lifecycles, as data the core's state machine enforces.

States which values a check's ``paymentStatus`` and an order's
``guestOrderStatus`` may hold and which moves are legal, published at
``GET /__unit/machines``. INVARIANT: terminal means no outgoing edges.

DOCUMENTED (https://doc.toasttab.com/toast-api-specifications/toast-orders-api.yaml,
https://doc.toasttab.com/doc/devguide/apiVoidOrder.html): Check
``paymentStatus`` is ``OPEN | PAID | CLOSED``, plus ``VOIDED`` from the void
walkthrough, a fourth value the schema's enum omits. Each transition's
rationale is on its ``StateDef.summary`` below (konyklabs/roadmap#56).

DOCUMENTED (https://doc.toasttab.com/doc/devguide/devOrdersWebhookRef.html,
guestOrderStatusUpdated): order
``guestOrderStatus`` starts ``RECEIVED`` with documented edges to every other
state. JUDGMENT: edges onward from ``IN_PREPARATION``/``READY_FOR_PICKUP``
are forward only; ``approvalStatus`` is deliberately not a machine, since an
API-created order is ``APPROVED`` and stays so.
"""

from __future__ import annotations

from enum import StrEnum

from vendorfake.core.state.machine import MachineDef, StateDef

__all__ = [
    "CHECK_MACHINE",
    "CHECK_MACHINE_NAME",
    "GUEST_ORDER_MACHINE",
    "GUEST_ORDER_MACHINE_NAME",
    "CheckPaymentStatus",
    "GuestOrderStatus",
]


class CheckPaymentStatus(StrEnum):
    """The three documented enum values plus the fourth the void example shows."""

    OPEN = "OPEN"
    PAID = "PAID"
    CLOSED = "CLOSED"
    VOIDED = "VOIDED"


class GuestOrderStatus(StrEnum):
    """The five values the guest-order-status webhook documents."""

    RECEIVED = "RECEIVED"
    IN_PREPARATION = "IN_PREPARATION"
    READY_FOR_PICKUP = "READY_FOR_PICKUP"
    CLOSED = "CLOSED"
    VOIDED = "VOIDED"


CHECK_MACHINE_NAME = "check"
GUEST_ORDER_MACHINE_NAME = "order"

CHECK_MACHINE = MachineDef(
    field="paymentStatus",
    initial=CheckPaymentStatus.OPEN.value,
    states={
        CheckPaymentStatus.OPEN.value: StateDef(
            summary="Unpaid. Takes selections, discounts and payments.",
            to=(CheckPaymentStatus.PAID.value, CheckPaymentStatus.CLOSED.value, CheckPaymentStatus.VOIDED.value),
        ),
        CheckPaymentStatus.PAID.value: StateDef(
            summary="A CREDIT payment covers totalAmount and its tip is not yet adjusted (DOCUMENTED). Takes the tip.",
            to=(CheckPaymentStatus.CLOSED.value, CheckPaymentStatus.VOIDED.value),
        ),
        CheckPaymentStatus.CLOSED.value: StateDef(
            summary="No remaining amount due (DOCUMENTED). Voidable through the order.",
            to=(CheckPaymentStatus.VOIDED.value,),
        ),
        CheckPaymentStatus.VOIDED.value: StateDef(
            summary="'Once an order has been voided, it can not be updated.' Terminal.",
        ),
    },
)
"""The check lifecycle. See the module docstring for what is documented."""

GUEST_ORDER_MACHINE = MachineDef(
    field="guestOrderStatus",
    initial=GuestOrderStatus.RECEIVED.value,
    states={
        GuestOrderStatus.RECEIVED.value: StateDef(
            summary="Documented start; documented edges to every other state.",
            to=(
                GuestOrderStatus.IN_PREPARATION.value,
                GuestOrderStatus.READY_FOR_PICKUP.value,
                GuestOrderStatus.CLOSED.value,
                GuestOrderStatus.VOIDED.value,
            ),
        ),
        GuestOrderStatus.IN_PREPARATION.value: StateDef(
            summary="Kitchen working (JUDGMENT edges: forward only).",
            to=(
                GuestOrderStatus.READY_FOR_PICKUP.value,
                GuestOrderStatus.CLOSED.value,
                GuestOrderStatus.VOIDED.value,
            ),
        ),
        GuestOrderStatus.READY_FOR_PICKUP.value: StateDef(
            summary="Ready (JUDGMENT edges: forward only).",
            to=(GuestOrderStatus.CLOSED.value, GuestOrderStatus.VOIDED.value),
        ),
        GuestOrderStatus.CLOSED.value: StateDef(summary="Fulfilled. Terminal."),
        GuestOrderStatus.VOIDED.value: StateDef(summary="Voided. Terminal."),
    },
)
"""The guest-order lifecycle. Only the first row's edges are documented."""
