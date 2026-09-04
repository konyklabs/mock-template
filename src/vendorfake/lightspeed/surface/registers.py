"""The Registers tag: the list, one register, the two actions, and the summary.

DOCUMENTED, five of the tag's seven operations (``button_layouts`` is the pair
this slice leaves out -- see ``capabilities.py``):

===========================================  ==========================  ==========================
``GET /registers``                           ``ListRegisters``           ``registers:read``
``GET /registers/{register_id}``             ``GetRegisterByID``         ``registers:read``
``PUT .../actions/open``                     ``OpenRegister``            ``register:open``
``PUT .../actions/close``                    ``CloseRegister``           ``register:close`` and
                                                                         ``payment_types:read``
``GET .../payments_summary``                 ``RegisterPaymentsSummary`` ``payments:read``
===========================================  ==========================  ==========================

The scope on ``CloseRegister`` is worth a note: ``surface.txt``'s machine
extraction reported it as unannotated, because the operation's description
names a **pair** (``🔒 Requires: `register:close` `payment_types:read`
scopes``) and the extractor's pattern matched a single backtick-quoted scope.
Read out of the document directly, both are required, and both are declared
below.

CLOSING A REGISTER IS THE ONE MUTATION IN THIS SLICE THAT FIRES AN EVENT.
``register_closure.create`` "fires every register close", and there is no REST
resource for a closure anywhere in the 135 documented paths -- so the closure
is synthesised here, inserted into its own collection, and that insert is what
the journal turns into the webhook. The payload the delivery carries is
``model/webhooks.py``'s ``project_register_closure``.

JUDGMENT, at their sites:

* **opening an open register, or closing a closed one, is a 409.** The schema
  documents ``is_open`` and the two actions and says nothing about repeating
  one. Answering 200 would let a consumer's "close at end of day" run twice
  and report success both times, which is the defect a fake should surface.
* **the closure's sequence number** counts closures for that register, from 1.
  The documented example prints ``"register_closure_sequence_number": 5`` and
  nothing says what it counts; per-register is the reading that makes the
  number useful.
* **``payments_summary`` reports the register's most recent closure.** The
  endpoint is documented as "payment totals for all payments types defined in
  the account for a single register" and its example prints a
  ``register_closure_id``, a sequence number and a ``register_open_time``, so
  it is a view of ONE till session rather than an all-time total.

WHAT A CLOSURE'S TOTALS ARE MADE OF, since slice L2b of konyklabs/roadmap#94
put real sales behind them: the payments the register actually TOOK while it
was open, summed per payment type. The close request's own declared totals are
validated -- a total naming a payment type this retailer does not have is a
422 -- and then they are NOT added.

JUDGMENT, and it replaces an earlier reading that summed the two. A cashier's
counted cash and the rung-up cash are the SAME money, so adding them reports
a till twice over: ring up one $10.00 cash sale, close declaring the counted
$10.00, and the summed reading answers $20.00, which is neither the declared
total nor the observed one. The specification decides it: the input's
``RegisterClosePaymentType`` and the output's
``RegisterPaymentSummaryPaymentType`` each carry ONE ``total``, described
identically ("The Total amount for this Payment Type"), with no
expected/counted pair anywhere to map the two halves onto. One number out
means one reading, and the observed one is the one this API can actually see:
a fake reporting the caller's own declaration back would assert nothing about
the sales it holds. A consumer that wants the variance has both numbers -- it
sent one of them.
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitContext, UnitError, UnitErrorKind
from vendorfake.lightspeed.config import (
    SCOPE_PAYMENT_TYPES_READ,
    SCOPE_PAYMENTS_READ,
    SCOPE_REGISTER_CLOSE,
    SCOPE_REGISTER_OPEN,
    SCOPE_REGISTERS_READ,
)
from vendorfake.lightspeed.entities import COL, PaymentTypeEntity, RegisterClosureEntity, RegisterEntity, SaleEntity
from vendorfake.lightspeed.machine import SaleState
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.money import to_minor
from vendorfake.lightspeed.model.payment_type import project_payments_summary
from vendorfake.lightspeed.model.retailer import RegisterCloseRequest, RegisterOpenRequest, project_register
from vendorfake.lightspeed.model.sale import aggregate_payments_by_type
from vendorfake.lightspeed.paths import (
    CLOSE_REGISTER,
    GET_REGISTER_BY_ID,
    LIST_REGISTERS,
    OPEN_REGISTER,
    REGISTER_PAYMENTS_SUMMARY,
)
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, stamp_version, wire_time
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select, single

__all__ = ["CAPABILITY", "LightspeedRegistersSurface", "register_routes"]

CAPABILITY = "registers"


class LightspeedRegistersSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=LIST_REGISTERS,
                capability=CAPABILITY,
                handler=self.list_registers,
                auth=BEARER_AUTH,
                scopes=(SCOPE_REGISTERS_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListRegisters",
                summary="Registers, ascending by version; after/before/page_size/deleted.",
            ),
            Route(
                method="GET",
                path=GET_REGISTER_BY_ID,
                capability=CAPABILITY,
                handler=self.get_register,
                auth=BEARER_AUTH,
                scopes=(SCOPE_REGISTERS_READ,),
                operation_id="GetRegisterByID",
                summary="One register by id.",
            ),
            Route(
                method="PUT",
                path=OPEN_REGISTER,
                capability=CAPABILITY,
                handler=self.open_register,
                auth=BEARER_AUTH,
                scopes=(SCOPE_REGISTER_OPEN,),
                operation_id="OpenRegister",
                summary="Open a register. 409 if it is already open.",
            ),
            # NO `example_body` HERE, and its absence is deliberate. This route
            # carried the vendor's published example through the chassis slice,
            # which made conformance C18 -- "drive the example mutation three
            # times on one unit, with webhooks on, off and on again" --
            # unaskable: closing an already-closed register is a 409 (see the
            # module docstring), so the second drive was refused and the check
            # failed on all four webhook-enabled profiles. The example moved to
            # `POST /sales`, which commits, announces `sale.update`, and can be
            # driven any number of times. See surface/sales.py.
            Route(
                method="PUT",
                path=CLOSE_REGISTER,
                capability=CAPABILITY,
                handler=self.close_register,
                auth=BEARER_AUTH,
                scopes=(SCOPE_REGISTER_CLOSE, SCOPE_PAYMENT_TYPES_READ),
                operation_id="CloseRegister",
                summary="Close a register and record its totals; fires register_closure.create. 409 if closed.",
            ),
            Route(
                method="GET",
                path=REGISTER_PAYMENTS_SUMMARY,
                capability=CAPABILITY,
                handler=self.payments_summary,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PAYMENTS_READ,),
                operation_id="RegisterPaymentsSummary",
                summary="Payment totals for a register's most recent closure.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_registers(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        rows = select(args.ctx.store.collection(COL.registers).all(), query)
        return json_(envelope([project_register(row) for row in rows]))

    def get_register(self, args: HandlerArgs) -> ReplyInit:
        return json_(single(project_register(self._require(args))))

    # -- actions ------------------------------------------------------------

    def open_register(self, args: HandlerArgs) -> ReplyInit:
        # THE BODY IS READ BEFORE THE PATH IS RESOLVED, and the order is
        # deliberate: a body that is not valid JSON is malformed whichever
        # register it was addressed to, so it must answer the vendor's 400
        # rather than the 404 for whatever id happened to be in the path.
        # Conformance C04 aims a malformed body at the first mutating route
        # and asserts exactly this.
        request = validate_body(RegisterOpenRequest, args.body())
        stored = self._require(args)
        register = RegisterEntity.from_entity(stored)
        if register.is_open:
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=f"Register {register.id} is already open.",
                field="register_id",
                info={"is_open": True},
            )
        opened_at = request.register_open_time or wire_time(args.ctx.clock)
        sequence_id = self._deps.ids.sequence_id()
        deps = self._deps

        def mutate(draft: dict[str, Any]) -> None:
            draft["is_open"] = True
            draft["register_open_time"] = opened_at
            draft["register_open_sequence_id"] = sequence_id
            draft.pop("register_close_time", None)
            stamp_version(draft, deps)

        updated = args.ctx.store.collection(COL.registers).update(
            register.id, mutate, meta={"operation_id": "OpenRegister"}
        )
        return json_(single(project_register(updated)))

    def close_register(self, args: HandlerArgs) -> ReplyInit:
        # The body first, for the reason `open_register` records.
        request = validate_body(RegisterCloseRequest, args.body())
        stored = self._require(args)
        register = RegisterEntity.from_entity(stored)
        if not register.is_open:
            raise UnitError(
                UnitErrorKind.INVALID_TRANSITION,
                detail=f"Register {register.id} is not open.",
                field="register_id",
                info={"is_open": False},
            )
        closed_at = wire_time(args.ctx.clock)
        # Validated, not summed: see the module docstring on what a closure's
        # totals are made of.
        self._check_declared(args.ctx, request)
        taken = self._amounts_taken(args.ctx, register, closed_at)
        payments = aggregate_payments_by_type(taken, names=self._payment_type_names(args.ctx))
        deps = self._deps

        def mutate(draft: dict[str, Any]) -> None:
            draft["is_open"] = False
            draft["register_close_time"] = closed_at
            stamp_version(draft, deps)

        updated = args.ctx.store.collection(COL.registers).update(
            register.id, mutate, meta={"operation_id": "CloseRegister"}
        )
        # The closure is inserted AFTER the register is updated, so the journal
        # entry the webhook mapper reads describes a register that is already
        # closed. The insert is what fires register_closure.create.
        closures = args.ctx.store.collection(COL.register_closures)
        sequence = 1 + sum(1 for row in closures.all() if row.get("register_id") == register.id)
        closure = RegisterClosureEntity(
            id=self._deps.ids.register_closure(),
            register_id=register.id,
            outlet_id=register.outlet_id,
            sequence_number=sequence,
            register_open_time=register.register_open_time,
            register_close_time=closed_at,
            payments=payments,
            # The ids this closure consumed, so the next one on this register
            # cannot count them again. See `_amounts_taken`.
            counted_payment_ids=[str(payment["id"]) for payment in taken if payment.get("id")],
            object_version=self._deps.versions.bump(),
        )
        closures.insert(closure.to_entity(), {"operation_id": "CloseRegister"})
        return json_(single(project_register(updated)))

    def payments_summary(self, args: HandlerArgs) -> ReplyInit:
        register = RegisterEntity.from_entity(self._require(args))
        closures = [
            RegisterClosureEntity.from_entity(row)
            for row in args.ctx.store.collection(COL.register_closures).all()
            if row.get("register_id") == register.id
        ]
        if not closures:
            # Nothing has closed this register, so there is no closure to
            # report. JUDGMENT: 404 rather than an empty summary, because every
            # member of the documented example -- the closure id, its sequence
            # number -- names a closure that does not exist, and a body full of
            # nulls would be a worse answer than a refusal.
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Register {register.id} has no closure yet; close it to produce a payments summary.",
                field="register_id",
            )
        latest = max(closures, key=lambda row: row.sequence_number)
        return json_(
            single(
                project_payments_summary(
                    payments=latest.payments,
                    register_closure_id=latest.id,
                    register_closure_sequence_number=latest.sequence_number,
                    register_open_time=latest.register_open_time,
                )
            )
        )

    # -- helpers ------------------------------------------------------------

    def _require(self, args: HandlerArgs) -> dict[str, Any]:
        register_id = args.params["register_id"]
        stored = args.ctx.store.collection(COL.registers).get(register_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND, detail=f"Register {register_id} was not found.", field="register_id"
            )
        return stored

    def _payment_type_names(self, ctx: UnitContext) -> dict[str, str]:
        return {str(row["id"]): str(row.get("name", "")) for row in ctx.store.collection(COL.payment_types).all()}

    def _check_declared(self, ctx: UnitContext, request: RegisterCloseRequest) -> None:
        """Read the close request's declared totals for their refusals.

        The totals themselves are not reported -- the module docstring records
        why -- but they are still VALIDATED, because a body this action
        accepts silently is a body a consumer cannot learn anything from. A
        total naming a payment type this retailer does not have is a 422: the
        summary reports the type's *name*, so an unresolvable id names a row
        nobody could read. An amount that is not a decimal is
        ``to_minor``'s own 422 on ``payments[n].total``.
        """
        payment_types = {
            row["id"]: PaymentTypeEntity.from_entity(row) for row in ctx.store.collection(COL.payment_types).all()
        }
        for index, declared in enumerate(request.payments):
            if payment_types.get(declared.payment_type_id) is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"payments[{index}].payment_type_id {declared.payment_type_id!r} is not a payment type.",
                    field=f"payments[{index}].payment_type_id",
                )
            to_minor(declared.total, field=f"payments[{index}].total", allow_negative=True)

    def _amounts_taken(self, ctx: UnitContext, register: RegisterEntity, closed_at: str) -> list[dict[str, Any]]:
        """Every payment this register actually took while it was open.

        THE WINDOW is the register's own: from ``register_open_time`` (or from
        the beginning of the scenario, when the register carries none) to the
        instant of this close. JUDGMENT on the boundary, because no page
        describes how the summary and a closure relate -- but the endpoint's
        documented example prints a ``register_closure_id`` and a
        ``register_open_time`` beside the totals, which is a view of ONE till
        session and is the reading taken here.

        Instants compare as strings because ``surface/common.py``'s
        :func:`wire_time` spells every one of them the same way -- RFC 3339 to
        the second, with a ``Z`` -- so lexical order is chronological order.
        Recorded rather than assumed: a scenario that seeds a sale payment with
        an offset spelling (``+00:00``) sorts differently, which is why the
        shipped seed spells them the vendor's ``Z`` way.

        THE WINDOW IS NOT WHAT KEEPS A PAYMENT OUT OF TWO CLOSURES, and it
        cannot be: those instants are spelled to the SECOND, so a close, a
        reopen and a second close inside one wall-clock second -- the ordinary
        case in a test that drives four requests in a few milliseconds -- gave
        the second closure the window ``[T, T]`` and re-admitted the first
        session's money. Every closure therefore records the payment ids it
        consumed (``counted_payment_ids``) and a later closure on the same
        register skips them. That is exact whatever the clock's resolution is,
        which the boundary arithmetic was not.

        A VOIDED SALE'S PAYMENTS ARE NOT MONEY THE TILL TOOK. JUDGMENT, beside
        the window: the state machine allows ``parked -> voided`` and an update
        rebuilds the whole document including ``payments``, so a cancelled sale
        keeps its payment rows -- and counting them would tell a consumer's
        cancel-a-sale test that the till holds cash for a sale that never
        completed. Nothing else is filtered: a ``parked`` or ``pending`` sale
        with a payment on it is a layby part-payment, which is real money in
        the drawer.
        """
        opened = register.register_open_time
        already_counted = self._counted_before(ctx, register)
        taken: list[dict[str, Any]] = []
        for row in ctx.store.collection(COL.sales).all():
            sale = SaleEntity.from_entity(row)
            if sale.state == SaleState.VOIDED.value:
                continue
            for payment in sale.payments:
                if str(payment.get("register_id", "")) != register.id:
                    continue
                if str(payment.get("id", "")) in already_counted:
                    continue
                when = str(payment.get("date", ""))
                if (opened is not None and when < opened) or when > closed_at:
                    continue
                taken.append(payment)
        return taken

    def _counted_before(self, ctx: UnitContext, register: RegisterEntity) -> set[str]:
        """Every payment id an earlier closure of this register consumed."""
        counted: set[str] = set()
        for row in ctx.store.collection(COL.register_closures).all():
            if str(row.get("register_id", "")) != register.id:
                continue
            counted.update(str(payment_id) for payment_id in RegisterClosureEntity.from_entity(row).counted_payment_ids)
        return counted


def register_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedRegistersSurface(deps).routes()
