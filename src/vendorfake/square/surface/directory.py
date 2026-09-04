"""Merchant reference data: the entities an order points at.

FOR: serving the read endpoints a consumer needs before it can create a single
order -- who the seller is, which location, and which catalog variation.

=================  =============================================================
ListMerchants      ``GET /v2/merchants``
                   https://developer.squareup.com/reference/square/merchants-api/list-merchants
RetrieveMerchant   ``GET /v2/merchants/{merchant_id}``
                   https://developer.squareup.com/reference/square/merchants-api/retrieve-merchant
ListLocations      ``GET /v2/locations``
                   https://developer.squareup.com/reference/square/locations-api/list-locations
ListCatalog        ``GET /v2/catalog/list``
                   https://developer.squareup.com/reference/square/catalog-api/list-catalog
=================  =============================================================

INVARIANT: **this is a separate capability from ``order-lifecycle``, and that
is a demonstration and not a taxonomy.** A consumer that only syncs the catalog
enables ``merchant-directory`` alone; one whose fixtures already hard-code
location ids switches it off and sees the documented 501 rather than a 404.
Two capabilities over one obvious surface is how this project shows that
capabilities are configuration rather than a fixed list of four.

Nothing here mutates. Every route is a read, so none takes an idempotency
key, none appends to the journal and none can emit a webhook -- which is the
whole reason this file is short. The catalog's other reads and its one write
live in :mod:`vendorfake.square.surface.catalog`, under the same capability.

ORDERING IS CODE-POINT, NOT LOCALE
----------------------------------
The reference sorts the catalog with ``localeCompare``, which is ICU collation:
it puts ``"a"`` before ``"B"``. Python's ``sorted`` compares code points and
puts ``"B"`` before ``"a"``. Square catalog ids are upper-case alphanumeric so
the two agree on the shipped scenario, but page order is on the wire and a
consumer seeding mixed-case ids would see a different page from each
implementation. Code point everywhere, as stated in
``vendorfake.core.util.json``: one ordering, reproducible from any language.

SHRINK (prototype): only ``ITEM`` and ``ITEM_VARIATION`` are modelled. Square's
``CatalogObjectType`` also has categories, taxes, discounts, modifiers and
more; none of them changes an order's price in this unit, and each would be a
row in the seed document and a branch in the projection rather than new
behaviour.
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
"""ListCatalog pages at 100 by default. Square publishes no default for this
endpoint -- the ``cursor`` field is documented and the page size is not -- so
this is the reference's number and is JUDGMENT."""

CATALOG_MAX_LIMIT = 1000
"""The ceiling the core clamps to. Also JUDGMENT: Square documents a maximum
for SearchOrders and not for ListCatalog."""

ME = "me"
"""RetrieveMerchant's alias for the caller's own merchant: "If the string
``me`` is supplied as the ID, then the request returns the merchant that is
currently accessible to this call."
https://developer.squareup.com/reference/square/merchants-api/retrieve-merchant
"""


