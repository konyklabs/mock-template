"""The sale lifecycle, as data the core's state machine enforces; published
at ``GET /__unit/machines``, probeable at ``POST /__unit/machines/probe``.

DOCUMENTED: ``SaleRequestBase.state`` declares the four-value enum used here;
older API 1.0 values appear in one example only (``capabilities.py``'s
``sale-status-vocabulary``). INVARIANT: terminal means no outgoing edges.
JUDGMENT: no lifecycle page states which moves are legal -- each state's own
``summary`` below gives the reading and its reason.
"""

from __future__ import annotations

from enum import StrEnum

from vendorfake.core.state.machine import MachineDef, StateDef

__all__ = ["SALE_MACHINE", "SALE_MACHINE_NAME", "SALE_STATE_FIELD", "SaleState"]


class SaleState(StrEnum):
    """The four values ``SaleRequestBase.state`` declares, in the enum's own order."""

    PARKED = "parked"
    PENDING = "pending"
    VOIDED = "voided"
    CLOSED = "closed"


SALE_MACHINE_NAME = "sale"
"""The key the machine is registered under in ``VendorDefinition.machines``."""

SALE_STATE_FIELD = "state"
"""The entity field holding it -- the schema's own member name."""

SALE_MACHINE = MachineDef(
    field=SALE_STATE_FIELD,
    initial=SaleState.PARKED.value,
    states={
        SaleState.PARKED.value: StateDef(
            summary=(
                "Saved and still editable. Takes line items, payments and edits; re-sending 'parked' is the "
                "ordinary update. DOCUMENTED value, JUDGMENT edges."
            ),
            to=(SaleState.PENDING.value, SaleState.CLOSED.value, SaleState.VOIDED.value),
            allow_self=True,
        ),
        SaleState.PENDING.value: StateDef(
            summary=(
                "In progress, with CONFIRMED line items read-only. Forward only: moving back to parked would "
                "make a read-only line item editable again."
            ),
            to=(SaleState.CLOSED.value, SaleState.VOIDED.value),
            allow_self=True,
        ),
        SaleState.VOIDED.value: StateDef(summary="Cancelled. Terminal."),
        SaleState.CLOSED.value: StateDef(
            summary=(
                "Completed and paid. Terminal: the one documented operation on a closed sale is "
                "POST /sales/{sale_id}/actions/return, which creates a NEW sale rather than editing this one."
            ),
        ),
    },
)
"""The sale lifecycle: values are the vendor's, edges are JUDGMENT (see the module docstring)."""
