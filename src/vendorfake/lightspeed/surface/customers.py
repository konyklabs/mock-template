"""Customers tag: the version-cursor list, one customer, and the three writes.

DOCUMENTED, all five operations. Status codes differ from the Products tag's: create is
201 (the only one on this vendor's resource surface) and delete is 204 (also the only
one) -- unlike Products' 200-with-ids create and empty-200 delete; neither is normalised
toward the other.

Every write fires ``customer.update`` -- the webhooks page
(https://x-series-api.lightspeedhq.com/docs/webhooks) covers "create/delete/modify,
including balance changes" under that one event. Delete is soft (``deleted_at`` set, row
kept, dropped from the list unless ``deleted=true``), so it fires the event too.

JUDGMENT: ``PUT`` is a replace, not a merge -- the schema declares the same
``CustomerBase`` body as create with no partial-update variant, so an absent ``email``
is cleared, not left alone.

Customer groups: the scenario seeds one default group; an unnamed ``customer_group_id``
joins it, an unknown one is a 422. No group routes here -- Customer Groups is deferred;
see ``capabilities.py``.
"""

from __future__ import annotations

from typing import Any

from vendorfake.core.kernel.reply import json_, no_content
from vendorfake.core.kernel.types import (
    HandlerArgs,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.lightspeed.config import SCOPE_CUSTOMERS_READ, SCOPE_CUSTOMERS_WRITE
from vendorfake.lightspeed.entities import COL, CustomerEntity
from vendorfake.lightspeed.model.common import validate_body
from vendorfake.lightspeed.model.customer import (
    CustomerBody,
    generate_customer_code,
    project_customer,
)
from vendorfake.lightspeed.paths import (
    CREATE_CUSTOMER,
    DELETE_CUSTOMER_BY_ID,
    GET_CUSTOMER_BY_ID,
    LIST_CUSTOMERS,
    UPDATE_CUSTOMER_BY_ID,
)
from vendorfake.lightspeed.surface.common import BEARER_AUTH, LightspeedDeps, stamp_version, wire_time
from vendorfake.lightspeed.surface.outlets import VERSION_CURSOR_PAGINATION
from vendorfake.lightspeed.versioning import envelope, read_list_query, select, single

__all__ = ["CAPABILITY", "LightspeedCustomersSurface", "customer_routes"]

CAPABILITY = "customers"


class LightspeedCustomersSurface:
    __slots__ = ("_deps",)

    def __init__(self, deps: LightspeedDeps) -> None:
        self._deps = deps

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path=LIST_CUSTOMERS,
                capability=CAPABILITY,
                handler=self.list_customers,
                auth=BEARER_AUTH,
                scopes=(SCOPE_CUSTOMERS_READ,),
                pagination=VERSION_CURSOR_PAGINATION,
                operation_id="ListCustomers",
                summary="Customers, ascending by version; after/before/page_size/deleted.",
            ),
            Route(
                method="GET",
                path=GET_CUSTOMER_BY_ID,
                capability=CAPABILITY,
                handler=self.get_customer,
                auth=BEARER_AUTH,
                scopes=(SCOPE_CUSTOMERS_READ,),
                operation_id="GetCustomerByID",
                summary="One customer by id.",
            ),
            Route(
                method="POST",
                path=CREATE_CUSTOMER,
                capability=CAPABILITY,
                handler=self.create_customer,
                auth=BEARER_AUTH,
                scopes=(SCOPE_CUSTOMERS_WRITE,),
                operation_id="CreateCustomer",
                summary="Create a customer; 201 with the whole record. Fires customer.update.",
            ),
            Route(
                method="PUT",
                path=UPDATE_CUSTOMER_BY_ID,
                capability=CAPABILITY,
                handler=self.update_customer,
                auth=BEARER_AUTH,
                scopes=(SCOPE_CUSTOMERS_WRITE,),
                operation_id="UpdateCustomerByID",
                summary="Replace a customer from the same CustomerBase body the create takes; 404 otherwise.",
            ),
            Route(
                method="DELETE",
                path=DELETE_CUSTOMER_BY_ID,
                capability=CAPABILITY,
                handler=self.delete_customer,
                auth=BEARER_AUTH,
                scopes=(SCOPE_CUSTOMERS_WRITE,),
                operation_id="DeleteCustomerByID",
                summary="Soft-delete a customer; documented 204, no body. Fires customer.update.",
            ),
        )

    # -- reads --------------------------------------------------------------

    def list_customers(self, args: HandlerArgs) -> ReplyInit:
        query = read_list_query(args)
        rows = select(args.ctx.store.collection(COL.customers).all(), query)
        return json_(envelope([project_customer(row) for row in rows]))

    def get_customer(self, args: HandlerArgs) -> ReplyInit:
        return json_(single(project_customer(self._require(args))))

    # -- writes -------------------------------------------------------------

    def create_customer(self, args: HandlerArgs) -> ReplyInit:
        body = validate_body(CustomerBody, args.body())
        group_id = self._resolve_group(args.ctx, body.customer_group_id)
        code = body.customer_code or generate_customer_code(body.first_name, self._deps.credential_ids.customer_code())
        customer = CustomerEntity(
            id=self._deps.ids.customer(),
            first_name=body.first_name,
            last_name=body.last_name,
            customer_code=code,
            customer_group_id=group_id,
            email=body.email,
            document=body.document(),
            object_version=self._deps.versions.bump(),
        )
        created = args.ctx.store.collection(COL.customers).insert(
            customer.to_entity(), {"operation_id": "CreateCustomer"}
        )
        # DOCUMENTED 201 with the whole record (CustomerResponse's example prints it).
        return json_(single(project_customer(created)), 201)

    def update_customer(self, args: HandlerArgs) -> ReplyInit:
        # The body first, then the 404. See `surface/registers.py::open_register`.
        body = validate_body(CustomerBody, args.body())
        stored = self._require_live(args)
        customer = CustomerEntity.from_entity(stored)
        group_id = self._resolve_group(args.ctx, body.customer_group_id, fallback=customer.customer_group_id)
        code = body.customer_code or customer.customer_code
        document = body.document()
        deps = self._deps

        def mutate(draft: dict[str, Any]) -> None:
            draft["first_name"] = body.first_name
            draft["last_name"] = body.last_name
            draft["customer_code"] = code
            draft["customer_group_id"] = group_id
            if body.email is None:
                draft.pop("email", None)
            else:
                draft["email"] = body.email
            draft["document"] = document
            stamp_version(draft, deps)

        updated = args.ctx.store.collection(COL.customers).update(
            customer.id, mutate, meta={"operation_id": "UpdateCustomerByID"}
        )
        return json_(single(project_customer(updated)))

    def delete_customer(self, args: HandlerArgs) -> ReplyInit:
        # `_require_live`: a repeat delete is a 404, not a second 204 re-firing customer.update.
        stored = self._require_live(args)
        customer = CustomerEntity.from_entity(stored)
        deleted_at = wire_time(args.ctx.clock)
        deps = self._deps

        def mutate(draft: dict[str, Any]) -> None:
            draft["deleted_at"] = deleted_at
            stamp_version(draft, deps)

        args.ctx.store.collection(COL.customers).update(
            customer.id, mutate, meta={"operation_id": "DeleteCustomerByID"}
        )
        # DOCUMENTED: `"204": {}` with no content block at all.
        return no_content()

    # -- helpers ------------------------------------------------------------

    def _resolve_group(self, ctx: UnitContext, supplied: str | None, *, fallback: str | None = None) -> str:
        """The group this customer belongs to: named, existing, or the retailer's default."""
        groups = ctx.store.collection(COL.customer_groups)
        if supplied is not None:
            if groups.get(supplied) is None:
                raise UnitError(
                    UnitErrorKind.INVALID_VALUE,
                    detail=f"customer_group_id {supplied!r} is not a customer group of this retailer.",
                    field="customer_group_id",
                )
            return supplied
        if fallback:
            return fallback
        rows = groups.all()
        if not rows:
            raise UnitError(
                UnitErrorKind.INTERNAL,
                detail=(
                    "This unit's scenario loaded no customer group. Every customer belongs to one, and the "
                    "Customer Groups tag has no create operation here, so the seed document must provide it."
                ),
            )
        return str(rows[0]["id"])

    def _require(self, args: HandlerArgs) -> dict[str, Any]:
        customer_id = args.params["customer_id"]
        stored = args.ctx.store.collection(COL.customers).get(customer_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND, detail=f"Customer {customer_id} was not found.", field="customer_id"
            )
        return stored

    def _require_live(self, args: HandlerArgs) -> dict[str, Any]:
        """The row a WRITE addresses: present, and not already deleted.

        JUDGMENT: a soft-deleted row stays readable (``GET``, ``?deleted=true``) but not
        writable -- otherwise a ``PUT`` on it would report success while every default
        list omits it.
        """
        stored = self._require(args)
        if stored.get("deleted_at") is not None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Customer {args.params['customer_id']} was deleted and can no longer be written.",
                field="customer_id",
            )
        return stored


def customer_routes(deps: LightspeedDeps) -> tuple[Route, ...]:
    return LightspeedCustomersSurface(deps).routes()
