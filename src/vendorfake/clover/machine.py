"""The Clover order lifecycle: legal ``state`` values and transitions, enforced
by the core (``GET /__unit/machines``). Terminal means no outgoing edges.

JUDGMENT: Clover documents only that ``open``/``locked`` are the values and
that ``locked`` is set on device completion
(https://docs.clover.com/dev/docs/creating-custom-orders); this project reads
``open -> locked`` with ``locked`` terminal, canonically lowercase (storage
verbatim, comparisons case-insensitive).
"""

from __future__ import annotations

from enum import StrEnum

from vendorfake.core.state.machine import MachineDef, StateDef

__all__ = ["ORDER_MACHINE", "ORDER_MACHINE_NAME", "OrderState"]


class OrderState(StrEnum):
    OPEN = "open"
    LOCKED = "locked"


ORDER_MACHINE_NAME = "order"

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
