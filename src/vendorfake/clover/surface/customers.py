"""The customers surface: list, filter, create.

GetCustomers ``GET /v3/merchants/{mId}/customers``
(https://docs.clover.com/dev/reference/customersgetcustomers), CreateCustomer
``POST`` on the same path
(https://docs.clover.com/dev/reference/customerscreatecustomer).

DOCUMENTED: a customer carries ``firstName``, ``lastName`` (each "Maximum 64
characters") and ``addresses[{address1, address2, city, state, zip,
country}]``; ``filter`` repeats and the clauses are ANDed
(konyklabs/roadmap#37).

JUDGMENT: a create must carry at least one of the two names ("the request
body cannot be null" is all the page says); ``customerSince`` is stamped at
creation in ms.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from vendorfake.clover.entities import COL
from vendorfake.clover.model.common import validate_body
from vendorfake.clover.model.references import CustomerCreateRequest
from vendorfake.clover.surface.common import CloverDeps, elements, owned_by, page_window, require_merchant
from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, PaginationSpec, ReplyInit, Route, UnitError, UnitErrorKind
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
                summary="Customers in the elements envelope; filter=firstName=...&filter=lastName=... (ANDed)",
                pagination=PaginationSpec(style="offset", items_path="elements"),
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
        predicate = _filters(args.query_all("filter"))
        mine = owned_by(merchant_id)
        limit, offset = page_window(args)
        rows = [row for row in args.ctx.store.collection(COL.customers).all() if mine(row) and predicate(row)]
        page = rows[offset : offset + limit]
        base = self._deps.config.base_url
        return json_(
            elements(
                [_project(row) for row in page],
                [f"{base}/v3/merchants/{merchant_id}/customers/{row['id']}" for row in page],
            )
        )

    def create_customer(self, args: HandlerArgs) -> ReplyInit:
        merchant_id = require_merchant(args)
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
                "merchant_id": merchant_id,
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
    return compact({k: v for k, v in entity.items() if k not in ("version", "created_at", "updated_at", "merchant_id")})


def _filters(raws: Sequence[str]) -> Any:
    """``filter=firstName=Ada`` (repeatable, ANDed): equality on firstName,
    lastName or id. No filter at all is a predicate that keeps every row."""
    clauses: list[tuple[str, str]] = []
    for raw in raws:
        field, separator, value = raw.partition("=")
        field = field.strip()
        if not separator or field not in _FILTERABLE:
            raise UnitError(
                UnitErrorKind.INVALID_VALUE,
                detail=f"filter {raw!r} is not <field>=<value> on one of {', '.join(sorted(_FILTERABLE))}.",
                field="filter",
            )
        clauses.append((field, value.strip()))
    return lambda row: all(str(row.get(field, "")) == wanted for field, wanted in clauses)
