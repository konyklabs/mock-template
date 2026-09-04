"""Payment Types tag: one route, the version-cursor list.

DOCUMENTED (``GET /payment_types``, ``ListPaymentTypes``, scope
``payment_types:read``): answers ``PaymentTypeCollection``, filterable by
``outlet_id``, ``currency`` and ``only_lspay`` beyond the version parameters.
``outlet_id`` is honoured against the entity's ``outlet_ids``; ``currency`` and
``only_lspay`` have no schema member to select on and are accepted but
change nothing. Internal types are excluded per the scope's own wording (see
``model/payment_type.py``).
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route
from vendorfake.lightspeed.config import SCOPE_PAYMENT_TYPES_READ
from vendorfake.lightspeed.entities import COL, PaymentTypeEntity
from vendorfake.lightspeed.model.payment_type import project_payment_type
from vendorfake.lightspeed.paths import LIST_PAYMENT_TYPES
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select

__all__ = ["CAPABILITY", "OUTLET_FILTER_PARAM", "LightspeedPaymentTypesSurface", "payment_type_routes"]

CAPABILITY = "payment_types"

OUTLET_FILTER_PARAM = "outlet_id"
"""The one documented filter this surface honours; see the module docstring."""


class LightspeedPaymentTypesSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=LIST_PAYMENT_TYPES,
                capability=CAPABILITY,
                handler=self.list_payment_types,
                auth=BEARER_AUTH,
                scopes=(SCOPE_PAYMENT_TYPES_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListPaymentTypes",
                summary="Payment types, ascending by version; internal types excluded, as the scope says.",
            ),
        )

    def list_payment_types(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        outlet_id = args.query(OUTLET_FILTER_PARAM)
        rows = [
            row for row in select(args.ctx.store.collection(COL.payment_types).all(), query) if _visible(row, outlet_id)
        ]
        return json_(envelope([project_payment_type(row) for row in rows]))


def _visible(entity: dict[str, Any], outlet_id: str | None) -> bool:
    payment_type = PaymentTypeEntity.from_entity(entity)
    if payment_type.internal:
        return False
    if outlet_id is None:
        return True
    # Empty `outlet_ids` means every outlet (nullable, optional member).
    return not payment_type.outlet_ids or outlet_id in payment_type.outlet_ids


def payment_type_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedPaymentTypesSurface(deps).routes()
