"""Registers tag: list, one register, open/close actions, and the payments summary.

DOCUMENTED: ListRegisters, GetRegisterByID, OpenRegister, CloseRegister (scopes
``register:close`` + ``payment_types:read``), RegisterPaymentsSummary. Closing fires
``register_closure.create``, synthesised here since no REST resource exists for a closure
(``model/webhooks.py``'s ``project_register_closure`` is the delivered payload).

JUDGMENT: repeat open/close is a 409; a closure's sequence number counts per-register
from 1; ``payments_summary`` reports the most recent closure, not an all-time total. A
closure's totals are payments actually taken while open, summed per type -- the close
request's own declared totals are validated (422 on an unresolvable payment type) but
discarded, since the schema gives no expected/counted pair to reconcile and summing both
would double-count the same money.
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
            # No `example_body`: closing an already-closed register is a 409, so
            # C18's repeat-drive can't target this route. See surface/sales.py.
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
        # Body read before the path is resolved: malformed JSON is a 400
        # regardless of the register id (conformance C04).
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
        # Validated, not summed: see the module docstring.
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
        # Inserted after the register update, so the webhook sees it already closed.
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
            # JUDGMENT: 404 rather than an empty summary with nulls.
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
        """Validate (not report; see module docstring) the close request's declared totals.

        422 on a ``payment_type_id`` this retailer doesn't have; ``to_minor`` raises its
        own 422 on a non-decimal ``payments[n].total``.
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
        """Every payment this register actually took while open.

        JUDGMENT window: ``[register_open_time, closed_at]``, compared as strings since
        ``wire_time`` spells every instant RFC 3339-to-the-second. It alone can't dedupe
        two closes in the same second, so each closure also records the payment ids it
        consumed (``counted_payment_ids``) and a later closure skips them.

        JUDGMENT: a voided sale's payments are excluded (the till never took that money);
        a parked or pending sale's is not -- it's a real layby part-payment.
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
