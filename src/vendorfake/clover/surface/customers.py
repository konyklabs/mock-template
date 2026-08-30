"""The customers surface: list, filter, create.

====================  ========================================================
GetCustomers          ``GET  /v3/merchants/{mId}/customers``
                      https://docs.clover.com/dev/reference/customersgetcustomers
CreateCustomer        ``POST /v3/merchants/{mId}/customers``
                      https://docs.clover.com/dev/reference/customerscreatecustomer
====================  ========================================================

Documented: a customer carries ``firstName``, ``lastName`` (each "Maximum 64
characters") and ``addresses[{address1, address2, city, state, zip,
country}]``; the list takes ``filter`` and the elements envelope. As on the
orders list, one ``filter`` per request: the core collapses a repeated query
key to its last value (``surface/orders.py`` says why), so
``filter=firstName=Ada`` and ``filter=lastName=Lovelace`` are two requests
here rather than one.

JUDGMENT: a create must carry at least one of the two names (the page says
only "the request body cannot be null"); ``customerSince`` is stamped at
creation, in ms, because the field is documented and a consumer displaying
it needs a value.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.clover.entities import COL
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.references import CustomerCreateRequest
from vendorfake.clover.surface.common import CloverDeps, elements, page_window, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact

__all__ = ["CAPABILITY", "CloverCustomersSurface", "customer_routes"]

CAPABILITY = "customers"

_FILTERABLE = frozenset({"firstName", "lastName", "id"})


class CloverCustomersSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: CloverDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        base = "/v3/merchants/{mId}/customers"
        return (
            Route(
                method="GET",
                path=base,
                capability=CAPABILITY,
                handler=self.list_customers,
                auth="bearer",
                scopes=("CUSTOMERS_R",),
                operation_id="GetCustomers",
                summary="Customers in the elements envelope; filter=firstName=... or lastName=...",
            ),
            Route(
                method="POST",
                path=base,
                capability=CAPABILITY,
                handler=self.create_customer,
                auth="bearer",
                scopes=("CUSTOMERS_W",),
                operation_id="CreateCustomer",
                summary="Create a customer with firstName, lastName and addresses.",
                example_body={"firstName": "Ada", "lastName": "Lovelace"},
            ),
        )

    def list_customers(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
        predicate = _filter(args.query("filter"))
        limit, offset = page_window(args)
        rows = [row for row in args.ctx.store.collection(COL.customers).all() if predicate(row)]
        page = rows[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [_project(row) for row in page],
                [f"{base}/v3/merchants/{merchant_id}/customers/{row['id']}" for row in page],
            )
        )

    def create_customer(self, args: HandlerArgs) -> ReplyInit:
        require_merchant(args)
        request = validate_body(CustomerCreateRequest, args.body())
        if not request.firstName and not request.lastName:
            raise UnitError(
                UnitErrorKind.MISSING_FIELD,
                detail="A customer needs a firstName or a lastName.",
                field="firstName",
            )
        entity = compact(
            {
                "id": self._deps.ids.customer(),
                "firstName": request.firstName,
                "lastName": request.lastName,
                "customerSince": int(args.ctx.clock.now()),
                "addresses": [
                    compact(address.model_dump()) for address in (request.addresses or []) if address.model_fields_set
                ]
                or None,
            }
        )
        stored = args.ctx.store.collection(COL.customers).insert(entity, {"operation_id": "CreateCustomer"})
        return json_(_project(stored))


def customer_routes(deps: CloverDeps) -> tuple[Route, ...]:
    return CloverCustomersSurface(deps).routes()


def _project(entity: Mapping[str, Any]) -> dict[str, Any]:
    return compact({k: v for k, v in entity.items() if k not in ("version", "created_at", "updated_at")})


def _filter(raw: str | None) -> Any:
    """``filter=firstName=Ada``: equality on firstName, lastName or id."""
    if raw is None:
        return lambda row: True
    field, separator, value = raw.partition("=")
    field = field.strip()
    if not separator or field not in _FILTERABLE:
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail=f"filter {raw!r} is not <field>=<value> on one of {', '.join(sorted(_FILTERABLE))}.",
            field="filter",
        )
    wanted = value.strip()
    return lambda row: str(row.get(field, "")) == wanted
