"""The Payment Types tag: one route, the version-cursor list.

DOCUMENTED (``GET /payment_types``, operationId ``ListPaymentTypes``,
``🔒 Requires: `payment_types:read` scope``): "Returns a paginated collection
of payment types", answering ``PaymentTypeCollection``. Beyond the four version
parameters it declares three filters -- ``outlet_id`` ("Only effective when the
payment_type_controls feature is enabled"), ``currency`` and ``only_lspay``
("If true, returns only LSPay payment types").

``outlet_id`` is honoured: the entity carries ``outlet_ids`` and filtering on
it is what the parameter means. The feature flag its description mentions is
not modelled -- there is no way to read a retailer's feature flags in this
specification -- so the filter is always effective here, which is the
permissive reading and is stated at the site.

``currency`` and ``only_lspay`` are NOT modelled: ``PaymentType`` carries no
currency member at all, and the ``GlobalPaymentType`` embedded object has no
LSPay marker either, so there is nothing in the documented schema for either
filter to select on. Both are accepted and change nothing, which is recorded
here rather than silently.

THE SCOPE'S OWN WORDING drives the default: ``payment_types:read`` is "Read
payment types, **excluding internal payment types**", so an internal type is
absent from the list. See ``model/payment_type.py``.
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
    # An empty `outlet_ids` means "every outlet": the member is nullable and
    # optional, so a type that names none is not scoped to one.
    return not payment_type.outlet_ids or outlet_id in payment_type.outlet_ids


def payment_type_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedPaymentTypesSurface(deps).routes()
