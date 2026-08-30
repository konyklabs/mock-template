"""The Clover order lifecycle, as data the core's state machine enforces.

FOR: stating which values a Clover order's ``state`` may hold and which moves
between them are legal, once, as a table -- so that the rule is enforced by the
core, published at ``GET /__unit/machines``, and probeable at
``POST /__unit/machines/probe`` without any consumer importing this module.

INVARIANT: **terminal means no outgoing edges, and nothing else.** The core
derives terminality from an empty ``to`` tuple, so ``locked`` cannot drift
into being "terminal" while still listing a transition.

JUDGMENT -- the whole machine, because Clover publishes no transition table.
What the docs actually say, all of it:

* the documented values are ``open``, ``locked``, and null -- "null is the
  default for hidden orders" (order reference field listing);
* "Clover recommends manually setting the order state value to Open"
  (https://docs.clover.com/dev/docs/creating-custom-orders);
* "locked is automatically set by Clover" when an order completes on a device
  (same page).

From that this project reads: ``open -> locked``, and ``locked`` is terminal
for state changes. No page states either rule; a consumer must not treat a
transition this machine refuses as one the real API necessarily refuses.

Casing -- JUDGMENT, because the docs themselves mix it: the create example on
creating-custom-orders sends ``"state": "Open"`` while the field listing and
other pages write ``open`` and ``locked`` in lowercase. This unit's rule,
applied at the surfaces (PR C): storage is verbatim (what the client sent),
comparisons are case-insensitive, and the machine's canonical values -- what
the seed uses and what this table declares -- are lowercase.

``paymentState`` is a plain field defaulting to ``OPEN``, not machined: this
unit has no payments surface, so nothing moves it. See ``model/order.py``.

Self-transitions: ``open`` declares ``allow_self`` because the documented way
to update an order (POST the order back with changed fields,
https://docs.clover.com/dev/docs/orderupdateorder) can legitimately echo
``"state": "open"`` on an order that is already open. ``locked`` cannot,
which keeps the terminal state closed to writes of any kind.
"""

from __future__ import annotations

from enum import StrEnum

from vendorfake.core.state.machine import MachineDef, StateDef

__all__ = ["ORDER_MACHINE", "ORDER_MACHINE_NAME", "OrderState"]


class OrderState(StrEnum):
    """The two documented non-null ``state`` values, canonically lowercase."""

    OPEN = "open"
    LOCKED = "locked"


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
        OrderState.OPEN.value: StateDef(
            summary="Updatable. 'Clover recommends manually setting the order state value to Open.'",
            to=(OrderState.LOCKED.value,),
            allow_self=True,
        ),
        OrderState.LOCKED.value: StateDef(
            summary="'locked is automatically set by Clover.' Terminal for state changes (JUDGMENT).",
        ),
    },
)
"""The order lifecycle. ``locked`` lists no transitions, which is what makes it
terminal. The whole table is JUDGMENT; see the module docstring."""
