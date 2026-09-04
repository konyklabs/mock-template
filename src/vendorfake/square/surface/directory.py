"""Merchant reference data: the entities an order points at.

ListMerchants, RetrieveMerchant, ListLocations and ListCatalog -- the read
endpoints a consumer needs before it can create an order. All are reads, so
none takes an idempotency key, appends to the journal, or emits a webhook.
The catalog's other reads and its one write live in
:mod:`vendorfake.square.surface.catalog`, under the same ``merchant-directory``
capability -- split from ``order-lifecycle`` as a demonstration that
capabilities are configuration, not a fixed list of four.

Catalog listing sorts by code point, not locale collation, matching
``vendorfake.core.util.json``'s one reproducible ordering.

SHRINK (prototype): only ``ITEM`` and ``ITEM_VARIATION`` are modelled; none of
Square's other ``CatalogObjectType`` values changes an order's price here.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import (
    HandlerArgs,
    PaginationSpec,
    ReplyInit,
    Route,
    UnitContext,
    UnitError,
    UnitErrorKind,
)
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import as_int
from vendorfake.square.entities import COL
from vendorfake.square.model.catalog import ITEM, project_catalog_object, project_location, project_merchant

__all__ = [
    "CAPABILITY",
    "CATALOG_DEFAULT_LIMIT",
    "CATALOG_MAX_LIMIT",
    "ME",
    "DirectorySurface",
    "directory_routes",
    "main_location_of",
]

CAPABILITY = "merchant-directory"
"""The capability both routes below belong to."""

CATALOG_DEFAULT_LIMIT = 100
"""ListCatalog pages at 100 by default. JUDGMENT -- Square documents no
default for this endpoint."""

CATALOG_MAX_LIMIT = 1000
"""The ceiling the core clamps to. JUDGMENT -- Square documents no maximum
for ListCatalog."""

ME = "me"
"""RetrieveMerchant's alias for the caller's own merchant.
https://developer.squareup.com/reference/square/merchants-api/retrieve-merchant
"""


class DirectorySurface:
    """The four reference-data routes; the only surface with no vendor
    dependency -- all routes are reads over seeded reference data."""

    __slots__ = ()

    def routes(self) -> tuple[Route, ...]:
        return (
            Route(
                method="GET",
                path="/v2/merchants",
                capability=CAPABILITY,
                handler=self.list_merchants,
                auth="bearer",
                scopes=("MERCHANT_PROFILE_READ",),
                operation_id="ListMerchants",
                summary="Every merchant the caller can reach -- one, in this unit.",
                pagination=PaginationSpec(
                    style="cursor",
                    items_path="merchant",
                    walkable=False,
                    unwalkable_reason=(
                        "Square's documented cursor here is an integer offset and the endpoint takes "
                        "no limit parameter, so the page size cannot be narrowed and no page "
                        "boundary can be forced over the single seeded merchant."
                    ),
                ),
            ),
            Route(
                method="GET",
                path="/v2/merchants/{merchant_id}",
                capability=CAPABILITY,
                handler=self.retrieve_merchant,
                auth="bearer",
                scopes=("MERCHANT_PROFILE_READ",),
                operation_id="RetrieveMerchant",
                summary="One merchant by id, or `me` for the caller's own.",
            ),
            Route(
                method="GET",
                path="/v2/locations",
                capability=CAPABILITY,
                handler=self.list_locations,
                auth="bearer",
                scopes=("MERCHANT_PROFILE_READ",),
                operation_id="ListLocations",
                summary="Every location for the seeded merchant.",
            ),
            Route(
                method="GET",
                path="/v2/catalog/list",
                capability=CAPABILITY,
                handler=self.list_catalog,
                auth="bearer",
                scopes=("ITEMS_READ",),
                operation_id="ListCatalog",
                summary="Catalog objects, filtered by type and cursor-paginated.",
                pagination=PaginationSpec(style="cursor", items_path="objects"),
            ),
        )

    # -- GET /v2/merchants --------------------------------------------------

    def list_merchants(self, args: HandlerArgs) -> ReplyInit:
        """Every merchant, under Square's documented key ``merchant``
        (singular) with an integer ``cursor``.
        https://developer.squareup.com/reference/square/merchants-api/list-merchants
        JUDGMENT: the cursor is an offset; unexercised since this unit seeds
        exactly one merchant.
        """
        merchants = args.ctx.store.collection(COL.merchants).all()
        offset = _requested_offset(args.query("cursor"))
        page = merchants[offset:]
        return json_({"merchant": [project_merchant(entity, main_location_of(args.ctx, entity)) for entity in page]})

    # -- GET /v2/merchants/{merchant_id} ------------------------------------

    def retrieve_merchant(self, args: HandlerArgs) -> ReplyInit:
        """One merchant, resolving the documented ``me`` alias to the bearer
        token's own merchant."""
        merchant_id = args.params["merchant_id"]
        if merchant_id == ME and args.auth is not None:
            merchant_id = args.auth.principal_id
        stored = args.ctx.store.collection(COL.merchants).get(merchant_id)
        if stored is None:
            raise UnitError(
                UnitErrorKind.NOT_FOUND,
                detail=f"Merchant {merchant_id} was not found.",
                field="merchant_id",
            )
        return json_({"merchant": project_merchant(stored, main_location_of(args.ctx, stored))})

    # -- GET /v2/locations --------------------------------------------------

    def list_locations(self, args: HandlerArgs) -> ReplyInit:
        """Every location, unpaginated -- ListLocations takes no cursor or limit.
        https://developer.squareup.com/reference/square/locations-api/list-locations
        """
        locations = args.ctx.store.collection(COL.locations).all()
        return json_({"locations": [project_location(entity) for entity in locations]})

    # -- GET /v2/catalog/list ----------------------------------------------

    def list_catalog(self, args: HandlerArgs) -> ReplyInit:
        """Catalog objects of the requested types, cursor-paginated.
        https://developer.squareup.com/reference/square/catalog-api/list-catalog

        ``types`` defaults to ``ITEM``, Square's documented default when
        unspecified. Deleted objects (``is_deleted``) are filtered out, so a
        catalog sync never resurrects a row it just removed.
        """
        collection = args.ctx.store.collection(COL.catalog)
        types = _requested_types(args.query("types"))
        catalog = collection.all()
        matching = sorted(
            (
                entity
                for entity in catalog
                if str(entity.get("object_type", "")) in types and entity.get("is_deleted") is not True
            ),
            # Code point, never locale collation -- see the module docstring.
            key=lambda entity: str(entity["id"]),
        )
        page = collection.paginate(
            matching,
            limit=_requested_limit(args.query("limit")),
            cursor=args.query("cursor"),
            # Fingerprint excludes limit: changing page size mid-walk is fine,
            # changing the filter is not (as on SearchOrders).
            fingerprint={"types": sorted(types)},
            default_limit=CATALOG_DEFAULT_LIMIT,
            max_limit=CATALOG_MAX_LIMIT,
        )
        return json_(
            compact(
                {
                    "objects": [project_catalog_object(entity, catalog) for entity in page.items],
                    # "The last page of the result set doesn't include a cursor."
                    "cursor": page.cursor,
                }
            )
        )


