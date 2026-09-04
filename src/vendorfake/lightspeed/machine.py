"""The sale lifecycle, as data the core's state machine enforces.

FOR: stating which values a sale's ``state`` may hold and which moves between
them are legal, once, as a table -- so that the rule is enforced by the core,
published at ``GET /__unit/machines``, and probeable at
``POST /__unit/machines/probe`` without any consumer importing this module.

INVARIANT: **terminal means no outgoing edges, and nothing else.** The core
derives terminality from an empty ``to`` tuple.

DOCUMENTED: the four values and the field name. ``SaleRequestBase.state`` is
``{"description": "State of the sale.", "enum": ["parked", "pending",
"voided", "closed"], "example": "closed", "type": "string"}`` and it is one of
the schema's two ``required`` members (the other is ``source``); the ``Sale``
response schema declares the same four-value enum on the same field name. The
enum is lower-case, and that is worth stating because Lightspeed's *older*
API 1.0 vocabulary -- ``SAVED``, ``CLOSED``, ``LAYBY``, ``ONACCOUNT``,
``VOIDED`` -- still shows up in this document, in exactly one place: the
``initReturnSale`` operation's response EXAMPLE, which prints an API 1.0 sale
carrying BOTH ``"state": "parked"`` and ``"status": "SAVED"``. Nothing in the
2026-07 schemas declares a ``status`` member on a sale, and no schema anywhere
in the document declares ``LAYBY`` or ``ONACCOUNT`` as an enum value at all
(checked over all 373 component schemas). So the machine below is the schema's
four-value ``state``, and ``status`` is recorded as a deviation rather than
modelled -- see ``capabilities.py``'s ``sale-status-vocabulary``.

An account sale is expressed the way the schema expresses it, through
``attributes``: ``SaleRequestBase.attributes`` is "An array of attributes" with
the documented example value ``["onaccount"]``. A layby or account sale is
therefore a ``parked``/``pending`` sale carrying that attribute, not a fifth
state, which is also what makes the webhooks page's "may fire multiple times
for layby/account sales" reachable here -- such a sale is updated more than
once before it closes, and every update fires ``sale.update``.

JUDGMENT -- **every edge below.** The document declares the four values and
says nothing whatever about which moves between them are legal; there is no
sale-lifecycle page. The reading taken here, and why:

* ``parked -> pending``, ``parked -> closed``, ``parked -> voided``. A parked
  sale is the one a cashier can still change, so it can move anywhere.
* ``pending -> closed``, ``pending -> voided``, and deliberately **not**
  ``pending -> parked``. A pending sale is one whose line items may be
  ``CONFIRMED`` and therefore "added as **read-only**"
  (``SaleLineItem.status``); moving it back to parked would make a read-only
  line item editable again, which is the thing that flag exists to prevent.
* ``parked`` and ``pending`` each ``allow_self``, because re-sending the state
  a sale is already in is what ``PUT /sales/{sale_id}`` does on every ordinary
  edit -- adding a line item to a parked sale sends ``state: "parked"`` again.
* ``closed`` is **terminal**, and this is the load-bearing call. The one
  documented operation on a closed sale is the return: "Initializes a return
  for an existing **closed** sale and returns the newly created SAVED return
  sale" (``initReturnSale``). A return creates a *second* sale rather than
  editing the first, which is precisely how a system that treats a closed sale
  as a financial record behaves -- so ``PUT`` on a closed sale is refused here,
  with the 409 the chassis shapes from ``invalid_transition``. The return
  route is the one carve-out and says so at its own site: it appends the new
  sale's id to the original's ``return.return_sale_ids`` without asking the
  machine, because that is the documented effect of a documented operation.
* ``voided`` is terminal. A voided sale is the cancellation of a sale; there is
  nothing to move it to. ``sales:write`` is documented as "Create sales and
  payments, and adjust, void or return sales", which names voiding as an
  end state and returning as the alternative to it.
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
"""The sale lifecycle. The four values are the vendor's; every edge is this
project's -- see the module docstring for the reading and its reasons."""
