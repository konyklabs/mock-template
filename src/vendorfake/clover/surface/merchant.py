"""The merchant surface: the record and its configuration lists.

FOR: what a consumer reads once to build its configuration pickers -- the
merchant, its employees, tenders, order types and default service charge.

=========================  ====================================================
GetMerchant                ``GET /v3/merchants/{mId}``
                           https://docs.clover.com/dev/reference/merchantgetmerchant
GetEmployees               ``GET /v3/merchants/{mId}/employees``
                           https://docs.clover.com/dev/reference/employeegetemployees
GetTenders                 ``GET /v3/merchants/{mId}/tenders``
                           https://docs.clover.com/dev/reference/paygetmerchanttenders
GetOrderTypes              ``GET /v3/merchants/{mId}/order_types``
                           https://docs.clover.com/dev/reference/merchantgetordertypes
GetDefaultServiceCharge    ``GET /v3/merchants/{mId}/default_service_charge``
                           https://docs.clover.com/dev/reference/merchantgetdefaultservicecharge
=========================  ====================================================

Every list is the ``{"elements": [...]}`` envelope with per-element ``href``,
and every element is projected as stored: the reference pages document the
fields (employee ``name``, ``nickname``, ``role`` ADMIN|MANAGER|EMPLOYEE;
tender ``label``, ``labelKey``, ``enabled``, ``visible``, ``opensCashDrawer``,
``editable``; order type ``label``, ``labelKey``, ``taxable``, ``isDefault``,
``filterCategories``, ``isHidden``, ``fee``; service charge ``name``,
``enabled``, ``percentageDecimal`` "Percent to charge times 10000, for
example, 12.5% will be 125000") and the seed supplies documents in that
vocabulary. All read-only here (``CLOVER_NOT_MODELED``).

JUDGMENT: these routes form a ``merchant`` capability of their own -- five
reads over one merchant's configuration is a coherent thing to switch off
together, distinct from the inventory a line item points at.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.clover.entities import COL, MerchantEntity
from vendorfake.clover.surface.common import CloverDeps, elements, page_window, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverMerchantSurface", "merchant_routes"]

CAPABILITY = "merchant"
"""The capability every route below belongs to."""


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
        """``id``, ``name``, ``owner{...}`` and ``address{...}`` from the store.

        The nested documents are emitted as stored: the merchant reference
        lists both as objects with undocumented contents, and this unit adds
        no shape of its own beyond what the scenario seeded.
        """
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
        return self._list(args, COL.employees, "employees")

    def list_tenders(self, args: HandlerArgs) -> ReplyInit:
        return self._list(args, COL.tenders, "tenders")

    def list_order_types(self, args: HandlerArgs) -> ReplyInit:
        return self._list(args, COL.order_types, "order_types")

    def get_default_service_charge(self, args: HandlerArgs) -> ReplyInit:
        """The one service charge the merchant configured as default; a
        merchant without one answers 404 (JUDGMENT -- the reference documents
        the shape and not the no-charge case)."""
        require_merchant(args)
        found = args.ctx.store.collection(COL.service_charges).find(lambda e: e.get("isDefault") is True)
        if found is None:
            raise UnitError(UnitErrorKind.NOT_FOUND, detail="This merchant has no default service charge.")
        return json_(_project(found, strip=("isDefault",)))

    def _list(self, args: HandlerArgs, collection: str, segment: str) -> ReplyInit:
        merchant_id = require_merchant(args)
        limit, offset = page_window(args)
        rows = args.ctx.store.collection(collection).all()[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [_project(row) for row in rows],
                [f"{base}/v3/merchants/{merchant_id}/{segment}/{row['id']}" for row in rows],
            )
        )


def merchant_routes(deps: CloverDeps) -> tuple[Route, ...]:
    """The merchant routes for one vendor."""
    return CloverMerchantSurface(deps).routes()


def _project(entity: Mapping[str, Any], *, strip: tuple[str, ...] = ()) -> dict[str, Any]:
    """A reference document as stored, minus this unit's internal keys.

    ``isDefault`` stays: it is a documented field of an order type and of a
    tax rate. The one row that strips it is the default service charge,
    whose ``isDefault`` is this unit's own selector for *which* charge the
    ``/default_service_charge`` route answers, not a documented field.
    """
    hidden = ("version", "created_at", "updated_at", *strip)
    return compact({k: v for k, v in entity.items() if k not in hidden})
