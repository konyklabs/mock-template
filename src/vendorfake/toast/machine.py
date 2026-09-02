"""The two Toast lifecycles, as data the core's state machine enforces.

FOR: stating which values a check's ``paymentStatus`` and an order's
``guestOrderStatus`` may hold and which moves between them are legal, once, as
tables -- so that the rule is enforced by the core, published at
``GET /__unit/machines``, and probeable at ``POST /__unit/machines/probe``
without any consumer importing this module.

INVARIANT: **terminal means no outgoing edges, and nothing else.** The core
derives terminality from an empty ``to`` tuple.

Check ``paymentStatus`` -- PARTIAL
----------------------------------
DOCUMENTED values: ``OPEN | PAID | CLOSED`` on the Check schema
(https://doc.toasttab.com/toast-api-specifications/toast-orders-api.yaml), and
``"paymentStatus": "VOIDED"`` in the void walkthrough's result
(https://doc.toasttab.com/doc/devguide/apiVoidOrder.html) -- a fourth value the
schema's enum does not list, which is why the machine carries it.

The transitions, each labelled:

* ``OPEN -> CLOSED`` when the payments on the check cover its ``totalAmount``
  and none of them awaits a tip -- DOCUMENTED: the schema describes ``CLOSED``
  as "there is no remaining amount due on this check", and the payment
  walkthrough answers ``CLOSED`` for an OTHER payment covering the total
  (https://doc.toasttab.com/doc/devguide/apiCreatingAnOrderWithPaymentInformation.html).
  The fidelity corpus found this (konyklabs/roadmap#56); the unit said
  ``PAID`` before.
* ``OPEN -> PAID`` when a CREDIT payment covers the check and its tip has not
  been adjusted -- DOCUMENTED value description: "a credit card payment was
  applied, but the tip has not been adjusted".
* ``PAID -> CLOSED`` when that tip is adjusted (``PATCH .../payments/{guid}``
  with ``tipAmount``) -- the value descriptions imply it; JUDGMENT that the
  tip PATCH is the adjusting act.
* ``OPEN -> VOIDED``, ``PAID -> VOIDED`` and ``CLOSED -> VOIDED`` through
  ``POST /orders/{guid}/void``, documented as voiding "the order and its
  payments" -- the void walkthrough voids a check an OTHER payment had closed.
  "Once an order has been voided, it can not be updated", so ``VOIDED`` is
  terminal.

Order ``guestOrderStatus`` -- PARTIAL
-------------------------------------
DOCUMENTED: the ``guest_order_status`` webhook page lists the transitions
``RECEIVED -> IN_PREPARATION | READY_FOR_PICKUP | CLOSED | VOIDED``
(https://doc.toasttab.com/doc/devguide/devOrdersWebhookRef.html, the
guestOrderStatusUpdated section). The field is absent from the Order schema and
present in the void example (``"guestOrderStatus": "VOIDED"``).

JUDGMENT: the edges *from* ``IN_PREPARATION`` and ``READY_FOR_PICKUP`` are this
project's reading of a kitchen flow -- forward only, never back to
``RECEIVED`` -- and ``CLOSED``/``VOIDED`` are terminal. An API order starts
``RECEIVED``; only the void route moves it (to ``VOIDED``); every other edge is
the control plane's.

``approvalStatus`` is deliberately not a machine: an order created through the
API is ``APPROVED`` and stays so (JUDGMENT -- the documented values
``NEEDS_APPROVAL | APPROVED | FUTURE | NOT_APPROVED`` belong to POS approval
flows this package does not model).
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
"""The keys the two machines are registered under in ``VendorDefinition.machines``."""

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
