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

__all__ = ["ORDER_MACHINE", "ORDER_MACHINE_NAME", "OrderState"]


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
