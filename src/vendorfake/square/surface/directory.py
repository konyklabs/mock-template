"""Merchant reference data: the entities an order points at.

FOR: serving the two read endpoints a consumer needs before it can create a
single order -- which location, and which catalog variation.

=============  =============================================================
ListLocations  ``GET /v2/locations``
               https://developer.squareup.com/reference/square/locations-api/list-locations
ListCatalog    ``GET /v2/catalog/list``
               https://developer.squareup.com/reference/square/catalog-api/list-catalog
=============  =============================================================

INVARIANT: **this is a separate capability from ``order-lifecycle``, and that
is a demonstration and not a taxonomy.** A consumer that only syncs the catalog
enables ``merchant-directory`` alone; one whose fixtures already hard-code
location ids switches it off and sees the documented 501 rather than a 404.
Two capabilities over one obvious surface is how this project shows that
capabilities are configuration rather than a fixed list of four.

Nothing here mutates. Both routes are reads, so neither takes an idempotency
key, neither appends to the journal and neither can emit a webhook -- which is
the whole reason this file is short.

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
behaviour. ``RetrieveCatalogObject``, ``SearchCatalogObjects`` and every write
endpoint are likewise absent: a fake catalog that can be edited is a catalog
whose fixtures drift from the seed document that defines the scenario.
"""

from __future__ import annotations

from vendorfake.core.kernel.reply import json_
from vendorfake.core.kernel.types import HandlerArgs, ReplyInit, Route, UnitError, UnitErrorKind
from vendorfake.core.util.json import compact
from vendorfake.core.util.numbers import as_int
from vendorfake.square.entities import COL
from vendorfake.square.model.catalog import ITEM, project_catalog_object, project_location

__all__ = [
    "CAPABILITY",
    "CATALOG_DEFAULT_LIMIT",
    "CATALOG_MAX_LIMIT",
    "DirectorySurface",
    "directory_routes",
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


class DirectorySurface:
    """The two reference-data routes.

    The only surface here that holds no vendor dependency: it mints no ids and
    reads no configuration, only the store. Stated rather than left implicit,
    because "this surface needs nothing" is a fact about the endpoints -- both
    are reads over seeded reference data -- and a later handler that reaches
    for ``deps`` is making a change worth noticing.
    """

    __slots__ = ()

    def routes(self) -> tuple[Route, ...]:
        return (
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
            ),
        )

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
