"""The merchant surface: the record and its configuration lists (employees,
tenders, order types, default service charge) that a consumer reads once to
build its configuration pickers. Endpoints documented at
https://docs.clover.com/dev/reference/merchantgetmerchant and its four
sibling ``merchantget*``/``employeeget*``/``paygetmerchanttenders`` pages:
https://docs.clover.com/dev/reference/employeegetemployees,
https://docs.clover.com/dev/reference/paygetmerchanttenders,
https://docs.clover.com/dev/reference/merchantgetordertypes,
https://docs.clover.com/dev/reference/merchantgetdefaultservicecharge.

DOCUMENTED: every list is the ``{"elements": [...]}`` envelope with
per-element ``href``. All read-only here (``CLOVER_NOT_MODELED``).

JUDGMENT: these five reads form a ``merchant`` capability of their own.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.clover.entities import COL, MerchantEntity
from vendorfake.clover.surface.common import CloverDeps, elements, owned_by, page_window, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, PaginationSpec, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverMerchantSurface", "merchant_routes"]

CAPABILITY = "merchant"


class CloverMerchantSurface:
    """The merchant record and its four configuration lists."""

    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        base = "/v3/merchants/{mId}"
        return (
            Route(
                method="GET",
                path=base,
                capability=CAPABILITY,
                handler=self.get_merchant,
                auth="bearer",
                scopes=("MERCHANT_R",),
                operation_id="GetMerchant",
                summary="The merchant record: id, name, owner and address.",
            ),
            Route(
                method="GET",
                path=f"{base}/employees",
                capability=CAPABILITY,
                handler=self.list_employees,
                auth="bearer",
                scopes=("EMPLOYEES_R",),
                operation_id="GetEmployees",
                summary="Employees: id, name, nickname, role.",
                pagination=PaginationSpec(style="offset", items_path="elements"),
            ),
            Route(
                method="GET",
                path=f"{base}/tenders",
                capability=CAPABILITY,
                handler=self.list_tenders,
                auth="bearer",
                scopes=("MERCHANT_R",),
                operation_id="GetTenders",
                summary="Tenders: id, label, labelKey, enabled, visible, opensCashDrawer, editable.",
                pagination=PaginationSpec(style="offset", items_path="elements"),
            ),
            Route(
                method="GET",
                path=f"{base}/order_types",
                capability=CAPABILITY,
                handler=self.list_order_types,
                auth="bearer",
                scopes=("MERCHANT_R",),
                operation_id="GetOrderTypes",
                summary="Order types: id, label, labelKey, taxable, isDefault, filterCategories, isHidden, fee.",
                pagination=PaginationSpec(style="offset", items_path="elements"),
            ),
            Route(
                method="GET",
                path=f"{base}/default_service_charge",
                capability=CAPABILITY,
                handler=self.get_default_service_charge,
                auth="bearer",
                scopes=("MERCHANT_R",),
                operation_id="GetDefaultServiceCharge",
                summary="The merchant's default service charge: id, name, percentageDecimal, enabled.",
            ),
        )

    def get_merchant(self, args: HandlerArgs) -> ReplyInit:
        """``id``, ``name``, ``owner{...}`` and ``address{...}`` from the
        store, the nested documents emitted as stored."""
        merchant_id = require_merchant(args)
        stored = args.ctx.store.collection(COL.merchants).get(merchant_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=f"The bearer resolved to merchant {merchant_id}, which is not in the store.",
            )
        merchant = MerchantEntity.from_entity(stored)
        return json_(
            compact({"id": merchant.id, "name": merchant.name, "owner": merchant.owner, "address": merchant.address})
        )

    def list_employees(self, args: HandlerArgs) -> ReplyInit:
        return self._list(args, COL.employees, "employees", scoped=True)

    def list_tenders(self, args: HandlerArgs) -> ReplyInit:
        return self._list(args, COL.tenders, "tenders", scoped=False)

    def list_order_types(self, args: HandlerArgs) -> ReplyInit:
        return self._list(args, COL.order_types, "order_types", scoped=True)

    def get_default_service_charge(self, args: HandlerArgs) -> ReplyInit:
        """The one default service charge, or 404 (JUDGMENT: the no-charge
        case is undocumented)."""
        require_merchant(args)
        found = args.ctx.store.collection(COL.service_charges).find(lambda e: e.get("isDefault") is True)
        if found is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail="This merchant has no default service charge.")
        return json_(_project(found, strip=("isDefault",)))

    def _list(self, args: HandlerArgs, collection: str, segment: str, *, scoped: bool) -> ReplyInit:
        """``scoped`` rows carry a ``merchant_id`` and only the path
        merchant's are listed; tenders are the unit's."""
        merchant_id = require_merchant(args)
        limit, offset = page_window(args)
        every = args.ctx.store.collection(collection).all()
        mine = owned_by(merchant_id)
        rows = [row for row in every if not scoped or mine(row)][offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [_project(row) for row in rows],
                [f"{base}/v3/merchants/{merchant_id}/{segment}/{row['id']}" for row in rows],
            )
        )


def merchant_routes(deps: CloverDeps) -> tuple[Route, ...]:
    return CloverMerchantSurface(deps).routes()


def _project(entity: Mapping[str, Any], *, strip: tuple[str, ...] = ()) -> dict[str, Any]:
    """A reference document as stored, minus this unit's internal keys.

    ``isDefault`` stays except on the default service charge, where it is
    this unit's own selector rather than a documented field.
    """
    hidden = ("version", "created_at", "updated_at", "merchant_id", *strip)
    return compact({k: v for k, v in entity.items() if k not in hidden})