def directory_routes() -> tuple[Route, ...]:
    """The merchant-directory routes."""
    return DirectorySurface().routes()


def main_location_of(ctx: UnitContext, merchant: Mapping[str, Any]) -> str | None:
    """The merchant's ``main_location_id``. JUDGMENT: Square documents the
    field (https://developer.squareup.com/reference/square/objects/Merchant)
    but not how it's chosen; this unit takes the first seeded location, or
    ``None``.
    """
    merchant_id = str(merchant.get("id", ""))
    for entity in ctx.store.collection(COL.locations).all():
        if entity.get("merchant_id") == merchant_id:
            return str(entity["id"])
    return None


def _requested_offset(raw: str | None) -> int:
    """ListMerchants' integer ``cursor`` as an offset, or a 400 naming it."""
    if raw is None or not raw.strip():
        return 0
    value = as_int(raw, -1)
    if value < 0 or str(value) != raw.strip():
        raise UnitError(
            UnitErrorKind.INVALID_CURSOR,
            detail="cursor must be the integer returned by the previous ListMerchants response.",
            field="cursor",
            info={"supplied": raw},
        )
    return value


def _requested_types(raw: str | None) -> frozenset[str]:
    """``"item, ITEM_VARIATION"`` -> ``{"ITEM", "ITEM_VARIATION"}``, upper-cased,
    trimmed, empty segments dropped."""
    if raw is None or not raw.strip():
        return frozenset({ITEM})
    return frozenset(part.strip().upper() for part in raw.split(",") if part.strip())


def _requested_limit(raw: str | None) -> int | None:
    """The ``limit`` query parameter as an integer, or a 400 naming it --
    refused rather than silently defaulted, since query params get no
    strict-model validation."""
    if raw is None:
        return None
    value = as_int(raw, -1)
    if value <= 0 or str(value) != raw.strip():
        raise UnitError(
            UnitErrorKind.INVALID_VALUE,
            detail="limit must be a positive integer.",
            field="limit",
            info={"supplied": raw},
        )
    return value