class DirectorySurface:
    """The four reference-data routes.

    The only surface here that holds no vendor dependency: it mints no ids and
    reads no configuration, only the store. Stated rather than left implicit,
    because "this surface needs nothing" is a fact about the endpoints -- all
    are reads over seeded reference data -- and a later handler that reaches
    for ``deps`` is making a change worth noticing.
    """

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
        """Every merchant, under the key Square really uses.

        The response array is named ``merchant`` -- singular -- and the cursor
        is an **integer**: "cursor: The cursor generated by the previous
        response", and the example response prints ``"cursor": 1``. Both are
        Square's documented shape and neither is a typo here.
        https://developer.squareup.com/reference/square/merchants-api/list-merchants

        JUDGMENT -- the integer cursor is an offset into the merchant list,
        which is what a value of ``1`` after a page holding one merchant means.
        Square publishes no other reading, and this unit seeds exactly one
        merchant, so no shipped scenario ever emits one.
        """
        merchants = args.ctx.store.collection(COL.merchants).all()
        offset = _requested_offset(args.query("cursor"))
        page = merchants[offset:]
        return json_({"merchant": [project_merchant(entity, main_location_of(args.ctx, entity)) for entity in page]})

    # -- GET /v2/merchants/{merchant_id} ------------------------------------

    def retrieve_merchant(self, args: HandlerArgs) -> ReplyInit:
        """One merchant, resolving the documented ``me`` alias to the caller.

        ``me`` is the merchant the bearer token belongs to -- the auth adapter
        resolves ``principal_id`` to the token's ``merchant_id`` -- which is
        the call an integration makes right after OAuth connect to learn the
        ``business_name`` it just connected.
        """
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
        """Every location, unpaginated.

        Square's ListLocations takes no cursor and no limit -- "Provides
        details about all of the seller's locations" -- so neither is invented
        here. A seller with three hundred locations is not a scenario this
        fake is for.
        """
        locations = args.ctx.store.collection(COL.locations).all()
        return json_({"locations": [project_location(entity) for entity in locations]})

    # -- GET /v2/catalog/list ----------------------------------------------

    def list_catalog(self, args: HandlerArgs) -> ReplyInit:
        """Catalog objects of the requested types, cursor-paginated.

        ``types`` is a comma-separated list and defaults to ``ITEM``, which is
        Square's documented default: "If this is unspecified, the operation
        returns objects of all the top level types". This unit's only top-level
        type is ``ITEM`` -- variations are returned nested inside their item,
        never as siblings -- so the two readings coincide, and asking for
        ``ITEM_VARIATION`` explicitly returns them flat.

        Deleted objects are filtered out. Square documents ``is_deleted`` as
        "If ``true``, the object has been deleted from the database", and
        returning them from a list endpoint would make a consumer's catalog
        sync recreate rows it had just removed.
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
            # The fingerprint is the query the cursor was issued for. `limit`
            # is excluded, as it is on SearchOrders: changing the page size
            # mid-walk is legitimate, changing the filter is not.
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
    """The merchant's ``main_location_id``: its first seeded location.

    JUDGMENT. Square documents the field as "The ID of the main Location for
    this merchant" (https://developer.squareup.com/reference/square/objects/Merchant)
    and publishes nothing about how the main one is chosen -- it is set from
    the Square Dashboard. The seed document lists locations in an order, and
    the first is the one a scenario author wrote down first; ``None`` for a
    merchant with no locations at all, which ``compact`` then omits.
    """
    merchant_id = str(merchant.get("id", ""))
    for entity in ctx.store.collection(COL.locations).all():
        if entity.get("merchant_id") == merchant_id:
            return str(entity["id"])
    return None


def _requested_offset(raw: str | None) -> int:
    """ListMerchants' integer ``cursor`` as an offset, or a 400 naming it.

    Refused rather than ignored, for the reason :func:`_requested_limit` gives:
    a consumer who sent a cursor from another endpoint by mistake should learn
    so, not silently receive page one again.
    """
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
    """``"item, ITEM_VARIATION"`` -> ``{"ITEM", "ITEM_VARIATION"}``.

    Upper-cased and trimmed because a query string is typed by a human as often
    as it is built by a client, and an empty segment (``"ITEM,"``) is dropped
    rather than becoming a type named ``""`` that matches nothing.
    """
    if raw is None or not raw.strip():
        return frozenset({ITEM})
    return frozenset(part.strip().upper() for part in raw.split(",") if part.strip())


def _requested_limit(raw: str | None) -> int | None:
    """The ``limit`` query parameter as an integer, or a 400 naming it.

    Refused rather than ignored. The reference does ``Number(query('limit'))``,
    so ``limit=abc`` becomes ``NaN``, which its pagination reads as "no limit"
    -- a consumer who mistyped a page size silently receives the default and
    never learns. There is no query-string equivalent of the strict request
    models the JSON surfaces use, so the check is written out.
    """
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
